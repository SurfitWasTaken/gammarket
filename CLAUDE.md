# Market Simulator — Claude Project Context

## Project Identity
This is a **closed-loop, multi-agent artificial equity + options market simulator**.
The goal is to generate realistic price dynamics endogenously through agent interactions,
culminating in a full delta/gamma hedging feedback loop between an options dealer and
the underlying equity limit order book.

## Operating Docs — start here
This file is the **authoritative** source for architecture, coding standards, and
per-phase contracts. The **operating layer** (goals, roadmap, current workplan,
TODO) lives in **`docs/`** — start at **`docs/README.md`**:
- `docs/GOALS.md` — north star + definition of done (stylised facts)
- `docs/RESEARCH.md` — how to run experiments / write papers off the simulator
- `docs/ROADMAP.md` — the six-phase plan and current position
- `docs/PHASE_6_WORKPLAN.md` — completed: Calibration, Analytics, Full Run
- `docs/PHASE_5_WORKPLAN.md` — completed: Options Dealer + Delta Hedging
- `docs/PHASE_4_WORKPLAN.md` — completed: Options Pricing + Chain (reference)
- `docs/TODO.md` — the living checklist
- `results/phase6/report.md` — the full-run validation report (F9 verdict)
If `docs/` and this file ever disagree, **CLAUDE.md wins** — fix one to match the
other in the same commit.

## Architecture Overview
Legend: `[x]` exists on disk today (end of Phase 6); `[ ]` planned for a later
phase and not yet created. Keep this tree in sync with reality — do not list a
module here until it is committed.

```
sim/
├── core/
│   ├── lob.py            [x] Limit order book engine (price-time priority).
│   │                         Matching lives INSIDE lob.py (_sweep); there is no
│   │                         separate matching.py.
│   ├── clock.py          [x] Discrete event scheduler + MarketState builder.
│   │                         Fills credit the *owning* agent (Order.agent_id),
│   │                         not the stepping agent (Phase 5 E1 owner-routing).
│   │                         Optional on_step callback + StepRecord (Phase 6 F1).
│   ├── tape.py           [x] Central fill tape (callback-injected into LOB)
│   └── events.py         [x] Event types: Order, Fill, Cancel, Side
├── agents/
│   ├── base.py           [x] Agent base class + MarketState dataclass
│   ├── retail.py         [x] Noise traders (Poisson arrivals, market orders;
│   │                         config-gated vol_feedback, Phase 6 F8, default off;
│   │                         config-gated RetailRegime calm/excited sentiment
│   │                         chain, polish pass 2026-07-15, default off)
│   ├── institution.py    [x] Mean-reverting (OU-signal) limit speculator
│   ├── equity_mm.py      [x] Equity market maker (inventory + vol-aware quoting)
│   ├── options_mm.py     [x] Options dealer: BS quoting + delta hedging — Phase 5
│   └── options_flow.py   [x] Options-demand flow (Poisson taker, E1) — Phase 5
├── live/                 [x] Phase 6 live dashboard: multi-terminal + 3D surface
│   ├── state_writer.py   [x] Agent state extraction + atomic JSON write
│   ├── agent_viewer.py   [x] Rich per-agent terminal dashboard (8 viewers)
│   ├── surface_viz.py    [x] Matplotlib 3D options price surface (strike × expiry)
│   ├── sim_runner.py     [x] Step loop with live state broadcasting to file
│   └── launch.py         [x] macOS Terminal.app spawner via osascript
├── options/              [x] options pricing library — Phase 4
│   ├── pricer.py         [x] Black-Scholes pricing, Greeks (delta, gamma, vega)
│   ├── surface.py        [x] Vol surfaces (FlatVolSurface; EwmaVolSurface, Phase 6
│   │                         F6, default off; surface_from_config switch)
│   └── chain.py          [x] Options chain (strikes×expiries) + D1/D2 conversion sites
├── analytics/
│   ├── metrics.py        [x] returns, ACF, trade sizes + Phase 6 F2: bar
│   │                         resampling, realized vol, effective spread, Roll,
│   │                         Kyle λ, price impact, Ljung-Box, kurtosis
│   ├── collector.py      [x] MarketDataCollector: in-memory per-step snapshots
│   │                         via Clock.on_step (Phase 6 F1)
│   ├── facts.py          [x] Stylised-facts evaluator — the F9 pass criteria
│   ├── sweep.py          [x] Calibration sweep harness (dotted-path overrides)
│   ├── export.py         [x] Post-run CSV/JSON export + paper-ready
│   │                         metrics_summary (polish pass; no hot-loop I/O)
│   └── figures.py        [x] Standard five-figure set, parameterized
│                             (extracted from run_phase6; shared with
│                             run_experiment)
├── config/
│   ├── params.yaml       [x] All tunable parameters (general default)
│   ├── phase6.yaml       [x] Calibrated full-run config (Phase 6 F8, flat surface)
│   ├── phase6_ewma.yaml  [x] Calibrated dynamic-surface config (polish pass:
│   │                         ewma_lambda 0.99 → 7/7 on all 3 pre-registered
│   │                         seeds; held-out results in the file header)
│   └── loader.py         [x] Single YAML read site (load_config)
├── viz.py                [x] Live matplotlib LOB viz (dev tool, subprocess)
├── snapshot.py           [x] Pure LOB-state serialization for viz IPC (dev tool)
├── repl.py               [x] Interactive LOB REPL (dev tool)
├── agents_repl.py        [x] Agent-driven live REPL with viz (dev tool)
tests/                    [x] 364 passing tests (LOB, agents, clock, options,
                              analytics, stability, e2e, tooling, regime,
                              export, live state)
run_sim.py                [x] Phase 3/5 entry point (options dealer + flow switch
                              on when `agents.options_flow` is present in config)
run_phase6.py             [x] Phase 6 validation runner: 3 seeds × 60k steps →
                              stylised-facts verdict + results/phase6/report.md
run_experiment.py         [x] Generic research runner: any config × seeds →
                              manifest/metrics JSON + CSVs + figures +
                              experiment.md (see docs/RESEARCH.md)
results/phase6/           [x] Full-run validation report + figures (committed)
research/                 [x] Publishable material (2026-07-15): paper.md
                              (working paper w/ citations), experiments/
                              (4 reproducible run_experiment outputs),
                              figures/ + make_figures.py (mechanism-
                              comparison harness), configs/ (paper-only
                              regime config). See research/README.md.
CLAUDE.md                 [x] This file
```

