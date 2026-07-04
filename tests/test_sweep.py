"""Tests for the Phase 6 calibration sweep harness (F8)."""

from __future__ import annotations

import copy

import pytest

from sim.analytics.facts import FACT_NAMES
from sim.analytics.sweep import apply_overrides, facts_table, run_once, run_sweep
from sim.config.loader import load_config


@pytest.fixture(scope="module")
def base_cfg():
    return load_config()


def test_apply_overrides_scalar(base_cfg) -> None:
    out = apply_overrides(base_cfg, {"agents.retail.order_size_mean": 9})
    assert out["agents"]["retail"]["order_size_mean"] == 9
    assert base_cfg["agents"]["retail"]["order_size_mean"] != 9  # copy


def test_apply_overrides_list_index(base_cfg) -> None:
    out = apply_overrides(base_cfg, {"agents.equity_mms.0.spread_target": 7})
    assert out["agents"]["equity_mms"][0]["spread_target"] == 7
    assert out["agents"]["equity_mms"][1]["spread_target"] != 7


def test_apply_overrides_wildcard(base_cfg) -> None:
    out = apply_overrides(base_cfg, {"agents.equity_mms.*.risk_aversion": 0.001})
    assert all(m["risk_aversion"] == 0.001 for m in out["agents"]["equity_mms"])


def test_apply_overrides_typo_raises(base_cfg) -> None:
    with pytest.raises(KeyError):
        apply_overrides(base_cfg, {"agents.retial.order_size_mean": 9})
    with pytest.raises(KeyError):
        apply_overrides(base_cfg, {"agents.equity_mms.9.spread_target": 7})
    with pytest.raises(KeyError):
        apply_overrides(base_cfg, {"agents.equity_mms.*": 7})


def test_run_once_evaluates_facts(base_cfg) -> None:
    row = run_once(base_cfg, {}, seed=1, max_steps=800)
    assert tuple(row["facts"].keys()) == FACT_NAMES
    assert 0 <= row["n_passed"] <= len(FACT_NAMES)
    assert row["result"]["clock"].step_count == 800


def test_run_sweep_grid_by_seeds(base_cfg) -> None:
    rows = run_sweep(
        base_cfg,
        grid=[{}, {"agents.retail.order_size_mean": 4}],
        seeds=[1, 2],
        max_steps=400,
    )
    assert len(rows) == 4
    assert all("result" not in r for r in rows)
    table = facts_table(rows)
    assert "seed" in table and "PASS" in table or "fail" in table
