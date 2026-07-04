"""Known-value tests for the Phase 6 (F2) metrics additions.

The six pre-existing metric functions are pinned by `test_metrics.py`;
everything here exercises only the additive Phase 6 surface.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest

from sim.analytics.metrics import (
    effective_spread_bps,
    excess_kurtosis,
    kyle_lambda,
    ljung_box,
    price_impact_by_quartile,
    realized_vol,
    resample_mid,
    roll_measure,
    signed_volume_bars,
)
from sim.core.events import Fill, Side


def _fill(t: float, price: int, qty: int, side: Side) -> Fill:
    return Fill(
        taker_order_id=uuid.uuid4(),
        maker_order_id=uuid.uuid4(),
        taker_agent_id="taker",
        maker_agent_id="maker",
        aggressor_side=side,
        price=price,
        qty=qty,
        timestamp=t,
    )


# ------------------------------------------------------------ resample_mid


def test_resample_mid_locf() -> None:
    times = np.array([0.0, 0.4, 1.3, 2.7, 3.9])
    mids = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
    bar_times, bar_mids = resample_mid(times, mids, bar_minutes=1.0)
    # Bars close at 1.0, 2.0, 3.0 (3.9 - 0.0 = 3.9 -> 3 bars).
    assert bar_times.tolist() == [1.0, 2.0, 3.0]
    assert bar_mids.tolist() == [101.0, 102.0, 103.0]


def test_resample_mid_skips_nan() -> None:
    times = np.array([0.0, 0.5, 1.5])
    mids = np.array([np.nan, 100.0, 101.0])
    bar_times, bar_mids = resample_mid(times, mids, bar_minutes=1.0)
    # First finite mid at t=0.5 -> single bar closing at 1.5.
    assert bar_times.tolist() == [1.5]
    assert bar_mids.tolist() == [101.0]


def test_resample_mid_empty_cases() -> None:
    bt, bm = resample_mid(np.array([0.0]), np.array([np.nan]), 1.0)
    assert len(bt) == 0 and len(bm) == 0
    bt, bm = resample_mid(np.array([0.0, 0.1]), np.array([1.0, 2.0]), 1.0)
    assert len(bt) == 0
    with pytest.raises(ValueError, match="bar_minutes"):
        resample_mid(np.array([0.0]), np.array([1.0]), 0.0)


# ------------------------------------------------------------ realized_vol


def test_realized_vol_known_value() -> None:
    r = np.array([0.01, -0.01, 0.01, -0.01])
    # population std = 0.01; annualisation sqrt(525600 / 1)
    expected = 0.01 * np.sqrt(525_600.0)
    assert realized_vol(r, 1.0, 525_600.0) == pytest.approx(expected)


def test_realized_vol_degenerate() -> None:
    assert np.isnan(realized_vol(np.array([0.01]), 1.0, 525_600.0))


# ---------------------------------------------------------- eff. spread


def test_effective_spread_known_value() -> None:
    snap_times = np.array([0.0, 1.0, 2.0])
    snap_mids = np.array([100.0, 100.0, 100.0])
    # Taker BUY at 101 against mid 100 -> 2*(101-100)/100*1e4 = 200 bps.
    # Taker SELL at 99 against mid 100 -> 2*(-1)*(99-100)/100*1e4 = 200 bps.
    fills = [_fill(1.5, 101, 1, Side.BUY), _fill(1.5, 99, 1, Side.SELL)]
    es = effective_spread_bps(fills, snap_times, snap_mids)
    assert es.tolist() == [200.0, 200.0]


def test_effective_spread_uses_pre_trade_mid() -> None:
    """A snapshot at exactly the fill's timestamp is post-trade and must
    be ignored in favour of the previous one."""
    snap_times = np.array([0.0, 1.0])
    snap_mids = np.array([100.0, 999.0])
    es = effective_spread_bps([_fill(1.0, 101, 1, Side.BUY)], snap_times, snap_mids)
    assert es.tolist() == [200.0]


def test_effective_spread_skips_fill_before_first_mid() -> None:
    es = effective_spread_bps(
        [_fill(0.0, 101, 1, Side.BUY)], np.array([1.0]), np.array([100.0])
    )
    assert len(es) == 0


# ------------------------------------------------------------ roll measure


def test_roll_measure_perfect_bounce() -> None:
    """Deterministic bid-ask alternation: dp = +-s, population
    cov(dp_t, dp_{t-1}) = -s^2 -> roll = 2s."""
    prices = np.array([100, 102, 100, 102, 100, 102, 100, 102, 100])
    # Finite-sample mean subtraction shaves the exact 4.0 slightly.
    assert roll_measure(prices) == pytest.approx(4.0, rel=0.02)


def test_roll_measure_trending_series_is_zero() -> None:
    prices = np.array([100, 101, 102, 103, 104])
    assert roll_measure(prices) == 0.0


def test_roll_measure_degenerate() -> None:
    assert np.isnan(roll_measure(np.array([100, 101])))


# --------------------------------------------------- signed volume / Kyle


def test_signed_volume_bars_buckets() -> None:
    bar_times = np.array([1.0, 2.0])
    fills = [
        _fill(0.5, 100, 3, Side.BUY),   # bar 0
        _fill(1.0, 100, 2, Side.SELL),  # bar 0 (inclusive right edge)
        _fill(1.5, 100, 5, Side.BUY),   # bar 1
        _fill(2.5, 100, 9, Side.BUY),   # beyond last bar -> dropped
    ]
    assert signed_volume_bars(fills, bar_times).tolist() == [1, 5]


def test_kyle_lambda_recovers_slope() -> None:
    rng = np.random.default_rng(0)
    v = rng.integers(-50, 51, size=500).astype(np.float64)
    dm = 0.3 * v + rng.normal(0.0, 0.01, size=500)
    assert kyle_lambda(dm, v) == pytest.approx(0.3, abs=0.01)


def test_kyle_lambda_degenerate() -> None:
    assert np.isnan(kyle_lambda(np.array([1.0, 2.0]), np.array([5.0, 5.0])))
    with pytest.raises(ValueError, match="length mismatch"):
        kyle_lambda(np.array([1.0]), np.array([1.0, 2.0]))


# ------------------------------------------------------------ price impact


def test_price_impact_large_beats_small() -> None:
    """Large trades constructed to move the mid 5 ticks, small ones 1."""
    snap_times = np.array([0.0, 1.0, 2.0, 3.0, 4.0], dtype=np.float64)
    snap_mids = np.array([100.0, 101.0, 101.0, 106.0, 106.0])
    fills = [
        _fill(0.5, 100, 1, Side.BUY),   # before: 100 (t=0), after: 101 (t=1)
        _fill(2.5, 101, 10, Side.BUY),  # before: 101 (t=2), after: 106 (t=3)
    ]
    small, large = price_impact_by_quartile(fills, snap_times, snap_mids)
    assert small == pytest.approx(1.0)
    assert large == pytest.approx(5.0)


def test_price_impact_empty() -> None:
    small, large = price_impact_by_quartile([], np.array([0.0]), np.array([1.0]))
    assert np.isnan(small) and np.isnan(large)


# -------------------------------------------------------------- Ljung-Box


def test_ljung_box_white_noise_not_significant() -> None:
    rng = np.random.default_rng(42)
    x = rng.normal(size=2_000)
    q, p = ljung_box(x, lags=10)
    assert q > 0
    assert p > 0.01


def test_ljung_box_ar1_significant() -> None:
    rng = np.random.default_rng(0)
    x = np.zeros(2_000)
    for i in range(1, len(x)):
        x[i] = 0.6 * x[i - 1] + rng.normal()
    _, p = ljung_box(x, lags=10)
    assert p < 1e-6


def test_ljung_box_squared_garch_returns_significant() -> None:
    """A GARCH-like series: returns themselves are ~uncorrelated but the
    squares are — exactly the vol-clustering signature."""
    rng = np.random.default_rng(7)
    n = 4_000
    sigma2 = np.empty(n)
    r = np.empty(n)
    sigma2[0] = 1.0
    r[0] = rng.normal()
    for i in range(1, n):
        sigma2[i] = 0.1 + 0.2 * r[i - 1] ** 2 + 0.75 * sigma2[i - 1]
        r[i] = np.sqrt(sigma2[i]) * rng.normal()
    _, p_r = ljung_box(r, lags=10)
    _, p_r2 = ljung_box(r**2, lags=10)
    assert p_r2 < 1e-6
    assert p_r2 < p_r


def test_ljung_box_degenerate() -> None:
    q, p = ljung_box(np.array([1.0, 2.0]), lags=5)
    assert np.isnan(q) and np.isnan(p)
    with pytest.raises(ValueError, match="lags"):
        ljung_box(np.ones(100), lags=0)


# ---------------------------------------------------------------- kurtosis


def test_excess_kurtosis_normal_near_zero() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=200_000)
    assert abs(excess_kurtosis(x)) < 0.05


def test_excess_kurtosis_laplace_positive() -> None:
    rng = np.random.default_rng(2)
    x = rng.laplace(size=100_000)
    # Laplace excess kurtosis is exactly 3.
    assert excess_kurtosis(x) == pytest.approx(3.0, abs=0.2)


def test_excess_kurtosis_degenerate() -> None:
    assert np.isnan(excess_kurtosis(np.array([1.0, 1.0, 1.0, 1.0])))
    assert np.isnan(excess_kurtosis(np.array([1.0, 2.0])))
