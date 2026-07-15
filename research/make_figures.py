"""Paper comparison figures: the flow-mechanism study (research/paper.md).

Runs the three retail-flow mechanisms on one seed (123 — the seed where
the validated config misses volatility clustering, i.e. the hardest case)
and writes the two comparison figures plus the statistics they display:

    research/figures/mechanism_acf.png   — return ACF (top row) and
        squared-return ACF (bottom row) per mechanism
    research/figures/vol_paths.png       — windowed realized vol per
        mechanism, shared y-scale
    research/figures/mechanism_stats.json — the numbers cited in the paper

Reproduces in ~1 minute:  .venv/bin/python research/make_figures.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sim.analytics.figures import (  # noqa: E402
    C_AQUA, C_BLUE, INK, INK_2, SURFACE, style_axes,
)
from sim.analytics.metrics import (  # noqa: E402
    autocorrelation, excess_kurtosis, ljung_box, log_returns, resample_mid,
)
from sim.analytics.sweep import run_once  # noqa: E402
from sim.config.loader import load_config  # noqa: E402

C_YELLOW = "#eda100"  # categorical slot 3 (validated with slots 1-2)

SEED = 123
BAR_MINUTES = 0.25
WINDOW_BARS = 60
ACF_LAGS = 20
LB_LAGS = 10

# Fixed entity order and colors (color follows the mechanism, never rank).
MECHANISMS = (
    ("no feedback", C_BLUE, {"agents.retail.vol_feedback": 0.0}, None),
    ("vol feedback (validated)", C_AQUA, {}, None),
    ("feedback + regime", C_YELLOW, {}, "regime"),
)


def _bar_returns(result) -> np.ndarray:
    c = result["collector"]
    _, bar_mids = resample_mid(c.timestamps(), c.mids(), BAR_MINUTES)
    return log_returns(bar_mids)


def _windowed_vol(r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n_windows = len(r) // WINDOW_BARS
    idx = np.arange(n_windows)
    vols = np.array([
        float(np.std(r[w * WINDOW_BARS:(w + 1) * WINDOW_BARS])) * 1e4
        for w in idx
    ])
    return idx, vols


def main() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = ROOT / "research" / "figures"
    out.mkdir(parents=True, exist_ok=True)

    base = load_config(ROOT / "sim" / "config" / "phase6.yaml")
    regime_cfg = load_config(ROOT / "research" / "configs" / "phase6_regime.yaml")

    runs: list[tuple[str, str, np.ndarray]] = []
    stats: dict[str, dict] = {}
    for name, color, overrides, special in MECHANISMS:
        cfg = copy.deepcopy(regime_cfg if special == "regime" else base)
        print(f"running: {name} (seed {SEED})...", flush=True)
        row = run_once(
            cfg, overrides, SEED,
            bar_minutes=BAR_MINUTES, window_bars=WINDOW_BARS,
        )
        r = _bar_returns(row["result"])
        runs.append((name, color, r))
        _, lb_p = ljung_box(r**2, LB_LAGS)
        acf = autocorrelation(r, ACF_LAGS)
        band = 3.0 / np.sqrt(len(r))
        stats[name] = {
            "seed": SEED,
            "n_bars": int(len(r) + 1),
            "ljung_box_p_sq_returns": float(lb_p),
            "excess_kurtosis": float(excess_kurtosis(r)),
            "acf_frac_inside_band": float(np.mean(np.abs(acf) < band)),
            "facts_passed": row["n_passed"],
        }

    # --- Figure 1: return ACF (top) and squared-return ACF (bottom) -------
    lags = np.arange(1, ACF_LAGS + 1)
    fig, axes = plt.subplots(
        2, len(runs), figsize=(10.5, 5.4), sharey="row", facecolor=SURFACE,
    )
    for col, (name, color, r) in enumerate(runs):
        band = 3.0 / np.sqrt(len(r))
        for row_i, series in ((0, r), (1, r**2)):
            ax = axes[row_i, col]
            acf = autocorrelation(series, ACF_LAGS)
            ax.bar(lags, acf, width=0.62, color=color, edgecolor=SURFACE,
                   linewidth=0.5)
            ax.axhline(0.0, color=INK_2, linewidth=0.8)
            for y in (band, -band):
                ax.axhline(y, color=INK_2, linewidth=0.8,
                           linestyle=(0, (4, 3)))
            style_axes(ax)
        p = stats[name]["ljung_box_p_sq_returns"]
        axes[0, col].set_title(name, fontsize=9, loc="left", color=INK)
        axes[1, col].set_title(f"LB(10) p = {p:.2g}", fontsize=8, loc="left")
        axes[1, col].set_xlabel("lag (bars)", fontsize=8)
    axes[0, 0].set_ylabel("return ACF", fontsize=8)
    axes[1, 0].set_ylabel("squared-return ACF", fontsize=8)
    axes[0, -1].text(ACF_LAGS + 0.4, 3.0 / np.sqrt(len(runs[-1][2])),
                     "±3/√n", fontsize=7, color=INK_2, va="bottom",
                     ha="right")
    fig.suptitle(
        f"Retail-flow mechanisms: efficiency (top) vs volatility "
        f"clustering (bottom) — seed {SEED}",
        color=INK, fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(out / "mechanism_acf.png", dpi=130)
    plt.close(fig)

    # --- Figure 2: windowed realized vol paths, shared scale --------------
    fig, axes = plt.subplots(
        len(runs), 1, figsize=(9, 1.9 * len(runs)), sharex=True, sharey=True,
        facecolor=SURFACE,
    )
    for ax, (name, color, r) in zip(np.atleast_1d(axes), runs):
        idx, vols = _windowed_vol(r)
        ax.plot(idx, vols, color=color, linewidth=1.4, marker="o",
                markersize=3.0)
        # Direct label (relief rule for the sub-3:1 contrast slots).
        ax.text(0.995, 0.92, name, transform=ax.transAxes, fontsize=8.5,
                color=INK, ha="right", va="top")
        ax.set_ylabel("vol (bps/bar)", fontsize=8)
        style_axes(ax)
    np.atleast_1d(axes)[-1].set_xlabel(
        f"{WINDOW_BARS}-bar window index", fontsize=8
    )
    fig.suptitle(
        f"Windowed realized volatility by flow mechanism — seed {SEED}",
        color=INK, fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(out / "vol_paths.png", dpi=130)
    plt.close(fig)

    (out / "mechanism_stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))
    print(f"wrote {out}/mechanism_acf.png, vol_paths.png, "
          f"mechanism_stats.json")


if __name__ == "__main__":
    main()
