"""Phase 6 Step 2 (F3) — long-run stability guardrails on the equity MM.

Pins the three guards that keep a long run alive:
  * vol_ratio_cap breaks the bounce-vol spread feedback loop,
  * quote prices are clamped to 1 <= bid < ask (a clamped quote is legal,
    a non-positive price is a LOB ValueError),
  * post_only quotes never cross the live BBO (stops MM-vs-MM churn).
"""

from __future__ import annotations

import numpy as np
import pytest

from sim.agents.base import MarketState
from sim.agents.equity_mm import EquityMarketMaker, EquityMMConfig
from sim.core.events import Order, Side


def _mm(**overrides) -> EquityMarketMaker:
    kwargs = dict(
        arrival_rate=10.0,
        spread_target=3,
        inventory_limit=10_000,
        risk_aversion=0.05,
        quote_size=5,
        max_orders_per_side=1,
        vol_window=20,
        vol_multiplier=2.0,
        baseline_vol_bps=5.0,
        vol_ratio_cap=10.0,
        post_only=False,
    )
    kwargs.update(overrides)
    return EquityMarketMaker("mm", EquityMMConfig(**kwargs), np.random.default_rng(0))


def _state(
    *,
    mid: float | None,
    best_bid: int | None = None,
    best_ask: int | None = None,
    vol_bps: float | None = None,
) -> MarketState:
    return MarketState(
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        last_fill_price=None,
        own_position=0,
        timestamp=1.0,
        rolling_vol_bps=vol_bps,
    )


def _quotes(actions) -> tuple[Order, Order]:
    orders = [a for a in actions if isinstance(a, Order)]
    assert len(orders) == 2
    bid = next(o for o in orders if o.side is Side.BUY)
    ask = next(o for o in orders if o.side is Side.SELL)
    return bid, ask


def _expected_spread(mid: float, effective_spread: int) -> int:
    """Mirror the MM's independent bid/ask rounding (banker's round)."""
    half = effective_spread / 2.0
    return int(round(mid + half)) - int(round(mid - half))


def test_vol_ratio_is_capped() -> None:
    """A vol reading 1000x baseline must produce the capped spread, not a
    runaway one: spread_target * (1 + vol_multiplier * (cap - 1)) = 57."""
    mm = _mm()
    actions = mm.step(_state(mid=10_000.0, vol_bps=5_000.0))
    bid, ask = _quotes(actions)
    assert ask.price - bid.price == _expected_spread(10_000.0, 57)


def test_vol_ratio_below_cap_unchanged() -> None:
    """The cap must not touch ordinary readings (ratio 2 << cap 10):
    spread_target * (1 + vol_multiplier * (2 - 1)) = 9."""
    mm = _mm()
    actions = mm.step(_state(mid=10_000.0, vol_bps=10.0))
    bid, ask = _quotes(actions)
    assert ask.price - bid.price == _expected_spread(10_000.0, 9)


def test_quote_prices_clamped_positive() -> None:
    """Near-zero mid + wide vol spread must clamp to 1 <= bid < ask
    instead of raising in the LOB (the -1388 crash)."""
    mm = _mm()
    actions = mm.step(_state(mid=5.0, vol_bps=5_000.0))
    bid, ask = _quotes(actions)
    assert bid.price >= 1
    assert ask.price > bid.price


def test_huge_short_skew_clamped() -> None:
    mm = _mm(risk_aversion=1.0)
    mm.position = 10_000 - 1  # long -> skew pushes quotes far below zero
    actions = mm.step(_state(mid=100.0, vol_bps=None))
    bid, ask = _quotes(actions)
    assert bid.price >= 1
    assert ask.price > bid.price


def test_post_only_bid_never_crosses_ask() -> None:
    mm = _mm(post_only=True, risk_aversion=1.0)
    mm.position = -500  # short -> skew lifts both quotes far above mid
    actions = mm.step(
        _state(mid=100.5, best_bid=100, best_ask=101, vol_bps=None)
    )
    bid, ask = _quotes(actions)
    assert bid.price < 101  # strictly inside the ask
    assert ask.price > bid.price


def test_post_only_ask_never_crosses_bid() -> None:
    mm = _mm(post_only=True, risk_aversion=1.0)
    mm.position = 500  # long -> skew drops both quotes far below mid
    actions = mm.step(
        _state(mid=100.5, best_bid=100, best_ask=101, vol_bps=None)
    )
    bid, ask = _quotes(actions)
    assert ask.price > 100  # strictly above the bid
    assert bid.price < ask.price


def test_post_only_off_can_cross() -> None:
    """Default (post_only=False) preserves the pre-Phase-6 behaviour:
    a skewed quote may be marketable on submission (Audit P0-2)."""
    mm = _mm(post_only=False, risk_aversion=1.0)
    mm.position = -500
    actions = mm.step(
        _state(mid=100.5, best_bid=100, best_ask=101, vol_bps=None)
    )
    bid, _ = _quotes(actions)
    assert bid.price >= 101  # would lift the ask


def test_spread_log_records_clamped_spread() -> None:
    mm = _mm()
    mm.step(_state(mid=10_000.0, vol_bps=5_000.0))
    assert mm.spread_log[-1] == _expected_spread(10_000.0, 57)
