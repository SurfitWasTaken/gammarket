from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from sim.agents.equity_mm import EquityMarketMaker
from sim.agents.institution import Institution
from sim.agents.options_flow import OptionsFlow
from sim.agents.options_mm import OptionsMarketMaker
from sim.agents.retail import Retail
from sim.core.clock import Clock
from sim.core.lob import LimitOrderBook
from sim.core.tape import Tape
from sim.options.chain import spot_from_book
from sim.options.pricer import bs_greeks, bs_price
from sim.options.surface import EwmaVolSurface

_RECENT_FILLS_MAX = 30
_RECENT_HEDGES_MAX = 6


def _compute_rolling_vol(tape: Tape, vol_window: int) -> float | None:
    fills = tape.fills
    if len(fills) < vol_window + 2:
        return None
    recent = fills[-(vol_window + 1):]
    prices = np.array([f.price for f in recent], dtype=float)
    returns = prices[1:] / prices[:-1] - 1.0
    if len(returns) < 2:
        return None
    # Population std (ddof=0): the Clock's rolling vol is canonical (F4).
    return float(np.std(returns) * 1e4)


def _extract_fills(tape: Tape) -> list[dict]:
    out: list[dict] = []
    for f in tape.fills[-_RECENT_FILLS_MAX:]:
        out.append({
            "ts": float(f.timestamp),
            "side": f.aggressor_side.value,
            "price": int(f.price),
            "qty": int(f.qty),
        })
    return out


def _extract_market_state(book: LimitOrderBook) -> dict:
    return {
        "best_bid": book.best_bid(),
        "best_ask": book.best_ask(),
        "mid": book.mid(),
        "spread": book.spread(),
    }


def _extract_agent_state(
    agent: Any, mid: float | None, tick_size: int, now: float,
    rolling_vol_bps: float | None = None,
) -> dict:
    s: dict[str, Any] = {
        "agent_id": agent.agent_id,
        "type": type(agent).__name__,
        "position": agent.position,
        "open_orders": len(agent.open_order_ids),
    }

    if isinstance(agent, Retail):
        effective_mean = agent.order_size_mean
        if agent.vol_feedback > 0.0 and rolling_vol_bps is not None:
            ratio = min(
                rolling_vol_bps / agent.baseline_vol_bps, agent.vol_ratio_cap
            )
            effective_mean = max(
                agent.order_size_mean
                * (1.0 + agent.vol_feedback * (ratio - 1.0)),
                1.0,
            )
        s.update({
            "vol_feedback": agent.vol_feedback,
            "effective_size_mean": effective_mean,
        })

    elif isinstance(agent, Institution):
        s.update({
            "signal": agent.signal,
            "target": int(agent.signal * agent.scale),
            "position_limit": agent.position_limit,
            "has_resting": agent.resting_order_id is not None,
        })

    elif isinstance(agent, EquityMarketMaker):
        unrealized = agent.position * (mid or 0.0)
        s.update({
            "cash_flow": agent.cash_flow,
            "total_pnl": agent.total_pnl,
            "unrealized_pnl": unrealized,
            "current_spread": agent._spread_log[-1] if agent._spread_log else None,
            "avg_spread": agent.avg_spread,
            "spread_target": agent.config.spread_target,
            "inventory_limit": agent.config.inventory_limit,
            "risk_aversion": agent.config.risk_aversion,
            "quote_size": agent.config.quote_size,
            "vol_multiplier": agent.config.vol_multiplier,
            "baseline_vol_bps": agent._baseline_vol_bps,
        })

    elif isinstance(agent, OptionsMarketMaker):
        spot = None
        if mid is not None:
            spot = spot_from_book(float(mid), tick_size)

        positions: list[dict] = []
        for series, qty in agent._option_positions.items():
            g = None
            p = None
            if spot is not None and now is not None:
                T = max(series.expiry_minutes - now, 0.0) / agent.minutes_per_year
                sigma = agent.surface.vol(series.strike, series.expiry_minutes)
                g = bs_greeks(spot, float(series.strike), T, agent.risk_free_rate, sigma, is_call=series.is_call)
                p = bs_price(spot, float(series.strike), T, agent.risk_free_rate, sigma, is_call=series.is_call)
            positions.append({
                "strike": series.strike,
                "expiry_minutes": series.expiry_minutes,
                "is_call": series.is_call,
                "qty": qty,
                "mid_price": p,
                "delta": g.delta if g else None,
                "gamma": g.gamma if g else None,
            })

        net_delta = None
        port_gamma = None
        if spot is not None:
            net_delta = agent.net_delta_lots(spot, now)
            port_gamma = agent.portfolio_gamma(spot, now)

        s.update({
            "option_cash_flow": agent.option_cash_flow,
            "gamma_rejections": agent.gamma_rejections,
            "n_option_trades": len(agent.trade_log),
            "n_hedges": len(agent.hedge_log),
            "net_delta_lots": net_delta,
            "portfolio_gamma": port_gamma,
            "gamma_limit": agent.config.gamma_limit,
            "vol_estimate": agent.config.vol_estimate,
            "surface_mode": (
                "ewma" if isinstance(agent.surface, EwmaVolSurface) else "flat"
            ),
            "surface_sigma": getattr(agent.surface, "sigma", None),
            "option_positions": positions,
            "recent_hedges": [
                {
                    "timestamp": h.timestamp,
                    "pre_delta_lots": h.pre_delta_lots,
                    "intended_qty_lots": h.intended_qty_lots,
                    "filled_qty_lots": h.filled_qty_lots,
                }
                for h in agent._hedge_log[-_RECENT_HEDGES_MAX:]
            ],
        })

    elif isinstance(agent, OptionsFlow):
        dealer = agent.dealer
        s.update({
            "total_trades_initiated": len(dealer.trade_log),
            "max_lots": agent.config.max_lots,
        })
        if dealer.trade_log:
            last = dealer.trade_log[-1]
            s["last_trade"] = {
                "series_strike": last.series.strike,
                "series_expiry_minutes": last.series.expiry_minutes,
                "series_is_call": last.series.is_call,
                "side": last.side.value,
                "qty": last.qty,
                "price": last.price,
                "timestamp": last.timestamp,
            }

    return s