Note: `viz.py`, `snapshot.py`, `repl.py`, `agents_repl.py` are developer tooling
that live at the `sim/` top level, NOT under `analytics/`. They are outside the
phase contracts and are not load-bearing for the simulation itself. However,
**`agents_repl.py` must be updated in the same commit that adds new agent types
to a phase** — it is the primary visual feedback loop and keeping it stale makes
the simulation opaque.

## Build Phases
The project is structured as six incremental phases. Each phase produces a working,
observable experiment before the next adds complexity.

| Phase | Name | Status |
|-------|------|--------|
| 1 | LOB Engine | [x] |
| 2 | Equity Agents + Basic Microstructure | [x] |
| 3 | Equity Market Maker | [x] |
| 4 | Options Pricing + Chain | [x] |
| 5 | Options Dealer + Delta Hedging | [x] |
| 6 | Calibration, Analytics, Full Run | [x] |

**Current position (2026-07-15): ALL SIX PHASES COMPLETE + post-MVP polish
pass done.** 364 tests pass
(`.venv/bin/python -m pytest tests/ -q`) and the F9 validation gate is met:
`run_phase6.py` on `sim/config/phase6.yaml` (3 pre-registered seeds × 60k
steps) reproduces **all seven stylised facts on 2/3 seeds** (42 and 7 = 7/7,
123 = 6/7 — clustering). The report + figures live in `results/phase6/`.

**Post-MVP polish pass (2026-07-15)** cleared the `docs/TODO.md` backlog:
(1) the live dashboards surface the Phase 6 state (retail vol-feedback
size, regime state, dealer surface mode/σ); (2) `RetailRegime` — a shared
calm/excited sentiment chain multiplying retail order size, the structural
vol-clustering mechanism — ships config-gated and **default off**: sweeps
show it makes clustering pass 6/6 seeds but the episodic variance inflates
the return-ACF estimator past the iid ±3/√n band (findings + next levers in
`docs/TODO.md`); (3) `sim/config/phase6_ewma.yaml` calibrates the dynamic
surface (`ewma_lambda 0.99` → 7/7 on all 3 pre-registered seeds; held-out
5/6/6, disclosed); (4) the P2-2 shim and time-convention backlog items are
closed as standing decisions; (5) the research layer — `run_experiment.py`,
`sim/analytics/{export,figures}.py`, `docs/RESEARCH.md` — turns any run
into paper-ready artifacts (manifest/metrics JSON, CSVs, figures).

Phase 6 shipped in nine committed steps (see `docs/PHASE_6_WORKPLAN.md`):
the F1–F9 contracts; the O(n²) hot-loop vol fix and the self-trade wash (F4,
F5); the long-run stability work (F3 — before it, the default config
**crashed** at ~2–5k steps via the bounce-vol spread feedback, and MM-vs-MM
hot-potato churn plus the inventory-skew integrator trended the mid without
bound; fixed by `vol_ratio_cap`, `post_only` quoting, price clamps, and a
near-zero calibrated `risk_aversion`); per-step market-data collection (F1);
the microstructure metrics and stylised-facts evaluator (F2, F9); the sweep
harness and calibration (F8 — including the approved `retail.vol_feedback`
fallback, default off, which makes volatility self-exciting); the EWMA vol
surface (F6, default flat); and the validation run + report. Measurement
caveats (bar-length/horizon dependence, held-out-seed results, EWMA-mode
outcome) are disclosed in the report and in the F9 amendment above the
Known Design Decisions section.

## Phase 3 Audit (2026-06-09) — Resolved 2026-06-10
A full line-by-line review of every committed `.py` (excluding dev tooling) was
run at the close of Phase 3. Every item below has since been cleared; this
section is kept as a record of what changed and why, not an open backlog.

### Scorecard (out of 10) — post-cleanup
| Dimension | Score | One-line justification |
|-----------|:----:|------------------------|
| **Logical consistency** | **9.0** | equity_mm now matches its spec (rolling-median vol baseline, party-to-fill P&L); dead branches and the dead scheduling API are gone. |
| **Elegant / realistic solutions** | **8.5** | LOB (SortedDict+deque+callback tape), frozen events, and the OU institution remain elegant; the vol no-op round-trip and the `price==0` sentinel have been removed. |
| **Per module** | | |
| `core/lob.py` | 9.0 | Clean price-time priority, in-place partial fills, immutable `Order` + `_with_qty`. Market-order surplus-rest behavior is unusual but documented. |
| `core/events.py` | 9.0 | Frozen dataclasses, good docstrings. Market orders now carry an explicit `is_market` flag (documented), not a `price==0` convention. |
| `core/tape.py` | 9.0 | Tiny, single-purpose, exactly as specced. |
| `core/clock.py` | 9.0 | heapq scheduler + correct fill routing; vol is now computed directly in bps and routing keys on `is_market`. |
| `agents/base.py` | 8.5 | Centralised position tracking; state initialised only in `__init__` (no `dataclasses.field()` on a non-dataclass). |
| `agents/retail.py` | 8.5 | Clean Poisson/geometric noise trader; emits explicit market orders. |
| `agents/institution.py` | 8.5 | Exact OU discretisation, signal-anchored pricing, partial-fill preservation — the most realistic agent. |
| `agents/equity_mm.py` | 8.5 | Spec-aligned vol baseline, correct taker/maker P&L, no-reference-price guard, no dead scheduling API. |
| `analytics/metrics.py` | 9.0 | Pure NumPy, correct, well-edge-cased. |
| `config/loader.py` | 9.0 | Single read site, good errors. |
| `run_sim.py` | 8.0 | Clear wiring; retains the `equity_mm`/`equity_mms` dual-path (see P2-2 below). |

