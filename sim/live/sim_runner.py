"""Simulation runner that writes live agent state after each step.

Intended to be launched by `sim.live.launch` alongside the agent viewer
terminals and the 3D surface viz.  Reuses the existing `run_sim` wiring.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from sim.agents.equity_mm import EquityMarketMaker, EquityMMConfig
from sim.agents.institution import Institution
from sim.agents.options_flow import OptionsFlow, OptionsFlowConfig
from sim.agents.options_mm import OptionsMarketMaker, OptionsMMConfig
from sim.agents.retail import Retail, regime_from_config
from sim.config.loader import load_config
from sim.core.clock import Clock
from sim.core.events import Order, Side
from sim.core.lob import LimitOrderBook
from sim.core.tape import Tape
from sim.live.state_writer import extract_all_state, write_state
from sim.options.chain import build_chain
from sim.options.surface import surface_from_config


def _build_sim(cfg: dict) -> tuple[LimitOrderBook, Tape, Clock, list]:
    market = cfg["market"]
    rng = np.random.default_rng(int(market["seed"]))
    tick_size = int(market["tick_size"])

    tape = Tape()
    book = LimitOrderBook(tick_size=tick_size, on_fill=tape.append)

    # Seed BBO
    init_price = int(market["initial_price"])
    book.submit_limit(
        Order(uuid.uuid4(), "seed", Side.BUY, init_price - tick_size,
              int(market["initial_bid_size"]), 0.0)
    )
    book.submit_limit(
        Order(uuid.uuid4(), "seed", Side.SELL, init_price + tick_size,
              int(market["initial_ask_size"]), 0.0)
    )

    agents: list = []

    # Retail
    rc = cfg["agents"]["retail"]
    regime = regime_from_config(rc, int(market["seed"]))
    for i in range(int(rc["n_agents"])):
        agents.append(Retail(
            agent_id=f"retail_{i}",
            order_size_mean=float(rc["order_size_mean"]),
            direction_bias=float(rc["direction_bias"]),
            rng=rng,
            vol_feedback=float(rc.get("vol_feedback", 0.0)),
            baseline_vol_bps=float(rc.get("baseline_vol_bps", 5.0)),
            vol_ratio_cap=float(rc.get("vol_ratio_cap", 10.0)),
            regime=regime,
        ))

    # Institution
    ic = cfg["agents"]["institution"]
    agents.append(Institution(
        agent_id="inst0",
        signal_halflife=float(ic["signal_halflife"]),
        signal_sigma=float(ic["signal_sigma"]),
        threshold=float(ic["threshold"]),
        position_limit=int(ic["position_limit"]),
        quote_offset_ticks=int(ic["quote_offset_ticks"]),
        scale=float(ic["scale"]),
        signal_price_scale=float(ic["signal_price_scale"]),
        initial_price=init_price,
        rng=rng,
    ))

    # Equity MMs
    mm_cfgs = cfg["agents"].get("equity_mms", [cfg["agents"].get("equity_mm")])
    for mm_cfg in mm_cfgs:
        if mm_cfg is None:
            continue
        agents.append(EquityMarketMaker(
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
        ))

    # Options dealer + flow
    if "options_flow" in cfg["agents"]:
        om_cfg = cfg["agents"]["options_mm"]
        of_cfg = cfg["agents"]["options_flow"]
        chain = build_chain(
            float(init_price), 0.0,
            strikes_pct=[float(p) for p in cfg["options"]["strikes_pct"]],
            expiries_days=[float(d) for d in cfg["options"]["expiries_days"]],
            tick_size=tick_size,
        )
        dealer = OptionsMarketMaker(
            agent_id="options_mm",
            config=OptionsMMConfig(
                arrival_rate=float(om_cfg["arrival_rate"]),
                vol_estimate=float(om_cfg["vol_estimate"]),
                spread_vols=float(om_cfg["spread_vols"]),
                delta_hedge_threshold=float(om_cfg["delta_hedge_threshold"]),
                gamma_limit=float(om_cfg["gamma_limit"]),
                option_tick=int(om_cfg.get("option_tick", tick_size)),
            ),
            rng=rng,
            chain=chain,
            surface=surface_from_config(om_cfg, float(market["minutes_per_year"])),
            risk_free_rate=float(cfg["options"]["risk_free_rate"]),
            minutes_per_year=float(market["minutes_per_year"]),
            tick_size=tick_size,
        )
        flow = OptionsFlow(
            agent_id="options_flow",
            config=OptionsFlowConfig(
                arrival_rate=float(of_cfg["arrival_rate"]),
                max_lots=int(of_cfg["max_lots"]),
            ),
            rng=rng,
            dealer=dealer,
        )
        agents.append(dealer)
        agents.append(flow)

    clock = Clock(book, tape, rng, vol_window=int(market.get("vol_window", 20)))
    retail_rate = float(rc["arrival_rate"])
    inst_rate = float(ic["arrival_rate"])
    for a in agents:
        if isinstance(a, Retail):
            clock.register(a, retail_rate)
        elif isinstance(a, Institution):
            clock.register(a, inst_rate)
        elif isinstance(a, EquityMarketMaker):
            m = next((m for m in mm_cfgs if m and m.get("id", "mm0") == a.agent_id), mm_cfgs[0])
            clock.register(a, float(m["arrival_rate"]))
        elif isinstance(a, OptionsMarketMaker):
            clock.register(a, float(om_cfg["arrival_rate"]))
        elif isinstance(a, OptionsFlow):
            clock.register(a, float(of_cfg["arrival_rate"]))

    return book, tape, clock, agents


def _print_progress(clock: Clock, tape: Tape, max_steps: int) -> None:
    mid = clock.book.mid()
    bid = clock.book.best_bid()
    ask = clock.book.best_ask()
    pct = 100.0 * clock.step_count / max_steps if max_steps else 0
    mid_str = f"{mid:.1f}" if mid is not None else "—"
    spread_str = f"{ask - bid}" if bid is not None and ask is not None else "—"
    print(
        f"\r  [{pct:5.1f}%] "
        f"step={clock.step_count:4d}/{max_steps}  "
        f"t={clock.now:8.2f}  "
        f"fills={len(tape):4d}  "
        f"mid={mid_str}  "
        f"spread={spread_str}  ",
        end="", flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="gammarket live sim runner")
    parser.add_argument("--config", type=str, default=None, help="path to config yaml")
    parser.add_argument(
        "--state-path",
        default="/tmp/gammarket_agent_state.json",
        help="path for agent state output",
    )
    parser.add_argument("--step-delay", type=float, default=0.0, help="delay per step (s)")
    args = parser.parse_args()

    cfg = load_config(args.config) if args.config else load_config()
    max_steps = int(cfg["market"]["max_steps"])
    vol_window = int(cfg["market"].get("vol_window", 20))
    tick_size = int(cfg["market"]["tick_size"])

    book, tape, clock, agents = _build_sim(cfg)

    running = True

    def _on_sigint(*_: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _on_sigint)

    print(f"  gammarket live sim — {len(agents)} agents, {max_steps} max steps")
    print(f"  state → {args.state_path}")
    print("  Ctrl-C to stop early\n")

    try:
        while running and clock.step_count < max_steps:
            try:
                clock.step()
            except StopIteration:
                print("\n  (event heap empty)")
                break

            state = extract_all_state(
                book, clock, tape, agents,
                tick_size=tick_size,
                vol_window=vol_window,
                status="running",
            )
            state["max_steps"] = max_steps
            write_state(state, args.state_path)

            _print_progress(clock, tape, max_steps)

            if args.step_delay > 0:
                time.sleep(args.step_delay)
    except KeyboardInterrupt:
        print("\n  (interrupted)")
    finally:
        print()
        state = extract_all_state(
            book, clock, tape, agents,
            tick_size=tick_size,
            vol_window=vol_window,
            status="complete",
        )
        state["max_steps"] = max_steps
        write_state(state, args.state_path)
        print(f"  Final state written — {len(tape)} fills, t={clock.now:.2f}")
        print("  Press Enter to close this window.")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
