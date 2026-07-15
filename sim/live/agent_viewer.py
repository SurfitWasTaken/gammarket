from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path
from typing import Any

from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.console import Console, Group, RenderableType

from sim.live.state_writer import read_state, _STATE_PATH


def _fmt(v: Any, dp: int = 1) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{dp}f}"
    return f"{v:,}"


def _pos_style(val: int | float, dp: int = 1) -> Text:
    if val is None:
        return Text("—", style="dim")
    if isinstance(val, float):
        txt = f"{val:+,.{dp}f}"
    else:
        txt = f"{val:+,}"
    style = "green" if val > 0 else "red" if val < 0 else "white"
    return Text(txt, style=style)


def _pnl_row(label: str, value: float, dp: int = 1) -> Text:
    return Text(f"  {label}:  ", style="bold") + _pos_style(value, dp)


# ── renderers ──────────────────────────────────────────────


def render_market(state: dict) -> Panel:
    mkt = state.get("market", {})
    fills = state.get("recent_fills", [])
    agents = state.get("agents", {})
    max_steps = state.get("max_steps", "?")
    status = state.get("status", "running")

    lines: list[RenderableType] = []

    header = Text(
        f"t={mkt.get('timestamp', 0):8.2f} min  "
        f"step={state.get('step_count', 0):5d}/{max_steps}  "
        f"fills={state.get('n_fills', 0):4d}  "
        f"[{'RUN' if status == 'running' else 'DONE'}]",
        style="bold cyan",
    )
    lines.append(header)
    lines.append(Text(""))

    mid = mkt.get("mid")
    bid = mkt.get("best_bid")
    ask = mkt.get("best_ask")
    spread = mkt.get("spread")
    last_px = mkt.get("last_fill_price")
    vol = mkt.get("rolling_vol_bps")

    book_t = Text("  Book:  ")
    if bid is not None:
        book_t += Text(f"Bid {bid:,}  ", style="green")
    if ask is not None:
        book_t += Text(f"Ask {ask:,}  ", style="red")
    if mid is not None:
        book_t += Text(f"Mid {mid:.1f}  ", style="bold yellow")
    if spread is not None:
        book_t += Text(f"Spread {spread}  ")
    lines.append(book_t)

    extra = Text()
    if last_px is not None:
        extra += Text(f"  Last fill: {last_px:,}")
    if vol is not None:
        extra += Text(f"  Vol: {vol:.1f} bps")
    if extra:
        lines.append(extra)
    lines.append(Text(""))

    if fills:
        t = Table(
            box=None, padding=(0, 1),
            header_style="bold", show_header=True,
        )
        t.add_column("#", justify="right", width=3)
        t.add_column("Side", justify="center", width=5)
        t.add_column("Price", justify="right", width=8)
        t.add_column("Qty", justify="right", width=5)
        t.add_column("Time", justify="right", width=7)
        for i, f in enumerate(reversed(fills), 1):
            side_text = Text(
                f["side"],
                style="green" if f["side"] == "BUY" else "red",
            )
            t.add_row(
                str(i),
                side_text,
                f"{f['price']:,}",
                str(f["qty"]),
                f"{f['ts']:.1f}",
            )
        lines.append(Text("  Recent fills:", style="bold underline"))
        lines.append(t)
        lines.append(Text(""))

    if agents:
        lines.append(Text("  Agents:", style="bold underline"))
        for aid, a in sorted(agents.items()):
            t = type(a).__name__ if isinstance(a, dict) else ""
            pos = a.get("position", 0)
            line = Text(f"  {aid:<20}")
            line += _pos_style(pos)
            if "total_pnl" in a and a["total_pnl"] is not None:
                line += Text("  P&L: ")
                line += _pos_style(a["total_pnl"])
            if "net_delta_lots" in a and a["net_delta_lots"] is not None:
                line += Text("  net_δ: ")
                line += _pos_style(a["net_delta_lots"])
            lines.append(line)

    return Panel(Group(*lines), title="[bold blue]Market Dashboard[/]", border_style="blue")