### Resolution log
**P0-1 — vol baseline now matches spec** (`equity_mm.py`). The MM keeps a
`_vol_history` of observed `rolling_vol_bps`; the baseline is the config seed
during warm-up and the **median of all observed readings** once `vol_window` of
them have accumulated. The 2-point `median([baseline, rolling])` blend, the
unreachable `else` branch, and `_vol_initialized` are gone. Code and the
"Vol-Adjusted Spread" contract below now agree.

**P0-2 — MM P&L counts every fill it is party to** (`equity_mm.on_fills`). Cash
flow updates for taker *and* maker fills, signed by whether the MM bought or
sold, so an inventory-skewed marketable quote no longer corrupts `total_pnl`.

**P1-1 — clock vol no-op collapsed** (`clock.py`). Returns are already fractional,
so `rolling_vol_bps = std(returns) * 1e4` directly; the `*mid … /mid` round-trip
is deleted (identical numeric output).

**P1-2 — base.py** initialises `position`/`open_order_ids` only in `__init__`;
the `dataclasses.field()` import/usage on the non-dataclass `Agent` is removed.

**P1-3 — explicit order type.** `Order` carries `is_market: bool = False`
(documented in `events.py`); the Clock routes on `action.is_market`, and Retail
sets it. The `price==0` market-order sentinel no longer drives control flow.

**P1-4 — dead scheduling API removed** (`equity_mm.py`). `schedule_next`,
`next_event_time`, and `_next_event_time` are deleted (the Clock owns
scheduling); the two unit tests that pinned them were removed.

**P1-5 — no-reference-price guard** (`equity_mm.step`). When both `mid` and
`last_fill_price` are None the MM returns `[]` instead of quoting around a 0.0
mid (which produced a negative bid and a LOB `ValueError`).

**P2-1 — Parameters Reference** block below updated to integer ticks.

**P2-2 — `equity_mm`/`equity_mms` shim intentionally retained.** The frozen
`test_e2e_phase2.py` passes config under the singular `equity_mm` key, so
`run_sim.py` still accepts it. The shim cannot be dropped without modifying a
frozen test; revisit only if that test is ever unfrozen.

**P2-3 — duplicate bookkeeping removed.** `equity_mm` no longer re-adds quote
`order_id`s to `open_order_ids`; the Clock is the sole owner.

## Coding Standards

### Language & Libraries
- **Python 3.11+** throughout
- **Core sim**: pure Python + NumPy (no Pandas in the hot path)
- **Options pricing**: SciPy for norm CDF in Black-Scholes
- **Event scheduling**: custom priority queue (heapq) — no SimPy dependency
- **Visualisation**: Matplotlib (post-run, 3D surface), Rich for terminal dashboards
- **Testing**: pytest
- **Config**: PyYAML for params.yaml

### Performance Rules
- The LOB matching engine must run in O(log n) per order — use a `SortedDict` (sortedcontainers)
- Agent `act()` methods must never block — all actions return an event list
- No database calls, no file I/O in the simulation hot loop
- Profile before optimising — use `cProfile` on Phase 2 before considering Numba/Cython

### Code Style
- Type hints on all public functions and class methods
- Dataclasses for all data structures (Order, Fill, Quote, Greeks)
- No global mutable state — pass the simulation context explicitly
- All monetary values in integer ticks (avoid float drift in the LOB)
- Docstrings on every class and public method (one-line summary + params)

## Key Domain Concepts

### Limit Order Book (LOB)
- Price-time priority: best price wins; ties broken by arrival order
- Bid side: sorted descending (highest bid = best)
- Ask side: sorted ascending (lowest ask = best)
- A market order sweeps the book until filled or liquidity exhausted
- Partial fills are normal — track remaining quantity on resting orders
- Spread = best_ask - best_bid (must always be ≥ 1 tick)

### Agent Loop
Every simulation step, each agent runs:
1. `perceive(market_state)` → update internal belief
2. `decide()` → choose action (submit/cancel/do nothing)
3. `act()` → return list of Order events to the exchange

Agents are asynchronous — they act on their own schedules (Poisson or fixed interval).

### Black-Scholes Greeks (for options_mm.py)
```
Delta = N(d1)                          # sensitivity to underlying price
Gamma = N'(d1) / (S * σ * √T)         # delta's sensitivity to price
Vega  = S * N'(d1) * √T               # sensitivity to volatility
d1    = [ln(S/K) + (r + σ²/2)*T] / (σ*√T)
d2    = d1 - σ*√T
```

### Delta Hedging Loop (Phase 5 critical path)
After every options fill:
1. Recalculate portfolio delta across all open option positions
2. Compute hedge quantity = -net_delta * lot_size
3. Submit market order to equity LOB to flatten delta
4. This equity trade moves the underlying price
5. Which changes the theoretical option values
6. Which may trigger re-quoting on the options market
→ This feedback loop is the core experiment

