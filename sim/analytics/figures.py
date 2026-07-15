"""Publication figures for completed runs (extracted from run_phase6).

Each function takes a "row" as produced by `sim.analytics.sweep.run_once`
(needs `row["result"]["collector"]` and `row["seed"]`) and writes one PNG.
Bar length / window size are explicit parameters so any experiment spec
can reuse them — `run_phase6.py` and `run_experiment.py` both do.

Matplotlib only, Agg-safe; import cost is deferred to call time.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from sim.analytics.metrics import (
    autocorrelation,
    excess_kurtosis,
    log_returns,
    resample_mid,
)

# Reference dataviz palette (light mode, validated set — see the dataviz
# skill's references/palette.md). Slot order is the CVD-safety mechanism.
C_BLUE = "#2a78d6"
C_AQUA = "#1baf7a"
INK = "#0b0b0b"
INK_2 = "#52514e"
SURFACE = "#fcfcfb"
GRID = "#d9d8d4"


def style_axes(ax) -> None:
    """Apply the shared light-mode axis style."""
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8)
    ax.title.set_color(INK)
    ax.xaxis.label.set_color(INK_2)
    ax.yaxis.label.set_color(INK_2)


def fig_price(rows: list[dict], out: Path) -> None:
    """Small multiples: one mid-price panel per seed (single series each)."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(
        len(rows), 1, figsize=(9, 2.2 * len(rows)), sharex=False,
        facecolor=SURFACE,
    )
    for ax, row in zip(np.atleast_1d(axes), rows):
        c = row["result"]["collector"]
        t, m = c.timestamps(), c.mids()
        ok = np.isfinite(m)
        ax.plot(t[ok], m[ok], color=C_BLUE, linewidth=1.0)
        ax.set_title(f"seed {row['seed']}", fontsize=9, loc="left")
        ax.set_ylabel("mid (ticks)", fontsize=8)
        style_axes(ax)
    np.atleast_1d(axes)[-1].set_xlabel("simulation time (minutes)", fontsize=8)
    fig.suptitle("Mid price — calibrated full runs", color=INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "price.png", dpi=130)
    plt.close(fig)


