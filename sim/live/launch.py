"""Unified simulation dashboard (single-terminal TUI).

Starts the sim runner as a background subprocess, then shows a combined
Rich dashboard in the current terminal.  Press Ctrl-C to stop.

The 3D options surface viz opens as a separate matplotlib window when
`--surface` is passed.

Usage:
    python -m sim.live.launch
    python -m sim.live.launch --surface
"""

from __future__ import annotations

import argparse
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.console import Console, Group, RenderableType

from sim.config.loader import load_config
from sim.live.state_writer import read_state, _STATE_PATH
from sim.live.agent_viewer import (
    render_market,
    render_retail,
    render_institution,
    render_equity_mm,
    render_options_mm,
    render_options_flow,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_agents(state: dict) -> dict[str, dict]:
    agents = state.get("agents", {})
    retail = {}
    others = {}
    for aid, a in agents.items():
        if a.get("type") == "Retail":
            retail[aid] = a
        else:
            others[aid] = a

    def sort_key(item):
        t = item[1].get("type", "")
        order = {"Institution": 0, "EquityMarketMaker": 1, "OptionsMarketMaker": 2, "OptionsFlow": 3}
        return order.get(t, 99)

    sorted_others = dict(sorted(others.items(), key=sort_key))
    return dict(sorted(retail.items())) | sorted_others


def _short_type(t: str) -> str:
    return {
        "Retail": "R",
        "Institution": "I",
        "EquityMarketMaker": "MM",
        "OptionsMarketMaker": "O",
        "OptionsFlow": "F",
    }.get(t, t)


def _build_layout(
    state: dict, n_fills: int, elapsed: float, agent_order: list[str]
) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=1),
    )

    status = state.get("status", "running")
    mkt = state.get("market", {})
    mid = mkt.get("mid")
    spread = mkt.get("spread")
    vol = mkt.get("rolling_vol_bps")
    bid = mkt.get("best_bid")
    ask = mkt.get("best_ask")
    last_px = mkt.get("last_fill_price")
    step = state.get("step_count", 0)
    max_steps = state.get("max_steps", "?")
    n_agents = len(state.get("agents", {}))

    hdr = Text()
    hdr += Text("γ-market  ", style="bold cyan")
    hdr += Text(f"step={step:>4d}/{max_steps}  ")
    hdr += Text(f"t={elapsed:>5.1f}s  ")
    hdr += Text(f"fills={n_fills:>3d}  ")
    if bid is not None:
        hdr += Text(f"B{bid:,}  ", style="green")
    if ask is not None:
        hdr += Text(f"A{ask:,}  ", style="red")
    if spread is not None:
        hdr += Text(f"sp={spread}  ")
    if vol is not None:
        hdr += Text(f"σ={vol:.1f}  ")
    hdr += Text(f"agents={n_agents}  ")
    hdr += (
        Text("[RUN]", style="bold green")
        if status == "running"
        else Text("[DONE]", style="bold red")
    )
    layout["header"].update(Panel(hdr, border_style="bold"))

    agents = state.get("agents", {})
    body_lines: list[RenderableType] = []

    # Market data section at the top of body
    mkt_line = Text("  ")
    if bid is not None:
        mkt_line += Text(f"Bid {bid:,}  ", style="bold green")
    if ask is not None:
        mkt_line += Text(f"Ask {ask:,}  ", style="bold red")
    if mid is not None:
        mkt_line += Text(f"Mid {mid:.1f}  ", style="bold yellow")
    if spread is not None:
        mkt_line += Text(f"Spread {spread}  ")
    if vol is not None:
        mkt_line += Text(f"Vol {vol:.1f} bps  ", style="bold cyan")
    if last_px is not None:
        mkt_line += Text(f"Last {last_px:,}")
    body_lines.append(Text("  ══ Market ══", style="bold white on blue"))
    body_lines.append(mkt_line)
    body_lines.append(Text(""))

    for aid in agent_order:
        a = agents.get(aid)
        if a is None:
            continue
        atype = a.get("type", "")
        pos = a.get("position", 0)
        short = _short_type(atype)
        pos_style = "green" if pos > 0 else "red" if pos < 0 else "white"
        line = Text(f"  {short:<4}", style="bold")
        line += Text(f" {aid:<22}  ")
        line += Text(f"pos={pos:>+6}", style=pos_style)

        if atype == "EquityMarketMaker":
            tp = a.get("total_pnl")
            if tp is not None:
                pnl_style = "green" if tp >= 0 else "red"
                line += Text(f"  P&L={tp:+,.0f}", style=pnl_style)
            cs = a.get("current_spread")
            if cs is not None:
                line += Text(f"  spread={cs}")
        elif atype == "OptionsMarketMaker":
            nd = a.get("net_delta_lots")
            if nd is not None:
                d_style = "green" if abs(nd) < 0.5 else "red"
                line += Text(f"  net_δ={nd:+.4f}", style=d_style)
            rg = a.get("portfolio_gamma")
            if rg is not None:
                line += Text(f"  γ={rg:+.1f}")
            nopt = a.get("n_option_trades", 0)
            nhedge = a.get("n_hedges", 0)
            line += Text(f"  trades={nopt}  hedges={nhedge}")
        elif atype == "OptionsFlow":
            ntr = a.get("total_trades_initiated", 0)
            line += Text(f"  trades={ntr}")
        elif atype == "Institution":
            sig = a.get("signal", 0)
            limit = a.get("position_limit", 0)
            line += Text(f"  signal={sig:+.4f}  limit=±{limit}")

        body_lines.append(line)

    if not body_lines:
        body_lines.append(Text("  (waiting for simulation...)", style="dim"))

    layout["body"].update(Panel(Group(*body_lines), border_style="blue"))
    layout["footer"].update(
        Text("  Ctrl-C to stop  |  --surface for 3D viz", style="dim")
    )

    return layout


