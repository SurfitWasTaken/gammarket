"""Equity market maker agent (Phase 3).

Provides continuous two-sided liquidity with a target spread, inventory-aware
quote skew, and volatility-adjusted spread. Places limit orders on both sides
of the book centered around the mid price (or last fill price when one-sided).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

import numpy as np

from sim.agents.base import Agent, MarketState
from sim.core.events import Cancel, Fill, Order, Side


@dataclass(frozen=True)
class EquityMMConfig:
    """Configuration for the equity market maker."""

    arrival_rate: float
    spread_target: int
    inventory_limit: int
    risk_aversion: float
    quote_size: int
    max_orders_per_side: int
    vol_window: int
    vol_multiplier: float
    baseline_vol_bps: float
    # F3: rolling vol is measured off trade-price returns, which bounce
    # between the MM's own bid and ask — so an uncapped vol_ratio lets the
    # spread feed its own vol reading and run away. The cap breaks the loop.
    vol_ratio_cap: float = 10.0
    # F3: when True, quotes are clipped inside the BBO so they can never
    # cross the book. Without this, two inventory-skewed MMs lift each
    # other's stale quotes in a hot-potato loop that trends the mid
    # without any external flow. Default False preserves the pre-Phase-6
    # trajectories that the e2e tests pin.
    post_only: bool = False


class EquityMarketMaker(Agent):
    """A symmetric market maker with inventory skew and vol-adjusted spread.

    On each step, cancels existing quotes and places fresh limit orders
    on both sides with:
      - mid_price = (best_bid + best_ask) / 2  (or last_fill_price if one-sided)
      - rolling_vol_bps from MarketState (or baseline_vol_bps during warm-up)
      - vol_ratio = min(rolling_vol_bps / baseline_vol_bps, vol_ratio_cap)  (F3)
      - effective_spread = spread_target * (1 + vol_multiplier * (vol_ratio - 1))
      - half_spread = effective_spread / 2
      - skew = risk_aversion * position
      - bid_price = round(mid_price - half_spread - skew)
      - ask_price = round(mid_price + half_spread - skew)

    Only places orders if position is within inventory_limit.
    Tracks cash flow and inventory value for P&L.
    """

    def __init__(
        self,
        agent_id: str,
        config: EquityMMConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(agent_id)
        self.config = config
        self.rng = rng
        self._resting_bid_id: Optional[uuid.UUID] = None
        self._resting_ask_id: Optional[uuid.UUID] = None
        self._cash_flow: float = 0.0
        self._spread_log: list[int] = []
        self._current_mid: Optional[float] = None
        self._baseline_vol_bps: float = config.baseline_vol_bps
        self._vol_history: list[float] = []

    @property
    def cash_flow(self) -> float:
        return self._cash_flow

    @property
    def spread_log(self) -> list[int]:
        return self._spread_log

    @property
    def total_pnl(self) -> float:
        inventory_value = self.position * self._current_mid if self._current_mid else 0.0
        return self._cash_flow + inventory_value

    @property
    def avg_spread(self) -> float:
        if not self._spread_log:
            return 0.0
        return float(np.mean(self._spread_log))

    def step(self, state: MarketState) -> list[Order | Cancel]:
        actions: list[Order | Cancel] = []

        if abs(self.position) >= self.config.inventory_limit:
            if self._resting_bid_id:
                actions.append(Cancel(self._resting_bid_id, self.agent_id, state.timestamp))
                self._resting_bid_id = None
            if self._resting_ask_id:
                actions.append(Cancel(self._resting_ask_id, self.agent_id, state.timestamp))
                self._resting_ask_id = None
            return actions

        mid = state.mid
        if mid is None:
            if state.last_fill_price is None:
                # No book and no tape: there is no reference price to quote
                # around. Skip this step rather than emit a price-0 bid that
                # the LOB would reject (Audit P1-5).
                return actions
            mid = float(state.last_fill_price)

        self._current_mid = mid

        # Baseline volatility: the config seed during warm-up, then the
        # median of all observed rolling-vol readings once `vol_window`
        # of them have accumulated (Audit P0-1).
        rolling_vol = state.rolling_vol_bps
        if rolling_vol is not None:
            self._vol_history.append(rolling_vol)
            if len(self._vol_history) >= self.config.vol_window:
                self._baseline_vol_bps = float(np.median(self._vol_history))
            else:
                self._baseline_vol_bps = self.config.baseline_vol_bps
            vol_ratio = (
                rolling_vol / self._baseline_vol_bps
                if self._baseline_vol_bps > 0
                else 1.0
            )
            vol_ratio = min(vol_ratio, self.config.vol_ratio_cap)
        else:
            vol_ratio = 1.0

        effective_spread = self.config.spread_target * (1.0 + self.config.vol_multiplier * (vol_ratio - 1.0))
        effective_spread = max(1, int(round(effective_spread)))
        half_spread = effective_spread / 2.0
        skew = self.config.risk_aversion * self.position

        bid_price = int(round(mid - half_spread - skew))
        ask_price = int(round(mid + half_spread - skew))

        if bid_price >= ask_price:
            bid_price = ask_price - 1

        # F3: post-only — never cross the live BBO. The snapshot BBO may
        # include this MM's own about-to-be-cancelled quotes, which only
        # makes the clip more conservative.
        if self.config.post_only:
            if state.best_ask is not None and bid_price >= state.best_ask:
                bid_price = state.best_ask - 1
            if state.best_bid is not None and ask_price <= state.best_bid:
                ask_price = state.best_bid + 1

        # F3: a clamped quote is legal; a non-positive price is a LOB
        # ValueError. Keep 1 <= bid < ask even under extreme skew/vol.
        if bid_price < 1:
            bid_price = 1
        if ask_price <= bid_price:
            ask_price = bid_price + 1

        if self._resting_bid_id:
            actions.append(Cancel(self._resting_bid_id, self.agent_id, state.timestamp))
        if self._resting_ask_id:
            actions.append(Cancel(self._resting_ask_id, self.agent_id, state.timestamp))

        bid_order = Order(
            order_id=uuid.uuid4(),
            agent_id=self.agent_id,
            side=Side.BUY,
            price=bid_price,
            qty=self.config.quote_size,
            timestamp=state.timestamp,
        )
        ask_order = Order(
            order_id=uuid.uuid4(),
            agent_id=self.agent_id,
            side=Side.SELL,
            price=ask_price,
            qty=self.config.quote_size,
            timestamp=state.timestamp,
        )

        self._resting_bid_id = bid_order.order_id
        self._resting_ask_id = ask_order.order_id

        self._spread_log.append(ask_price - bid_price)

        actions.append(bid_order)
        actions.append(ask_order)

        # The Clock adds every submitted order to `open_order_ids`
        # (clock.py), so the MM does not duplicate that bookkeeping here.
        return actions

    def on_fills(self, fills: list[Fill]) -> None:
        super().on_fills(fills)
        for fill in fills:
            taker_mine = fill.taker_agent_id == self.agent_id
            maker_mine = fill.maker_agent_id == self.agent_id
            if not taker_mine and not maker_mine:
                continue

            if maker_mine:
                if fill.maker_order_id == self._resting_bid_id:
                    self._resting_bid_id = None
                elif fill.maker_order_id == self._resting_ask_id:
                    self._resting_ask_id = None

            if taker_mine and maker_mine:
                # Self-trade: the buy and sell legs cancel, so cash washes
                # to zero — mirror of the base-class position wash (F5).
                continue

            # Cash flow must update for every fill the MM is party to,
            # taker or maker — an inventory-skewed quote can be marketable
            # on submission, making the MM the taker (Audit P0-2).
            i_bought = (taker_mine and fill.aggressor_side is Side.BUY) or (
                maker_mine and fill.aggressor_side is Side.SELL
            )
            if i_bought:
                self._cash_flow -= fill.price * fill.qty
            else:
                self._cash_flow += fill.price * fill.qty