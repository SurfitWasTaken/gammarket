"""Agent-driven live REPL (Phase 5).

Drives the discrete-event clock with the full Phase 5 agent set —
retail noise traders, an institutional speculator, competing equity
market makers, the options dealer, and the options-demand flow — and
streams updates to the matplotlib live view (the same `sim.viz`
subprocess used by `sim.repl`). The market makers provide continuous
two-sided equity liquidity; the options dealer quotes and delta-hedges
against the flow. Manual orders can still be submitted alongside the
agents.

Usage:
    python -m sim.agents_repl [--viz] [--config path/to/params.yaml]
    python -m sim.agents_repl --viz --config sim/config/params.yaml

Inside the REPL:
    step()               run one sim event
    run(n=10)            run n events
    auto([delay=0.02])   run continuously; Ctrl-C to stop
    state()              print clock time, fill count, agents, dealer δ
    reset()              rebuild the sim from `cfg` (or reload config)
    viz_on() / viz_off() start/stop the matplotlib view
    blimit / slimit / bmarket / smarket   manual orders
    cancel(order)        cancel a manual order
    show()               print full LOB depth
    book, tape, clock, agents, cfg, fills  inspect sim pieces
"""

from __future__ import annotations

import argparse
import atexit
import code
import json
import subprocess
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Optional

if __package__ in (None, ""):
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import numpy as np

from sim.agents.equity_mm import EquityMarketMaker, EquityMMConfig
from sim.agents.institution import Institution
from sim.agents.options_flow import OptionsFlow, OptionsFlowConfig
from sim.agents.options_mm import OptionsMarketMaker, OptionsMMConfig
from sim.agents.retail import Retail
from sim.config.loader import load_config
from sim.core.clock import Clock
from sim.core.events import Order, Side
from sim.core.lob import LimitOrderBook
from sim.core.tape import Tape
from sim.options.chain import build_chain, spot_from_book
from sim.options.surface import surface_from_config
from sim.snapshot import book_snapshot


SNAPSHOT_PATH: Path = Path("/tmp/gammarket_snapshot.json")
_RECENT_FILLS_MAX: int = 500


_fills_view: deque = deque(maxlen=_RECENT_FILLS_MAX)


def _on_fill(fill) -> None:
    """Bound to the LOB. Records every fill in the central tape and
    in the bounded `fills_view` deque used by the snapshot writer."""
    tape.append(fill)
    _fills_view.append(
        {
            "ts": float(fill.timestamp),
            "side": fill.aggressor_side.value,
            "price": int(fill.price),
            "qty": int(fill.qty),
        }
    )


book: LimitOrderBook = LimitOrderBook(tick_size=1, on_fill=_on_fill)
tape: Tape = Tape()
clock: Optional[Clock] = None
agents: list = []
cfg: dict = {}
viz_process: Optional[subprocess.Popen] = None
_manual_ts: list[float] = [0.0]


def _now() -> float:
    if clock is not None:
        return float(clock.now)
    _manual_ts[0] += 1.0
    return _manual_ts[0]


def _write_snapshot() -> None:
    if viz_process is None:
        return
    try:
        payload = json.dumps(book_snapshot(book, _fills_view, _now()))
    except (TypeError, ValueError):
        return
    try:
        tmp = SNAPSHOT_PATH.with_suffix(SNAPSHOT_PATH.suffix + ".tmp")
        tmp.write_text(payload)
        tmp.replace(SNAPSHOT_PATH)
    except OSError:
        pass


