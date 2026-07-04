"""Pure post-run analytics on the fill tape and step snapshots.

All functions are NumPy-only (plus `scipy.stats.chi2` for the Ljung-Box
p-value); no matplotlib, no file I/O. The Phase 2-5 functions operate on
the fill tape; the Phase 6 additions (F2) also consume the per-step
snapshot series produced by `MarketDataCollector` (timestamps + mids).
The canonical return series for the stylised facts is **log returns of
the mid resampled to fixed sim-time bars** (`resample_mid`), not
event-time returns.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import chi2

from sim.core.events import Fill, Side


def fill_prices(fills: list[Fill]) -> np.ndarray:
    """Array of fill prices (in ticks), in chronological order."""
    if not fills:
        return np.empty(0, dtype=np.int64)
    return np.fromiter((f.price for f in fills), dtype=np.int64, count=len(fills))


def fill_quantities(fills: list[Fill]) -> np.ndarray:
    """Array of fill quantities (in lots), in chronological order."""
    if not fills:
        return np.empty(0, dtype=np.int64)
    return np.fromiter((f.qty for f in fills), dtype=np.int64, count=len(fills))


def trade_sizes(fills: list[Fill]) -> np.ndarray:
    """Alias for `fill_quantities`. Used by Phase 2 reporting."""
    return fill_quantities(fills)


def simple_returns(prices: np.ndarray) -> np.ndarray:
    """Simple returns `r[t] = (p[t] - p[t-1]) / p[t-1]`.

    Args:
        prices: Array of prices in chronological order.

    Returns:
        Float array of length `len(prices) - 1`. Returns NaN-safe
        behaviour: a single price (or empty input) returns an empty
        array.
    """
    if len(prices) < 2:
        return np.empty(0, dtype=np.float64)
    p = prices.astype(np.float64)
    return np.diff(p) / p[:-1]


def log_returns(prices: np.ndarray) -> np.ndarray:
    """Log returns `r[t] = log(p[t] / p[t-1])`. Same edge-case rules
    as `simple_returns`."""
    if len(prices) < 2:
        return np.empty(0, dtype=np.float64)
    p = prices.astype(np.float64)
    return np.log(p[1:] / p[:-1])


def autocorrelation(returns: np.ndarray, max_lag: int) -> np.ndarray:
    """Sample autocorrelation at lags 1..max_lag.

    Args:
        returns: Float array of returns (any centred series is fine).
        max_lag: Maximum lag to compute; must be >= 1.

    Returns:
        Float array of length `max_lag`. Degenerate inputs (constant
        series) return zeros.
    """
    if max_lag < 1:
        raise ValueError(f"max_lag must be >= 1, got {max_lag}")
    if len(returns) < 2:
        return np.zeros(max_lag, dtype=np.float64)
    r = returns - returns.mean()
    var = float(np.sum(r * r))
    if var == 0.0:
        return np.zeros(max_lag, dtype=np.float64)
    out = np.empty(max_lag, dtype=np.float64)
    for lag in range(1, max_lag + 1):
        out[lag - 1] = float(np.sum(r[:-lag] * r[lag:]) / var)
    return out


# --------------------------------------------------------------------------
# Phase 6 (F2) — microstructure + stylised-fact metrics
# --------------------------------------------------------------------------


def resample_mid(
    times: np.ndarray, mids: np.ndarray, bar_minutes: float
) -> tuple[np.ndarray, np.ndarray]:
    """Resample an event-time mid series to fixed sim-time bars (LOCF).

    Bars start at the time of the first finite mid and close every
    `bar_minutes`; each bar carries the last finite mid observed at or
    before its close (last observation carried forward).

    Args:
        times: Snapshot timestamps in minutes, non-decreasing.
        mids: Snapshot mids (float, may contain NaN for one-sided books).
        bar_minutes: Bar length in simulation minutes; must be positive.

    Returns:
        `(bar_times, bar_mids)` float arrays of equal length; empty if
        fewer than one bar fits or no finite mid exists.
    """
    if bar_minutes <= 0:
        raise ValueError(f"bar_minutes must be positive, got {bar_minutes}")
    finite = np.isfinite(mids)
    if not finite.any():
        return np.empty(0), np.empty(0)
    t = np.asarray(times, dtype=np.float64)[finite]
    m = np.asarray(mids, dtype=np.float64)[finite]
    t0, t_end = float(t[0]), float(t[-1])
    n_bars = int(np.floor((t_end - t0) / bar_minutes))
    if n_bars < 1:
        return np.empty(0), np.empty(0)
    bar_times = t0 + bar_minutes * np.arange(1, n_bars + 1, dtype=np.float64)
    idx = np.searchsorted(t, bar_times, side="right") - 1
    return bar_times, m[idx]


def realized_vol(
    bar_returns: np.ndarray, bar_minutes: float, minutes_per_year: float
) -> float:
    """Annualised realized volatility of bar log returns.

    `std(returns) * sqrt(minutes_per_year / bar_minutes)` with the
    population std (ddof=0, matching the Clock convention).

    Args:
        bar_returns: Log returns of the bar mid series.
        bar_minutes: Bar length in minutes; must be positive.
        minutes_per_year: Calendar convention (D1), e.g. 525_600.

    Returns:
        Annualised vol as a fraction (0.2 = 20%); NaN if < 2 returns.
    """
    if bar_minutes <= 0:
        raise ValueError(f"bar_minutes must be positive, got {bar_minutes}")
    if len(bar_returns) < 2:
        return float("nan")
    return float(np.std(bar_returns) * np.sqrt(minutes_per_year / bar_minutes))


def effective_spread_bps(
    fills: list[Fill], snap_times: np.ndarray, snap_mids: np.ndarray
) -> np.ndarray:
    """Per-fill effective spread `2*d*(p - m)/m * 1e4` in bps (F2).

    `d` is +1 for a taker BUY and -1 for a taker SELL; `m` is the last
    finite snapshot mid *strictly before* the fill's timestamp (the
    snapshot at the fill's own step is post-trade, so ties look back).
    Fills with no prior mid are skipped.

    Args:
        fills: Chronological fills (the tape).
        snap_times: Per-step snapshot timestamps (collector order).
        snap_mids: Per-step snapshot mids, NaN where one-sided.

    Returns:
        Float array (one entry per usable fill), possibly empty.
    """
    finite = np.isfinite(snap_mids)
    t = np.asarray(snap_times, dtype=np.float64)[finite]
    m = np.asarray(snap_mids, dtype=np.float64)[finite]
    out: list[float] = []
    for f in fills:
        i = int(np.searchsorted(t, f.timestamp, side="left")) - 1
        if i < 0:
            continue
        mid = m[i]
        if mid <= 0:
            continue
        d = 1.0 if f.aggressor_side is Side.BUY else -1.0
        out.append(2.0 * d * (f.price - mid) / mid * 1e4)
    return np.array(out, dtype=np.float64)


def roll_measure(trade_prices: np.ndarray) -> float:
    """Roll (1984) implied spread: `2*sqrt(max(0, -cov(dp_t, dp_{t-1})))`.

    Computed on trade-price first differences with the population
    covariance. Returns 0.0 when the serial covariance is non-negative
    (no bounce detectable) and NaN with < 3 prices.
    """
    if len(trade_prices) < 3:
        return float("nan")
    dp = np.diff(np.asarray(trade_prices, dtype=np.float64))
    a, b = dp[:-1], dp[1:]
    cov = float(np.mean(a * b) - np.mean(a) * np.mean(b))
    return 2.0 * float(np.sqrt(max(0.0, -cov)))


def signed_volume_bars(fills: list[Fill], bar_times: np.ndarray) -> np.ndarray:
    """Signed taker volume per bar (taker BUY +qty, taker SELL -qty).

    Bar k collects fills with `timestamp` in `(bar_times[k-1],
    bar_times[k]]` (the first bar reaches back to -inf, matching the
    LOCF bars from `resample_mid`).

    Args:
        fills: Chronological fills.
        bar_times: Bar close times from `resample_mid`.

    Returns:
        Int64 array, same length as `bar_times`.
    """
    out = np.zeros(len(bar_times), dtype=np.int64)
    if len(bar_times) == 0:
        return out
    bt = np.asarray(bar_times, dtype=np.float64)
    for f in fills:
        k = int(np.searchsorted(bt, f.timestamp, side="left"))
        if k >= len(bt):
            continue
        out[k] += f.qty if f.aggressor_side is Side.BUY else -f.qty
    return out


def kyle_lambda(mid_changes: np.ndarray, signed_volume: np.ndarray) -> float:
    """Kyle's lambda: OLS slope of per-bar mid changes on signed volume.

    `lambda = cov(v, dm) / var(v)` (population moments). Positive lambda
    means buy pressure moves the mid up — the price-impact stylised
    fact. Returns NaN for degenerate inputs (< 2 bars or zero variance).
    """
    if len(mid_changes) != len(signed_volume):
        raise ValueError(
            f"length mismatch: {len(mid_changes)} mid changes vs "
            f"{len(signed_volume)} volume bars"
        )
    if len(mid_changes) < 2:
        return float("nan")
    v = np.asarray(signed_volume, dtype=np.float64)
    dm = np.asarray(mid_changes, dtype=np.float64)
    var = float(np.var(v))
    if var == 0.0:
        return float("nan")
    cov = float(np.mean(v * dm) - v.mean() * dm.mean())
    return cov / var


def price_impact_by_quartile(
    fills: list[Fill], snap_times: np.ndarray, snap_mids: np.ndarray
) -> tuple[float, float]:
    """Mean directional mid impact of small vs large trades (F9 fact 3).

    For each fill, impact = `d * (mid_after - mid_before)` in ticks,
    where `mid_before` is the last finite snapshot mid strictly before
    the fill and `mid_after` the first at/after it, and `d` is the taker
    direction. Fills are split by quantity: bottom quartile (small) vs
    top quartile (large).

    Returns:
        `(small_mean, large_mean)`; NaN entries when a bucket is empty.
    """
    finite = np.isfinite(snap_mids)
    t = np.asarray(snap_times, dtype=np.float64)[finite]
    m = np.asarray(snap_mids, dtype=np.float64)[finite]
    qtys: list[int] = []
    impacts: list[float] = []
    for f in fills:
        i_before = int(np.searchsorted(t, f.timestamp, side="left")) - 1
        i_after = int(np.searchsorted(t, f.timestamp, side="left"))
        if i_before < 0 or i_after >= len(t):
            continue
        d = 1.0 if f.aggressor_side is Side.BUY else -1.0
        qtys.append(f.qty)
        impacts.append(d * (m[i_after] - m[i_before]))
    if not impacts:
        return float("nan"), float("nan")
    q = np.array(qtys, dtype=np.float64)
    imp = np.array(impacts, dtype=np.float64)
    q25, q75 = np.quantile(q, 0.25), np.quantile(q, 0.75)
    small = imp[q <= q25]
    large = imp[q >= q75]
    small_mean = float(small.mean()) if len(small) else float("nan")
    large_mean = float(large.mean()) if len(large) else float("nan")
    return small_mean, large_mean


def ljung_box(series: np.ndarray, lags: int) -> tuple[float, float]:
    """Ljung-Box portmanteau test for autocorrelation up to `lags`.

    `Q = n*(n+2) * sum_k acf_k^2 / (n-k)`; the p-value comes from the
    chi-squared distribution with `lags` degrees of freedom
    (`scipy.stats.chi2` — statsmodels is not an approved dependency).
    Applied to squared returns this is the ARCH-effect / volatility-
    clustering test (F9 fact 5).

    Args:
        series: The series to test (e.g. squared bar returns).
        lags: Number of lags; must be >= 1 and < len(series).

    Returns:
        `(Q, p_value)`; `(nan, nan)` for degenerate series.
    """
    if lags < 1:
        raise ValueError(f"lags must be >= 1, got {lags}")
    n = len(series)
    if n <= lags + 1:
        return float("nan"), float("nan")
    acf = autocorrelation(np.asarray(series, dtype=np.float64), lags)
    if not np.isfinite(acf).all():
        return float("nan"), float("nan")
    k = np.arange(1, lags + 1, dtype=np.float64)
    q = float(n * (n + 2) * np.sum(acf**2 / (n - k)))
    p = float(chi2.sf(q, df=lags))
    return q, p


def excess_kurtosis(x: np.ndarray) -> float:
    """Excess kurtosis (Fisher): `E[(x-mu)^4]/sigma^4 - 3`, population
    moments. Positive values = fatter tails than a normal (F9 fact 6).
    Returns NaN for < 4 observations or zero variance.
    """
    if len(x) < 4:
        return float("nan")
    v = np.asarray(x, dtype=np.float64)
    c = v - v.mean()
    var = float(np.mean(c**2))
    if var == 0.0:
        return float("nan")
    return float(np.mean(c**4) / var**2 - 3.0)
