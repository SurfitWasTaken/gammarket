"""Implied-volatility surfaces (Phase 4 flat, Phase 6 EWMA).

A volatility surface maps `(strike, expiry)` to an annualised σ behind the
tiny `VolSurface` protocol — the Phase 5 dealer and the chain only ever call
`vol(strike, expiry)`. Phase 4 shipped the **flat** surface; Phase 6 adds
`EwmaVolSurface` (F6), which tracks realized vol from the mid series the
dealer observes and stays flat across strikes/expiries.

σ here is a plain annualised fraction (e.g. 0.20), feeding `pricer.bs_price`
directly. Strikes are in price units (ticks, D2); expiry is whatever key a
caller indexes by — both shipped surfaces ignore the arguments.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable


@runtime_checkable
class VolSurface(Protocol):
    """The stable interface every surface implements.

    A single method so callers (chain, Phase 5 dealer) never branch on the
    surface kind. `strike` is in price units; `expiry` may be expiry-minutes,
    a series, or years — the contract only fixes that some (strike, expiry)
    pair goes in and an annualised σ comes out.
    """

    def vol(self, strike: float, expiry: float) -> float:
        """Return the annualised volatility for this (strike, expiry)."""
        ...


class FlatVolSurface:
    """Constant-σ surface: returns the same vol for every (strike, expiry).

    The Phase 4 default. Construct from config
    `agents.options_mm.vol_estimate` (or an `options.vol`).

    Args:
        sigma: The constant annualised volatility (fraction, > 0).

    Raises:
        ValueError: If `sigma <= 0`.
    """

    def __init__(self, sigma: float) -> None:
        if sigma <= 0:
            raise ValueError(f"sigma must be positive, got {sigma}")
        self._sigma: float = float(sigma)

    @property
    def sigma(self) -> float:
        """The constant volatility this surface returns."""
        return self._sigma

    def vol(self, strike: float, expiry: float) -> float:
        """Return the constant σ, ignoring `strike` and `expiry`."""
        return self._sigma

    def __repr__(self) -> str:
        return f"FlatVolSurface(sigma={self._sigma})"


class EwmaVolSurface:
    """EWMA realized-vol surface (Phase 6, F6): flat across strikes and
    expiries, but σ tracks the market.

    Maintains an EWMA of squared per-minute mid log returns; the
    annualised σ is `sqrt(var_per_min * minutes_per_year)` clamped to
    `[sigma_floor, sigma_cap]`. The decay is time-adjusted
    (`lambda ** dt`) so irregular event spacing behaves like a fixed
    per-minute λ. The owner (the dealer's `step`, F6) feeds observations
    via `update(mid, now_minutes)`; between updates the surface is a
    plain `VolSurface`.

    Args:
        sigma0: Initial annualised σ (fraction, > 0) — the config
            `vol_estimate` seed.
        ewma_lambda: Per-minute decay in (0, 1); higher = slower.
        sigma_floor: Lower clamp on the annualised σ (> 0).
        sigma_cap: Upper clamp on the annualised σ (>= sigma_floor).
        minutes_per_year: Calendar convention (D1).
    """

    def __init__(
        self,
        sigma0: float,
        *,
        ewma_lambda: float = 0.97,
        sigma_floor: float = 0.05,
        sigma_cap: float = 1.0,
        minutes_per_year: float = 525_600.0,
    ) -> None:
        if sigma0 <= 0:
            raise ValueError(f"sigma0 must be positive, got {sigma0}")
        if not 0.0 < ewma_lambda < 1.0:
            raise ValueError(f"ewma_lambda must be in (0, 1), got {ewma_lambda}")
        if sigma_floor <= 0 or sigma_cap < sigma_floor:
            raise ValueError(
                f"need 0 < sigma_floor <= sigma_cap, got "
                f"[{sigma_floor}, {sigma_cap}]"
            )
        if minutes_per_year <= 0:
            raise ValueError(
                f"minutes_per_year must be positive, got {minutes_per_year}"
            )
        self._lam = float(ewma_lambda)
        self._floor = float(sigma_floor)
        self._cap = float(sigma_cap)
        self._mpy = float(minutes_per_year)
        self._var_per_min: float = float(sigma0) ** 2 / self._mpy
        self._last_mid: float | None = None
        self._last_now: float = 0.0

    @property
    def sigma(self) -> float:
        """Current annualised σ, clamped to `[sigma_floor, sigma_cap]`."""
        raw = math.sqrt(self._var_per_min * self._mpy)
        return min(max(raw, self._floor), self._cap)

    def vol(self, strike: float, expiry: float) -> float:
        """Return the current σ, ignoring `strike` and `expiry`."""
        return self.sigma

    def update(self, mid: float, now_minutes: float) -> None:
        """Fold one mid observation into the EWMA.

        Non-positive mids are ignored; the first observation (and any
        non-advancing timestamp) only records state, updating nothing.

        Args:
            mid: Underlying mid/spot in price units.
            now_minutes: Clock minutes of the observation.
        """
        if mid <= 0:
            return
        if self._last_mid is None or now_minutes <= self._last_now:
            self._last_mid = float(mid)
            self._last_now = float(now_minutes)
            return
        dt = now_minutes - self._last_now
        r = math.log(mid / self._last_mid)
        var_obs_per_min = (r * r) / dt
        w = self._lam**dt
        self._var_per_min = w * self._var_per_min + (1.0 - w) * var_obs_per_min
        self._last_mid = float(mid)
        self._last_now = float(now_minutes)

    def __repr__(self) -> str:
        return (
            f"EwmaVolSurface(sigma={self.sigma:.4f}, lambda={self._lam}, "
            f"clamp=[{self._floor}, {self._cap}])"
        )


def surface_from_config(
    mm_cfg: dict, minutes_per_year: float
) -> VolSurface:
    """Build the dealer's surface from `agents.options_mm` config (F6).

    `surface_mode: flat` (default) -> `FlatVolSurface(vol_estimate)`;
    `surface_mode: ewma` -> `EwmaVolSurface` seeded from `vol_estimate`
    with `ewma_lambda` / `sigma_floor` / `sigma_cap` keys. This is the
    single construction switch shared by `run_sim` and `sim/live`.

    Raises:
        ValueError: On an unknown `surface_mode`.
    """
    mode = str(mm_cfg.get("surface_mode", "flat"))
    sigma0 = float(mm_cfg["vol_estimate"])
    if mode == "flat":
        return FlatVolSurface(sigma0)
    if mode == "ewma":
        return EwmaVolSurface(
            sigma0,
            ewma_lambda=float(mm_cfg.get("ewma_lambda", 0.97)),
            sigma_floor=float(mm_cfg.get("sigma_floor", 0.05)),
            sigma_cap=float(mm_cfg.get("sigma_cap", 1.0)),
            minutes_per_year=minutes_per_year,
        )
    raise ValueError(f"unknown surface_mode {mode!r} (expected flat|ewma)")
