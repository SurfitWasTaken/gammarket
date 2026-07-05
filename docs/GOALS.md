# Goals & Definition of Done

## North star
Build a **closed-loop, multi-agent artificial equity + options market** in which
realistic price dynamics emerge *endogenously* from agent interactions — and in
which an options dealer's **delta/gamma hedging** feeds back into the underlying
equity limit order book. That feedback loop is the experiment; everything else
is scaffolding for it.

## What "working" means
The simulation is only successful when the emergent price series reproduces the
**stylised facts** of real markets (tracked in `CLAUDE.md`; ✅ validated
2026-07-05 by `run_phase6.py` — verdict + caveats in `results/phase6/report.md`):

- [x] Positive bid-ask spread at all times
- [x] Spread widens with volatility (Roll measure)
- [x] Price impact: large orders move the mid more than small orders
- [x] Autocorrelation of returns near zero (weak-form efficiency)
- [x] Volatility clustering (ARCH effects) — the weakest fact: passes the
      F9 gate (2/3 seeds), seed-sensitive at this run length
- [x] Fat tails in the return distribution (excess kurtosis > 0)
- [x] Options dealer net delta near zero after each hedge cycle

These are the acceptance criteria for the *project*, validated in Phase 6.
Phases 4–5 are judged by their own narrower contracts (below and in the
workplans), but every phase should be checked against "does this move us toward
reproducing the stylised facts?"

## Per-phase definition of done
- **Phase 4 (next):** A `sim/options/` package that prices European options and
  Greeks correctly (validated against known Black-Scholes values and put-call
  parity), exposes a flat implied-vol surface, and builds an options chain
  (strikes × expiries) anchored to the live underlying. No agent uses it yet —
  it is a *library* the Phase 5 dealer will call. Done when its unit tests pass
  and the Greeks are numerically correct.
- **Phase 5:** An options dealer agent that quotes options, takes fills, and
  after every fill recomputes portfolio delta and submits an equity hedge order
  — closing the loop. Done when post-hedge net delta is within threshold of
  zero (the Phase 5 test contract) and the equity book visibly reacts.
- **Phase 6:** Calibration + analytics: effective spread / depth / realized-vol
  metrics, parameter sweeps, and a full run that reproduces the stylised facts
  above. Done when the checklist is green.

## Non-goals (scope boundaries — do not drift)
- **No options-on-options.** Equity + vanilla European options only.
- **No full options LOB in Phase 4.** Phase 4/5 use a *quote-driven* options
  market (dealer quotes on request). A real options LOB is added only if Phase 5
  is stable and the project explicitly decides to.
- **Flat vol surface to start.** Surface dynamics are a Phase 6 concern.
- **No new dependencies** beyond the approved set without flagging first.
- **No Pandas in the simulation hot loop.**