def _setup_sim(config: dict) -> None:
    """(Re)build the sim from `config`. Idempotent."""
    global clock, agents, cfg
    market = config["market"]
    rng = np.random.default_rng(int(market["seed"]))

    book.clear()
    tape.fills.clear()
    _fills_view.clear()
    agents = []
    cfg = dict(config)

    initial_price = int(market["initial_price"])
    tick = int(market["tick_size"])
    bid_qty = int(market["initial_bid_size"])
    ask_qty = int(market["initial_ask_size"])
    book.submit_limit(
        Order(uuid.uuid4(), "seed", Side.BUY, initial_price - tick, bid_qty, _now())
    )
    book.submit_limit(
        Order(uuid.uuid4(), "seed", Side.SELL, initial_price + tick, ask_qty, _now())
    )

    retail_cfg = config["agents"]["retail"]
    for i in range(int(retail_cfg["n_agents"])):
        agents.append(
            Retail(
                agent_id=f"retail_{i}",
                order_size_mean=float(retail_cfg["order_size_mean"]),
                direction_bias=float(retail_cfg["direction_bias"]),
                rng=rng,
                vol_feedback=float(retail_cfg.get("vol_feedback", 0.0)),
                baseline_vol_bps=float(retail_cfg.get("baseline_vol_bps", 5.0)),
                vol_ratio_cap=float(retail_cfg.get("vol_ratio_cap", 10.0)),
            )
        )
    inst_cfg = config["agents"]["institution"]
    agents.append(
        Institution(
            agent_id="inst0",
            signal_halflife=float(inst_cfg["signal_halflife"]),
            signal_sigma=float(inst_cfg["signal_sigma"]),
            threshold=float(inst_cfg["threshold"]),
            position_limit=int(inst_cfg["position_limit"]),
            quote_offset_ticks=int(inst_cfg["quote_offset_ticks"]),
            scale=float(inst_cfg["scale"]),
            signal_price_scale=float(inst_cfg["signal_price_scale"]),
            initial_price=initial_price,
            rng=rng,
        )
    )

    # Phase 3 market makers. Without these the book has no continuous
    # two-sided liquidity provider: retail trades only via market orders
    # and the institution rests a single order at a time, so the book
    # gets swept empty -> one-sided -> spread/mid == None -> erratic price.
    # Accept the "equity_mms" list (spec form) and the legacy singular
    # "equity_mm" key, mirroring run_sim._build_agents (Audit P2-2).
    mm_cfgs = config["agents"].get("equity_mms")
    if mm_cfgs is None:
        mm_cfgs = [config["agents"]["equity_mm"]]
    for mm_cfg in mm_cfgs:
        agents.append(
            EquityMarketMaker(
                agent_id=mm_cfg.get("id", "mm0"),
                config=EquityMMConfig(
                    arrival_rate=float(mm_cfg["arrival_rate"]),
                    spread_target=int(mm_cfg["spread_target"]),
                    inventory_limit=int(mm_cfg["inventory_limit"]),
                    risk_aversion=float(mm_cfg["risk_aversion"]),
                    quote_size=int(mm_cfg["quote_size"]),
                    max_orders_per_side=int(mm_cfg["max_orders_per_side"]),
                    vol_window=int(mm_cfg.get("vol_window", 20)),
                    vol_multiplier=float(mm_cfg.get("vol_multiplier", 1.0)),
                    baseline_vol_bps=float(mm_cfg.get("baseline_vol_bps", 5.0)),
                    vol_ratio_cap=float(mm_cfg.get("vol_ratio_cap", 10.0)),
                    post_only=bool(mm_cfg.get("post_only", False)),
                ),
                rng=rng,
            )
        )

    # Phase 5 options dealer + demand flow. Mirrors run_sim._build_agents:
    # the presence of agents.options_flow switches on the dealer path.
    options_mm_cfg = config["agents"].get("options_mm")
    options_flow_cfg = config["agents"].get("options_flow")
    if options_mm_cfg is not None and options_flow_cfg is not None:
        tick_size = int(market["tick_size"])
        chain = build_chain(
            float(initial_price), 0.0,
            strikes_pct=[float(p) for p in config["options"]["strikes_pct"]],
            expiries_days=[float(d) for d in config["options"]["expiries_days"]],
            tick_size=tick_size,
        )
        dealer = OptionsMarketMaker(
            agent_id="options_mm",
            config=OptionsMMConfig(
                arrival_rate=float(options_mm_cfg["arrival_rate"]),
                vol_estimate=float(options_mm_cfg["vol_estimate"]),
                spread_vols=float(options_mm_cfg["spread_vols"]),
                delta_hedge_threshold=float(options_mm_cfg["delta_hedge_threshold"]),
                gamma_limit=float(options_mm_cfg["gamma_limit"]),
                option_tick=int(options_mm_cfg.get("option_tick", tick_size)),
            ),
            rng=rng,
            chain=chain,
            surface=surface_from_config(options_mm_cfg, float(market["minutes_per_year"])),
            risk_free_rate=float(config["options"]["risk_free_rate"]),
            minutes_per_year=float(market["minutes_per_year"]),
            tick_size=tick_size,
        )
        flow = OptionsFlow(
            agent_id="options_flow",
            config=OptionsFlowConfig(
                arrival_rate=float(options_flow_cfg["arrival_rate"]),
                max_lots=int(options_flow_cfg["max_lots"]),
            ),
            rng=rng,
            dealer=dealer,
        )
        agents.append(dealer)
        agents.append(flow)

    clock = Clock(book, tape, rng, vol_window=int(market.get("vol_window", 20)))
    for a in agents:
        if isinstance(a, Retail):
            clock.register(a, float(retail_cfg["arrival_rate"]))
        elif isinstance(a, Institution):
            clock.register(a, float(inst_cfg["arrival_rate"]))
        elif isinstance(a, EquityMarketMaker):
            mm_cfg = next(m for m in mm_cfgs if m.get("id", "mm0") == a.agent_id)
            clock.register(a, float(mm_cfg["arrival_rate"]))
        elif isinstance(a, OptionsMarketMaker):
            clock.register(a, float(options_mm_cfg["arrival_rate"]))
        elif isinstance(a, OptionsFlow):
            clock.register(a, float(options_flow_cfg["arrival_rate"]))

    _write_snapshot()


