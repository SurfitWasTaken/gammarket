"""Tests for the retail vol-feedback extension (Phase 6, F8 fallback)."""

from __future__ import annotations

import numpy as np
import pytest

from sim.agents.base import MarketState
from sim.agents.retail import Retail


def _state(vol_bps: float | None) -> MarketState:
    return MarketState(
        best_bid=99,
        best_ask=101,
        mid=100.0,
        last_fill_price=100,
        own_position=0,
        timestamp=1.0,
        rolling_vol_bps=vol_bps,
    )


def _retail(**kw) -> Retail:
    kwargs = dict(
        agent_id="r",
        order_size_mean=2.0,
        direction_bias=0.0,
        rng=np.random.default_rng(0),
    )
    kwargs.update(kw)
    return Retail(**kwargs)


def test_default_off_matches_pre_phase6_rng_stream() -> None:
    """vol_feedback=0 must produce byte-identical draws to a plain
    retail agent regardless of the observed vol."""
    a = _retail()
    b = _retail(vol_feedback=0.0)
    qa = [a.step(_state(500.0))[0].qty for _ in range(50)]
    qb = [b.step(_state(500.0))[0].qty for _ in range(50)]
    assert qa == qb


def test_feedback_scales_size_mean_with_vol() -> None:
    calm = _retail(vol_feedback=1.0)
    wild = _retail(vol_feedback=1.0)
    n = 4_000
    calm_sizes = [calm.step(_state(5.0))[0].qty for _ in range(n)]
    wild_sizes = [wild.step(_state(25.0))[0].qty for _ in range(n)]
    # ratio 5 -> effective mean 2 * (1 + (5-1)) = 10 vs 2.
    assert np.mean(calm_sizes) == pytest.approx(2.0, rel=0.15)
    assert np.mean(wild_sizes) == pytest.approx(10.0, rel=0.15)


def test_feedback_capped_at_vol_ratio_cap() -> None:
    r = _retail(vol_feedback=1.0, vol_ratio_cap=10.0)
    # ratio 10_000 would give mean 2*(1+9999); the cap holds it at
    # 2*(1+9) = 20.
    assert r._size_p(_state(50_000.0)) == pytest.approx(1.0 / 20.0)


def test_feedback_calm_regime_clamped_at_one_lot() -> None:
    r = _retail(vol_feedback=1.0)
    # ratio 0 -> mean 2*(1-1) = 0 -> clamped to 1 lot minimum.
    assert r._size_p(_state(0.0)) == pytest.approx(1.0)


def test_feedback_ignored_during_warmup() -> None:
    r = _retail(vol_feedback=1.0)
    assert r._size_p(_state(None)) == pytest.approx(1.0 / 2.0)


def test_negative_feedback_rejected() -> None:
    with pytest.raises(ValueError, match="vol_feedback"):
        _retail(vol_feedback=-0.1)
