"""Calibration sweep harness (Phase 6, F8).

Runs the simulator across parameter overrides x seeds, evaluating the
stylised facts for each run. Everything stays in memory — callers may
serialise results to `results/` **after** the runs complete (no file
I/O here, keeping the F8 contract).

Override keys are dotted config paths, e.g.
`"agents.retail.order_size_mean"`. A path component that is an integer
indexes a list; the wildcard `*` fans out over every list element:
`"agents.equity_mms.*.risk_aversion"` touches both MMs.
"""

from __future__ import annotations

import copy
from typing import Any

from run_sim import run
from sim.analytics.facts import FactResult, evaluate_stylised_facts


def apply_overrides(cfg: dict, overrides: dict[str, Any]) -> dict:
    """Return a deep copy of `cfg` with dotted-path overrides applied.

    Args:
        cfg: Base config dict (unmodified).
        overrides: Mapping of dotted path -> new value. Integer
            components index lists; `*` applies to every list element.

    Returns:
        The overridden copy.

    Raises:
        KeyError: If a path component does not exist (typo guard —
            sweeps must not silently no-op).
    """
    out = copy.deepcopy(cfg)
    for path, value in overrides.items():
        _set_path(out, path.split("."), value, path)
    return out


def _set_path(node: Any, parts: list[str], value: Any, full_path: str) -> None:
    head, rest = parts[0], parts[1:]
    if head == "*":
        if not isinstance(node, list):
            raise KeyError(f"'*' in {full_path!r} needs a list, got {type(node)}")
        if not rest:
            raise KeyError(f"{full_path!r} ends on '*' — nothing to set")
        for item in node:
            _set_path(item, rest, value, full_path)
        return
    key: Any = head
    if isinstance(node, list):
        key = int(head)
        if not -len(node) <= key < len(node):
            raise KeyError(f"index {key} out of range in {full_path!r}")
    elif head not in node:
        raise KeyError(f"unknown config key {head!r} in {full_path!r}")
    if rest:
        _set_path(node[key], rest, value, full_path)
    else:
        node[key] = value


def run_once(
    base_cfg: dict,
    overrides: dict[str, Any],
    seed: int,
    *,
    max_steps: int | None = None,
    **fact_kwargs: Any,
) -> dict[str, Any]:
    """One simulation run + facts evaluation.

    Args:
        base_cfg: Base config (copied, not mutated).
        overrides: Dotted-path overrides for this run.
        seed: Overrides `market.seed`.
        max_steps: Optional `market.max_steps` override.
        **fact_kwargs: Forwarded to `evaluate_stylised_facts`.

    Returns:
        Dict with `overrides`, `seed`, `facts` (name -> FactResult),
        `n_passed`, `all_passed`, and the run `result` itself.
    """
    cfg = apply_overrides(base_cfg, overrides)
    cfg["market"]["seed"] = int(seed)
    if max_steps is not None:
        cfg["market"]["max_steps"] = int(max_steps)
    result = run(cfg)
    facts = evaluate_stylised_facts(result, **fact_kwargs)
    n_passed = sum(f.passed for f in facts.values())
    return {
        "overrides": dict(overrides),
        "seed": int(seed),
        "facts": facts,
        "n_passed": n_passed,
        "all_passed": n_passed == len(facts),
        "result": result,
    }


def run_sweep(
    base_cfg: dict,
    grid: list[dict[str, Any]],
    seeds: list[int],
    *,
    max_steps: int | None = None,
    **fact_kwargs: Any,
) -> list[dict[str, Any]]:
    """Run every override set in `grid` across every seed, sequentially.

    Returns:
        Flat list of `run_once` outputs (grid-major, seed-minor). The
        heavyweight `result` entry is dropped from sweep outputs to keep
        memory bounded across large grids.
    """
    out: list[dict[str, Any]] = []
    for overrides in grid:
        for seed in seeds:
            row = run_once(
                base_cfg, overrides, seed, max_steps=max_steps, **fact_kwargs
            )
            row.pop("result")
            out.append(row)
    return out


def facts_table(rows: list[dict[str, Any]]) -> str:
    """Render sweep rows as a fixed-width text table (for logs/report)."""
    if not rows:
        return "(no runs)"
    names = list(rows[0]["facts"].keys())
    header = f"{'overrides':<48} {'seed':>6} " + " ".join(
        n[:12].rjust(12) for n in names
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        ov = ",".join(f"{k.split('.')[-1]}={v}" for k, v in r["overrides"].items())
        cells = " ".join(
            ("PASS" if r["facts"][n].passed else "fail").rjust(12) for n in names
        )
        lines.append(f"{ov:<48.48} {r['seed']:>6} {cells}")
    return "\n".join(lines)