def _emit(fills: list) -> None:
    for f in fills:
        side = f.aggressor_side.value
        print(f"  FILL {f.qty:>4} @ {f.price:<6} ({side})")
    _write_snapshot()


def blimit(price: int, qty: int = 1, agent: str = "you") -> Order:
    o = Order(uuid.uuid4(), agent, Side.BUY, int(price), int(qty), _now())
    _emit(book.submit_limit(o))
    return o


def slimit(price: int, qty: int = 1, agent: str = "you") -> Order:
    o = Order(uuid.uuid4(), agent, Side.SELL, int(price), int(qty), _now())
    _emit(book.submit_limit(o))
    return o


def bmarket(qty: int = 1, agent: str = "you") -> Order:
    o = Order(uuid.uuid4(), agent, Side.BUY, 0, int(qty), _now())
    _emit(book.submit_market(o))
    return o


def smarket(qty: int = 1, agent: str = "you") -> Order:
    o = Order(uuid.uuid4(), agent, Side.SELL, 0, int(qty), _now())
    _emit(book.submit_market(o))
    return o


def cancel(order) -> bool:
    ok = book.cancel(order.order_id)
    _write_snapshot()
    return ok


def show() -> None:
    """Print the LOB depth (best, full book)."""
    print("--- LOB ---")
    print(
        f"  best_bid={book.best_bid()}  best_ask={book.best_ask()}  "
        f"spread={book.spread()}  mid={book.mid()}"
    )
    print(f"  resting orders: {len(book)}")
    if book.asks:
        print("  asks (worst -> best):")
        for p in reversed(book.asks.keys()):
            print(f"    ASK {p:>6}  qty={book.depth(Side.SELL, p)}")
    if book.bids:
        print("  bids (best -> worst):")
        for p in reversed(book.bids.keys()):
            print(f"    BID {p:>6}  qty={book.depth(Side.BUY, p)}")


def _max_steps() -> int:
    """Respect the config's max_steps limit so the sim doesn't degenerate."""
    return int(cfg.get("market", {}).get("max_steps", 0))


def step() -> float:
    """Run one sim event. Returns the new clock time."""
    if clock is None:
        print("(no sim running; call reset())")
        return 0.0
    if _max_steps() and clock.step_count >= _max_steps():
        print(f"(max_steps={_max_steps()} reached)")
        return float(clock.now)
    try:
        t = clock.step()
    except StopIteration:
        print("(event heap empty)")
        return float(clock.now)
    _write_snapshot()
    return t


def run(n: int = 10) -> float:
    """Run n sim events. Returns the final clock time."""
    if clock is None:
        print("(no sim running; call reset())")
        return 0.0
    cap = _max_steps()
    for _ in range(max(0, n)):
        if cap and clock.step_count >= cap:
            print(f"(max_steps={cap} reached)")
            break
        try:
            clock.step()
        except StopIteration:
            print("(event heap empty)")
            break
    _write_snapshot()
    state()
    return float(clock.now)


def auto(delay: float = 0.02) -> None:
    """Run continuously; Ctrl-C to stop. Writes a snapshot at each
    step so the matplotlib viz updates in real time."""
    if clock is None:
        print("(no sim running; call reset())")
        return
    cap = _max_steps()
    print(f"  auto-running  delay={delay}s/step  Ctrl-C to stop")
    try:
        while True:
            if cap and clock.step_count >= cap:
                print(f"(max_steps={cap} reached)")
                break
            try:
                clock.step()
            except StopIteration:
                print("(event heap empty)")
                return
            _write_snapshot()
            time.sleep(delay)
    except KeyboardInterrupt:
        print("\n  (auto stopped)")


def state() -> None:
    """Print clock time, fill count, book summary, and institution state."""
    if clock is None:
        print("(no sim running)")
        return
    print(
        f"  t={clock.now:8.4f} min   step={clock.step_count:5d}   "
        f"fills={len(tape):4d}"
    )
    print(f"  {book()}")
    inst = next((a for a in agents if isinstance(a, Institution)), None)
    if inst is not None:
        resting = "none" if inst.resting_order_id is None else "yes"
        print(
            f"  inst: signal={inst.signal:+7.3f}  pos={inst.position:+5d}  "
            f"resting={resting}"
        )
    for mm in (a for a in agents if isinstance(a, EquityMarketMaker)):
        print(
            f"  {mm.agent_id}: pos={mm.position:+5d}  pnl={mm.total_pnl:+10.1f}  "
            f"cash={mm.cash_flow:+10.1f}"
        )
    dealer = next((a for a in agents if isinstance(a, OptionsMarketMaker)), None)
    if dealer is not None:
        mid = book.mid()
        if mid is not None:
            spot = spot_from_book(float(mid), dealer.tick_size)
            net = dealer.net_delta_lots(spot, float(clock.now))
            gamma = dealer.portfolio_gamma(spot, float(clock.now))
            print(
                f"  options_mm: trades={len(dealer.trade_log):3d}  "
                f"hedges={len(dealer.hedge_log):3d}  "
                f"gamma_rej={dealer.gamma_rejections:3d}  "
                f"pos={dealer.position:+5d}  "
                f"net_\u03b4={net:+7.4f}  "
                f"gamma={gamma:+9.3f}"
            )


