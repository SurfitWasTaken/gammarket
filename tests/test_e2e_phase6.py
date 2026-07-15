"""Phase 6 end-to-end tests.

Long-run stability smoke (Step 2, F3): before Phase 6, the default-shaped
config crashed within ~2-5k steps (an equity MM computed a negative bid once
the bounce-vol feedback loop blew the spread up). The full stack must now
survive a 10k-step run with sane quotes throughout.

Calibrated-config checks (Step 6, F8/F9): `sim/config/phase6.yaml` must load
and reproduce the engineered stylised facts. The in-suite assertions are the
robust subset — the statistical facts (clustering, ACF, kurtosis) are seed-
sensitive by nature, so pinning all 7 here would make the suite brittle; the
full 3-seed 7/7 validation is `run_phase6.py`'s job (results/phase6/).
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from sim.agents.equity_mm import EquityMarketMaker
from sim.analytics.facts import evaluate_stylised_facts
from sim.config.loader import load_config
from run_sim import run

PHASE6_CFG = Path("sim/config/phase6.yaml")


@pytest.fixture(scope="module")
def long_result():
    cfg = copy.deepcopy(load_config())
    cfg["market"]["max_steps"] = 10_000
    return run(cfg)


class TestLongRunStability:
    def test_run_completes(self, long_result):
        assert long_result["clock"].step_count == 10_000

    def test_book_two_sided_at_end(self, long_result):
        book = long_result["book"]
        bb, ba = book.best_bid(), book.best_ask()
        assert bb is not None and ba is not None
        assert ba - bb >= 1

    def test_mm_quoted_spreads_always_positive(self, long_result):
        mms = [
            a
            for a in long_result["agents"]
            if isinstance(a, EquityMarketMaker)
        ]
        assert mms, "the default config must include equity MMs"
        for mm in mms:
            assert mm.spread_log, f"{mm.agent_id} never quoted"
            assert min(mm.spread_log) >= 1

    def test_all_fill_prices_positive(self, long_result):
        prices = long_result["tape"].prices()
        assert len(prices) > 1_000
        assert prices.min() >= 1

    def test_no_mm_vs_mm_churn(self, long_result):
        """post_only quotes (F3) must eliminate the MM-vs-MM hot-potato
        loop that trended the mid without external flow."""
        churn = sum(
            f.qty
            for f in long_result["tape"].fills
            if f.taker_agent_id.startswith("mm_")
            and f.maker_agent_id.startswith("mm_")
        )
        assert churn == 0


class TestCalibratedConfig:
    def test_phase6_yaml_loads_with_calibrated_values(self):
        cfg = load_config(PHASE6_CFG)
        assert cfg["market"]["max_steps"] >= 30_000
        assert cfg["agents"]["retail"]["vol_feedback"] > 0
        for mm in cfg["agents"]["equity_mms"]:
            assert mm["post_only"] is True
            assert mm["risk_aversion"] <= 0.001
        assert min(cfg["options"]["strikes_pct"]) <= -0.10

    def test_phase6_ewma_yaml_builds_ewma_dealer(self):
        """The dynamic-surface config (polish pass) loads and actually
        constructs an EWMA dealer; its full 3-seed validation is
        recorded in the config header + docs."""
        from sim.agents.options_mm import OptionsMarketMaker
        from sim.options.surface import EwmaVolSurface

        cfg = copy.deepcopy(load_config(Path("sim/config/phase6_ewma.yaml")))
        assert cfg["agents"]["options_mm"]["surface_mode"] == "ewma"
        assert cfg["agents"]["options_mm"]["ewma_lambda"] == 0.99
        cfg["market"]["max_steps"] = 300
        result = run(cfg)
        dealer = next(
            a for a in result["agents"] if isinstance(a, OptionsMarketMaker)
        )
        assert isinstance(dealer.surface, EwmaVolSurface)

    def test_calibrated_run_reproduces_robust_facts(self):
        """One seed of the F9 validation, robust subset. The full
        3-seed 7/7 gate runs in run_phase6.py."""
        cfg = copy.deepcopy(load_config(PHASE6_CFG))
        result = run(cfg)
        # The pinned F9 measurement spec (run_phase6.BAR_MINUTES).
        facts = evaluate_stylised_facts(
            result, bar_minutes=0.25, window_bars=60
        )
        assert facts["positive_spread"].passed
        assert facts["price_impact"].passed
        assert facts["dealer_delta_flat"].passed
        n_passed = sum(f.passed for f in facts.values())
        assert n_passed >= 5, {
            n: f.detail for n, f in facts.items() if not f.passed
        }