def fig_acf(row: dict, out: Path, bar_minutes: float) -> None:
    """Return ACF (efficiency) and squared-return ACF (clustering)."""
    import matplotlib.pyplot as plt

    c = row["result"]["collector"]
    _, bar_mids = resample_mid(c.timestamps(), c.mids(), bar_minutes)
    r = log_returns(bar_mids)
    band = 3.0 / np.sqrt(len(r))
    lags = np.arange(1, 21)

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.2), facecolor=SURFACE)
    for ax, series, color, title in (
        (axes[0], r, C_BLUE, "bar returns"),
        (axes[1], r**2, C_AQUA, "squared bar returns"),
    ):
        acf = autocorrelation(series, 20)
        ax.bar(lags, acf, width=0.62, color=color, edgecolor=SURFACE,
               linewidth=0.5)
        ax.axhline(0.0, color=INK_2, linewidth=0.8)
        for y in (band, -band):
            ax.axhline(y, color=INK_2, linewidth=0.8, linestyle=(0, (4, 3)))
        ax.set_title(f"ACF of {title}", fontsize=9, loc="left")
        ax.set_xlabel("lag (bars)", fontsize=8)
        style_axes(ax)
    axes[0].text(20.4, band, "±3/√n", fontsize=7, color=INK_2, va="bottom",
                 ha="right")
    fig.suptitle(
        f"Efficiency vs volatility clustering — seed {row['seed']}",
        color=INK, fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(out / "acf.png", dpi=130)
    plt.close(fig)


def fig_distribution(row: dict, out: Path, bar_minutes: float) -> None:
    """Bar-return histogram against the fitted normal (fat tails)."""
    import matplotlib.pyplot as plt

    c = row["result"]["collector"]
    _, bar_mids = resample_mid(c.timestamps(), c.mids(), bar_minutes)
    r = log_returns(bar_mids) * 1e4  # bps for readable ticks

    fig, ax = plt.subplots(figsize=(6.5, 3.6), facecolor=SURFACE)
    n_bins = 45
    ax.hist(r, bins=n_bins, density=True, color=C_BLUE,
            edgecolor=SURFACE, linewidth=0.5)
    mu, sd = float(r.mean()), float(r.std())
    xs = np.linspace(r.min(), r.max(), 400)
    pdf = np.exp(-0.5 * ((xs - mu) / sd) ** 2) / (sd * np.sqrt(2 * np.pi))
    ax.plot(xs, pdf, color=INK_2, linewidth=1.4, linestyle=(0, (4, 3)))
    ax.text(xs[-1], pdf[len(pdf) // 2], f"N({mu:.1f}, {sd:.1f}²)",
            fontsize=7.5, color=INK_2, ha="right")
    k = excess_kurtosis(r)
    ax.set_title(
        f"Bar-return distribution — seed {row['seed']} "
        f"(excess kurtosis {k:.2f})",
        fontsize=9, loc="left",
    )
    ax.set_xlabel("bar log return (bps)", fontsize=8)
    ax.set_ylabel("density", fontsize=8)
    style_axes(ax)
    fig.tight_layout()
    fig.savefig(out / "distribution.png", dpi=130)
    plt.close(fig)


def fig_spread_vol(
    row: dict, out: Path, bar_minutes: float, window_bars: int
) -> None:
    """Windowed realized vol and mean quoted spread — two panels, one
    x-axis, never a dual axis."""
    import matplotlib.pyplot as plt

    c = row["result"]["collector"]
    bar_times, bar_mids = resample_mid(c.timestamps(), c.mids(), bar_minutes)
    r = log_returns(bar_mids)
    n_windows = len(r) // window_bars
    times, spreads = c.timestamps(), c.spreads()
    ok = np.isfinite(spreads)
    times, spreads = times[ok], spreads[ok]
    w_t, w_vol, w_sp = [], [], []
    for w in range(n_windows):
        chunk = r[w * window_bars : (w + 1) * window_bars]
        t_lo = bar_times[w * window_bars]
        t_hi = bar_times[min((w + 1) * window_bars, len(bar_times) - 1)]
        in_win = (times > t_lo) & (times <= t_hi)
        if not in_win.any():
            continue
        w_t.append((t_lo + t_hi) / 2.0)
        w_vol.append(float(np.std(chunk)) * 1e4)
        w_sp.append(float(spreads[in_win].mean()))

    corr = float(np.corrcoef(w_vol, w_sp)[0, 1])
    fig, axes = plt.subplots(2, 1, figsize=(9, 4.4), sharex=True,
                             facecolor=SURFACE)
    axes[0].plot(w_t, w_vol, color=C_BLUE, linewidth=1.4, marker="o",
                 markersize=3.5)
    axes[0].set_ylabel("realized vol (bps/bar)", fontsize=8)
    axes[0].set_title("windowed realized vol", fontsize=9, loc="left")
    axes[1].plot(w_t, w_sp, color=C_AQUA, linewidth=1.4, marker="o",
                 markersize=3.5)
    axes[1].set_ylabel("mean quoted spread (ticks)", fontsize=8)
    axes[1].set_title("windowed mean spread", fontsize=9, loc="left")
    axes[1].set_xlabel("simulation time (minutes)", fontsize=8)
    for ax in axes:
        style_axes(ax)
    fig.suptitle(
        f"Spread widens with volatility — seed {row['seed']} "
        f"(corr {corr:.2f})",
        color=INK, fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(out / "spread_vol.png", dpi=130)
    plt.close(fig)


def fig_depth(row: dict, out: Path) -> None:
    """BBO depth over time, bid and ask series."""
    import matplotlib.pyplot as plt

    c = row["result"]["collector"]
    t = c.timestamps()
    stride = max(1, len(t) // 4_000)  # thin long series
    fig, ax = plt.subplots(figsize=(9, 3.2), facecolor=SURFACE)
    for depths, color, label in (
        (c.bid_depths(), C_BLUE, "bid depth"),
        (c.ask_depths(), C_AQUA, "ask depth"),
    ):
        ax.plot(t[::stride], depths[::stride], color=color, linewidth=0.9,
                label=label, alpha=0.9)
    ax.legend(loc="upper right", fontsize=8, frameon=False,
              labelcolor=INK_2)
    ax.set_xlabel("simulation time (minutes)", fontsize=8)
    ax.set_ylabel("resting lots at BBO", fontsize=8)
    ax.set_title(f"Best-quote depth — seed {row['seed']}", fontsize=9,
                 loc="left")
    style_axes(ax)
    fig.tight_layout()
    fig.savefig(out / "depth.png", dpi=130)
    plt.close(fig)


def make_figures(
    rows: list[dict], out: Path, bar_minutes: float, window_bars: int
) -> list[str]:
    """Write the standard five-figure set; headline = first row.

    Returns:
        The written PNG basenames, in report order.
    """
    import matplotlib

    matplotlib.use("Agg")
    headline = rows[0]
    fig_price(rows, out)
    fig_acf(headline, out, bar_minutes)
    fig_distribution(headline, out, bar_minutes)
    fig_spread_vol(headline, out, bar_minutes, window_bars)
    fig_depth(headline, out)
    return ["price.png", "acf.png", "distribution.png", "spread_vol.png",
            "depth.png"]
