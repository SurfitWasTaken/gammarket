# gammarket — Operating Docs (Fable handoff)

Welcome, Fable. This folder is your operating layer: **what to build, in what
order, and how you'll know it's done.** It sits on top of — and defers to —
`CLAUDE.md`, which remains the single source of truth for architecture,
coding standards, and the per-phase implementation contracts.

## Read in this order
1. **`../CLAUDE.md`** — project identity, architecture tree, coding standards,
   domain concepts (LOB, BS Greeks, delta-hedging loop), and the frozen
   implementation contracts. **Authoritative.** If anything here disagrees with
   it, CLAUDE.md wins (or fix one to match the other in the same commit).
2. **`GOALS.md`** — the north star and the definition of done (stylised facts).
3. **`ROADMAP.md`** — the six-phase plan and where we are.
4. **`PHASE_6_WORKPLAN.md`**, **`PHASE_5_WORKPLAN.md`**, **`PHASE_4_WORKPLAN.md`**
   — the completed phase plans, kept for reference. Their decisions (F1–F9,
   E1–E6, D1–D5) are frozen in `CLAUDE.md` as per-phase Implementation
   Contracts. The Phase 6 validation verdict lives in `../results/phase6/report.md`.
5. **`TODO.md`** — the living checklist. Keep it current as you work.

## Current state (PROJECT COMPLETE — Phase 6 closed 2026-07-05)
- **All six phases complete.** LOB engine, equity agents, discrete-event
  clock, central tape, the `sim/options/` library, the options dealer +
  delta-hedging loop, and the Phase 6 analytics/calibration/validation layer.
- **All 340 tests pass:** `.venv/bin/python -m pytest tests/ -q`
- **The definition of done is met:** `.venv/bin/python run_phase6.py` runs the
  calibrated config across 3 pre-registered seeds × 60k steps and reproduces
  **all seven stylised facts on 2/3 seeds** (F9 gate). Report + figures:
  `results/phase6/report.md` — including honest caveats (bar-length/horizon
  dependence, held-out seeds, EWMA-mode outcome).
- **Phase 6 shipped** `sim/analytics/{collector,metrics,facts,sweep}.py`, the
  F3 long-run stability fixes (the old config crashed at ~2–5k steps),
  `retail.vol_feedback` + `EwmaVolSurface` (both config-gated, default off),
  `sim/config/phase6.yaml`, and `run_phase6.py`. F1–F9 are frozen in
  `CLAUDE.md` (Phase 6 Implementation Contracts).
- **Remaining backlog** (non-blocking) lives at the bottom of `TODO.md`.

## How to work here (house rules, from CLAUDE.md)
- **One module at a time.** Complete and test a file before starting the next.
- **Tests are the checkpoint system.** After each module:
  `.venv/bin/python -m pytest tests/test_<module>.py -v`
- **Commit checkpoints** after each passing module:
  `git add -A && git commit -m "Phase 4: <module> complete"`
- **No refactoring working modules during a feature session.** If you find
  tech debt, log it (a new "Phase 4 Audit" backlog section, same format as the
  Phase 3 one) and clear it in a dedicated cleanup commit — don't fix inline.
- **`test_e2e_phase2.py` is frozen.** Do not modify it. Add new e2e tests per
  phase (`test_e2e_phase4.py`, etc.).
- **No new libraries without flagging.** Allowed: NumPy, SciPy (norm CDF),
  PyYAML, matplotlib, pytest, sortedcontainers. No Pandas in the hot loop.
- **Integer ticks for all LOB prices.** Options pricing works in float; the
  tick/year/strike conversions are an explicit Phase 4 decision (see workplan).

## Quick commands
```bash
.venv/bin/python -m pytest tests/ -q        # full suite
.venv/bin/python run_sim.py --no-plot       # run the full sim (Phase 5 loop), print summary
.venv/bin/python run_sim.py                 # + writes results/phase3.png
.venv/bin/python run_phase6.py              # F9 validation: 3 seeds × 60k steps → report
```
