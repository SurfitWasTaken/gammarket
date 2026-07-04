"""Phase 3 simulation runner.

Builds a market with N retail noise-traders, 1 institutional
speculator, and 2 equity market makers, seeds a tight BBO, drives the
discrete-event clock, and produces a 3-panel summary plot (price
series, return autocorrelation, trade-size distribution).

Usage:
    python run_sim.py            # uses sim/config/params.yaml
    python run_sim.py --no-plot  # skip the matplotlib summary
"""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from sim.agents.equity_mm import EquityMarketMaker, EquityMMConfig
from sim.agents.institution import Institution
from sim.agents.options_flow import OptionsFlow, OptionsFlowConfig
from sim.agents.options_mm import OptionsMarketMaker, OptionsMMConfig
from sim.agents.retail import Retail
from sim.analytics.metrics import (
    autocorrelation,
    fill_prices,
    simple_returns,
    trade_sizes,
)
from sim.config.loader import load_config
from sim.core.clock import Clock
from sim.core.events import Order, Side
from sim.core.lob import LimitOrderBook
from sim.core.tape import Tape
from sim.options.chain import build_chain, spot_from_book
from sim.options.surface import FlatVolSurface


def _build_book_and_tape(cfg_market: dict) -> tuple[LimitOrderBook, Tape]:
    tape = Tape()
    book = LimitOrderBook(
        tick_size=cfg_market["tick_size"], on_fill=tape.append
    )
    return book, tape


def _seed_bbo(book: LimitOrderBook, cfg_market: dict) -> None:
    initial_price = int(cfg_market["initial_price"])
    tick = int(cfg_market["tick_size"])
    bid_price = initial_price - tick
    ask_price = initial_price + tick
    bid_qty = int(cfg_market["initial_bid_size"])
    ask_qty = int(cfg_market["initial_ask_size"])
    now = 0.0
    book.submit_limit(
        Order(uuid.uuid4(), "seed", Side.BUY, bid_price, bid_qty, now)
    )
    book.submit_limit(
        Order(uuid.uuid4(), "seed", Side.SELL, ask_price, ask_qty, now)
    )


def _build_agents(cfg: dict, rng: np.random.Generator) -> list:
    retail_cfg = cfg["agents"]["retail"]
    agents: list = []
    for i in range(int(retail_cfg["n_agents"])):
        agents.append(
            Retail(
                agent_id=f"retail_{i}",
                order_size_mean=float(retail_cfg["order_size_mean"]),
                direction_bias=float(retail_cfg["direction_bias"]),
                rng=rng,
            )
        )
    inst_cfg = cfg["agents"]["institution"]
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
            initial_price=int(cfg["market"]["initial_price"]),
            rng=rng,
        )
    )
    # Accept both "equity_mms" (list, spec form) and the legacy singular
    # "equity_mm" key. The shim is retained because the frozen
    # test_e2e_phase2.py passes config under the singular key (Audit P2-2).
    mm_cfgs = cfg["agents"].get("equity_mms")
    if mm_cfgs is None:
        mm_cfgs = [cfg["agents"]["equity_mm"]]
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
    # Phase 5 path: the presence of agents.options_flow switches on the
    # options dealer + demand flow. Older configs (frozen Phase 2/3 e2e
    # tests) lack the key and run the equity-only market unchanged.
    if "options_flow" in cfg["agents"]:
        dealer = _build_dealer(cfg, rng)
        flow_cfg = cfg["agents"]["options_flow"]
        flow = OptionsFlow(
            agent_id="options_flow",
            config=OptionsFlowConfig(
                arrival_rate=float(flow_cfg["arrival_rate"]),
                max_lots=int(flow_cfg["max_lots"]),
            ),
            rng=rng,
            dealer=dealer,
        )
        agents.append(dealer)
        agents.append(flow)
    return agents


