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


class RetailRegime:
    """Shared calm/excited market-sentiment regime (polish backlog item).

    A two-state continuous-time Markov chain advanced lazily in sim
    minutes: holding times are exponential with `enter_rate_per_min`
    (calm -> excited) and `exit_rate_per_min` (excited -> calm) hazards.
    While excited, retail order-size means are multiplied by
    `excited_size_mult` — all noise traders get loud together, which is
    what makes volatility clustering structural rather than a feedback
    off the vol estimate ([F8] `vol_feedback` remains available and
    composes multiplicatively).

    One instance is shared by every retail agent (passed explicitly —
    no globals) and consumes its own dedicated `rng`, so enabling the
    regime leaves the agents' noise streams untouched. A zero hazard
    rate means that transition never fires.

    Args:
        excited_size_mult: Order-size multiplier while excited (> 0).
        enter_rate_per_min: Hazard of calm -> excited (>= 0).
        exit_rate_per_min: Hazard of excited -> calm (>= 0).
        rng: Dedicated NumPy `Generator` (not an agent's stream).
        start_excited: Initial state; defaults to calm.
    """

    def __init__(
        self,
        excited_size_mult: float,
        enter_rate_per_min: float,
        exit_rate_per_min: float,
        rng: np.random.Generator,
        start_excited: bool = False,
    ) -> None:
        if excited_size_mult <= 0:
            raise ValueError(
                f"excited_size_mult must be positive, got {excited_size_mult}"
            )
        if enter_rate_per_min < 0 or exit_rate_per_min < 0:
            raise ValueError(
                f"hazard rates must be >= 0, got "
                f"({enter_rate_per_min}, {exit_rate_per_min})"
            )
        self.excited_size_mult: float = excited_size_mult
        self._enter_rate: float = enter_rate_per_min
        self._exit_rate: float = exit_rate_per_min
        self._rng: np.random.Generator = rng
        self._excited: bool = start_excited
        self._next_switch: float = self._holding_time(0.0)

    def _holding_time(self, now: float) -> float:
        rate = self._exit_rate if self._excited else self._enter_rate
        if rate <= 0.0:
            return float("inf")
        return now + float(self._rng.exponential(1.0 / rate))

    @property
    def excited(self) -> bool:
        """Whether the chain is currently in the excited state."""
        return self._excited

    def advance(self, now: float) -> None:
        """Advance the chain to sim-minute `now` (monotone, idempotent)."""
        while self._next_switch <= now:
            switch_time = self._next_switch
            self._excited = not self._excited
            self._next_switch = self._holding_time(switch_time)

    def size_multiplier(self, now: float) -> float:
        """Advance to `now` and return the current order-size multiplier."""
        self.advance(now)
        return self.excited_size_mult if self._excited else 1.0


def regime_from_config(
    retail_cfg: dict, seed: int
) -> RetailRegime | None:
    """Build the shared retail regime from `agents.retail.regime` config.

    Absent key (or explicit null) -> None = off, the pre-existing
    default. The regime draws from its own seed-derived generator so
    agent noise streams are unchanged whether it is on or off. This is
    the single construction switch shared by `run_sim`, `sim/live`, and
    `agents_repl` (same pattern as `surface_from_config`).
    """
    regime_cfg = retail_cfg.get("regime")
    if not regime_cfg:
        return None
    return RetailRegime(
        excited_size_mult=float(regime_cfg["excited_size_mult"]),
        enter_rate_per_min=float(regime_cfg["enter_rate_per_min"]),
        exit_rate_per_min=float(regime_cfg["exit_rate_per_min"]),
        rng=np.random.default_rng([int(seed), 0x5E91]),
    )


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
        regime: Optional shared `RetailRegime` (default None = off,
            pre-existing behaviour and RNG stream untouched). When set,
            the order-size mean is additionally multiplied by
            `regime.size_multiplier(now)`.
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
        regime: RetailRegime | None = None,
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
        self.regime: RetailRegime | None = regime
        self._p: float = 1.0 / order_size_mean
        self._p_buy: float = 0.5 + direction_bias

    def effective_size_mean(
        self, rolling_vol_bps: float | None, now: float
    ) -> float:
        """Order-size mean after regime and vol-feedback scaling (>= 1 lot)."""
        mean = self.order_size_mean
        if self.regime is not None:
            mean *= self.regime.size_multiplier(now)
        if self.vol_feedback > 0.0 and rolling_vol_bps is not None:
            ratio = min(rolling_vol_bps / self.baseline_vol_bps, self.vol_ratio_cap)
            mean *= 1.0 + self.vol_feedback * (ratio - 1.0)
        return max(mean, 1.0)

    def _size_p(self, state: MarketState) -> float:
        """Geometric `p` for this step's order size (vol feedback + regime)."""
        if self.regime is None and (
            self.vol_feedback == 0.0 or state.rolling_vol_bps is None
        ):
            return self._p
        return 1.0 / self.effective_size_mean(
            state.rolling_vol_bps, state.timestamp
        )

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