## Parameters Reference (params.yaml keys)
> `sim/config/params.yaml` is the single source of truth. All keys below are
> live (integer ticks, `equity_mms` list; the `options` block landed with
> Phase 4, `options_mm`/`options_flow` consumption with Phase 5).
```yaml
# --- live today (Phase 1–5) ---
market:
  tick_size: 1            # integer ticks; no decimal prices in the LOB
  lot_size: 100
  initial_price: 10000    # ticks
  initial_bid_size: 200
  initial_ask_size: 200
  max_steps: 200          # event count, not trading days
  seed: 42
  vol_window: 20          # fills used for rolling-vol (clock + MM baseline)
  minutes_per_year: 525600  # D1: continuous calendar (365×24×60)

agents:
  retail:
    n_agents: 10
    arrival_rate: 10.0     # orders per minute (Poisson lambda)
    order_size_mean: 2     # lots
    direction_bias: 0.0    # 0 = perfectly random
    vol_feedback: 0.0      # F8: size scales with vol ratio (0 = off)
    baseline_vol_bps: 5.0  # vol_ratio denominator
    vol_ratio_cap: 10.0    # cap on vol_ratio (F3 mirror)
    # regime:              # calm/excited sentiment chain (absent = off;
    #   excited_size_mult: 4.0    #   polish pass — see RetailRegime)
    #   enter_rate_per_min: 0.02
    #   exit_rate_per_min: 0.2
  institution:
    arrival_rate: 5.0
    signal_halflife: 30.0  # minutes
    signal_sigma: 1.0
    threshold: 0.0
    position_limit: 500    # lots
    quote_offset_ticks: 1
    scale: 100
    signal_price_scale: 5
  equity_mms:              # list form; each entry has an explicit id
    - id: "mm_aggressive"
      arrival_rate: 100.0
      spread_target: 3     # ticks
      inventory_limit: 2000
      risk_aversion: 0.05
      quote_size: 5
      max_orders_per_side: 1
      vol_window: 20
      vol_multiplier: 2.0
      baseline_vol_bps: 5.0
    - id: "mm_conservative"
      arrival_rate: 100.0
      spread_target: 5
      inventory_limit: 2000
      risk_aversion: 0.1
      quote_size: 5
      max_orders_per_side: 1
      vol_window: 20
      vol_multiplier: 2.0
      baseline_vol_bps: 5.0

  options_mm:              # Phase 5 options dealer
    arrival_rate: 20.0     # re-hedge checks per minute (E3)
    vol_estimate: 0.20     # annualised σ for BS pricing / FlatVolSurface
    spread_vols: 2.0       # quotes at σ ± spread_vols vol points; 1 pt = 0.01 σ (E4)
    delta_hedge_threshold: 0.05  # lots (E3; 0.5-lot quantisation floor applies, E2)
    gamma_limit: 500       # portfolio gamma cap, lots/tick (E5)
    option_tick: 1         # option quote grid (E4)
    surface_mode: flat     # F6: flat | ewma (+ ewma_lambda/sigma_floor/sigma_cap)
  options_flow:            # Phase 5 quote-driven options demand (E1)
    arrival_rate: 5.0      # option trades per minute
    max_lots: 3            # contracts per trade ~ U[1, max_lots]

options:
  strikes_pct: [-0.05, -0.025, 0.0, 0.025, 0.05]  # moneyness offsets (D3)
  expiries_days: [7, 14, 30]
  risk_free_rate: 0.05
```

## Testing Philosophy
- Phase 1: LOB must pass an exact matching test suite before Phase 2 begins
  - Test: market order fully fills against resting limit orders
  - Test: partial fill leaves correct residual in book
  - Test: price-time priority is respected with two orders at same price
  - Test: cancellation removes order from book, does not affect price
- Phase 3: equity MM must produce a non-zero spread within 100 steps
- Phase 5: delta after hedge must be within `max(delta_hedge_threshold, 0.5)`
  lots of zero (the E2 quantisation floor — integer-lot hedging cannot do better)

Run all tests after every session: `pytest tests/ -v`

## Session Workflow (how to work with Claude)
1. **State the phase** at the start of every session: "We are working on Phase N"
2. **Paste the failing test or specific error** — don't describe it, paste it
3. **One module at a time** — complete and test one file before moving to the next
4. **After each module**, run: `python -m pytest tests/test_<module>.py -v`
5. **Commit checkpoints** — after each passing module: `git add -A && git commit -m "Phase N: <module> complete"`
6. **Keep agents_repl.py in sync** — when a phase adds a new agent type, update
   `sim/agents_repl.py` in the same commit so the synthetic market is always
   inspectable. A phase that cannot be visually observed in the REPL is not done.

## Phase 3 Implementation Contracts

### Vol-Adjusted Spread — units and baseline
- `MarketState` carries `rolling_vol_bps: float | None` (volatility in basis-points, computed from fractional fill-to-fill returns, not raw returns)
- `rolling_vol_bps` is computed in `Clock._build_state()` (`std(returns) * 1e4`), never inside the MM agent
- `Clock.__init__` receives `tape: Tape` explicitly — no global tape access
- `baseline_vol_bps` is a config seed value used during warm-up; once `vol_window` readings have accumulated the baseline switches to the **median of the rolling-vol series so far**
  - ✅ **Resolved (Audit P0-1):** the MM keeps `_vol_history` and takes its median once `len(_vol_history) >= vol_window`; before that it uses the config `baseline_vol_bps`. Code and spec now agree.
- Effective spread formula — **canonical form (matches code + the vol-spread test below):**
  `effective_spread = max(1, int(round(spread_target * (1 + vol_multiplier * (vol_ratio - 1)))))`
  where `vol_ratio = rolling_vol_bps / baseline_vol_bps`.
  - The older `max(min_spread, round(spread_target * vol_ratio))` form (no `vol_multiplier`) is **superseded** — it contradicted the vol-spread test contract and is no longer used. Do not reintroduce it.

### MM Competition — config and IDs
- `params.yaml` key is `equity_mms` (list); each entry has an explicit `id` string field
- MMs must have different `spread_target` values (not just different `risk_aversion`) so undercutting is observable in logs
- Loop: `for mm_cfg in cfg["agents"]["equity_mms"]: agents.append(EquityMarketMaker(mm_cfg["id"], mm_cfg, rng))`
- Phase 2 e2e test (`test_e2e_phase2.py`) is **frozen** — do not modify it. Add `test_e2e_phase3.py` for Phase 3 assertions

### MM P&L — correct decomposition
Track two quantities on `EquityMarketMaker`, updated on every fill:
```
cash_flow += fill.price * fill.qty   # positive for sells, negative for buys
```
Report in summary:
```
inventory_value = position * current_mid
total_pnl = cash_flow + inventory_value
```
Do NOT compute P&L by summing fill prices without sign — that produces cash flow only, not true P&L.
- ✅ **Resolved (Audit P0-2):** `EquityMarketMaker.on_fills` updates `cash_flow` for **every fill the MM is party to** — taker or maker — signed by whether the MM bought (`-price*qty`) or sold (`+price*qty`). A marketable, inventory-skewed quote (MM as taker) is now counted, so `total_pnl` stays correct.

