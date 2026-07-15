# Using gammarket for Research

How to run experiments on the simulator and get paper-ready artifacts out
of it: reproducible runs, machine-readable metrics, tidy CSVs, and the
standard figure set. This is the operating guide for the research layer
shipped in the post-MVP polish pass; the architecture and measurement
contracts it relies on are frozen in `../CLAUDE.md` (F1–F9).

## The one-command experiment

```bash
.venv/bin/python run_experiment.py --out results/experiments/baseline
```

runs the validated config (`sim/config/phase6.yaml`) on the three
pre-registered seeds (42, 7, 123) and writes to the output directory:

| File | Contents |
|---|---|
| `experiment.md` | Human-readable summary: facts table, metrics table, figures |
| `manifest.json` | Full provenance: resolved config, overrides, seeds, measurement spec, git commit, timestamp |
| `metrics.json` | Per-seed stylised-fact verdicts + headline microstructure metrics |
| `steps_s{seed}.csv` | Per-step snapshots: BBO, mid, spread, depth, rolling vol (F1 records) |
| `fills_s{seed}.csv` | The full trade tape: price, qty, aggressor side, both agent ids |
| `bars_s{seed}.csv` | Canonical bar series: LOCF mid bars + log returns (F2) |
| `*.png` | Five standard figures for the headline (first) seed |

### Varying parameters

`--set` takes dotted config paths (list indices and `*` fan-out work, same
syntax as `sim/analytics/sweep.py`); values are parsed as YAML:

```bash
.venv/bin/python run_experiment.py \
    --out results/experiments/no_feedback \
    --set agents.retail.vol_feedback=0.0 \
    --set agents.equity_mms.*.spread_target=4 \
    --seeds 42 7 123 5 99 2024 \
    --label "clustering without the vol-feedback channel"
```

Every run is deterministic given (config, seed): the manifest's
`resolved_config` + `seeds` reproduce the experiment exactly at the
recorded `git_commit`.

### Choosing the measurement spec

`--bar-minutes` (default 0.25) and `--window-bars` (default 60) are the
pinned F9 spec. **The stylised facts are frequency- and horizon-dependent**
(see `results/phase6/report.md` caveats): coarser bars lose clustering
significance on some seeds; longer horizons expose small genuine return
autocorrelation. If you change the spec, report it — comparing verdicts
across specs without saying so is the main way to fool yourself here.

## Programmatic use

For grids/sweeps, stay in memory (no per-run file I/O):

```python
from sim.analytics.sweep import run_sweep, run_once, facts_table
from sim.analytics.export import metrics_summary
from sim.config.loader import load_config

cfg = load_config("sim/config/phase6.yaml")
rows = run_sweep(
    cfg,
    grid=[{"agents.retail.vol_feedback": v} for v in (0.0, 0.35, 0.7)],
    seeds=[42, 7, 123],
    bar_minutes=0.25, window_bars=60,
)
print(facts_table(rows))

row = run_once(cfg, {}, seed=42, bar_minutes=0.25, window_bars=60)
print(metrics_summary(row["result"], bar_minutes=0.25))
```

`run_experiment.run_experiment(...)` is also importable if you want the
full artifact directory from code.

## What the metrics mean

`metrics_summary` returns (per run): annualised realized vol on the bar
series, mean quoted spread and one-sided-snapshot fraction, mean effective
spread (bps), the Roll implied spread, Kyle λ, mean |ACF| of bar returns,
Ljung-Box Q/p on squared returns, excess kurtosis, and the dealer block
(option trades, hedges, worst post-hedge |delta|, gamma rejections, option
cash flow). Definitions are in `sim/analytics/metrics.py` and the F2
contract; the fact thresholds are in `sim/analytics/facts.py` (F9).

## Mechanisms you can switch on

All config-gated, all default-off in `params.yaml`; each was validated in
its own sweep before shipping:

- `agents.retail.vol_feedback` (float, F8) — retail order size scales
  with the rolling vol ratio; makes volatility self-exciting (ARCH-like).
  The flat-surface validated config uses 0.7.
- `agents.retail.regime` (block) — shared calm/excited sentiment regime
  (two-state continuous-time Markov chain); multiplies retail order size
  by `excited_size_mult` during excited episodes. Keys:
  `excited_size_mult`, `enter_rate_per_min`, `exit_rate_per_min`. A
  structural clustering mechanism independent of the vol estimate.
  Known trade-off (from the shipping sweep, recorded in `TODO.md`): it
  makes volatility clustering essentially guaranteed (6/6 seeds), but
  the episodic variance inflates the sample return ACF beyond the iid
  ±3/√n band the efficiency fact uses — a textbook ARCH effect worth a
  paragraph in any paper that uses it.
- `agents.options_mm.surface_mode: ewma` (F6) — the dealer prices off an
  EWMA realized-vol surface instead of the flat one
  (`ewma_lambda`, `sigma_floor`, `sigma_cap`). See
  `sim/config/phase6_ewma.yaml` for the calibrated dynamic-surface config.

## Suggested workflow for a paper

1. Pre-register seeds and the measurement spec before sweeping.
2. Calibrate on the pre-registered seeds via `run_sweep` (in memory).
3. Validate the chosen config with `run_experiment.py` on the
   pre-registered seeds **and** a held-out set; report both.
4. Cite `manifest.json`'s git commit in the methods section; commit the
   experiment directory (or at least `experiment.md` + `metrics.json` +
   `manifest.json`) alongside the draft.
5. Disclose spec-dependence: rerun step 3 at one coarser bar length and
   report how the verdicts move (the caveats section of
   `results/phase6/report.md` is the template).
