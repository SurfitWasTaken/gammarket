# Phase 6 Workplan — Calibration, Analytics, Full Run

> Goal: the **project's definition of done**. Measure the market the simulator
> generates, stabilise it over long horizons, calibrate it, and validate the
> emergent price series against the seven stylised facts in `GOALS.md` on a
> full run — then write the report. Done when `run_phase6.py` completes
> 3 seeds × ≥30k steps and **all 7 stylised facts pass on ≥ 2 of 3 seeds**.

Defer to `CLAUDE.md` for architecture, coding standards, the frozen D1–D5 /
E1–E6 contracts, and scope boundaries. This file is the build order, the open
decisions, and the test plan.

**Prerequisite (met):** Phases 1–5 complete, 255 tests green. The live
dashboard half of Phase 6 (`sim/live/`) already shipped.

---

## What recon found (verified 2026-07-05 — these drive the build order)

1. **Long runs crash.** Default config at 5k steps: the book destabilises
   (best_bid 9999 / best_ask 28929 observed), rolling vol spikes, the equity
   MM's vol-adjusted spread explodes, and `mm_conservative` submits a **bid at
   −1388 ticks** → `ValueError` in `lob._validate_limit`. Stability work must
   precede calibration.
2. **O(n²) hot loop.** `Clock._build_state` calls `tape.prices()` (full tape
   scan) every step to use only the last `vol_window` prices.
3. **No mid/spread/depth time series exists anywhere.** `MarketState` is
   transient, `Fill` carries no mid. Effective spread / depth / quoted-spread
   metrics and clean return series need per-step snapshots.
4. **Metrics gaps**: no effective spread, realized vol, Roll measure,
   |r|/r²-ACF, kurtosis, ARCH test, or price impact in `analytics/metrics.py`.
5. **Self-trade edge** (backlog): `base.on_fills` books a self-trade as net
   ±qty instead of 0. Cheap fix, becomes reachable if calibration thins the book.

## Step 0 — Resolve the open design decisions FIRST (do not skip)

