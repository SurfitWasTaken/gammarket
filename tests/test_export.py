"""Research export layer (sim/analytics/export.py + run_experiment.py).

Pins the CSV schemas, the metrics summary contract, and the experiment
runner's output directory — the artifacts a paper is written from.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from run_experiment import run_experiment
from run_sim import run
from sim.analytics.export import (
    bars_to_csv,
    facts_to_dict,
    fills_to_csv,
    metrics_summary,
    steps_to_csv,
    write_json,
)
from sim.analytics.facts import FACT_NAMES, evaluate_stylised_facts
from tests.test_e2e_phase5 import make_config

BAR_MINUTES = 0.25


@pytest.fixture(scope="module")
def result() -> dict:
    return run(make_config(max_steps=800, seed=42))


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open() as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def test_steps_csv_schema_and_length(result, tmp_path):
    path = steps_to_csv(result["collector"], tmp_path / "steps.csv")
    header, rows = _read_csv(path)
    assert header == [
        "step", "timestamp", "best_bid", "best_ask", "mid", "spread",
        "bid_depth", "ask_depth", "last_fill_price", "rolling_vol_bps",
    ]
    assert len(rows) == len(result["collector"])
    assert rows[0][0] == "1"  # 1-based step counter


def test_fills_csv_schema_and_length(result, tmp_path):
    path = fills_to_csv(result["tape"], tmp_path / "fills.csv")
    header, rows = _read_csv(path)
    assert header[:4] == ["timestamp", "price", "qty", "aggressor_side"]
    assert len(rows) == len(result["tape"].fills)
    assert rows[0][3] in ("BUY", "SELL")


def test_bars_csv_matches_resample(result, tmp_path):
    path = bars_to_csv(result["collector"], BAR_MINUTES, tmp_path / "bars.csv")
    header, rows = _read_csv(path)
    assert header == ["bar_close_time", "bar_mid", "log_return"]
    assert rows[0][2] == ""  # first bar has no prior close
    assert len(rows) >= 2
    assert float(rows[1][2]) == pytest.approx(
        math.log(float(rows[1][1]) / float(rows[0][1]))
    )


def test_metrics_summary_contract(result):
    m = metrics_summary(result, bar_minutes=BAR_MINUTES)
    assert m["n_steps"] == 800
    assert m["n_fills"] == len(result["tape"].fills)
    assert m["realized_vol_annualised"] > 0
    assert m["mean_quoted_spread_ticks"] >= 1.0
    # The Phase 5 dealer is in this run -> dealer block present.
    assert m["n_hedges"] >= 0 and m["n_option_trades"] >= 0
    # Everything must be JSON-serialisable as-is.
    json.dumps(m)


def test_facts_to_dict_roundtrip(result, tmp_path):
    facts = evaluate_stylised_facts(result, bar_minutes=BAR_MINUTES)
    d = facts_to_dict(facts)
    assert set(d) == set(FACT_NAMES)
    path = write_json(d, tmp_path / "facts.json")
    loaded = json.loads(path.read_text())
    assert loaded.keys() == d.keys()
    assert isinstance(loaded["fat_tails"]["passed"], bool)


def test_run_experiment_output_directory(tmp_path):
    cfg = make_config(max_steps=500, seed=42)
    out = tmp_path / "exp"
    manifest = run_experiment(
        cfg, [42, 7], out,
        overrides={"agents.retail.order_size_mean": 3},
        figures=False, label="unit test", config_name="unit",
    )
    assert (out / "experiment.md").exists()
    assert (out / "manifest.json").exists()
    metrics = json.loads((out / "metrics.json").read_text())
    assert set(metrics) == {"42", "7"}
    assert set(metrics["42"]["facts"]) == set(FACT_NAMES)
    for seed in (42, 7):
        assert (out / f"steps_s{seed}.csv").exists()
        assert (out / f"fills_s{seed}.csv").exists()
        assert (out / f"bars_s{seed}.csv").exists()
    # Overrides are applied and recorded.
    assert manifest["overrides"] == {"agents.retail.order_size_mean": 3}
    resolved = manifest["resolved_config"]
    assert resolved["agents"]["retail"]["order_size_mean"] == 3
    # The base cfg was not mutated.
    assert cfg["agents"]["retail"]["order_size_mean"] == 2
