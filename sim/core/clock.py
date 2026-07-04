"""Discrete event scheduler for the market simulation.

Uses a `heapq` priority queue. Each event records `(timestamp, seq)`
so ties are broken deterministically by insertion order. Agents
register with a Poisson arrival rate; the clock draws a fresh
exponential inter-arrival time when the agent's current event fires
and pushes a new event at `now + dt`.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import numpy as np

from sim.agents.base import Agent, MarketState
from sim.core.events import Cancel, Order
from sim.core.lob import LimitOrderBook
from sim.core.tape import Tape


@dataclass(order=True)
class _Event:
    """A scheduled agent step. Comparison is by (time, seq); `agent_id`
    is a lookup key only and is excluded from comparison."""

    time: float
    seq: int
    agent_id: str = field(compare=False)


class Clock:
    """Discrete event scheduler.

    Args:
        book: The LOB to submit orders into. `on_fill` on the book (if
            set) is the source of the fill log; the clock does not
            re-invoke the tape.
        tape: The fill log; used to look up `last_fill_price` and compute
            rolling volatility for the `MarketState` snapshot.
        rng: NumPy `Generator` for drawing exponential inter-arrival
            times. Passing a seeded generator makes the schedule
            deterministic.
        vol_window: Number of recent fills to use for rolling volatility
            calculation. Must be >= 2.
    """

    def __init__(
        self,
        book: LimitOrderBook,
        tape: Tape,
        rng: np.random.Generator,
        vol_window: int = 20,
    ) -> None:
        if vol_window < 2:
            raise ValueError(f"vol_window must be >= 2, got {vol_window}")
        self.book: LimitOrderBook = book
        self.tape: Tape = tape
        self.rng: np.random.Generator = rng
        self.vol_window: int = vol_window
        self.agents: dict[str, Agent] = {}
        self.rates: dict[str, float] = {}
        self._heap: list[_Event] = []
        self._seq: int = 0
        self.now: float = 0.0
        self.step_count: int = 0

    def register(self, agent: Agent, rate_per_min: float) -> None:
        """Register `agent` with a Poisson arrival rate of `rate_per_min`
        events per minute. The first event is scheduled at
        `t = Exp(rate_per_min)` minutes from `self.now`."""
        if rate_per_min <= 0:
            raise ValueError(f"rate_per_min must be positive, got {rate_per_min}")
        if agent.agent_id in self.agents:
            raise KeyError(f"agent_id {agent.agent_id!r} already registered")
        self.agents[agent.agent_id] = agent
        self.rates[agent.agent_id] = rate_per_min
        dt = self.rng.exponential(1.0 / rate_per_min)
        self._schedule_next(agent, dt=dt)

    def step(self) -> float:
        """Pop the next event, run the agent, route fills, and
        reschedule. Returns the new simulation time (the event's
        timestamp). Raises `StopIteration` when the heap is empty."""
        if not self._heap:
            raise StopIteration("event heap is empty")
        event = heapq.heappop(self._heap)
        self.now = event.time
        self.step_count += 1
        agent = self.agents[event.agent_id]
        state = self._build_state(agent)
        actions = agent.step(state)
        for action in actions:
            # An action is credited to its *owning* agent (the action's
            # agent_id), not necessarily the stepping agent: the Phase 5
            # options flow returns the dealer's hedge orders from its own
            # step (E1 owner-routing). For every agent that emits only its
            # own actions, owner == agent, so behaviour is unchanged.
            owner = self.agents.get(action.agent_id, agent)
            if isinstance(action, Order):
                owner.open_order_ids.add(action.order_id)
                if action.is_market:
                    fills = self.book.submit_market(action)
                else:
                    fills = self.book.submit_limit(action)
                if not fills:
                    continue
                owner.on_fills(fills)
                for fill in fills:
                    if fill.maker_agent_id == fill.taker_agent_id:
                        continue
                    maker_agent = self.agents.get(fill.maker_agent_id)
                    if maker_agent is not None and maker_agent is not owner:
                        maker_agent.on_fills([fill])
            elif isinstance(action, Cancel):
                self.book.cancel(action.order_id)
                owner.open_order_ids.discard(action.order_id)
        rate = self.rates[event.agent_id]
        self._schedule_next(agent, dt=self.rng.exponential(1.0 / rate))
        return self.now

    def run(self, max_steps: int) -> float:
        """Run up to `max_steps` events. Returns the final `self.now`."""
        for _ in range(max_steps):
            self.step()
        return self.now

    def _schedule_next(self, agent: Agent, *, dt: float) -> None:
        self._seq += 1
        heapq.heappush(
            self._heap,
            _Event(time=self.now + dt, seq=self._seq, agent_id=agent.agent_id),
        )

    def _build_state(self, agent: Agent) -> MarketState:
        mid = self.book.mid()
        rolling_vol_bps: float | None = None
        if mid is not None and mid > 0:
            # Only the last `vol_window` fills matter, so slice the tape tail
            # instead of materialising every price — O(window) per step, not
            # O(fills) (F4; numerically identical to the full scan).
            recent = self.tape.fills[-self.vol_window :]
            if len(recent) >= 2:
                window = np.fromiter(
                    (f.price for f in recent), dtype=np.float64, count=len(recent)
                )
                # Returns are already fractional (Δprice / price), so the
                # std is a fraction of price; * 1e4 converts directly to bps.
                returns = np.diff(window) / window[:-1]
                rolling_vol_bps = float(np.std(returns)) * 10_000.0
        return MarketState(
            best_bid=self.book.best_bid(),
            best_ask=self.book.best_ask(),
            mid=mid,
            last_fill_price=self.tape.last_fill_price(),
            own_position=agent.position,
            timestamp=self.now,
            rolling_vol_bps=rolling_vol_bps,
        )