### Vol spread test — config-aware assertion
```python
vol_multiplier = mm_cfg["vol_multiplier"]
expected_min_ratio = 1 + (vol_multiplier - 1) * 0.5
assert new_spread / old_spread >= expected_min_ratio
```
Test must read `vol_multiplier` from the config dict, not hardcode 20%.

## Phase 2 Implementation Contracts

### BBO Bootstrap (run_sim.py, not agents)
`run_sim` places two phantom seed orders before the first agent step:
- Seed BID: `initial_price - 1 tick`, qty = 1 lot, order_id = `"SEED_BID"`
- Seed ASK: `initial_price + 1 tick`, qty = 1 lot, order_id = `"SEED_ASK"`

These are regular resting limit orders, not special-cased objects. The equity MM in Phase 3
will naturally outcompete them and they will age out. Do NOT place seed orders inside any
agent's `__init__` or `act()` — bootstrap logic belongs exclusively in the runner.

### params.yaml is required from Phase 2 onwards
Create `config/params.yaml` at the start of Phase 2. All agents and the clock take a
`config: dict` parameter in their constructors — no magic globals. Load once in `run_sim.py`:
```python
import yaml
with open("config/params.yaml") as f:
    cfg = yaml.safe_load(f)
```
Then pass `cfg["agents"]["retail"]` etc. to each agent constructor. This is the only place
params.yaml is read. Tests pass their own config dicts directly — no file I/O in tests.

### Fill Logging — Central Tape with Callback Hook
`core/tape.py` owns the chronological fill list:
```python
@dataclass
class Tape:
    fills: list[Fill] = field(default_factory=list)
    def append(self, fill: Fill) -> None:
        self.fills.append(fill)
```
`LimitOrderBook.__init__` accepts `on_fill: Callable[[Fill], None] | None = None`.
When a fill is generated during matching, call `if self.on_fill: self.on_fill(fill)`.
The runner wires this at startup: `book = LimitOrderBook(on_fill=tape.append)`.
Existing Phase 1 tests construct `LimitOrderBook()` with no callback — behaviour unchanged.
Analytics layers in Phase 6 may inject additional callbacks without touching LOB or agents.

## Phase 4 Implementation Contracts

> Resolved at the start of Phase 4 (2026-06-11) from `docs/PHASE_4_WORKPLAN.md`
> Step 0. These five unit conversions are **frozen** — every Greek and every
> Phase 5 hedge depends on them. The `sim/options/` package implements them; the
> Phase 5 dealer consumes them. All recommended defaults were adopted.

### D1 — Simulation time → years (`T` in Black-Scholes)
- `MarketState.timestamp` / `Clock.now` is in **clock minutes**. BS needs `T` in
  **years**. Convention: **continuous calendar**, `market.minutes_per_year =
  525_600` (365 × 24 × 60). Matches the sim's continuous-time model (Poisson
  arrivals, OU in minutes); a trading-calendar convention (98_280) is deferred
  to Phase 6 calibration.
- Formula: `T_years = max(expiry_minutes − now_minutes, 0) / minutes_per_year`.
- **Single conversion site:** `chain.time_to_expiry_years(series, now_minutes,
  minutes_per_year)`. Never inline this division anywhere else.

### D2 — Integer-tick underlying → BS spot `S`
- BS is scale-free in S/K, so the tick price is used **directly** as `S` (and
  strikes as `K`): 1 tick = 1 price unit. At `tick_size = 1` this is exact.
- **Single conversion site:** `chain.spot_from_book(mid, tick_size) -> float`
  (returns `mid * tick_size`). If `tick_size` ever ≠ 1 only this function
  changes; callers never multiply by `tick_size` themselves.

### D3 — Moneyness → integer strikes
- Strikes are generated from **moneyness offsets** around an anchor spot, not
  hardcoded. Config: `options.strikes_pct: [-0.05, -0.025, 0.0, 0.025, 0.05]`.
- Rule: `K = round(anchor_spot * (1 + pct))` **snapped to the nearest tick
  multiple** (≥ 1 tick). At anchor 10_000 → `[9500, 9750, 10000, 10250,
  10500]`.
- **Anchor = the book mid at chain construction.** Phase 4 builds the chain
  **once** and keeps strikes fixed as spot drifts; re-striking is a Phase 5/6
  decision (logged in `docs/TODO.md` backlog).

### D4 — Option price units & rounding
- `bs_price` / `bs_greeks` return **pure floats** in the same unit as `S`
  (ticks). Phase 4 does **no** tick-rounding of option values.
- Quote-rounding policy (snapping dealer option quotes to a tradable grid) is
  **owned by Phase 5**, where the options market is defined.

### D5 — Greeks set
- Implement **delta, gamma, vega** only — the set Phase 5 hedging requires
  (delta + gamma) plus vega for surface sensitivity. The frozen `Greeks`
  dataclass carries exactly these three fields.
- **theta / rho are intentionally not shipped** in Phase 4 (avoid untested
  Greeks). They can be added later behind the same dataclass if a phase needs
  them, with their own known-value tests.

### Normal CDF / PDF source
- `bs_price` / `bs_greeks` use **SciPy** for the standard normal: `N(x)` via
  `scipy.special.ndtr`, `N'(x)` via `scipy.stats.norm.pdf`. This honours the
  CLAUDE.md design decision ("SciPy for norm CDF"). SciPy was not previously
  installed; it is now pinned in `requirements.txt` (`scipy>=1.10`) and is part
  of the approved dependency set.

## Phase 5 Implementation Contracts

> Resolved at the start of Phase 5 (2026-06-11) from `docs/PHASE_5_WORKPLAN.md`
> Step 0. All recommended defaults were adopted; E2 and E4 additionally pin
> down ambiguities the workplan flagged (delta quantisation floor; vol-bump
> unit). These are **frozen** — the hedge-loop tests depend on them.