def render_retail(state: dict) -> Panel:
    agents = state.get("agents", {})
    retail = {k: v for k, v in agents.items() if v.get("type") == "Retail"}

    t = Table(box=None, padding=(0, 2), header_style="bold", show_header=True)
    t.add_column("Agent", justify="left", width=14)
    t.add_column("Position", justify="right", width=8)
    t.add_column("Orders", justify="right", width=6)
    t.add_column("EffSize", justify="right", width=7)

    total_pos = 0
    any_feedback = False
    for aid, a in sorted(retail.items()):
        pos = a.get("position", 0)
        total_pos += pos
        eff = a.get("effective_size_mean")
        if a.get("vol_feedback", 0.0):
            any_feedback = True
        t.add_row(
            Text(aid),
            _pos_style(pos),
            str(a.get("open_orders", 0)),
            _fmt(eff, 1) if eff is not None else "—",
        )

    t.add_row("", Text("──────", style="dim"), "", "")
    t.add_row(
        Text(f"Total ({len(retail)} agents)", style="bold"),
        _pos_style(total_pos),
        Text(""),
        Text(""),
    )

    footer = Text(
        "  vol feedback: ON (size scales with vol ratio)" if any_feedback
        else "  vol feedback: off",
        style="cyan" if any_feedback else "dim",
    )
    return Panel(
        Group(t, footer),
        title="[bold green]Retail Agents[/]", border_style="green",
    )


def render_institution(a: dict) -> Panel:
    lines: list[RenderableType] = []

    lines.append(Text(f"  Position:  ") + _pos_style(a.get("position", 0)))
    sig = a.get("signal", 0)
    lines.append(Text(f"  Signal:    {sig:+.4f}"))
    target = a.get("target", 0)
    lines.append(Text(f"  Target:    {_fmt(target)}  (scale × signal)"))
    lines.append(Text(f"  Limit:     ±{_fmt(a.get('position_limit', 0))}"))
    resting = a.get("has_resting", False)
    lines.append(Text(f"  Resting:   {'Yes' if resting else 'No'}", style="green" if resting else "dim"))
    lines.append(Text(f"  Orders:    {a.get('open_orders', 0)}"))

    return Panel(Group(*lines), title="[bold yellow]Institution[/]", border_style="yellow")


def render_equity_mm(a: dict) -> Panel:
    lines: list[RenderableType] = []

    lines.append(Text(f"  Position:  ") + _pos_style(a.get("position", 0)))
    lines.append(Text(""))
    lines.append(Text("  ── P&L ──", style="bold underline"))
    cf = a.get("cash_flow")
    if cf is not None:
        lines.append(_pnl_row("Cash flow (realized)", cf))
    upnl = a.get("unrealized_pnl")
    if upnl is not None:
        lines.append(_pnl_row("Unrealized (pos×mid)", upnl))
    tp = a.get("total_pnl")
    if tp is not None:
        lines.append(_pnl_row("Total P&L", tp, dp=1))
    lines.append(Text(""))

    lines.append(Text("  ── Spread ──", style="bold underline"))
    cs = a.get("current_spread")
    if cs is not None:
        lines.append(Text(f"  Current:  {cs} ticks"))
    avg = a.get("avg_spread")
    if avg is not None:
        lines.append(Text(f"  Average:  {avg:.1f} ticks"))
    target = a.get("spread_target")
    if target is not None:
        lines.append(Text(f"  Target:   {target} ticks"))
    lines.append(Text(""))

    lines.append(Text("  ── Config ──", style="bold underline"))
    lines.append(Text(f"  Inv limit:  {_fmt(a.get('inventory_limit', ''))}"))
    lines.append(Text(f"  Risk av:    {_fmt(a.get('risk_aversion', ''), 3)}"))
    lines.append(Text(f"  Quote size: {_fmt(a.get('quote_size', ''))} lots"))
    lines.append(Text(f"  Vol base:   {_fmt(a.get('baseline_vol_bps', ''), 1)} bps"))
    lines.append(Text(f"  Vol mult:   {_fmt(a.get('vol_multiplier', ''), 1)}"))

    return Panel(Group(*lines), title=f"[bold green]{a.get('agent_id', 'MM')}[/]", border_style="green")