def _build_dealer(cfg: dict, rng: np.random.Generator) -> OptionsMarketMaker:
    """Build the options dealer with its chain anchored to the seeded BBO mid.

    The chain is built once at construction (E6): the anchor is the
    initial price, which equals the seeded book mid (`_seed_bbo` rests
    bid/ask symmetrically one tick around it).
    """
    market = cfg["market"]
    options = cfg["options"]
    mm_cfg = cfg["agents"]["options_mm"]
    tick_size = int(market["tick_size"])
    chain = build_chain(
        float(market["initial_price"]),
        0.0,
        strikes_pct=[float(p) for p in options["strikes_pct"]],
        expiries_days=[float(d) for d in options["expiries_days"]],
        tick_size=tick_size,
    )
    return OptionsMarketMaker(
        agent_id="options_mm",
        config=OptionsMMConfig(
            arrival_rate=float(mm_cfg["arrival_rate"]),
            vol_estimate=float(mm_cfg["vol_estimate"]),
            spread_vols=float(mm_cfg["spread_vols"]),
            delta_hedge_threshold=float(mm_cfg["delta_hedge_threshold"]),
            gamma_limit=float(mm_cfg["gamma_limit"]),
            option_tick=int(mm_cfg.get("option_tick", tick_size)),
        ),
        rng=rng,
        chain=chain,
        surface=FlatVolSurface(float(mm_cfg["vol_estimate"])),
        risk_free_rate=float(options["risk_free_rate"]),
        minutes_per_year=float(market["minutes_per_year"]),
        tick_size=tick_size,
    )


def _register(clock: Clock, agents: list, cfg: dict) -> None:
    retail_rate = float(cfg["agents"]["retail"]["arrival_rate"])
    inst_rate = float(cfg["agents"]["institution"]["arrival_rate"])
    mm_cfgs = cfg["agents"].get("equity_mms")
    if mm_cfgs is None:
        mm_cfgs = [cfg["agents"]["equity_mm"]]
    for a in agents:
        if isinstance(a, Retail):
            clock.register(a, retail_rate)
        elif isinstance(a, Institution):
            clock.register(a, inst_rate)
        elif isinstance(a, EquityMarketMaker):
            mm_cfg = next(m for m in mm_cfgs if m.get("id", "mm0") == a.agent_id)
            clock.register(a, float(mm_cfg["arrival_rate"]))
        elif isinstance(a, OptionsMarketMaker):
            clock.register(a, float(cfg["agents"]["options_mm"]["arrival_rate"]))
        elif isinstance(a, OptionsFlow):
            clock.register(a, float(cfg["agents"]["options_flow"]["arrival_rate"]))


def run(cfg: dict) -> dict[str, Any]:
    """Build the sim from `cfg`, run to completion, return diagnostics.

    The returned dict has keys `tape`, `book`, `clock`, `agents`, `cfg`.
    The function is pure of file I/O: the same `cfg` always yields the
    same outcome (modulo numpy seeding, which is honoured).
    """
    market = cfg["market"]
    rng = np.random.default_rng(int(market["seed"]))
    book, tape = _build_book_and_tape(market)
    _seed_bbo(book, market)
    agents = _build_agents(cfg, rng)
    vol_window = int(market.get("vol_window", 20))
    clock = Clock(book, tape, rng, vol_window=vol_window)
    _register(clock, agents, cfg)
    clock.run(int(market["max_steps"]))
    return {
        "tape": tape,
        "book": book,
        "clock": clock,
        "agents": agents,
        "cfg": cfg,
    }


