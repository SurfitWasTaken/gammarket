"""Tests for Phase 6 F1: `Clock.on_step` + `StepRecord` + `MarketDataCollector`."""

from __future__ import annotations

import uuid

import numpy as np

from sim.agents.base import Agent, MarketState
from sim.analytics.collector import MarketDataCollector
from sim.core.clock import Clock, StepRecord
from sim.core.events import Order, Side
from sim.core.lob import LimitOrderBook
from sim.core.tape import Tape


class _Quoter(Agent):
    """Rests one bid and one ask on its first step, then stays silent."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self._quoted = False

    def step(self, state: MarketState) -> list[Order]:
        if self._quoted:
            return []
        self._quoted = True
        return [
            Order(uuid.uuid4(), self.agent_id, Side.BUY, 99, 7, state.timestamp),
            Order(uuid.uuid4(), self.agent_id, Side.SELL, 101, 4, state.timestamp),
        ]


def _run_clock(n_steps: int) -> MarketDataCollector:
    book = LimitOrderBook(tick_size=1)
    tape = Tape()
    collector = MarketDataCollector()
    clock = Clock(book, tape, np.random.default_rng(0), on_step=collector)
    clock.register(_Quoter("q"), rate_per_min=10.0)
    clock.run(n_steps)
    return collector


def test_on_step_fires_once_per_step() -> None:
    collector = _run_clock(5)
    assert len(collector) == 5
    assert [r.step for r in collector.records] == [1, 2, 3, 4, 5]


def test_record_reflects_book_after_actions() -> None:
    """The first record is captured *after* the quoter's orders landed."""
    r = _run_clock(1).records[0]
    assert r.best_bid == 99
    assert r.best_ask == 101
    assert r.mid == 100.0
    assert r.spread == 2
    assert r.bid_depth == 7
    assert r.ask_depth == 4
    assert r.last_fill_price is None
    assert r.rolling_vol_bps is None


def test_record_none_fields_when_book_empty() -> None:
    book = LimitOrderBook(tick_size=1)
    tape = Tape()
    collector = MarketDataCollector()
    clock = Clock(book, tape, np.random.default_rng(0), on_step=collector)

    class _Silent(Agent):
        def step(self, state: MarketState) -> list[Order]:
            return []

    clock.register(_Silent("s"), rate_per_min=10.0)
    clock.run(2)
    for r in collector.records:
        assert r.best_bid is None and r.best_ask is None
        assert r.mid is None and r.spread is None
        assert r.bid_depth == 0 and r.ask_depth == 0


def test_no_callback_is_backward_compatible() -> None:
    book = LimitOrderBook(tick_size=1)
    clock = Clock(book, Tape(), np.random.default_rng(0))
    clock.register(_Quoter("q"), rate_per_min=10.0)
    clock.run(3)
    assert clock.step_count == 3


def test_collector_numpy_views() -> None:
    collector = _run_clock(4)
    ts = collector.timestamps()
    mids = collector.mids()
    spreads = collector.spreads()
    assert ts.shape == mids.shape == spreads.shape == (4,)
    assert np.all(np.diff(ts) >= 0)
    assert np.all(mids == 100.0)
    assert np.all(spreads == 2.0)
    assert collector.bid_depths().tolist() == [7, 7, 7, 7]
    assert collector.ask_depths().tolist() == [4, 4, 4, 4]
    assert np.all(np.isnan(collector.rolling_vols_bps()))


def test_nan_masking_for_empty_book() -> None:
    collector = MarketDataCollector()
    collector(
        StepRecord(
            step=1,
            timestamp=0.5,
            best_bid=None,
            best_ask=None,
            mid=None,
            spread=None,
            bid_depth=0,
            ask_depth=0,
            last_fill_price=None,
            rolling_vol_bps=None,
        )
    )
    assert np.isnan(collector.mids()).all()
    assert np.isnan(collector.spreads()).all()


def test_run_sim_returns_collector() -> None:
    import copy

    from run_sim import run
    from sim.config.loader import load_config

    cfg = copy.deepcopy(load_config())
    cfg["market"]["max_steps"] = 50
    result = run(cfg)
    collector = result["collector"]
    assert isinstance(collector, MarketDataCollector)
    assert len(collector) == 50
    spreads = collector.spreads()
    finite = spreads[np.isfinite(spreads)]
    assert len(finite) > 0 and finite.min() >= 1
