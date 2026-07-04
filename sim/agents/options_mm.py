"""Options dealer agent (Phase 5): BS quoting + delta hedging.

The dealer quotes two-sided option markets off the Phase 4 library
(`sim/options/`) and keeps an option position book in **contracts**
(one contract = one lot of underlying, E2). Option trades arrive via
`on_option_trade` (the quote-driven E1 trigger — there is no options
LOB); after every trade, and at every dealer step, the dealer recomputes
its portfolio delta in lots and emits an equity **market** order to
flatten it (E2/E3). The Phase 5 unit contracts (E1–E6) are frozen in
CLAUDE.md — this module implements them; do not re-derive conversions
elsewhere.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Optional

import numpy as np

from sim.agents.base import Agent, MarketState
from sim.core.events import Cancel, Fill, Order, Side
from sim.options.chain import OptionSeries, spot_from_book, time_to_expiry_years
from sim.options.pricer import Greeks, bs_greeks, bs_price
from sim.options.surface import VolSurface


@dataclass(frozen=True)
class OptionsMMConfig:
    """Configuration for the options dealer.

    Args:
        arrival_rate: Clock registration rate (dealer re-hedge checks
            per minute).
        vol_estimate: Annualised σ seeding the dealer's vol surface
            (informational here; the surface itself is injected).
        spread_vols: Quote half-width in **vol points** (1 point = 0.01
            of annualised σ, E4). Bid priced at σ − spread_vols·0.01,
            ask at σ + spread_vols·0.01.
        delta_hedge_threshold: Hedge only when |net delta| exceeds this
            (in lots, E3). Values below the 0.5-lot quantisation floor
            gate nothing extra (E2).
        gamma_limit: Portfolio gamma cap in lots/tick (E5). Trades that
            would push |gamma| past it (and increase it) are refused.
        option_tick: Tradable grid for option quotes (E4). Bid floored,
            ask ceiled to this grid.
    """

    arrival_rate: float
    vol_estimate: float
    spread_vols: float
    delta_hedge_threshold: float
    gamma_limit: float
    option_tick: int = 1


@dataclass(frozen=True)
class OptionTrade:
    """One executed option trade against the dealer's quote.

    Args:
        series: The traded option series.
        side: The **taker's** side (E1): BUY lifted the dealer's ask
            (dealer sold), SELL hit the dealer's bid (dealer bought).
        qty: Contracts traded. Always positive.
        price: Execution price — the dealer's quote on the traded side,
            in ticks per share (option_tick grid).
        timestamp: Clock minutes at execution.
    """

    series: OptionSeries
    side: Side
    qty: int
    price: int
    timestamp: float


@dataclass
class HedgeRecord:
    """One hedge cycle: the delta seen, the order sent, the fills back.

    Args:
        order_id: The equity market order's id (matches fills).
        timestamp: Clock minutes when the hedge was emitted.
        pre_delta_lots: Net delta (lots) that triggered the hedge.
        intended_qty_lots: Signed hedge quantity, `round(-pre_delta)`.
        filled_qty_lots: Signed lots actually filled so far (updated by
            `on_fills`; surplus may rest and fill later).
    """

    order_id: uuid.UUID
    timestamp: float
    pre_delta_lots: float
    intended_qty_lots: int
    filled_qty_lots: int = 0


class OptionsMarketMaker(Agent):
    """Quote-driven options dealer with a delta-hedging equity leg.

    Holds a fixed chain (E6), a vol surface, and an option position book
    in contracts. `quote` prices a two-sided market (E4);
    `on_option_trade` executes against it, enforces the gamma cap (E5),
    and returns the equity hedge order (E2/E3); `step` re-hedges on
    underlying drift. All equity orders carry this agent's `agent_id` so
    the Clock routes their fills here even when the flow agent submits
    them (E1 owner-routing).

    Args:
        agent_id: Stable identifier used in events and fills.
        config: Frozen `OptionsMMConfig`.
        rng: NumPy `Generator`; reserved (the dealer is deterministic),
            kept for constructor parity with the other agents.
        chain: The tradable `OptionSeries` list, built once (E6).
        surface: Vol surface; `vol(strike, expiry_minutes)` → σ.
        risk_free_rate: Continuously-compounded annual rate for BS.
        minutes_per_year: D1 calendar constant (`market.minutes_per_year`).
        tick_size: Equity book tick size (D2 spot conversion).
    """

    def __init__(
        self,
        agent_id: str,
        config: OptionsMMConfig,
        rng: np.random.Generator,
        *,
        chain: list[OptionSeries],
        surface: VolSurface,
        risk_free_rate: float,
        minutes_per_year: float,
        tick_size: int,
    ) -> None:
        super().__init__(agent_id)
        if not chain:
            raise ValueError("chain must be non-empty")
        if minutes_per_year <= 0:
            raise ValueError(f"minutes_per_year must be positive, got {minutes_per_year}")
        if tick_size <= 0:
            raise ValueError(f"tick_size must be positive, got {tick_size}")
        if config.option_tick <= 0:
            raise ValueError(f"option_tick must be positive, got {config.option_tick}")
        self.config = config
        self.rng = rng
        self.chain: list[OptionSeries] = list(chain)
        self.surface: VolSurface = surface
        self.risk_free_rate: float = risk_free_rate
        self.minutes_per_year: float = minutes_per_year
        self.tick_size: int = tick_size
        self._option_positions: dict[OptionSeries, int] = {}
        self._trade_log: list[OptionTrade] = []
        self._hedge_log: list[HedgeRecord] = []
        self._pending_hedges: dict[uuid.UUID, HedgeRecord] = {}
        self._option_cash_flow: float = 0.0
        self._gamma_rejections: int = 0

    # --- read-only diagnostics ------------------------------------------------

    @property
    def option_positions(self) -> dict[OptionSeries, int]:
        """Copy of the option book: series → signed contracts (+ = long)."""
        return dict(self._option_positions)

    @property
    def trade_log(self) -> list[OptionTrade]:
        """Chronological executed option trades."""
        return self._trade_log

    @property
    def hedge_log(self) -> list[HedgeRecord]:
        """Chronological hedge cycles (pre-delta, intended, filled)."""
        return self._hedge_log

    @property
    def option_cash_flow(self) -> float:
        """Signed premium received, per-share ticks × contracts (E4).

        Multiply by `lot_size` for cash units; diagnostic only.
        """
        return self._option_cash_flow

    @property
    def gamma_rejections(self) -> int:
        """Count of option trades refused by the gamma cap (E5)."""
        return self._gamma_rejections

    # --- pricing --------------------------------------------------------------

    def _series_greeks(self, series: OptionSeries, spot: float, now: float) -> Greeks:
        """Greeks of one series at the mid σ from the surface."""
        T = time_to_expiry_years(series, now, self.minutes_per_year)
        sigma = self.surface.vol(series.strike, series.expiry_minutes)
        return bs_greeks(
            spot, series.strike, T, self.risk_free_rate, sigma, is_call=series.is_call
        )

    def quote(self, series: OptionSeries, spot: float, now: float) -> tuple[int, int]:
        """Two-sided option quote on the option_tick grid (E4).

        Bid is priced at σ − spread_vols·0.01 and floored to the grid;
        ask at σ + spread_vols·0.01 and ceiled. Bid is floored at 0 and
        the ask is bumped one tick if rounding collapses the spread.

        Args:
            series: The series to quote.
            spot: Underlying spot `S` (from `spot_from_book`, D2).
            now: Clock minutes.

        Returns:
            `(bid, ask)` in integer ticks per share, `ask > bid >= 0`.
        """
        T = time_to_expiry_years(series, now, self.minutes_per_year)
        sigma = self.surface.vol(series.strike, series.expiry_minutes)
        bump = self.config.spread_vols * 0.01
        bid_f = bs_price(
            spot, series.strike, T, self.risk_free_rate,
            max(sigma - bump, 0.0), is_call=series.is_call,
        )
        ask_f = bs_price(
            spot, series.strike, T, self.risk_free_rate,
            sigma + bump, is_call=series.is_call,
        )
        tick = self.config.option_tick
        bid = max(0, int(math.floor(bid_f / tick)) * tick)
        ask = int(math.ceil(ask_f / tick)) * tick
        if ask <= bid:
            ask = bid + tick
        return bid, ask

    # --- E2 single conversion sites -------------------------------------------

    def net_delta_lots(self, spot: float, now: float) -> float:
        """Portfolio delta in lots (E2 — the single contract→lot site).

        `Σ contracts × bs_delta` plus the equity hedge inventory at
        +1 delta per long lot, so the hedge loop converges.

        Args:
            spot: Underlying spot `S`.
            now: Clock minutes.

        Returns:
            Signed net delta in lots.
        """
        total = float(self.position)
        for series, contracts in self._option_positions.items():
            total += contracts * self._series_greeks(series, spot, now).delta
        return total

    def portfolio_gamma(self, spot: float, now: float) -> float:
        """Portfolio gamma in lots/tick (E5; equity leg has zero gamma).

        Args:
            spot: Underlying spot `S`.
            now: Clock minutes.

        Returns:
            Signed portfolio gamma.
        """
        total = 0.0
        for series, contracts in self._option_positions.items():
            total += contracts * self._series_greeks(series, spot, now).gamma
        return total

    # --- trading + hedging ----------------------------------------------------

    def on_option_trade(
        self, series: OptionSeries, side: Side, qty: int, spot: float, now: float
    ) -> list[Order]:
        """Execute a taker trade against the dealer's quote (E1 trigger).

        Enforces the gamma cap (E5), updates the option book and premium
        cash flow at the quoted price (E4), then re-hedges (E3).

        Args:
            series: The traded series (must be in the dealer's chain).
            side: The taker's side — BUY lifts the ask (dealer sells),
                SELL hits the bid (dealer buys).
            qty: Contracts; must be positive.
            spot: Underlying spot `S` at trade time.
            now: Clock minutes.

        Returns:
            The equity hedge `Order` list (empty if refused, gated, or
            the hedge quantity rounds to zero).
        """
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        contracts_delta = -qty if side is Side.BUY else qty

        greeks = self._series_greeks(series, spot, now)
        current_gamma = self.portfolio_gamma(spot, now)
        new_gamma = current_gamma + contracts_delta * greeks.gamma
        if abs(new_gamma) > self.config.gamma_limit and abs(new_gamma) > abs(current_gamma):
            self._gamma_rejections += 1
            return []

        bid, ask = self.quote(series, spot, now)
        price = ask if side is Side.BUY else bid
        new_position = self._option_positions.get(series, 0) + contracts_delta
        if new_position == 0:
            self._option_positions.pop(series, None)
        else:
            self._option_positions[series] = new_position
        # Dealer sells on a taker BUY (premium in), buys on a taker SELL.
        self._option_cash_flow += price * qty if side is Side.BUY else -price * qty
        self._trade_log.append(
            OptionTrade(series=series, side=side, qty=qty, price=price, timestamp=now)
        )
        return self._hedge(spot, now)

    def _hedge(self, spot: float, now: float) -> list[Order]:
        """Build the equity market order flattening net delta (E2/E3).

        Returns an empty list when |delta| is within the threshold or
        the hedge quantity rounds to zero (no zero-qty orders).
        """
        net_delta = self.net_delta_lots(spot, now)
        if abs(net_delta) <= self.config.delta_hedge_threshold:
            return []
        hedge_qty = round(-net_delta)
        if hedge_qty == 0:
            return []
        order = Order(
            order_id=uuid.uuid4(),
            agent_id=self.agent_id,
            side=Side.BUY if hedge_qty > 0 else Side.SELL,
            price=0,
            qty=abs(hedge_qty),
            timestamp=now,
            is_market=True,
        )
        record = HedgeRecord(
            order_id=order.order_id,
            timestamp=now,
            pre_delta_lots=net_delta,
            intended_qty_lots=hedge_qty,
        )
        self._hedge_log.append(record)
        self._pending_hedges[order.order_id] = record
        return [order]

    def step(self, state: MarketState) -> list[Order | Cancel]:
        """Re-hedge against underlying drift at the dealer's own events (E3).

        Mirrors equity_mm Audit P1-5: with no mid and no last fill there
        is no reference price, so no hedge is emitted.
        """
        mid: Optional[float] = state.mid
        if mid is None:
            if state.last_fill_price is None:
                return []
            mid = float(state.last_fill_price)
        spot = spot_from_book(mid, self.tick_size)
        # F6: a dynamic surface folds the live mid in before this step's
        # pricing; the flat surface has no `update` and is left alone.
        update = getattr(self.surface, "update", None)
        if update is not None:
            update(spot, state.timestamp)
        return self._hedge(spot, state.timestamp)

    def on_fills(self, fills: list[Fill]) -> None:
        """Track realized hedge quantities on top of base position updates.

        A hedge's surplus may rest in the book (LOB market-order
        behaviour), so its order_id can come back as the maker side of a
        later fill; both directions accumulate into the hedge record.
        """
        super().on_fills(fills)
        for fill in fills:
            taker_mine = fill.taker_agent_id == self.agent_id
            maker_mine = fill.maker_agent_id == self.agent_id
            if not taker_mine and not maker_mine:
                continue
            order_id = fill.taker_order_id if taker_mine else fill.maker_order_id
            record = self._pending_hedges.get(order_id)
            if record is None:
                continue
            i_bought = (taker_mine and fill.aggressor_side is Side.BUY) or (
                maker_mine and fill.aggressor_side is Side.SELL
            )
            record.filled_qty_lots += fill.qty if i_bought else -fill.qty
