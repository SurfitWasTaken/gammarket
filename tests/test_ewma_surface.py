"""Tests for the Phase 6 EWMA vol surface (F6)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from sim.options.surface import (
    EwmaVolSurface,
    FlatVolSurface,
    VolSurface,
    surface_from_config,
)

MPY = 525_600.0


def test_protocol_conformance() -> None:
    assert isinstance(EwmaVolSurface(0.2), VolSurface)


def test_seeded_sigma_before_any_update() -> None:
    s = EwmaVolSurface(0.2, minutes_per_year=MPY)
    assert s.sigma == pytest.approx(0.2)
    assert s.vol(9_500, 10_080.0) == pytest.approx(0.2)


def test_first_observation_records_without_updating() -> None:
    s = EwmaVolSurface(0.2, minutes_per_year=MPY)
    s.update(10_000.0, 1.0)
    assert s.sigma == pytest.approx(0.2)


def test_ewma_recursion_known_value() -> None:
    lam = 0.9
    s = EwmaVolSurface(
        0.2, ewma_lambda=lam, sigma_floor=0.001, sigma_cap=10.0,
        minutes_per_year=MPY,
    )
    s.update(10_000.0, 0.0)
    s.update(10_100.0, 1.0)  # dt=1 -> w=lam
    r = math.log(10_100.0 / 10_000.0)
    var0 = 0.2**2 / MPY
    expected_var = lam * var0 + (1 - lam) * r * r
    assert s.sigma == pytest.approx(math.sqrt(expected_var * MPY))


def test_time_adjusted_decay() -> None:
    """dt=2 must weight the old variance by lambda^2, not lambda."""
    lam = 0.9
    s = EwmaVolSurface(
        0.2, ewma_lambda=lam, sigma_floor=0.001, sigma_cap=10.0,
        minutes_per_year=MPY,
    )
    s.update(10_000.0, 0.0)
    s.update(10_100.0, 2.0)
    r = math.log(10_100.0 / 10_000.0)
    var0 = 0.2**2 / MPY
    w = lam**2
    expected_var = w * var0 + (1 - w) * (r * r / 2.0)
    assert s.sigma == pytest.approx(math.sqrt(expected_var * MPY))


def test_floor_and_cap() -> None:
    s = EwmaVolSurface(
        0.2, ewma_lambda=0.5, sigma_floor=0.05, sigma_cap=0.6,
        minutes_per_year=MPY,
    )
    s.update(10_000.0, 0.0)
    for i in range(1, 200):  # constant mid -> variance decays toward 0
        s.update(10_000.0, float(i))
    assert s.sigma == pytest.approx(0.05)
    s2 = EwmaVolSurface(
        0.2, ewma_lambda=0.5, sigma_floor=0.05, sigma_cap=0.6,
        minutes_per_year=MPY,
    )
    s2.update(10_000.0, 0.0)
    s2.update(20_000.0, 1.0)  # a 100% jump in one minute
    assert s2.sigma == pytest.approx(0.6)


def test_non_advancing_and_bad_mids_ignored() -> None:
    s = EwmaVolSurface(0.2, minutes_per_year=MPY)
    s.update(10_000.0, 5.0)
    s.update(-1.0, 6.0)      # bad mid: ignored entirely
    s.update(12_000.0, 5.0)  # non-advancing time: re-records only
    assert s.sigma == pytest.approx(0.2)


def test_validation() -> None:
    with pytest.raises(ValueError):
        EwmaVolSurface(0.0)
    with pytest.raises(ValueError):
        EwmaVolSurface(0.2, ewma_lambda=1.0)
    with pytest.raises(ValueError):
        EwmaVolSurface(0.2, sigma_floor=0.5, sigma_cap=0.1)


def test_surface_from_config_switch() -> None:
    flat = surface_from_config({"vol_estimate": 0.25}, MPY)
    assert isinstance(flat, FlatVolSurface)
    ewma = surface_from_config(
        {"vol_estimate": 0.25, "surface_mode": "ewma", "ewma_lambda": 0.9},
        MPY,
    )
    assert isinstance(ewma, EwmaVolSurface)
    assert ewma.sigma == pytest.approx(0.25)
    with pytest.raises(ValueError, match="surface_mode"):
        surface_from_config({"vol_estimate": 0.25, "surface_mode": "spline"}, MPY)


def test_dealer_updates_ewma_surface_in_run() -> None:
    """Integration: with surface_mode=ewma the dealer's step feeds the
    surface, so sigma moves off its seed during a run."""
    import copy

    from run_sim import run
    from sim.agents.options_mm import OptionsMarketMaker
    from sim.config.loader import load_config

    cfg = copy.deepcopy(load_config())
    cfg["market"]["max_steps"] = 2_000
    cfg["agents"]["options_mm"]["surface_mode"] = "ewma"
    result = run(cfg)
    dealer = next(
        a for a in result["agents"] if isinstance(a, OptionsMarketMaker)
    )
    assert isinstance(dealer.surface, EwmaVolSurface)
    assert dealer.surface.sigma != pytest.approx(0.20)


def test_flat_default_keeps_phase5_dealer_untouched() -> None:
    import copy

    from run_sim import run
    from sim.agents.options_mm import OptionsMarketMaker
    from sim.config.loader import load_config

    cfg = copy.deepcopy(load_config())
    cfg["market"]["max_steps"] = 500
    result = run(cfg)
    dealer = next(
        a for a in result["agents"] if isinstance(a, OptionsMarketMaker)
    )
    assert isinstance(dealer.surface, FlatVolSurface)
    assert dealer.surface.sigma == pytest.approx(0.20)