### E1 — Trigger surface: quote-driven flow, no options LOB
- Option trades are generated by a lightweight **`OptionsFlow`** agent
  (`sim/agents/options_flow.py`): Poisson arrivals, registered on the Clock
  like any agent. On each event it picks a random series from the dealer's
  chain, a random side, and a random contract count (`1..max_lots`), and calls
  **`dealer.on_option_trade(series, side, qty, spot, now)`** directly. There is
  **no second LOB** — the options market is quote-driven and the equity book
  remains the only real book.
- `side` is the **taker's** side: `BUY` lifts the dealer's ask (dealer position
  −qty contracts), `SELL` hits the dealer's bid (dealer +qty).
- `on_option_trade` returns the dealer's equity hedge `Order`s (owned by the
  dealer's `agent_id`); the flow agent returns them from its own `step()`.
- **Clock owner-routing extension:** the Clock credits each submitted action to
  its **owning agent** (`self.agents.get(action.agent_id, stepping_agent)`) —
  `open_order_ids` bookkeeping and taker-fill routing go to the owner, not the
  stepping agent. Backward-compatible: for every pre-Phase-5 agent the owner
  *is* the stepping agent. This is what lets the flow carry dealer hedge orders
  to the book while the dealer's position updates correctly.

### E2 — Delta units: contracts → lots (single conversion site)
- An option **contract is written on one lot** (`lot_size` shares). The
  dealer's option book is in **contracts** (`dict[OptionSeries, int]`, + = long).
- Per-contract delta in **lots** = `bs_delta` (per share) × `lot_size` shares ÷
  `lot_size` shares/lot = **`bs_delta`** — `lot_size` cancels, so the dealer
  does **not** store it.
- `net_delta_lots = Σ_i contracts_i × delta_i + equity_position_lots` (the
  equity hedge inventory carries delta **+1 per long lot**, so the loop
  converges). **Single site:** `OptionsMarketMaker.net_delta_lots(spot, now)`;
  never inline the contract→lot arithmetic elsewhere.
- `hedge_qty_lots = round(−net_delta_lots)` (Python `round`, **half-to-even**),
  submitted as an equity **market** order (BUY if +, SELL if −, skip if 0).
- **Quantisation floor:** integer-lot hedging bounds the post-hedge residual at
  **0.5 lot**; sub-half-lot deltas round to no order. The Phase 5 assertion is
  therefore `abs(post_hedge_delta_lots) <= max(delta_hedge_threshold, 0.5)`.
  A `delta_hedge_threshold` below 0.5 gates nothing extra; it stays in config
  for a future sub-lot hedging mode.

### E3 — Hedge timing: every option fill + every dealer step
- Recompute `net_delta_lots` inside **every** `on_option_trade` (after the
  position update) **and** at each dealer `step()` (the underlying may have
  drifted). Submit a hedge only when `abs(net_delta_lots) >
  delta_hedge_threshold` (lots). The post-hedge delta is what the Phase 5 e2e
  asserts (within the E2 quantisation floor).
- No reference price (mid **and** last_fill both None): emit nothing — mirror
  of equity_mm Audit P1-5.

### E4 — Option quotes: vol-point bump, dealer-favourable tick rounding
- Mid prices come from the Phase 4 pricer at the live spot
  (`chain.spot_from_book(state.mid, tick_size)`), per-series `T`
  (`chain.time_to_expiry_years`), and `FlatVolSurface(vol_estimate)`.
- Two-sided quotes by **vol bump**: bid priced at `σ − spread_vols × 0.01`,
  ask at `σ + spread_vols × 0.01` — `spread_vols` is in **vol points**, 1 point
  = 0.01 of annualised σ (so the default `2.0` means σ ± 0.02). The bid σ is
  clamped at ≥ 0 (where `bs_price` degrades gracefully to discounted
  intrinsic). Vega ≥ 0 guarantees bid ≤ mid ≤ ask for calls and puts alike.
- **Quote rounding (D4 carry-over, owned here):** bid is **floored** and ask is
  **ceiled** to the `option_tick` grid (dealer-favourable); bid floored at 0;
  if rounding collapses them, ask is bumped one `option_tick` so the option
  spread is always ≥ 1 tick. `option_tick` defaults to the market `tick_size`.
- Trades execute **at the quote**: taker-BUY at the ask, taker-SELL at the bid.
  The dealer tracks `option_cash_flow` (per-share premium × contracts, signed)
  for diagnostics.

### E5 — Gamma limit: refuse |gamma|-increasing trades past the cap
- `portfolio_gamma = Σ_i contracts_i × gamma_i` (lots per tick — same unit
  treatment / lot_size cancellation as E2; the equity leg has zero gamma).
  Single site: `OptionsMarketMaker.portfolio_gamma(spot, now)`.
- A trade is **refused** when the post-trade `abs(portfolio_gamma)` would
  exceed `gamma_limit` **and** exceed the current `abs(portfolio_gamma)`
  (gamma-reducing trades are always accepted). Refusal = no position change,
  no hedge, `gamma_rejections` counter incremented, `on_option_trade` returns
  `[]`. Existing delta is still hedged at dealer steps.

### E6 — Chain lifecycle: built once, strikes fixed
- The chain is built **once at dealer construction** off the seeded BBO mid
  (Phase 4 D3 behaviour); strikes stay fixed as spot drifts. Re-striking is
  logged in `docs/TODO.md` backlog and deferred to Phase 6 unless a run shows
  the spot leaving the strike grid materially.

## Phase 6 Implementation Contracts

> Resolved at the start of Phase 6 execution (2026-07-05) from
> `docs/PHASE_6_WORKPLAN.md` Step 0. These are **frozen** — the stylised-facts
> validation and every Phase 6 metric depend on them.

### F1 — Per-step market-data collection (Clock callback, no file I/O)
- `Clock.__init__` gains `on_step: Callable[[StepRecord], None] | None = None`,
  fired **once at the end of every `step()`**. Backward-compatible: `None`
  changes nothing (all pre-Phase-6 call sites).