def reset(seed: Optional[int] = None) -> None:
    """Rebuild the sim. If `seed` is given, override `cfg.market.seed`
    before rebuilding."""
    if not cfg:
        cfg.update(load_config())
    if seed is not None:
        cfg["market"]["seed"] = int(seed)
    _setup_sim(cfg)
    print(f"  (sim reset: {len(agents)} agents, BBO seeded, seed={cfg['market']['seed']})")


def viz_on() -> None:
    """Spawn the matplotlib live view as a subprocess."""
    global viz_process
    if viz_process is not None and viz_process.poll() is None:
        print("  (viz already running)")
        return
    _write_snapshot()
    viz_process = subprocess.Popen(
        [sys.executable, "-m", "sim.viz", "--snapshot", str(SNAPSHOT_PATH)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(
        f"  (viz started, pid={viz_process.pid}, snapshot={SNAPSHOT_PATH})"
    )


def viz_off() -> None:
    """Terminate the matplotlib live view subprocess."""
    global viz_process
    if viz_process is None or viz_process.poll() is not None:
        viz_process = None
        print("  (viz not running)")
        return
    viz_process.terminate()
    try:
        viz_process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        viz_process.kill()
    viz_process = None
    print("  (viz stopped)")


atexit.register(viz_off)


_BANNER = """
gammarket agents REPL  (Phase 5: retail + institution + equity MMs + options)

  Start: python -m sim.agents_repl [--viz] [--config path/to/params.yaml]

  step()              run one event
  run(n=10)           run n events
  auto([delay=0.02])  run continuously, Ctrl-C to stop
  state()             show clock time, fills, agents, dealer diagnostics
  reset([seed])       rebuild sim (optionally override seed)
  viz_on() / viz_off()  start/stop the matplotlib live view

  blimit / slimit / bmarket / smarket   manual orders
  cancel(order)       cancel a manual order
  show()              print full LOB depth

  book, tape, clock, agents, cfg, fills  inspect sim pieces
  Side, Order, Institution, Retail, OptionsMarketMaker  types

Tip: viz_on(); auto();   <-- watch the depth + fills animate
"""


def help() -> None:
    print(_BANNER)


def main() -> None:
    parser = argparse.ArgumentParser(description="gammarket agents REPL")
    parser.add_argument(
        "--viz", action="store_true", help="open matplotlib live view on launch"
    )
    parser.add_argument(
        "--config", type=str, default=None, help="path to config yaml"
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="override the seed from the config"
    )
    args = parser.parse_args()

    if args.config is not None:
        loaded = load_config(args.config)
    else:
        loaded = load_config()
    if args.seed is not None:
        loaded["market"]["seed"] = int(args.seed)
    _setup_sim(loaded)

    print(_BANNER)
    n_mms = len(loaded["agents"].get("equity_mms", [loaded["agents"].get("equity_mm")]))
    has_options = "options_flow" in loaded["agents"]
    print(
        f"  loaded: n_retail={int(loaded['agents']['retail']['n_agents'])}, "
        f"1 institution, {n_mms} equity MM(s)"
        + (", options dealer + flow" if has_options else "")
        + f", max_steps={loaded['market']['max_steps']}, "
        f"seed={loaded['market']['seed']}"
    )
    if args.viz:
        viz_on()

    namespace = {
        "book": book,
        "tape": tape,
        "clock": clock,
        "agents": agents,
        "cfg": loaded,
        "fills": _fills_view,
        "step": step,
        "run": run,
        "auto": auto,
        "state": state,
        "reset": reset,
        "viz_on": viz_on,
        "viz_off": viz_off,
        "blimit": blimit,
        "slimit": slimit,
        "bmarket": bmarket,
        "smarket": smarket,
        "cancel": cancel,
        "show": show,
        "help": help,
        "Side": Side,
        "Order": Order,
        "Institution": Institution,
        "Retail": Retail,
        "EquityMarketMaker": EquityMarketMaker,
        "OptionsMarketMaker": OptionsMarketMaker,
        "OptionsFlow": OptionsFlow,
    }
    code.interact(banner="", local=namespace, exitmsg="bye")


if __name__ == "__main__":
    main()
