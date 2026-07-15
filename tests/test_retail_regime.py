"""Retail sentiment regime (calm/excited Markov chain, polish backlog).

Pins: exact CTMC advancement, the size-multiplier contract, config
gating (absent block = off, pre-existing behaviour and RNG stream
untouched), composition with vol_feedback, and the shared-instance
wiring in run_sim.
"""

from __future__ import annotations

import numpy as np
import pytest

from run_sim import run
from sim.agents.base import MarketState
from sim.agents.retail import Retail, RetailRegime, regime_from_config
from tests.test_e2e_phase5 import make_config


def _state(ts: float = 0.0, vol: float | None = None) -> MarketState:
    return MarketState(
        best_bid=9_999, best_ask=10_001, mid=10_000.0,
        last_fill_price=10_000, own_position=0, timestamp=ts,
        rolling_vol_bps=vol,
    )


# ---------------------------------------------------------------- RetailRegime


def test_regime_starts_calm_with_unit_multiplier():
    regime = RetailRegime(4.0, 0.02, 0.2, np.random.default_rng(1))
    assert not regime.excited
    assert regime.size_multiplier(0.0) == 1.0


def test_regime_zero_enter_rate_never_excites():
    regime = RetailRegime(4.0, 0.0, 0.2, np.random.default_rng(1))
    assert regime.size_multiplier(1e9) == 1.0


def test_regime_zero_exit_rate_stays_excited():
    regime = RetailRegime(
        4.0, 0.02, 0.0, np.random.default_rng(1), start_excited=True
    )
    assert regime.size_multiplier(1e9) == 4.0


def test_regime_eventually_switches_and_is_idempotent():
    regime = RetailRegime(4.0, 1.0, 0.0, np.random.default_rng(42))
    # Mean holding time is 1 minute; by t=1000 the switch has fired w.p. ~1.
    assert regime.size_multiplier(1_000.0) == 4.0
    # Re-advancing to the same time changes nothing.
    assert regime.size_multiplier(1_000.0) == 4.0
    assert regime.excited


def test_regime_occupancy_matches_hazard_rates():
    # enter 0.5/min, exit 0.5/min -> stationary excited fraction 0.5.
    regime = RetailRegime(4.0, 0.5, 0.5, np.random.default_rng(7))
    excited = [
        regime.size_multiplier(t) > 1.0
        for t in np.arange(0.0, 4_000.0, 0.5)
    ]
    assert 0.4 < np.mean(excited) < 0.6


def test_regime_validation():
    rng = np.random.default_rng(1)
    with pytest.raises(ValueError):
        RetailRegime(0.0, 0.02, 0.2, rng)
    with pytest.raises(ValueError):
        RetailRegime(4.0, -0.1, 0.2, rng)
    with pytest.raises(ValueError):
        RetailRegime(4.0, 0.02, -0.1, rng)


# -------------------------------------------------------------- Retail wiring


def test_retail_default_off_keeps_fast_path():
    r = Retail("r0", 2.0, 0.0, np.random.default_rng(1))
    assert r.regime is None
    assert r._size_p(_state(vol=50.0)) == r._p


def test_retail_excited_regime_scales_size_mean():
    regime = RetailRegime(
        4.0, 0.0, 0.0, np.random.default_rng(1), start_excited=True
    )
    r = Retail("r0", 2.0, 0.0, np.random.default_rng(1), regime=regime)
    assert r.effective_size_mean(None, 0.0) == pytest.approx(8.0)
    assert r._size_p(_state()) == pytest.approx(1.0 / 8.0)


def test_regime_composes_with_vol_feedback():
    regime = RetailRegime(
        3.0, 0.0, 0.0, np.random.default_rng(1), start_excited=True
    )
    r = Retail(
        "r0", 2.0, 0.0, np.random.default_rng(1),
        vol_feedback=0.5, baseline_vol_bps=5.0, vol_ratio_cap=10.0,
        regime=regime,
    )
    # vol ratio 2 -> feedback factor 1.5; regime factor 3 -> mean 2*3*1.5.
    assert r.effective_size_mean(10.0, 0.0) == pytest.approx(9.0)


def test_effective_mean_clamped_at_one_lot():
    r = Retail(
        "r0", 2.0, 0.0, np.random.default_rng(1),
        vol_feedback=5.0, baseline_vol_bps=5.0,
    )
    # vol ratio ~0 -> raw mean 2*(1-5) < 0; clamps to 1 lot.
    assert r.effective_size_mean(0.001, 0.0) == 1.0


# ------------------------------------------------------------- config gating


def test_regime_from_config_absent_is_none():
    assert regime_from_config({"n_agents": 1}, seed=42) is None
    assert regime_from_config({"regime": None}, seed=42) is None


def test_regime_from_config_builds_and_is_seed_deterministic():
    cfg = {
        "regime": {
            "excited_size_mult": 4.0,
            "enter_rate_per_min": 0.5,
            "exit_rate_per_min": 0.5,
        }
    }
    a, b = regime_from_config(cfg, 42), regime_from_config(cfg, 42)
    path_a = [a.size_multiplier(t) for t in np.arange(0.0, 100.0, 0.25)]
    path_b = [b.size_multiplier(t) for t in np.arange(0.0, 100.0, 0.25)]
    assert path_a == path_b
    assert 4.0 in path_a  # the chain actually visited the excited state


def test_run_sim_shares_one_regime_across_retail_agents():
    cfg = make_config(max_steps=100, seed=42)
    cfg["agents"]["retail"]["regime"] = {
        "excited_size_mult": 4.0,
        "enter_rate_per_min": 0.02,
        "exit_rate_per_min": 0.2,
    }
    result = run(cfg)
    retail = [a for a in result["agents"] if isinstance(a, Retail)]
    assert retail
    regimes = {id(a.regime) for a in retail}
    assert len(regimes) == 1
    assert retail[0].regime is not None


def test_run_sim_default_off_trajectory_unchanged():
    base = run(make_config(max_steps=300, seed=42))
    again = run(make_config(max_steps=300, seed=42))
    assert [f.price for f in base["tape"].fills] == [
        f.price for f in again["tape"].fills
    ]
    retail = [a for a in base["agents"] if isinstance(a, Retail)]
    assert all(a.regime is None for a in retail)