- `StepRecord` is a frozen dataclass: `step`, `timestamp`, `best_bid`,
  `best_ask`, `mid`, `spread`, `bid_depth`, `ask_depth` (resting qty at the
  BBO price level), `last_fill_price`, `rolling_vol_bps`.
- `sim/analytics/collector.py` `MarketDataCollector` accumulates records in
  memory and exposes NumPy views on demand. **Zero file I/O in the loop.**
- `run_sim.run()` always wires a collector and adds `"collector"` to its
  results dict (additive; frozen tests unaffected).

### F2 — Metric definitions (canonical return series = bar mid returns)
- **Canonical return series** for the stylised facts: log returns of the mid
  **resampled to fixed sim-time bars** (`resample_mid(times, mids,
  bar_minutes)`, last-observation-carried-forward), not event-time returns.
- Realized vol: `std(bar log returns) × sqrt(minutes_per_year / bar_minutes)`
  (annualised fraction).
- Effective spread (bps): `2·d·(p − m)/m × 1e4` per fill, `d` = +1 taker BUY /
  −1 taker SELL, `m` = latest snapshot mid at/before the fill's timestamp.
- Roll measure: `2·sqrt(max(0, −cov(Δp_t, Δp_{t−1})))` on trade prices.
- Price impact: Kyle λ = OLS slope of per-bar Δmid on per-bar signed volume
  (taker BUY +qty, SELL −qty); plus large-vs-small comparison (mean |Δmid|
  around top-quartile-qty fills vs bottom-quartile).
- Vol clustering: ACF of squared bar returns + **hand-rolled Ljung-Box** Q
  statistic with p-value from `scipy.stats.chi2` (statsmodels is not approved).
- Fat tails: excess kurtosis of bar returns (NumPy moments, Fisher convention).
- All additions to `analytics/metrics.py` are **additive** — the six existing
  functions are pinned by `test_metrics.py`.

### F3 — Long-run stability guardrails
- **Universal ≥1-tick clamp:** any agent-computed limit price is clamped to
  ≥ 1 tick before an `Order` is built (extends Audit P1-5; a clamped quote is
  legal, an exception is not).
- **Vol-ratio cap:** the equity MM spread formula becomes
  `effective_spread = max(1, round(spread_target · (1 + vol_multiplier ·
  (min(vol_ratio, vol_ratio_cap) − 1))))` with config
  `equity_mms[].vol_ratio_cap` (default `10.0`). The Phase 3 vol-spread test
  exercises ratios far below 10, so its contract is unchanged.
- **Post-only quoting:** `equity_mms[].post_only` (default `false` preserves
  pre-Phase-6 trajectories; `true` in shipped configs). When set, the MM clips
  its quotes strictly inside the live BBO so they can never cross the book.
- **Diagnosis results (2026-07-05).** Three interacting causes, none of them
  market-order surplus-rest:
  1. *Bounce-vol feedback* → the crash. Rolling vol is measured off
     trade-price returns, which bounce between the MMs' own quotes, so a
     wider spread raises the vol reading which widens the spread — runaway
     until `bid = mid − half_spread − skew < 0` → LOB `ValueError`.
     Fixed by `vol_ratio_cap` (+ the ≥1-tick clamp as a backstop).
  2. *MM-vs-MM hot-potato churn* → price ratchet. An inventory-skewed MM's
     fresh bid lifted the other MM's stale ask (65% of all volume was
     MM-vs-MM); mutual fills printed ever higher while pair inventory —
     which mutual trades cannot change — stayed displaced. Fixed by
     `post_only`.
  3. *Skew integrator* → residual drift. `skew = risk_aversion × position`
     shifts both quotes relative to a mid made of the MMs' own quotes; any
     persistently displaced inventory integrates into unbounded drift
     (drift at 20k steps: +3105 ticks at `risk_aversion 0.05`, +132 at
     0.002). Price-insensitive retail flow means skew cannot manage
     inventory anyway. This is a **calibration** knob: `phase6.yaml` runs
     with a small `risk_aversion`; the mechanism is documented, not removed.

### F4 — Hot-loop vol: O(window) per step
- `Clock._build_state` slices `self.tape.fills[-(vol_window+1):]` and builds
  the small price array directly — numerically identical to the old full-tape
  `tape.prices()` scan, O(window) instead of O(fills) per step.
- Vol remains **population std** (`ddof=0`) — `sim/live/state_writer.py` is
  aligned to match (it used `ddof=1`; the Clock is canonical).

### F5 — Self-trade accounting
- `base.on_fills` **skips the position update** when
  `fill.taker_agent_id == fill.maker_agent_id` (a self-trade is a wash, net 0);
  it still discards both order ids. Regression test pins it.

### F6 — Dynamic vol surface: EWMA realized vol, default off
- `EwmaVolSurface` (in `sim/options/surface.py`) implements the `VolSurface`
  protocol (flat across strikes/expiries) plus `update(mid, now_minutes)`:
  EWMA of squared per-minute mid log returns, annualised via
  `minutes_per_year`; σ clamped to `[sigma_floor, sigma_cap]`; seeded from
  `vol_estimate`.
- Config: `options_mm.surface_mode: flat | ewma` (**default `flat`** — Phase 5
  determinism preserved), `options_mm.ewma_lambda` (default 0.97),
  `sigma_floor` 0.05, `sigma_cap` 1.0.
- The dealer calls `surface.update(...)` at each `step()` **only** when the
  surface exposes it (`ewma` mode). Both construction sites switch on the key:
  `run_sim._build_dealer` and `sim/live/sim_runner.py`.

### F7 — No mid-run re-striking
- The E6 decision stands for the whole project: the chain is built once.
  The Phase 6 run config (`phase6.yaml`) **widens `strikes_pct` to ±10%**
  instead, and the report tracks the fraction of the run with spot inside the
  strike grid. Revisit only if validation shows material drift.

### F8 — Calibration harness + run config
- `sim/analytics/sweep.py`: `run_sweep(base_cfg, grid, seeds)` — sequential
  `run_sim.run()` calls, per-run fact metrics, results returned in memory
  (callers may write to `results/` **after** runs complete).