def render_options_mm(a: dict) -> Panel:
    lines: list[RenderableType] = []

    lines.append(
        Text("  Equity pos:  ") + _pos_style(a.get("position", 0))
        + Text("   |   Opt CF:  ") + _pos_style(a.get("option_cash_flow", 0))
    )
    nd = a.get("net_delta_lots")
    if nd is not None:
        lines.append(Text("  Net δ:       ") + _pos_style(nd, 4)
                      + Text("   |   Port γ:  ") + _pos_style(a.get("portfolio_gamma", 0), 1))
    rej = a.get("gamma_rejections", 0)
    glim = a.get("gamma_limit", 0)
    lines.append(Text(f"  γ rejections: {rej}   |   γ limit: {glim}"))
    mode = a.get("surface_mode")
    sig = a.get("surface_sigma")
    if mode is not None:
        surf = Text(f"  Surface:     {mode}", style="cyan" if mode == "ewma" else "white")
        if sig is not None:
            surf += Text(f"   σ = {sig:.4f}")
        lines.append(surf)
    lines.append(Text(""))

    positions = a.get("option_positions", [])
    if positions:
        lines.append(Text("  ── Positions ──", style="bold underline"))
        t = Table(
            box=None, padding=(0, 1),
            header_style="bold", show_header=True,
        )
        t.add_column("#", justify="right", width=2)
        t.add_column("Strike", justify="right", width=7)
        t.add_column("Exp", justify="left", width=6)
        t.add_column("Type", justify="center", width=5)
        t.add_column("Qty", justify="right", width=5)
        t.add_column("Price", justify="right", width=7)
        t.add_column("δ", justify="right", width=7)
        t.add_column("γ", justify="right", width=7)
        for i, p in enumerate(positions, 1):
            days = max(0, (p["expiry_minutes"] - state.get("timestamp", 0)) / 1440)
            exp_str = f"{days:.0f}d" if days >= 1 else f"{days*24:.0f}h"
            typ = "CALL" if p["is_call"] else "PUT"
            qty = p["qty"]
            price = p.get("mid_price")
            delta = p.get("delta")
            gamma = p.get("gamma")
            t.add_row(
                str(i),
                f"{p['strike']:,}",
                exp_str,
                typ,
                _pos_style(qty),
                _fmt(price, 2) if price is not None else "—",
                _fmt(delta, 4) if delta is not None else "—",
                _fmt(gamma, 4) if gamma is not None else "—",
            )
        lines.append(t)
        lines.append(Text(""))

    hedges = a.get("recent_hedges", [])
    if hedges:
        lines.append(Text("  ── Recent Hedges ──", style="bold underline"))
        ht = Table(
            box=None, padding=(0, 1),
            header_style="bold", show_header=True,
        )
        ht.add_column("#", justify="right", width=2)
        ht.add_column("Time", justify="right", width=7)
        ht.add_column("Pre-δ", justify="right", width=8)
        ht.add_column("Qty", justify="right", width=5)
        ht.add_column("Fill", justify="right", width=5)
        for i, h in enumerate(reversed(hedges), 1):
            filled = h.get("filled_qty_lots", 0) == h.get("intended_qty_lots", 0)
            ht.add_row(
                str(i),
                f"{h['timestamp']:.1f}",
                f"{h['pre_delta_lots']:+.2f}",
                str(h["intended_qty_lots"]),
                Text("✓" if filled else f"{h['filled_qty_lots']}/{h['intended_qty_lots']}",
                     style="green" if filled else "yellow"),
            )
        lines.append(ht)

    return Panel(Group(*lines), title=f"[bold magenta]{a.get('agent_id', 'Options MM')}[/]", border_style="magenta")


