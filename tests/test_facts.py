"""Tests for the Phase 6 stylised-facts evaluator (F9)."""

from __future__ import annotations

import copy
import math
import uuid
from dataclasses import dataclass

import numpy as np
import pytest

from sim.analytics.collector import MarketDataCollector
from sim.analytics.facts import (
    FACT_NAMES,
    FactResult,
    evaluate_stylised_facts,
    _fact_dealer_delta,
    _fact_positive_spread,
    _fact_return_acf,
    _fact_vol_clustering,
)
from sim.core.clock import StepRecord
from sim.core.events import Fill, Side
from sim.core.tape import Tape


def _record(step: int, t: float, bid: int | None, ask: int | None) -> StepRecord:
    mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
    spread = ask - bid if bid is not None and ask is not None else None
    return StepRecord(
        step=step,
        timestamp=t,
        best_bid=bid,
        best_ask=ask,
        mid=mid,
        spread=spread,
        bid_depth=10,
        ask_depth=10,
        last_fill_price=None,
        rolling_vol_bps=None,
    )


def _collector_from_quotes(quotes: list[tuple[float, int, int]]) -> MarketDataCollector:
    c = MarketDataCollector()
    for i, (t, bid, ask) in enumerate(quotes, start=1):
        c(_record(i, t, bid, ask))
    return c


# ----------------------------------------------------------- fact 1: spread


def test_positive_spread_passes() -> None:
    c = _collector_from_quotes([(0.5, 99, 101), (1.0, 100, 102)])
    r = _fact_positive_spread(c)
    assert r.passed and r.value == 2.0


def test_positive_spread_fails_on_frequent_one_sided_book() -> None:
    """One gap in two snapshots is 50% >> the 0.1% F9 tolerance."""
    c = _collector_from_quotes([(0.5, 99, 101)])
    c(_record(2, 1.0, None, None))
    assert not _fact_positive_spread(c).passed


def test_positive_spread_tolerates_rare_transient_gap() -> None:
    """A single sweep gap among 2000 snapshots (0.05%) still passes."""
    c = _collector_from_quotes(
        [(i * 0.01, 99, 101) for i in range(2_000)]
    )
    c(_record(2_001, 20.01, None, None))
    assert _fact_positive_spread(c).passed


def test_positive_spread_fails_when_empty() -> None:
    assert not _fact_positive_spread(MarketDataCollector()).passed


# ------------------------------------------------------ fact 4: return ACF


def test_return_acf_white_noise_passes() -> None:
    rng = np.random.default_rng(3)
    r = _fact_return_acf(rng.normal(size=2_000), acf_lags=20)
    assert r.passed


def test_return_acf_ar1_fails() -> None:
    rng = np.random.default_rng(3)
    x = np.zeros(2_000)
    for i in range(1, len(x)):
        x[i] = 0.7 * x[i - 1] + rng.normal()
    assert not _fact_return_acf(x, acf_lags=20).passed


# ----------------------------------------------------- fact 5: clustering


def test_vol_clustering_garch_passes_iid_fails() -> None:
    rng = np.random.default_rng(9)
    n = 4_000
    sigma2, r = np.empty(n), np.empty(n)
    sigma2[0], r[0] = 1.0, rng.normal()
    for i in range(1, n):
        sigma2[i] = 0.1 + 0.25 * r[i - 1] ** 2 + 0.7 * sigma2[i - 1]
        r[i] = math.sqrt(sigma2[i]) * rng.normal()
    assert _fact_vol_clustering(r, lb_lags=10).passed
    assert not _fact_vol_clustering(rng.normal(size=n), lb_lags=10).passed


# --------------------------------------------------- fact 7: dealer delta


@dataclass
class _HedgeRec:
    pre_delta_lots: float
    filled_qty_lots: int


class _StubDealerConfig:
    delta_hedge_threshold = 0.05


def test_dealer_delta_fact_uses_hedge_log() -> None:
    from sim.agents.options_mm import OptionsMarketMaker

    dealer = object.__new__(OptionsMarketMaker)  # skip heavy __init__
    dealer.config = _StubDealerConfig()
    dealer._hedge_log = [_HedgeRec(-3.4, 3), _HedgeRec(2.2, -2)]
    r = _fact_dealer_delta({"agents": [dealer]})
    assert r.passed
    assert r.value == pytest.approx(0.4)

    dealer._hedge_log = [_HedgeRec(-3.4, 2)]  # post-hedge -1.4 lots
    assert not _fact_dealer_delta({"agents": [dealer]}).passed


def test_dealer_delta_fact_without_dealer_fails() -> None:
    assert not _fact_dealer_delta({"agents": []}).passed


# ------------------------------------------------------------- end-to-end


def test_evaluate_on_real_run_returns_all_facts() -> None:
    from run_sim import run
    from sim.config.loader import load_config

    cfg = copy.deepcopy(load_config())
    cfg["market"]["max_steps"] = 3_000
    result = run(cfg)
    facts = evaluate_stylised_facts(result)
    assert tuple(facts.keys()) == FACT_NAMES
    for name, fact in facts.items():
        assert isinstance(fact, FactResult)
        assert isinstance(fact.passed, bool)
        assert fact.detail
    # The two facts already engineered to hold must hold even on a
    # short, uncalibrated run.
    assert facts["positive_spread"].passed
    assert facts["dealer_delta_flat"].passed
