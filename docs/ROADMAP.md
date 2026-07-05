# Roadmap

Six incremental phases. Each produces a **working, observable experiment**
before the next adds complexity. Status mirrors the table in `CLAUDE.md` — keep
the two in sync.

| Phase | Name | Status | Output you can observe |
|------:|------|:------:|------------------------|
| 1 | LOB Engine | ✅ done | Matching, price-time priority, partial fills |
| 2 | Equity Agents + Microstructure | ✅ done | Retail + institution moving a seeded book |
| 3 | Equity Market Maker | ✅ done | Two competing MMs, vol-adjusted spread, P&L |
| 4 | Options Pricing + Chain | ✅ done | BS prices + Greeks, flat surface, chain |
| 5 | Options Dealer + Delta Hedging | ✅ done | Dealer quotes → hedges → moves equity book |
| 6 | Calibration, Analytics, Full Run | ✅ done | `run_phase6.py` → 7/7 stylised facts on 2/3 seeds; report in `results/phase6/` |

## Where we are
**The project is complete (2026-07-05).** All six phases are done, **340 tests
pass**, and the definition of done is met: `run_phase6.py` runs the calibrated
config (`sim/config/phase6.yaml`) across 3 pre-registered seeds × 60k steps and
reproduces **all seven stylised facts on 2/3 seeds** (the F9 gate) — verdict,
figures, and honest caveats in `results/phase6/report.md`. Phase 6 followed
`PHASE_6_WORKPLAN.md`: frozen F1–F9 contracts, long-run stability debugging
(the pre-Phase-6 config crashed at ~2–5k steps; three root causes fixed or
calibrated away), per-step market-data collection, the microstructure metrics
+ facts evaluator, sweep-driven calibration (including the config-gated
`retail.vol_feedback` fallback for volatility clustering), the EWMA vol
surface (default flat), and the validation run + report.

## Phase 4 — Options Pricing + Chain  (detailed plan: `PHASE_4_WORKPLAN.md`)
New package `sim/options/`:
- `pricer.py` — Black-Scholes pricing + Greeks (delta, gamma, vega; theta/rho
  optional). Pure functions + a frozen `Greeks` dataclass. SciPy for the
  normal CDF.
- `surface.py` — implied-vol surface; flat (constant σ) to start, behind an
  interface that a dynamic surface can later implement.
- `chain.py` — options chain: strikes × expiries, series management, anchored
  to the live underlying mid.
- Config: add `options` and `agents.options_mm` blocks to `params.yaml`.

**Critical first step:** resolve the unit-conversion decisions (sim-time →
years, integer-tick spot → BS spot, moneyness → integer strikes). These are
load-bearing for every downstream Greek and hedge. They are written up as open
decisions in the workplan — resolve them, record the choice in `CLAUDE.md`, then
code.

## Phase 5 — Options Dealer + Delta Hedging  (detailed plan: `PHASE_5_WORKPLAN.md`)
Shipped `sim/agents/options_mm.py` (dealer: BS quoting, gamma cap, delta
hedging) and `sim/agents/options_flow.py` (Poisson taker driving the
quote-driven options market — no options LOB). After every option fill and at
every dealer step: recompute net delta in lots → `round(-net_delta)` equity
market order → underlying moves → re-hedge. The Clock now owner-routes fills
(`Order.agent_id`) so flow-carried dealer hedges credit the dealer. Test
contract (held, `test_e2e_phase5.py`): **net delta within
`max(delta_hedge_threshold, 0.5)` lots of zero after each hedge cycle** — the
0.5 is the integer-lot quantisation floor (E2).

## Phase 6 — Calibration, Analytics, Full Run  (detailed plan: `PHASE_6_WORKPLAN.md`)
Shipped `sim/analytics/{collector,metrics,facts,sweep}.py`, the F3 stability
guards (`vol_ratio_cap`, `post_only`, price clamps), the `retail.vol_feedback`
extension (default off), `EwmaVolSurface` (default flat), the calibrated
`sim/config/phase6.yaml`, and `run_phase6.py` — which validated the
stylised-facts checklist in `GOALS.md` (7/7 on seeds 42 and 7; 6/7 on 123).
F1–F9 are frozen in CLAUDE.md (Phase 6 Implementation Contracts).

## Sync rule
When a phase completes: flip its status here **and** in the `CLAUDE.md` phase
table and architecture tree (mark new modules `[x]`), and update the test count.
A phase is not "done" until CLAUDE.md, this roadmap, and the tests all agree.