def _summary(result: dict) -> dict[str, Any]:
    tape: Tape = result["tape"]
    fills = tape.fills
    prices = fill_prices(fills)
    sizes = trade_sizes(fills)
    out: dict[str, Any] = {
        "n_fills": len(fills),
        "n_steps": result["clock"].step_count,
        "final_clock_time": float(result["clock"].now),
    }
    if len(prices) > 0:
        out["first_fill_price"] = int(prices[0])
        out["last_fill_price"] = int(prices[-1])
        out["mean_trade_size"] = float(sizes.mean())
    if len(prices) > 2:
        r = simple_returns(prices)
        out["return_std"] = float(r.std())
        acf = autocorrelation(r, max_lag=5)
        out["autocorr_lag_1"] = float(acf[0])

    for a in result["agents"]:
        if isinstance(a, EquityMarketMaker):
            out[f"{a.agent_id}_total_pnl"] = a.total_pnl
            out[f"{a.agent_id}_cash_flow"] = a.cash_flow
            out[f"{a.agent_id}_avg_spread"] = a.avg_spread
            out[f"{a.agent_id}_n_quotes"] = len(a.spread_log)
            out[f"{a.agent_id}_final_position"] = a.position
        elif isinstance(a, OptionsMarketMaker):
            out[f"{a.agent_id}_n_option_trades"] = len(a.trade_log)
            out[f"{a.agent_id}_n_hedges"] = len(a.hedge_log)
            out[f"{a.agent_id}_gamma_rejections"] = a.gamma_rejections
            out[f"{a.agent_id}_option_cash_flow"] = a.option_cash_flow
            out[f"{a.agent_id}_final_equity_position"] = a.position
            mid = result["book"].mid()
            if mid is not None:
                spot = spot_from_book(float(mid), a.tick_size)
                out[f"{a.agent_id}_final_net_delta_lots"] = a.net_delta_lots(
                    spot, float(result["clock"].now)
                )
    return out


def _plot(result: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    tape: Tape = result["tape"]
    fills = tape.fills
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))

    prices = fill_prices(fills)
    if len(prices) > 0:
        axes[0].plot(prices, linewidth=0.7, color="#1f77b4")
        pad = max(1, (int(prices.max()) - int(prices.min())) // 10 + 1)
        axes[0].set_ylim(int(prices.min()) - pad, int(prices.max()) + pad)
        axes[0].yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x):,}"))
        axes[0].set_title("Fill price series")
        axes[0].set_xlabel("fill #")
        axes[0].set_ylabel("price (ticks)")
        axes[0].grid(True, alpha=0.3)
    else:
        axes[0].text(0.5, 0.5, "(no fills)", ha="center", va="center",
                     transform=axes[0].transAxes)

    if len(prices) > 2:
        r = simple_returns(prices)
        acf = autocorrelation(r, max_lag=min(20, len(r) - 1))
        axes[1].bar(range(1, len(acf) + 1), acf, color="#1f77b4")
        axes[1].axhline(0.0, color="black", linewidth=0.5)
        axes[1].set_title("Return autocorrelation")
        axes[1].set_xlabel("lag")
        axes[1].set_ylabel("acf")
        axes[1].grid(True, alpha=0.3)
    else:
        axes[1].text(0.5, 0.5, "(not enough fills)", ha="center", va="center",
                     transform=axes[1].transAxes)

    sizes = trade_sizes(fills)
    if len(sizes) > 0:
        n_bins = int(min(20, max(1, sizes.max())))
        axes[2].hist(sizes, bins=n_bins, edgecolor="black", color="#2ca02c")
        axes[2].set_title("Trade size distribution")
        axes[2].set_xlabel("quantity (lots)")
        axes[2].set_ylabel("count")
        axes[2].grid(True, alpha=0.3)
    else:
        axes[2].text(0.5, 0.5, "(no fills)", ha="center", va="center",
                     transform=axes[2].transAxes)

    fig.suptitle("gammarket Phase 3 — run summary")
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="gammarket Phase 3 runner")
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="skip the matplotlib summary plot",
    )
    parser.add_argument(
        "--plot-path",
        type=Path,
        default=Path("results/phase3.png"),
        help="output path for the summary plot (default: results/phase3.png)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="path to config yaml (default: sim/config/params.yaml)",
    )
    args = parser.parse_args()

    cfg_path = args.config if args.config is not None else None
    cfg = load_config(cfg_path) if cfg_path is not None else load_config()

    result = run(cfg)
    summary = _summary(result)

    for k, v in summary.items():
        print(f"  {k}: {v}")

    if not args.no_plot:
        plot_path: Path = args.plot_path
        plot_path.parent.mkdir(parents=True, exist_ok=True)
        _plot(result, plot_path)
        print(f"  plot: {plot_path}")


if __name__ == "__main__":
    main()