- The calibrated long-run configuration lives in **`sim/config/phase6.yaml`**;
  `sim/config/params.yaml` receives only **additive** keys so every config
  pinned by frozen tests is unchanged.
- Approved fallback if vol clustering / fat tails resist the parameter grid:
  **retail vol-feedback** — `retail.vol_feedback` (default `0.0` = off, no
  behaviour change): the retail order-size mean scales by
  `1 + vol_feedback · (min(vol_ratio, vol_ratio_cap) − 1)` off
  `rolling_vol_bps`. Config-gated, unit-tested.

### F9 — Validation spec (the project's pass criteria)
- Run: **3 seeds × ≥ 30_000 steps** on `phase6.yaml` via `run_phase6.py`.
  No crash. Per seed:
  1. Positive spread: quoted spread ≥ 1 tick at every **two-sided**
     snapshot, and one-sided (gap) snapshots ≤ 0.1% of steps. (Amended
     during Step 5: a large marketable order can empty one side for the
     1–2 events until the next MM arrival — a real LOB phenomenon at
     event granularity, observed at 0.07% frequency; "at all times"
     cannot literally hold in an event-driven book.)
  2. Spread↑ with vol: corr(windowed realized vol, windowed mean spread) > 0.2.
  3. Price impact: Kyle λ > 0 **and** top-quartile-qty fills move the mid more
     than bottom-quartile.
  4. Efficiency: |ACF_k(bar returns)| < 3/√n for ≥ 90% of lags 1..20.
  5. Vol clustering: Ljung-Box on squared bar returns (10 lags) p < 0.05.
  6. Fat tails: excess kurtosis of bar returns > 0.
  7. Dealer delta: post-hedge |net delta| ≤ max(threshold, 0.5) lots (E2).
- **Measurement spec (pinned in Step 8):** facts are evaluated on
  **0.25-minute bars** (~80 events/bar at the calibrated event rate) with
  60-bar vol windows, uniform across facts and seeds. The facts are
  frequency/horizon-dependent — coarser bars lose clustering significance on
  some seeds; a 2× horizon exposes small genuine return autocorrelation —
  and the report's caveats record those counter-observations explicitly.
- **The project is done when all 7 hold on ≥ 2 of 3 seeds** and the report
  (`results/phase6/report.md` + figures) is committed.

## Known Design Decisions & Rationale
- **Integer ticks for prices**: avoids floating-point drift corrupting the LOB sort order
- **`equity_mm`/`equity_mms` shim is permanent** (closed 2026-07-15): the frozen
  `test_e2e_phase2.py` passes the singular key, and it stays frozen for the life
  of the project, so `run_sim.py` accepts both forms indefinitely (Audit P2-2)
- **Continuous calendar is the project time convention** (closed 2026-07-15):
  all calibration and the F9 verdict use D1's `minutes_per_year: 525_600`.
  A trading calendar (98_280) is a one-key config change but invalidates the
  calibration, so it is a per-experiment choice, not a pending migration
- **Poisson arrivals for retail**: standard in market microstructure literature (Glosten-Milgrom)
- **BBO seeded in runner, not agents**: keeps agent logic and tests isolated from bootstrap state
- **params.yaml from Phase 2**: single source of truth; agents take config dicts, never read files
- **Tape via callback, not LOB coupling**: LOB stays pure and testable; runner injects logging
- **Flat vol surface to start**: simplifies Phase 4; surface dynamics added in Phase 6
- **Single options LOB per series deferred**: Phase 4 uses quote-driven market (dealer quotes on request); full options LOB added only if Phase 5 is stable
- **No options-on-options**: scope boundary — this simulator covers equity + vanilla options only

## Stylised Facts to Validate Against
The simulation is only "working" when it reproduces (validated 2026-07-05 by
`run_phase6.py` under the F9 spec — 60k steps, 0.25-min bars, seeds 42/7/123;
per-seed values and caveats in `results/phase6/report.md`):
- [x] Positive bid-ask spread at all times (≥ 1 tick at every two-sided
      snapshot; one-sided sweep gaps ≤ 0.1% of steps — F9 amendment)
- [x] Spread widens with volatility (windowed corr 0.37–0.69 across seeds)
- [x] Price impact: large orders move the mid more than small orders
      (Kyle λ > 0 on every seed; top-quartile impact ~10× bottom-quartile)
- [x] Autocorrelation of returns near zero (95–100% of lags 1..20 inside
      ±3/√n at the validation spec; small genuine ACF emerges at 2× horizon —
      disclosed in the report)
- [x] Volatility clustering (Ljung-Box on squared returns p < 0.05 on 2/3
      seeds — the F9 gate; the weakest fact, seed-sensitive)
- [x] Fat tails in return distribution (excess kurtosis 20–42 across seeds)
- [x] Delta of options_mm position near zero after each hedge cycle
      (within the 0.5-lot quantisation floor, E2 — pinned by `test_e2e_phase5.py`)

## What Claude Should NOT Do
- Do not add libraries not listed above without flagging it first
- Do not refactor working modules during a feature session
- Do not skip writing tests to "save time" — tests are the checkpoint system
- Do not use Pandas DataFrames inside the simulation loop (use NumPy arrays)
- Do not implement Phase N+1 features while Phase N is incomplete
- Do not hardcode prices, rates, or agent parameters — everything goes in params.yaml
- Do not add new agent types without updating `sim/agents_repl.py` — a phase that
  cannot be watched in the REPL is not inspectable and therefore not done

## Glossary
| Term | Meaning |
|------|---------|
| LOB | Limit Order Book |
| MM | Market Maker |
| ATM | At-the-money (option strike ≈ current price) |
| BS | Black-Scholes |
| Greeks | Delta, Gamma, Vega, Theta, Rho |
| IV / σ | Implied volatility |
| Tick | Minimum price increment |
| Lot | Standard trading unit (lot_size shares) |
| Fill | A matched trade between two orders |
| Tape | Chronological record of all fills |
| Stylised fact | Empirical regularity observed in real market data |