Same discipline as Phases 4/5: resolve each, **record in `CLAUDE.md`** ("Phase 6
Implementation Contracts", F1–F9), then code. Recommended defaults adopted.

- **F1 — Per-step collection**: optional `on_step` callback on the Clock +
  `StepRecord` + in-memory `MarketDataCollector`; `run_sim.run()` always wires
  one and returns it. No file I/O in the loop.
- **F2 — Metric definitions**: bar-resampled mid returns as the canonical
  return series; formulas for realized vol, effective spread, Roll, Kyle λ,
  Ljung-Box (hand-rolled, `scipy.stats.chi2`), excess kurtosis.
- **F3 — Stability guardrails**: universal ≥1-tick price clamp; config-gated
  `vol_ratio_cap` on the MM spread formula; surplus-rest handling if diagnosis
  implicates it. Config-gated or provably not pinned by frozen tests.
- **F4 — O(n²) fix**: slice `tape.fills[-(vol_window+1):]`; numerics unchanged.
- **F5 — Self-trade fix**: skip position update when taker == maker agent.
- **F6 — EWMA vol surface**: `EwmaVolSurface` behind `VolSurface`, updated by
  the dealer per step; `options_mm.surface_mode: flat|ewma`, default flat.
- **F7 — No mid-run re-striking**: widen `strikes_pct` in the Phase 6 run
  config instead; report tracks spot-in-grid fraction.
- **F8 — Sweep harness + `sim/config/phase6.yaml`**: `params.yaml` gets only
  additive keys; the calibrated long-run config lives in `phase6.yaml`.
- **F9 — Validation spec**: numeric pass criteria per stylised fact; 3 seeds ×
  ≥30k steps; project passes at ≥ 2/3 seeds all-green.

## Step 1 — Quick fixes (F4 + F5)
`sim/core/clock.py` vol slice + `sim/agents/base.py` self-trade skip, with
regression tests (`test_clock.py` addition pinning O(window) path numerics;
self-trade test in `test_agents_base` or equivalent).
Commit: `Phase 6: hot-loop vol fix + self-trade accounting`.

## Step 2 — Long-run stability (F3)
Reproduce the −1388 crash deterministically; diagnose root cause (market-order
surplus-rest, vol-ratio spread explosion, unclamped quote arithmetic);
implement the guards; add a **long-run smoke test** (≥10k steps: no exception,
quoted spread ≥ 1 tick at every step). Full suite green.
Commit: `Phase 6: long-run stability — <root cause>`.

## Step 3 — Collection (F1)
`StepRecord` + `Clock(on_step=...)` + `sim/analytics/collector.py` +
`run_sim.run()` returns `"collector"`. Tests: fires once per step, fields match
the book, backward-compatible (no callback → behaviour identical).
Commit: `Phase 6: per-step market data collection`.

## Step 4 — Metrics (F2)
Additive functions in `sim/analytics/metrics.py` with known-value unit tests
(synthetic series with hand-computed answers; a GARCH-like series for the
clustering stats). Existing six functions untouched.
Commit: `Phase 6: microstructure + stylised-fact metrics`.

## Step 5 — Facts evaluator (F9)
`sim/analytics/facts.py`: `evaluate_stylised_facts(...)` → per-fact
`(passed, value)` implementing the frozen criteria. Unit tests + a smoke test
on a short real run.
Commit: `Phase 6: stylised-facts evaluator`.

## Step 6 — Baseline + calibration (F8)
`sim/analytics/sweep.py`; measure the current config; sweep toward the facts;
**fallback if clustering/fat tails resist the parameter grid**: config-gated
retail vol-feedback (`retail.vol_feedback`, default 0.0 = off — arrival
rate/size scale with recent vol), unit-tested, then re-sweep. Freeze the
calibrated parameters in `sim/config/phase6.yaml`.
Commit(s): `Phase 6: sweep harness + baseline`, `Phase 6: calibration (+ vol
feedback if needed)`.

## Step 7 — EWMA surface (F6)
`EwmaVolSurface` + dealer update hook + `surface_mode` switch at both
construction sites (`run_sim._build_dealer`, `sim/live/sim_runner.py`).
Default flat keeps Phase 5 determinism. Tests.
Commit: `Phase 6: EWMA dynamic vol surface (default flat)`.

## Step 8 — Full run + report
`run_phase6.py`: 3 seeds × ≥30k steps on `phase6.yaml`; facts table; figures
(price series, return ACF, |r|-ACF, return histogram vs normal, spread & vol
series, depth) to `results/phase6/` + `results/phase6/report.md`.
Commit: `Phase 6: full validation run + report`.

## Step 9 — Close-out
CLAUDE.md: Phase 6 → `[x]`, stylised-facts boxes checked per the report,
architecture tree + test count updated; sync `ROADMAP.md`, `TODO.md`,
`README.md`. Commit: `Phase 6: close-out — project complete`.

---

## Definition of done (Phase 6 = the project)
- Long runs are stable (≥10k-step smoke test in the suite).
- `sim/analytics/{collector,metrics,facts,sweep}` exist, documented, tested.
- `run_phase6.py` produces `results/phase6/report.md` + figures; **all 7
  stylised facts pass on ≥ 2/3 seeds** at ≥30k steps.
- If a fact cannot be achieved even with the approved fallback, the report and
  `TODO.md` say so explicitly — the bar is not silently lowered.
- F1–F9 recorded in `CLAUDE.md`; no frozen test modified; no new deps beyond
  the approved set (SciPy yes, statsmodels no).

## House rules (unchanged)
One module at a time; tests with the module; full suite before every commit;
`test_e2e_phase2.py` frozen; integer ticks in the LOB; no file I/O in the hot
loop; log debt to a "Phase 6 Audit" backlog rather than fixing inline.
