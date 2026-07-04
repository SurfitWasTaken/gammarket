"""Stylised-facts evaluator (Phase 6, F9) — the project's pass criteria.

`evaluate_stylised_facts` turns one completed run (the dict returned by
`run_sim.run`) into a verdict per stylised fact, using the frozen F9
thresholds. The canonical return series is bar-resampled mid log returns
(F2). NumPy + the Phase 6 metrics only; no file I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from sim.agents.options_mm import OptionsMarketMaker
from sim.analytics.metrics import (
    autocorrelation,
    excess_kurtosis,
    kyle_lambda,
    ljung_box,
    log_returns,
    price_impact_by_quartile,
    resample_mid,
    signed_volume_bars,
)

FACT_NAMES: tuple[str, ...] = (
    "positive_spread",
    "spread_widens_with_vol",
    "price_impact",
    "return_acf_near_zero",
    "volatility_clustering",
    "fat_tails",
    "dealer_delta_flat",
)


@dataclass(frozen=True)
class FactResult:
    """Verdict for one stylised fact.

    Args:
        passed: Whether the F9 criterion held.
        value: The measured statistic the criterion was applied to.
        detail: One-line human-readable summary (statistic + threshold).
    """

    passed: bool
    value: float
    detail: str


def _fact_positive_spread(collector) -> FactResult:
    spreads = collector.spreads()
    if len(spreads) == 0:
        return FactResult(False, math.nan, "no snapshots collected")
    n_one_sided = int(np.sum(~np.isfinite(spreads)))
    finite = spreads[np.isfinite(spreads)]
    if len(finite) == 0:
        return FactResult(False, math.nan, "book never two-sided")
    min_spread = float(finite.min())
    gap_frac = n_one_sided / len(spreads)
    # F9: >= 1 tick at every two-sided snapshot; one-sided gap snapshots
    # (a sweep emptying one side until the next MM arrival) <= 0.1%.
    passed = gap_frac <= 0.001 and min_spread >= 1.0
    return FactResult(
        passed,
        min_spread,
        f"min spread {min_spread:.0f} ticks (need >= 1); one-sided "
        f"snapshots {gap_frac:.3%} (need <= 0.1%)",
    )


def _fact_spread_vol(
    collector,
    bar_times: np.ndarray,
    bar_returns: np.ndarray,
    window_bars: int,
    corr_min: float,
) -> FactResult:
    n_windows = len(bar_returns) // window_bars
    if n_windows < 5:
        return FactResult(
            False, math.nan, f"only {n_windows} vol windows (need >= 5)"
        )
    times = collector.timestamps()
    spreads = collector.spreads()
    ok = np.isfinite(spreads)
    times, spreads = times[ok], spreads[ok]
    vols: list[float] = []
    mean_spreads: list[float] = []
    for w in range(n_windows):
        chunk = bar_returns[w * window_bars : (w + 1) * window_bars]
        # Bar k closes at bar_times[k+1]; the window spans those closes.
        t_lo = bar_times[w * window_bars]
        t_hi = bar_times[min((w + 1) * window_bars, len(bar_times) - 1)]
        in_win = (times > t_lo) & (times <= t_hi)
        if not in_win.any():
            continue
        vols.append(float(np.std(chunk)))
        mean_spreads.append(float(spreads[in_win].mean()))
    if len(vols) < 5:
        return FactResult(False, math.nan, "too few usable windows")
    v, s = np.array(vols), np.array(mean_spreads)
    if v.std() == 0.0 or s.std() == 0.0:
        return FactResult(False, math.nan, "degenerate vol/spread windows")
    corr = float(np.corrcoef(v, s)[0, 1])
    return FactResult(
        corr > corr_min,
        corr,
        f"corr(windowed vol, windowed spread) {corr:.3f} (need > {corr_min})",
    )


def _fact_price_impact(result, bar_times, bar_mids) -> FactResult:
    fills = result["tape"].fills
    collector = result["collector"]
    if len(bar_mids) < 3:
        return FactResult(False, math.nan, "too few bars")
    lam = kyle_lambda(
        np.diff(bar_mids), signed_volume_bars(fills, bar_times)[1:]
    )
    small, large = price_impact_by_quartile(
        fills, collector.timestamps(), collector.mids()
    )
    passed = (
        np.isfinite(lam)
        and lam > 0.0
        and np.isfinite(small)
        and np.isfinite(large)
        and large > small
    )
    return FactResult(
        passed,
        lam,
        f"Kyle lambda {lam:.4g} (need > 0); impact small {small:.3f} vs "
        f"large {large:.3f} ticks (need large > small)",
    )


def _fact_return_acf(bar_returns: np.ndarray, acf_lags: int) -> FactResult:
    n = len(bar_returns)
    if n < acf_lags + 2:
        return FactResult(False, math.nan, f"only {n} bar returns")
    acf = autocorrelation(bar_returns, acf_lags)
    band = 3.0 / math.sqrt(n)
    frac_inside = float(np.mean(np.abs(acf) < band))
    return FactResult(
        frac_inside >= 0.9,
        frac_inside,
        f"{frac_inside:.0%} of lags 1..{acf_lags} inside +-3/sqrt(n)"
        f"={band:.3f} (need >= 90%)",
    )


def _fact_vol_clustering(bar_returns: np.ndarray, lb_lags: int) -> FactResult:
    _, p = ljung_box(bar_returns**2, lb_lags)
    if not np.isfinite(p):
        return FactResult(False, math.nan, "Ljung-Box degenerate")
    return FactResult(
        p < 0.05,
        p,
        f"Ljung-Box on squared returns p={p:.3g} (need < 0.05)",
    )


def _fact_fat_tails(bar_returns: np.ndarray) -> FactResult:
    k = excess_kurtosis(bar_returns)
    if not np.isfinite(k):
        return FactResult(False, math.nan, "kurtosis degenerate")
    return FactResult(k > 0.0, k, f"excess kurtosis {k:.3f} (need > 0)")


def _fact_dealer_delta(result) -> FactResult:
    dealer = next(
        (a for a in result["agents"] if isinstance(a, OptionsMarketMaker)),
        None,
    )
    if dealer is None:
        return FactResult(False, math.nan, "no options dealer in this run")
    if not dealer.hedge_log:
        return FactResult(False, math.nan, "dealer never hedged")
    bound = max(dealer.config.delta_hedge_threshold, 0.5)
    worst = max(
        abs(rec.pre_delta_lots + rec.filled_qty_lots)
        for rec in dealer.hedge_log
    )
    return FactResult(
        worst <= bound,
        worst,
        f"worst post-hedge |delta| {worst:.3f} lots over "
        f"{len(dealer.hedge_log)} hedges (need <= {bound})",
    )


def evaluate_stylised_facts(
    result: dict,
    *,
    bar_minutes: float = 1.0,
    acf_lags: int = 20,
    lb_lags: int = 10,
    window_bars: int = 30,
    spread_vol_corr_min: float = 0.2,
) -> dict[str, FactResult]:
    """Evaluate all seven stylised facts (F9) on one completed run.

    Args:
        result: The dict returned by `run_sim.run` (needs `tape`,
            `collector`, `agents`).
        bar_minutes: Bar length for the canonical mid return series.
        acf_lags: Lags for the efficiency (near-zero ACF) fact.
        lb_lags: Lags for the Ljung-Box vol-clustering test.
        window_bars: Bars per window for the spread-vol correlation.
        spread_vol_corr_min: Correlation threshold for fact 2.

    Returns:
        Mapping of fact name (see `FACT_NAMES`) to `FactResult`, in the
        F9 order.
    """
    collector = result["collector"]
    bar_times, bar_mids = resample_mid(
        collector.timestamps(), collector.mids(), bar_minutes
    )
    bar_returns = log_returns(bar_mids)

    return {
        "positive_spread": _fact_positive_spread(collector),
        "spread_widens_with_vol": _fact_spread_vol(
            collector, bar_times, bar_returns, window_bars, spread_vol_corr_min
        ),
        "price_impact": _fact_price_impact(result, bar_times, bar_mids),
        "return_acf_near_zero": _fact_return_acf(bar_returns, acf_lags),
        "volatility_clustering": _fact_vol_clustering(bar_returns, lb_lags),
        "fat_tails": _fact_fat_tails(bar_returns),
        "dealer_delta_flat": _fact_dealer_delta(result),
    }
