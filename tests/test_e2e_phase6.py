"""Phase 6 end-to-end tests.

Long-run stability smoke (Step 2, F3): before Phase 6, the default-shaped
config crashed within ~2-5k steps (an equity MM computed a negative bid once
the bounce-vol feedback loop blew the spread up). The full stack must now
survive a 10k-step run with sane quotes throughout.
"""

from __future__ import annotations

import copy

import pytest

from sim.agents.equity_mm import EquityMarketMaker
from sim.config.loader import load_config
from run_sim import run


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
