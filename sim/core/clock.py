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
from typing import Callable, Optional

import numpy as np

from sim.agents.base import Agent, MarketState
from sim.core.events import Cancel, Order, Side
from sim.core.lob import LimitOrderBook
from sim.core.tape import Tape


@dataclass(frozen=True)
class StepRecord:
    """Per-step market snapshot emitted through `Clock.on_step` (F1).

    Captured at the end of every `Clock.step()`, after the stepping
    agent's actions have been applied to the book. This is the only
    place mid/spread/depth exist as a time series — `MarketState` is
    transient and the tape records fills only.

    Args:
        step: 1-based step counter (equals `Clock.step_count`).
        timestamp: Simulation time in minutes.
        best_bid: Best bid in ticks, or None if the bid side is empty.
        best_ask: Best ask in ticks, or None if the ask side is empty.
        mid: (best_bid + best_ask) / 2, or None if one-sided.
        spread: best_ask - best_bid, or None if one-sided.
        bid_depth: Resting quantity at the best bid price level (0 if none).
        ask_depth: Resting quantity at the best ask price level (0 if none).
        last_fill_price: Most recent fill price, or None.
        rolling_vol_bps: Rolling volatility in bps, or None during warm-up.
    """

    step: int
    timestamp: float
    best_bid: Optional[int]
    best_ask: Optional[int]
    mid: Optional[float]
    spread: Optional[int]
    bid_depth: int
    ask_depth: int
    last_fill_price: Optional[int]
    rolling_vol_bps: Optional[float]


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
        on_step: Optional callback fired once at the end of every
            `step()` with a `StepRecord` snapshot (F1). None (the
            default) changes nothing — all pre-Phase-6 call sites.
    """

    def __init__(
        self,
        book: LimitOrderBook,
        tape: Tape,
        rng: np.random.Generator,
        vol_window: int = 20,
        on_step: Optional[Callable[[StepRecord], None]] = None,
    ) -> None:
        if vol_window < 2:
            raise ValueError(f"vol_window must be >= 2, got {vol_window}")
        self.book: LimitOrderBook = book
        self.tape: Tape = tape
        self.rng: np.random.Generator = rng
        self.vol_window: int = vol_window
        self.on_step: Optional[Callable[[StepRecord], None]] = on_step
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
        if self.on_step is not None:
            self.on_step(self._build_step_record())
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

    def _rolling_vol_bps(self, mid: Optional[float]) -> Optional[float]:
        """Rolling volatility of fill-to-fill returns in bps, or None
        during warm-up / without a reference mid."""
        if mid is None or mid <= 0:
            return None
        # Only the last `vol_window` fills matter, so slice the tape tail
        # instead of materialising every price — O(window) per step, not
        # O(fills) (F4; numerically identical to the full scan).
        recent = self.tape.fills[-self.vol_window :]
        if len(recent) < 2:
            return None
        window = np.fromiter(
            (f.price for f in recent), dtype=np.float64, count=len(recent)
        )
        # Returns are already fractional (Δprice / price), so the
        # std is a fraction of price; * 1e4 converts directly to bps.
        returns = np.diff(window) / window[:-1]
        return float(np.std(returns)) * 10_000.0

    def _build_step_record(self) -> StepRecord:
        bb, ba = self.book.best_bid(), self.book.best_ask()
        mid = self.book.mid()
        return StepRecord(
            step=self.step_count,
            timestamp=self.now,
            best_bid=bb,
            best_ask=ba,
            mid=mid,
            spread=(ba - bb) if bb is not None and ba is not None else None,
            bid_depth=self.book.depth(Side.BUY, bb) if bb is not None else 0,
            ask_depth=self.book.depth(Side.SELL, ba) if ba is not None else 0,
            last_fill_price=self.tape.last_fill_price(),
            rolling_vol_bps=self._rolling_vol_bps(mid),
        )

    def _build_state(self, agent: Agent) -> MarketState:
        mid = self.book.mid()
        rolling_vol_bps = self._rolling_vol_bps(mid)
        return MarketState(
            best_bid=self.book.best_bid(),
            best_ask=self.book.best_ask(),
            mid=mid,
            last_fill_price=self.tape.last_fill_price(),
            own_position=agent.position,
            timestamp=self.now,
            rolling_vol_bps=rolling_vol_bps,
        )
