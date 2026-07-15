"""Generic research experiment runner (polish backlog: the "write a
paper off it" entry point).

Runs any config across any seeds, then writes everything a methods
section needs into one output directory:

    manifest.json     — config snapshot, overrides, seeds, measurement
                        spec, git commit, timestamp (reproducibility)
    metrics.json      — per-seed stylised-fact verdicts + headline
                        microstructure metrics
    steps_s{seed}.csv — per-step market snapshots (F1 records)
    fills_s{seed}.csv — the full fill tape
    bars_s{seed}.csv  — canonical bar series (F2: LOCF mid + log returns)
    *.png             — the standard five-figure set (headline seed)
    experiment.md     — human-readable summary of all of the above

Usage:
    python run_experiment.py --out results/experiments/my_study
    python run_experiment.py --config sim/config/phase6.yaml \\
        --seeds 42 7 123 --set agents.retail.vol_feedback=0.9 \\
        --out results/experiments/feedback_09

`--set` takes dotted config paths (sim/analytics/sweep.py syntax:
integer components index lists, `*` fans out); values are parsed as
YAML, so `--set agents.equity_mms.*.post_only=false` works. All file
I/O happens after the runs complete (F1 contract).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from sim.analytics.export import (
    bars_to_csv,
    facts_to_dict,
    fills_to_csv,
    metrics_summary,
    steps_to_csv,
    write_json,
)
from sim.analytics.facts import FACT_NAMES
from sim.analytics.figures import make_figures
from sim.analytics.sweep import apply_overrides, run_once
from sim.config.loader import load_config

DEFAULT_SEEDS = (42, 7, 123)
DEFAULT_BAR_MINUTES = 0.25  # the pinned F9 measurement spec
DEFAULT_WINDOW_BARS = 60


def _parse_overrides(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set needs key=value, got {pair!r}")
        key, _, raw = pair.partition("=")
        out[key.strip()] = yaml.safe_load(raw)
    return out


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except Exception:
        return None


def _facts_md_table(rows: list[dict]) -> str:
    lines = [
        "| Stylised fact | "
        + " | ".join(f"seed {r['seed']}" for r in rows) + " |",
        "|---|" + "---|" * len(rows),
    ]
    for name in FACT_NAMES:
        cells = [
            "**PASS**" if r["facts"][name].passed else "FAIL" for r in rows
        ]
        lines.append(
            f"| {name.replace('_', ' ')} | " + " | ".join(cells) + " |"
        )
    lines.append(
        "| **total** | "
        + " | ".join(f"**{r['n_passed']}/7**" for r in rows) + " |"
    )
    return "\n".join(lines)


def _metrics_md_table(per_seed: dict[int, dict[str, Any]]) -> str:
    seeds = list(per_seed)
    names = list(next(iter(per_seed.values())))
    lines = [
        "| Metric | " + " | ".join(f"seed {s}" for s in seeds) + " |",
        "|---|" + "---|" * len(seeds),
    ]
    for name in names:
        cells = []
        for s in seeds:
            v = per_seed[s].get(name)
            cells.append(f"{v:.4g}" if isinstance(v, float) else str(v))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run_experiment(
    cfg: dict,
    seeds: list[int],
    out: Path,
    *,
    overrides: dict[str, Any] | None = None,
    bar_minutes: float = DEFAULT_BAR_MINUTES,
    window_bars: int = DEFAULT_WINDOW_BARS,
    label: str = "",
    figures: bool = True,
    csv_export: bool = True,
    config_name: str = "",
) -> dict[str, Any]:
    """Run the experiment and write the output directory.

    Args:
        cfg: Base config dict (copied per run, never mutated).
        seeds: Seeds to run, in order; the first is the figure headline.
        out: Output directory (created if missing).
        overrides: Dotted-path config overrides applied to every run.
        bar_minutes: Bar length for the canonical return series.
        window_bars: Bars per vol window (spread-vol fact + figure).
        label: Free-text experiment description for the manifest.
        figures: Write the five-figure set for the headline seed.
        csv_export: Write per-seed steps/fills/bars CSVs.
        config_name: Config provenance string for the manifest.

    Returns:
        The manifest dict (also written to `manifest.json`).
    """
    overrides = overrides or {}
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    rows: list[dict] = []
    per_seed_metrics: dict[int, dict[str, Any]] = {}
    for seed in seeds:
        print(f"  running seed {seed} "
              f"({cfg['market']['max_steps']} steps)...", flush=True)
        row = run_once(
            cfg, overrides, seed,
            bar_minutes=bar_minutes, window_bars=window_bars,
        )
        rows.append(row)
        per_seed_metrics[seed] = metrics_summary(
            row["result"], bar_minutes=bar_minutes
        )
        if csv_export:
            result = row["result"]
            steps_to_csv(result["collector"], out / f"steps_s{seed}.csv")
            fills_to_csv(result["tape"], out / f"fills_s{seed}.csv")
            bars_to_csv(
                result["collector"], bar_minutes, out / f"bars_s{seed}.csv"
            )
    wall = time.perf_counter() - t0

    fig_names: list[str] = []
    if figures:
        fig_names = make_figures(rows, out, bar_minutes, window_bars)

    manifest = {
        "label": label,
        "config": config_name,
        "overrides": overrides,
        "seeds": seeds,
        "max_steps": cfg["market"]["max_steps"],
        "bar_minutes": bar_minutes,
        "window_bars": window_bars,
        "git_commit": _git_commit(),
        "generated": _dt.datetime.now().isoformat(timespec="seconds"),
        "wall_seconds": round(wall, 1),
        "resolved_config": apply_overrides(cfg, overrides),
    }
    write_json(manifest, out / "manifest.json")
    write_json(
        {
            str(seed): {
                "facts": facts_to_dict(row["facts"]),
                "n_passed": row["n_passed"],
                "metrics": per_seed_metrics[seed],
            }
            for seed, row in zip(seeds, rows)
        },
        out / "metrics.json",
    )

    n_green = sum(r["all_passed"] for r in rows)
    md = f"""# Experiment: {label or out.name}

