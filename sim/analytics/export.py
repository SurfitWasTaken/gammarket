"""Post-run data export for research use (polish backlog).

Serialises one completed run (the dict returned by `run_sim.run`) to
CSV/JSON so results can feed external analysis and papers. Everything
here runs **after** the simulation completes — the F1 "zero file I/O in
the loop" contract is untouched. Pure stdlib csv/json + NumPy.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from sim.agents.options_mm import OptionsMarketMaker
from sim.analytics.facts import FactResult
from sim.analytics.metrics import (
    autocorrelation,
    effective_spread_bps,
    excess_kurtosis,
    kyle_lambda,
    ljung_box,
    log_returns,
    realized_vol,
    resample_mid,
    roll_measure,
    signed_volume_bars,
)
from sim.core.tape import Tape

_STEP_FIELDS = (
    "step", "timestamp", "best_bid", "best_ask", "mid", "spread",
    "bid_depth", "ask_depth", "last_fill_price", "rolling_vol_bps",
)

_FILL_FIELDS = (
    "timestamp", "price", "qty", "aggressor_side",
    "taker_agent_id", "maker_agent_id", "taker_order_id", "maker_order_id",
)


def steps_to_csv(collector, path: Path | str) -> Path:
    """Write the per-step snapshots (F1 records) to CSV.

    Args:
        collector: A `MarketDataCollector` from a completed run.
        path: Destination file.

    Returns:
        The written path.
    """
    path = Path(path)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_STEP_FIELDS)
        for r in collector.records:
            w.writerow([getattr(r, f) for f in _STEP_FIELDS])
    return path


def fills_to_csv(tape: Tape, path: Path | str) -> Path:
    """Write the full fill tape to CSV (one row per fill).

    Args:
        tape: The run's `Tape`.
        path: Destination file.

    Returns:
        The written path.
    """
    path = Path(path)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_FILL_FIELDS)
        for f in tape.fills:
            w.writerow([
                f.timestamp, f.price, f.qty, f.aggressor_side.value,
                f.taker_agent_id, f.maker_agent_id,
                str(f.taker_order_id), str(f.maker_order_id),
            ])
    return path


def bars_to_csv(
    collector, bar_minutes: float, path: Path | str
) -> Path:
    """Write the canonical bar series (F2: LOCF mid bars + log returns).

    Columns: `bar_close_time`, `bar_mid`, `log_return` (empty for the
    first bar, which has no prior close).

    Args:
        collector: A `MarketDataCollector` from a completed run.
        bar_minutes: Bar length in sim minutes.
        path: Destination file.

    Returns:
        The written path.
    """
    path = Path(path)
    bar_times, bar_mids = resample_mid(
        collector.timestamps(), collector.mids(), bar_minutes
    )
    returns = log_returns(bar_mids)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(("bar_close_time", "bar_mid", "log_return"))
        for i, (t, m) in enumerate(zip(bar_times, bar_mids)):
            w.writerow([t, m, "" if i == 0 else returns[i - 1]])
    return path


def metrics_summary(
    result: dict, *, bar_minutes: float, acf_lags: int = 20, lb_lags: int = 10
) -> dict[str, Any]:
    """Headline microstructure metrics for one run — a paper-ready table.

    Everything is computed on the F2 canonical series (bar mid log
    returns at `bar_minutes`) plus the fill tape. Values are plain
    Python floats/ints (JSON-serialisable); NaN where undefined.

    Args:
        result: The dict returned by `run_sim.run`.
        bar_minutes: Bar length for the return series.
        acf_lags: Lags for the mean |ACF| diagnostics.
        lb_lags: Lags for the Ljung-Box test on squared returns.

    Returns:
        Flat mapping of metric name to value.
    """
    collector = result["collector"]
    tape: Tape = result["tape"]
    fills = tape.fills
    minutes_per_year = float(result["cfg"]["market"]["minutes_per_year"])

    times, mids = collector.timestamps(), collector.mids()
    bar_times, bar_mids = resample_mid(times, mids, bar_minutes)
    r = log_returns(bar_mids)

    spreads = collector.spreads()
    finite_spreads = spreads[np.isfinite(spreads)]
    eff = effective_spread_bps(fills, times, mids)
    lb_q, lb_p = (
        ljung_box(r**2, lb_lags) if len(r) > lb_lags + 1
        else (math.nan, math.nan)
    )
    lam = (
        kyle_lambda(np.diff(bar_mids), signed_volume_bars(fills, bar_times)[1:])
        if len(bar_mids) >= 3 else math.nan
    )
    mean_abs_acf = (
        float(np.mean(np.abs(autocorrelation(r, acf_lags))))
        if len(r) > acf_lags + 1 else math.nan
    )

    out: dict[str, Any] = {
        "n_steps": len(collector),
        "n_fills": len(fills),
        "sim_minutes": float(times[-1]) if len(times) else math.nan,
        "bar_minutes": bar_minutes,
        "n_bars": int(len(bar_mids)),
        "realized_vol_annualised": realized_vol(
            r, bar_minutes, minutes_per_year
        ),
        "mean_quoted_spread_ticks": (
            float(finite_spreads.mean()) if len(finite_spreads) else math.nan
        ),
        "one_sided_snapshot_frac": (
            float(np.mean(~np.isfinite(spreads))) if len(spreads) else math.nan
        ),
        "mean_effective_spread_bps": (
            float(eff.mean()) if len(eff) else math.nan
        ),
        "roll_measure_ticks": roll_measure(
            np.array([f.price for f in fills], dtype=np.float64)
        ),
        "kyle_lambda": lam,
        "mean_abs_return_acf": mean_abs_acf,
        "ljung_box_q_sq_returns": float(lb_q),
        "ljung_box_p_sq_returns": float(lb_p),
        "excess_kurtosis": excess_kurtosis(r),
    }

    dealer = next(
        (a for a in result["agents"] if isinstance(a, OptionsMarketMaker)),
        None,
    )
    if dealer is not None:
        post_hedge = [
            abs(rec.pre_delta_lots + rec.filled_qty_lots)
            for rec in dealer.hedge_log
        ]
        out.update({
            "n_option_trades": len(dealer.trade_log),
            "n_hedges": len(dealer.hedge_log),
            "worst_post_hedge_delta_lots": (
                max(post_hedge) if post_hedge else math.nan
            ),
            "gamma_rejections": dealer.gamma_rejections,
            "option_cash_flow": dealer.option_cash_flow,
        })
    return out


def facts_to_dict(facts: dict[str, FactResult]) -> dict[str, dict]:
    """Convert a facts mapping into a JSON-serialisable dict."""
    return {
        name: {"passed": bool(f.passed), "value": float(f.value),
               "detail": f.detail}
        for name, f in facts.items()
    }


def write_json(obj: Any, path: Path | str) -> Path:
    """Write `obj` as indented JSON (NaN-safe via `allow_nan`)."""
    path = Path(path)
    path.write_text(json.dumps(obj, indent=2, default=str))
    return path
