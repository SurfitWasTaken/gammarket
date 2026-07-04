"""Regression tests for Phase 6 Step 1: the F5 self-trade wash in
`Agent.on_fills` (and the matching cash wash in the equity MM), plus the
F4 O(window) rolling-vol slice in `Clock._build_state`.
"""

from __future__ import annotations

import uuid

import numpy as np

from sim.agents.base import Agent, MarketState
from sim.agents.equity_mm import EquityMarketMaker, EquityMMConfig
from sim.core.clock import Clock
from sim.core.events import Fill, Order, Side
from sim.core.lob import LimitOrderBook
from sim.core.tape import Tape


class _StubAgent(Agent):
    """Concrete agent exposing only the base-class fill handling."""

    def step(self, state: MarketState) -> list[Order]:
        return []


def _fill(
    *,
    taker_agent: str,
    maker_agent: str,
    side: Side = Side.BUY,
    price: int = 100,
    qty: int = 3,
) -> Fill:
    return Fill(
        taker_order_id=uuid.uuid4(),
        maker_order_id=uuid.uuid4(),
        taker_agent_id=taker_agent,
        maker_agent_id=maker_agent,
        aggressor_side=side,
        price=price,
        qty=qty,
        timestamp=1.0,
    )


def _mm(agent_id: str = "mm") -> EquityMarketMaker:
    cfg = EquityMMConfig(
        arrival_rate=10.0,
        spread_target=3,
        inventory_limit=100,
        risk_aversion=0.05,
        quote_size=5,
        max_orders_per_side=1,
        vol_window=20,
        vol_multiplier=2.0,
        baseline_vol_bps=5.0,
    )
    return EquityMarketMaker(agent_id, cfg, np.random.default_rng(0))


# ---------------------------------------------------------------- F5: base


def test_self_trade_is_position_wash() -> None:
    a = _StubAgent("a")
    a.on_fills([_fill(taker_agent="a", maker_agent="a", side=Side.BUY)])
    assert a.position == 0
    a.on_fills([_fill(taker_agent="a", maker_agent="a", side=Side.SELL)])
    assert a.position == 0


def test_self_trade_discards_both_order_ids() -> None:
    a = _StubAgent("a")
    f = _fill(taker_agent="a", maker_agent="a")
    a.open_order_ids = {f.taker_order_id, f.maker_order_id}
    a.on_fills([f])
    assert a.open_order_ids == set()


def test_non_self_trades_still_update_position() -> None:
    a = _StubAgent("a")
    a.on_fills([_fill(taker_agent="a", maker_agent="b", side=Side.BUY, qty=4)])
    assert a.position == 4
    a.on_fills([_fill(taker_agent="c", maker_agent="a", side=Side.BUY, qty=1)])
    assert a.position == 3  # maker of a BUY aggressor -> agent sold


# ------------------------------------------------------------ F5: MM cash


def test_mm_self_trade_is_cash_wash() -> None:
    mm = _mm()
    mm.on_fills([_fill(taker_agent="mm", maker_agent="mm", price=100, qty=5)])
    assert mm.cash_flow == 0.0
    assert mm.position == 0


def test_mm_normal_fill_cash_unchanged() -> None:
    mm = _mm()
    mm.on_fills([_fill(taker_agent="mm", maker_agent="x", side=Side.BUY, price=100, qty=5)])
    assert mm.cash_flow == -500.0
    mm.on_fills([_fill(taker_agent="y", maker_agent="mm", side=Side.BUY, price=110, qty=5)])
    assert mm.cash_flow == 50.0  # sold 5 @ 110 after buying 5 @ 100


# ------------------------------------------------------- F4: vol slicing


def test_rolling_vol_matches_full_scan_formula() -> None:
    """The O(window) tape-tail slice must equal the old full-scan result:
    std of fractional returns over the last `vol_window` prices, in bps."""
    book = LimitOrderBook(tick_size=1)
    tape = Tape()
    clock = Clock(book, tape, np.random.default_rng(0), vol_window=20)
    agent = _StubAgent("a")
    clock.agents["a"] = agent

    # Seed a two-sided book so mid is available.
    for side, price in ((Side.BUY, 99), (Side.SELL, 101)):
        book.submit_limit(
            Order(
                order_id=uuid.uuid4(),
                agent_id="seed",
                side=side,
                price=price,
                qty=1,
                timestamp=0.0,
            )
        )

    rng = np.random.default_rng(7)
    prices = (100 + np.cumsum(rng.integers(-2, 3, size=50))).astype(np.int64)
    for p in prices:
        tape.append(_fill(taker_agent="x", maker_agent="y", price=int(p)))

    state = clock._build_state(agent)

    window = prices[-20:].astype(np.float64)
    expected = float(np.std(np.diff(window) / window[:-1])) * 10_000.0
    assert state.rolling_vol_bps is not None
    assert state.rolling_vol_bps == expected


def test_rolling_vol_none_with_short_tape() -> None:
    book = LimitOrderBook(tick_size=1)
    tape = Tape()
    clock = Clock(book, tape, np.random.default_rng(0), vol_window=20)
    agent = _StubAgent("a")
    clock.agents["a"] = agent
    for side, price in ((Side.BUY, 99), (Side.SELL, 101)):
        book.submit_limit(
            Order(
                order_id=uuid.uuid4(),
                agent_id="seed",
                side=side,
                price=price,
                qty=1,
                timestamp=0.0,
            )
        )
    tape.append(_fill(taker_agent="x", maker_agent="y", price=100))
    state = clock._build_state(agent)
    assert state.rolling_vol_bps is None