*Generated {_dt.date.today().isoformat()} by `run_experiment.py` —
config `{config_name}`, seeds {', '.join(map(str, seeds))},
{cfg['market']['max_steps']:,} steps each, {bar_minutes}-minute bars,
{wall:.0f}s wall time.*

Overrides: `{overrides or 'none'}`

## Stylised facts — {n_green}/{len(rows)} seeds all-green

{_facts_md_table(rows)}

## Microstructure metrics

{_metrics_md_table(per_seed_metrics)}
"""
    if fig_names:
        md += "\n## Figures (headline seed " + str(seeds[0]) + ")\n\n"
        md += "\n\n".join(f"![{n}]({n})" for n in fig_names) + "\n"
    if csv_export:
        md += (
            "\n## Data files\n\n"
            "Per seed: `steps_s{seed}.csv` (per-step snapshots), "
            "`fills_s{seed}.csv` (trade tape), `bars_s{seed}.csv` "
            "(canonical bar series). Machine-readable results in "
            "`metrics.json`; provenance in `manifest.json`.\n"
        )
    (out / "experiment.md").write_text(md)

    for r in rows:
        status = "ALL GREEN" if r["all_passed"] else "incomplete"
        print(f"  seed {r['seed']:>5}: {r['n_passed']}/7 ({status})")
    print(f"wrote {out}/ (experiment.md, metrics.json, manifest.json"
          + (", CSVs" if csv_export else "")
          + (", figures" if fig_names else "") + ")")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a gammarket research experiment"
    )
    parser.add_argument("--config", type=Path,
                        default=Path("sim/config/phase6.yaml"))
    parser.add_argument("--out", type=Path, required=True,
                        help="output directory for the experiment")
    parser.add_argument("--seeds", type=int, nargs="+",
                        default=list(DEFAULT_SEEDS))
    parser.add_argument("--set", dest="overrides", action="append",
                        default=[], metavar="KEY=VALUE",
                        help="dotted-path config override (repeatable)")
    parser.add_argument("--bar-minutes", type=float,
                        default=DEFAULT_BAR_MINUTES)
    parser.add_argument("--window-bars", type=int,
                        default=DEFAULT_WINDOW_BARS)
    parser.add_argument("--label", default="", help="experiment description")
    parser.add_argument("--no-figures", action="store_true")
    parser.add_argument("--no-csv", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    print(f"Experiment: {args.config} -> {args.out}")
    run_experiment(
        cfg,
        list(args.seeds),
        args.out,
        overrides=_parse_overrides(args.overrides),
        bar_minutes=args.bar_minutes,
        window_bars=args.window_bars,
        label=args.label,
        figures=not args.no_figures,
        csv_export=not args.no_csv,
        config_name=str(args.config),
    )


if __name__ == "__main__":
    main()