def _extract_options_surface(
    dealer: OptionsMarketMaker | None, now: float, mid: float | None, tick_size: int,
) -> dict | None:
    if dealer is None or mid is None:
        return None

    spot = spot_from_book(float(mid), tick_size)
    chain = dealer.chain
    risk_free_rate = dealer.risk_free_rate
    minutes_per_year = dealer.minutes_per_year

    strikes = sorted({s.strike for s in chain})
    expiries_minutes = sorted({s.expiry_minutes for s in chain})
    expiries_days = [round((e - now) / 1440, 1) for e in expiries_minutes]

    call_prices: list[list[float]] = []
    put_prices: list[list[float]] = []
    call_deltas: list[list[float]] = []
    put_deltas: list[list[float]] = []
    call_gammas: list[list[float]] = []
    put_gammas: list[list[float]] = []

    for expiry_minutes in expiries_minutes:
        T = max(expiry_minutes - now, 0.0) / minutes_per_year
        cp_row: list[float] = []
        pp_row: list[float] = []
        cd_row: list[float] = []
        pd_row: list[float] = []
        cg_row: list[float] = []
        pg_row: list[float] = []
        for strike in strikes:
            sigma = dealer.surface.vol(strike, expiry_minutes)
            K = float(strike)
            cp_row.append(bs_price(spot, K, T, risk_free_rate, sigma, is_call=True))
            pp_row.append(bs_price(spot, K, T, risk_free_rate, sigma, is_call=False))
            g_call = bs_greeks(spot, K, T, risk_free_rate, sigma, is_call=True)
            g_put = bs_greeks(spot, K, T, risk_free_rate, sigma, is_call=False)
            cd_row.append(g_call.delta)
            pd_row.append(g_put.delta)
            cg_row.append(g_call.gamma)
            pg_row.append(g_put.gamma)
        call_prices.append(cp_row)
        put_prices.append(pp_row)
        call_deltas.append(cd_row)
        put_deltas.append(pd_row)
        call_gammas.append(cg_row)
        put_gammas.append(pg_row)

    return {
        "spot": spot,
        "strikes": strikes,
        "expiries_days": expiries_days,
        "call_prices": call_prices,
        "put_prices": put_prices,
        "call_deltas": call_deltas,
        "put_deltas": put_deltas,
        "call_gammas": call_gammas,
        "put_gammas": put_gammas,
    }


def extract_all_state(
    book: LimitOrderBook,
    clock: Clock,
    tape: Tape,
    agents: list,
    *,
    tick_size: int,
    vol_window: int,
    status: str = "running",
) -> dict:
    now = float(clock.now)
    market_state = _extract_market_state(book)
    mid = market_state["mid"]
    rolling_vol_bps = _compute_rolling_vol(tape, vol_window)
    market_state["rolling_vol_bps"] = rolling_vol_bps
    market_state["last_fill_price"] = tape.fills[-1].price if tape.fills else None

    agents_state: dict[str, dict] = {}
    dealer: OptionsMarketMaker | None = None
    for a in agents:
        agents_state[a.agent_id] = _extract_agent_state(
            a, mid, tick_size, now, rolling_vol_bps
        )
        if isinstance(a, OptionsMarketMaker):
            dealer = a

    surface = _extract_options_surface(dealer, now, mid, tick_size)

    return {
        "status": status,
        "timestamp": now,
        "step_count": clock.step_count,
        "n_fills": len(tape),
        "market": market_state,
        "recent_fills": _extract_fills(tape),
        "agents": agents_state,
        "options_surface": surface,
    }


_STATE_PATH = Path("/tmp/gammarket_agent_state.json")


def write_state(state: dict, path: Path | str = _STATE_PATH) -> None:
    path = Path(path)
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, cls=_SafeEncoder))
        tmp.replace(path)
    except OSError:
        pass


def read_state(path: Path | str = _STATE_PATH) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


_NONE_SENTINEL = "__none__"


class _SafeEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if obj is None:
            return _NONE_SENTINEL
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        return super().default(obj)
