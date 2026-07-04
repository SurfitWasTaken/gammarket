"""In-memory per-step market-data collector (Phase 6, F1).

`MarketDataCollector` is the standard `Clock.on_step` sink: it appends
every `StepRecord` to an in-memory list and exposes the columns as NumPy
arrays on demand. It performs **zero file I/O** — callers who want to
persist a run serialise the arrays after the run completes.
"""

from __future__ import annotations

import math

import numpy as np

from sim.core.clock import StepRecord


class MarketDataCollector:
    """Accumulates `StepRecord`s and exposes NumPy column views.

    Optional fields (`mid`, `spread`, `rolling_vol_bps`, `best_bid`,
    `best_ask`, `last_fill_price`) are surfaced as float arrays with
    `np.nan` where the record held None, so downstream metrics can mask
    with `np.isfinite`.
    """

    def __init__(self) -> None:
        self.records: list[StepRecord] = []

    def __call__(self, record: StepRecord) -> None:
        """`Clock.on_step` entry point: append one record."""
        self.records.append(record)

    def __len__(self) -> int:
        return len(self.records)

    def _column(self, name: str) -> np.ndarray:
        return np.array(
            [
                math.nan if getattr(r, name) is None else float(getattr(r, name))
                for r in self.records
            ],
            dtype=np.float64,
        )

    def timestamps(self) -> np.ndarray:
        """Simulation timestamps (minutes) of every step, float64."""
        return np.array([r.timestamp for r in self.records], dtype=np.float64)

    def mids(self) -> np.ndarray:
        """Mid price per step, float64 with nan when one-sided."""
        return self._column("mid")

    def spreads(self) -> np.ndarray:
        """Quoted spread (ticks) per step, float64 with nan when one-sided."""
        return self._column("spread")

    def best_bids(self) -> np.ndarray:
        """Best bid per step, float64 with nan when the side is empty."""
        return self._column("best_bid")

    def best_asks(self) -> np.ndarray:
        """Best ask per step, float64 with nan when the side is empty."""
        return self._column("best_ask")

    def bid_depths(self) -> np.ndarray:
        """Resting quantity at the best bid per step, int64."""
        return np.array([r.bid_depth for r in self.records], dtype=np.int64)

    def ask_depths(self) -> np.ndarray:
        """Resting quantity at the best ask per step, int64."""
        return np.array([r.ask_depth for r in self.records], dtype=np.int64)

    def rolling_vols_bps(self) -> np.ndarray:
        """Rolling vol (bps) per step, float64 with nan during warm-up."""
        return self._column("rolling_vol_bps")