def main() -> None:
    parser = argparse.ArgumentParser(description="gammarket unified dashboard")
    parser.add_argument("--config", type=str, default=None, help="path to config yaml")
    parser.add_argument(
        "--surface", action="store_true", help="also open 3D options surface viz"
    )
    parser.add_argument(
        "--step-delay",
        type=float,
        default=0.1,
        help="delay between sim steps (default: 0.1s)",
    )
    args = parser.parse_args()

    try:

        cfg = load_config(args.config) if args.config else load_config()
        py = sys.executable
        root = _PROJECT_ROOT
        state_path = str(_STATE_PATH)
        step_delay = args.step_delay

        # Remove stale state
        try:
            _STATE_PATH.unlink(missing_ok=True)
        except OSError:
            pass

        print(f"  gammarket live dashboard — Ctrl-C to stop")
        sys.stdout.flush()

        # 1. Start sim runner as background subprocess
        sim_cmd = (
            f"cd {shlex.quote(str(root))} && {py} -m sim.live.sim_runner "
            f"--state-path {state_path}"
        )
        if step_delay > 0:
            sim_cmd += f" --step-delay {step_delay}"
        sim_proc = subprocess.Popen(
            sim_cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # 2. Surface viz (optional)
        surf_proc: subprocess.Popen | None = None
        if args.surface:
            print("  Opening 3D options surface viz...")
            sys.stdout.flush()
            surf_proc = subprocess.Popen(
                f"cd {shlex.quote(str(root))} && {py} -m sim.live.surface_viz "
                f"--state-path {state_path}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        # 3. Wait for state file (up to 8s)
        for _ in range(40):
            if _STATE_PATH.exists() and _STATE_PATH.stat().st_size > 0:
                break
            time.sleep(0.2)
        else:
            print("  (still waiting for sim to produce first state...)")
            sys.stdout.flush()

        # 4. Live dashboard loop
        stop = False

        def _on_sigint(*_: Any) -> None:
            nonlocal stop
            stop = True

        signal.signal(signal.SIGINT, _on_sigint)

        last_n_fills = 0
        t0 = time.time()
        agent_order: list[str] = []
        console = Console(force_terminal=True, highlight=False)

        try:
            with Live(auto_refresh=True, console=console, refresh_per_second=4) as live:
                while not stop:
                    state = read_state(_STATE_PATH)
                    if state is None:
                        live.update(
                            Panel(
                                "Waiting for simulation to start...",
                                border_style="yellow",
                            )
                        )
                    else:
                        if not agent_order:
                            agent_order = list(_find_agents(state).keys())
                        n_fills = state.get("n_fills", 0)
                        if n_fills != last_n_fills:
                            last_n_fills = n_fills
                        elapsed = time.time() - t0
                        layout = _build_layout(
                            state, n_fills, elapsed, agent_order
                        )
                        live.update(layout)
                    time.sleep(0.2)
        except KeyboardInterrupt:
            pass

        # 5. Cleanup
        print()
        print("  Shutting down...")
        sim_proc.terminate()
        try:
            sim_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            sim_proc.kill()
        if surf_proc:
            surf_proc.terminate()
            try:
                surf_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                surf_proc.kill()
        print("  Done.")

    except Exception as e:
        print(f"\n  ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
