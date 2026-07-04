"""Retail noise-trader agent.

Poisson-arriving at `arrival_rate` orders per minute (the rate is
registered with the clock; the agent itself does not schedule). Each
arrival submits a single market order with:
  * random side (50/50 by default; configurable `direction_bias`)
  * random size from a geometric distribution with
    `p = 1 / order_size_mean`, so `E[qty] = order_size_mean`.

Retail agents do not manage inventory, do not place resting orders, and
do not interact with the book state directly — they only consume
liquidity.
"""

from __future__ import annotations

import uuid
from typing import Optional

import numpy as np

from sim.agents.base import Agent, MarketState
from sim.core.events import Order, Side


class Retail(Agent):
    """Poisson-arriving market-order noise trader.

    Args:
        agent_id: Stable identifier used in events, fills, and the LOB.
        order_size_mean: Mean order size in lots. Must be > 0. The
            geometric distribution's support is {1, 2, ...} so the mean
            cannot be < 1.
        direction_bias: Shift to the buy probability in [-0.5, 0.5].
            `p_buy = 0.5 + direction_bias`. Defaults to 0.0 (50/50).
        rng: NumPy `Generator` for drawing direction and size.
        vol_feedback: F8 fallback, default 0.0 = off (pre-Phase-6
            behaviour, RNG stream untouched). When positive, the
            effective order-size mean scales with recent volatility:
            `mean * (1 + vol_feedback * (min(vol_ratio, vol_ratio_cap)
            - 1))`, clamped to >= 1 lot. Noise traders trading bigger in
            volatile regimes makes volatility self-exciting (ARCH).
        baseline_vol_bps: Denominator of `vol_ratio` (mirrors the MM
            config seed).
        vol_ratio_cap: Cap on `vol_ratio` (mirrors the MM cap, F3).
    """

    def __init__(
        self,
        agent_id: str,
        order_size_mean: float,
        direction_bias: float,
        rng: np.random.Generator,
        vol_feedback: float = 0.0,
        baseline_vol_bps: float = 5.0,
        vol_ratio_cap: float = 10.0,
    ) -> None:
        super().__init__(agent_id)
        if order_size_mean <= 0:
            raise ValueError(f"order_size_mean must be positive, got {order_size_mean}")
        if not -0.5 <= direction_bias <= 0.5:
            raise ValueError(f"direction_bias must be in [-0.5, 0.5], got {direction_bias}")
        if vol_feedback < 0:
            raise ValueError(f"vol_feedback must be >= 0, got {vol_feedback}")
        if baseline_vol_bps <= 0:
            raise ValueError(f"baseline_vol_bps must be positive, got {baseline_vol_bps}")
        self.order_size_mean: float = order_size_mean
        self.direction_bias: float = direction_bias
        self.rng: np.random.Generator = rng
        self.vol_feedback: float = vol_feedback
        self.baseline_vol_bps: float = baseline_vol_bps
        self.vol_ratio_cap: float = vol_ratio_cap
        self._p: float = 1.0 / order_size_mean
        self._p_buy: float = 0.5 + direction_bias

    def _size_p(self, state: MarketState) -> float:
        """Geometric `p` for this step's order size (vol feedback, F8)."""
        if self.vol_feedback == 0.0 or state.rolling_vol_bps is None:
            return self._p
        ratio = min(state.rolling_vol_bps / self.baseline_vol_bps, self.vol_ratio_cap)
        mean = self.order_size_mean * (1.0 + self.vol_feedback * (ratio - 1.0))
        return 1.0 / max(mean, 1.0)

    def step(self, state: MarketState) -> list[Order]:
        buy = self.rng.random() < self._p_buy
        side = Side.BUY if buy else Side.SELL
        qty = int(self.rng.geometric(self._size_p(state)))
        return [
            Order(
                order_id=uuid.uuid4(),
                agent_id=self.agent_id,
                side=side,
                price=0,
                qty=qty,
                timestamp=state.timestamp,
                is_market=True,
            )
        ]