def render_options_flow(a: dict) -> Panel:
    lines: list[RenderableType] = []

    lines.append(Text(f"  Trades initiated:  {a.get('total_trades_initiated', 0)}"))
    lines.append(Text(f"  Max lots/trade:   {a.get('max_lots', 0)}"))
    lines.append(Text(""))

    last = a.get("last_trade")
    if last:
        lines.append(Text("  ── Last Trade ──", style="bold underline"))
        typ = "CALL" if last.get("series_is_call") else "PUT"
        exp_days = max(0, (last.get("series_expiry_minutes", 0) - state.get("timestamp", 0)) / 1440)
        exp_str = f"{exp_days:.0f}d"
        side_str = {
            "BUY": ("Buy  (lifted ask)", "red"),
            "SELL": ("Sell (hit bid)", "green"),
        }.get(last.get("side", ""), (last.get("side", ""), "white"))
        lines.append(Text(f"  Series: {typ} {last.get('series_strike'):,} {exp_str}"))
        lines.append(Text(f"  Side:   ", style="bold") + Text(*side_str))
        lines.append(Text(f"  Qty:    {last.get('qty')}"))
        lines.append(Text(f"  Price:  {_fmt(last.get('price'), 2)}"))
        lines.append(Text(f"  Time:   {last.get('timestamp'):.2f} min"))
    else:
        lines.append(Text("  (no trades yet)", style="dim"))

    return Panel(Group(*lines), title=f"[bold cyan]{a.get('agent_id', 'Options Flow')}[/]", border_style="cyan")


# ── dispatch ──────────────────────────────────────────────


_RENDERERS: dict[str, Any] = {}
state: dict = {}


def _waiting_panel(msg: str) -> Panel:
    return Panel(
        Text(msg, style="bold yellow", justify="center"),
        title="[bold]gammarket[/]",
        border_style="yellow",
    )


def render_dashboard(state: dict, agent_id: str) -> Panel:
    if agent_id == "market":
        return render_market(state)
    if agent_id == "retail":
        return render_retail(state)

    agents = state.get("agents", {})
    a = agents.get(agent_id)
    if a is None:
        known = ", ".join(sorted(agents.keys()))
        return _waiting_panel(f"Agent '{agent_id}' not yet available.\nKnown: {known}")

    atype = a.get("type", "")
    if atype == "Institution":
        return render_institution(a)
    if atype == "EquityMarketMaker":
        return render_equity_mm(a)
    if atype == "OptionsMarketMaker":
        return render_options_mm(a)
    if atype == "OptionsFlow":
        return render_options_flow(a)
    if atype == "Retail":
        return render_retail(state)

    return _waiting_panel(f"Unknown agent type: {atype}")


def main() -> None:
    parser = argparse.ArgumentParser(description="gammarket agent viewer")
    parser.add_argument("--id", required=True, help="agent_id (or 'market'/'retail')")
    parser.add_argument(
        "--state-path",
        default=str(_STATE_PATH),
        help=f"path to state file (default: {_STATE_PATH})",
    )
    parser.add_argument("--refresh", type=float, default=0.2, help="refresh interval (s)")
    args = parser.parse_args()

    path = Path(args.state_path)
    agent_id = args.id
    refresh = max(0.05, args.refresh)

    stop = False

    def _on_sigint(*_: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _on_sigint)

    console = Console()
    try:
        with Live(auto_refresh=False, console=console) as live:
            while not stop:
                global state
                state = read_state(path)
                if state is None:
                    live.update(_waiting_panel("Waiting for simulation to start..."))
                else:
                    live.update(render_dashboard(state, agent_id))
                    if state.get("status") == "complete":
                        pass
                time.sleep(refresh)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
