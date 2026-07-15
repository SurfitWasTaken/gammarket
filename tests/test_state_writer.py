"""Live-dashboard state extraction (sim/live/state_writer.py).

Pins the Phase 6 backlog additions: retail vol-feedback effective order
size and the dealer's surface mode / current sigma are surfaced in the
extracted state so the terminal dashboards can display them.
"""

from __future__ import annotations

import pytest

from run_sim import run
from sim.live.state_writer import extract_all_state
from tests.test_e2e_phase5 import make_config


def _extract(cfg: dict) -> dict:
    result = run(cfg)
    market = cfg["market"]
    return extract_all_state(
        result["book"],
        result["clock"],
        result["tape"],
        result["agents"],
        tick_size=market["tick_size"],
        vol_window=market["vol_window"],
    )


@pytest.fixture(scope="module")
def phase6_state() -> dict:
    """One small run with vol_feedback on and the EWMA surface active."""
    cfg = make_config(max_steps=400, seed=42)
    cfg["agents"]["retail"]["vol_feedback"] = 0.7
    cfg["agents"]["retail"]["baseline_vol_bps"] = 5.0
    cfg["agents"]["retail"]["vol_ratio_cap"] = 10.0
    cfg["agents"]["options_mm"]["surface_mode"] = "ewma"
    return _extract(cfg)


def _agents_of(state: dict, atype: str) -> list[dict]:
    return [a for a in state["agents"].values() if a["type"] == atype]


def test_retail_state_carries_vol_feedback(phase6_state):
    retail = _agents_of(phase6_state, "Retail")
    assert retail, "no retail agents extracted"
    for a in retail:
        assert a["vol_feedback"] == 0.7
        assert a["effective_size_mean"] >= 1.0


def test_dealer_state_carries_ewma_surface(phase6_state):
    (dealer,) = _agents_of(phase6_state, "OptionsMarketMaker")
    assert dealer["surface_mode"] == "ewma"
    # EWMA sigma is clamped to [sigma_floor, sigma_cap] = [0.05, 1.0].
    assert 0.05 <= dealer["surface_sigma"] <= 1.0


def test_flat_defaults_unchanged():
    state = _extract(make_config(max_steps=200, seed=7))
    (dealer,) = _agents_of(state, "OptionsMarketMaker")
    assert dealer["surface_mode"] == "flat"
    assert dealer["surface_sigma"] == pytest.approx(0.20)
    for a in _agents_of(state, "Retail"):
        assert a["vol_feedback"] == 0.0
        assert a["effective_size_mean"] == pytest.approx(2.0)
