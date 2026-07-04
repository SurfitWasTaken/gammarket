"""Abstract base class for simulated agents and the `MarketState` snapshot.

Every concrete agent (retail, institution, market makers in later
phases) inherits from `Agent` and implements `step(state) -> list[Order]`.
The clock owns the loop and routes fills back to the right agent via
`on_fills`.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Union

from sim.core.events import Cancel, Fill, Order, Side


@dataclass(frozen=True)
class MarketState:
    """A read-only snapshot of market + agent state at one instant.

    Built by the clock on every step and passed to `agent.step`. The
    clock decides whether to compute a `mid` from the live book or fall
    back to the most recent fill price; `mid` may legitimately be None
    when the book is one-sided.

    Args:
        best_bid: Best bid price in ticks, or None if no bids.
        best_ask: Best ask price in ticks, or None if no asks.
        mid: (best_bid + best_ask) / 2 in ticks, or None.
        last_fill_price: Price of the most recent fill, or None.
        own_position: This agent's net position in lots.
        timestamp: Simulation time in minutes (clock unit).
        rolling_vol_bps: Recent return volatility in basis points of mid,
            or None during warm-up.
    """

    best_bid: Optional[int]
    best_ask: Optional[int]
    mid: Optional[float]
    last_fill_price: Optional[int]
    own_position: int
    timestamp: float
    rolling_vol_bps: Optional[float] = None


class Agent(ABC):
    """Abstract simulation agent.

    Agents are stateful objects that perceive, decide, and act. The clock
    drives them: at each scheduled event time, the clock builds a
    `MarketState` and calls `step(state)`; the agent returns a list of
    `Order` events which the clock submits to the LOB. Any resulting
    fills are routed back to the agent via `on_fills` (and also to the
    resting-side agent on the other side of each fill).

    Position tracking is centralised in the base class. Subclasses do
    not need to maintain their own position counter; they only need to
    decide which orders to emit.
    """

    agent_id: str
    position: int
    open_order_ids: set[uuid.UUID]

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.position = 0
        self.open_order_ids = set()

    @abstractmethod
    def step(self, state: MarketState) -> list[Union[Order, Cancel]]:
        """Perceive the state, decide, and return actions to take.

        Each action is either an `Order` (to be submitted to the LOB)
        or a `Cancel` (to remove a previously-submitted resting order).
        Actions are processed by the clock in the order returned. The
        combined list may be empty.
        """

    def on_fills(self, fills: list[Fill]) -> None:
        """Update position and clear filled order_ids from bookkeeping.

        Default implementation: for every fill, check whether this agent
        is the taker or the maker; if so, mutate `self.position` by
        `+/- qty` according to the side, and drop the order_id from
        `open_order_ids`. Subclasses may extend (e.g. to log a custom
        event) but should call `super().on_fills(fills)`.
        """
        for fill in fills:
            taker_mine = fill.taker_agent_id == self.agent_id
            maker_mine = fill.maker_agent_id == self.agent_id
            if not taker_mine and not maker_mine:
                continue
            if taker_mine and maker_mine:
                # Self-trade: the agent is both sides of the fill, so the
                # net position change is zero (F5) — only clear bookkeeping.
                self.open_order_ids.discard(fill.taker_order_id)
                self.open_order_ids.discard(fill.maker_order_id)
                continue
            i_bought = (taker_mine and fill.aggressor_side is Side.BUY) or (
                maker_mine and fill.aggressor_side is Side.SELL
            )
            if i_bought:
                self.position += fill.qty
            else:
                self.position -= fill.qty
            if taker_mine:
                self.open_order_ids.discard(fill.taker_order_id)
            if maker_mine:
                self.open_order_ids.discard(fill.maker_order_id)
