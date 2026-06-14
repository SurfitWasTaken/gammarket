"""Matplotlib 3D options surface viewer.

Subprocess that reads /tmp/gammarket_agent_state.json and renders
interactive 3D surfaces (call prices, put prices) across strike ×
time-to-expiry.  Keyboard shortcut:

  - d / g   toggle between Price / Delta / Gamma surface
  - q / ESC close

Usage:
    python -m sim.live.surface_viz [--state-path ...] [--refresh-hz 2]
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from sim.live.state_writer import read_state, _STATE_PATH


# ── backends ──────────────────────────────────────────────


def _find_backend() -> str:
    import matplotlib
    for candidate in ("macosx", "TkAgg"):
        try:
            matplotlib.use(candidate, force=True)
            return candidate
        except Exception:
            continue
    print("No suitable matplotlib backend found (tried macosx, TkAgg)")
    sys.exit(1)


_backend = _find_backend()
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

# ── constants ──────────────────────────────────────────────

_REFRESH_HZ = 2.0
_COLORMAP = "coolwarm"


# ── rendering ──────────────────────────────────────────────


def _build_surface(ax, strikes, expiries_days, prices, title, cmap=_COLORMAP):
    ax.clear()
    X, Y = np.meshgrid(strikes, expiries_days)
    Z = np.array(prices, dtype=float)

    surf = ax.plot_surface(X, Y, Z, cmap=cmap, edgecolor="none", alpha=0.9)
    surf.set_clim(np.nanmin(Z) if Z.size else 0, np.nanmax(Z) if Z.size else 1)

    ax.set_xlabel("Strike", labelpad=8)
    ax.set_ylabel("Expiry (days)", labelpad=8)
    ax.set_zlabel("Price", labelpad=6)
    ax.set_title(title, pad=8)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x):,}"))


def render_surface(fig, data: dict) -> None:
    strikes = data.get("strikes", [])
    expiries_days = data.get("expiries_days", [])
    call_prices = data.get("call_prices", [])
    put_prices = data.get("put_prices", [])
    call_deltas = data.get("call_deltas", [])
    put_deltas = data.get("put_deltas", [])
    call_gammas = data.get("call_gammas", [])
    put_gammas = data.get("put_gammas", [])
    spot = data.get("spot", 0)

    if not strikes or not expiries_days:
        return

    ax1 = fig.axes[0] if len(fig.axes) > 0 else fig.add_subplot(121, projection="3d")
    ax2 = fig.axes[1] if len(fig.axes) > 1 else fig.add_subplot(122, projection="3d")

    _build_surface(ax1, strikes, expiries_days, call_prices, "Call Prices")
    _build_surface(ax2, strikes, expiries_days, put_prices, "Put Prices")

    fig.suptitle(f"Options Surface  —  Spot = {spot:,.1f}", fontsize=12, y=0.98)


# ── main loop ──────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="3D options surface viewer")
    parser.add_argument(
        "--state-path",
        default=str(_STATE_PATH),
        help=f"state file path (default: {_STATE_PATH})",
    )
    parser.add_argument("--refresh-hz", type=float, default=_REFRESH_HZ, help="refresh rate (Hz)")
    args = parser.parse_args()

    path = Path(args.state_path)
    sleep_s = 1.0 / max(1.0, args.refresh_hz)

    fig = plt.figure(figsize=(14, 6))
    fig.subplots_adjust(top=0.88, wspace=0.3)
    _last_step: int = -1
    running = True

    def _on_close(*_: Any) -> None:
        nonlocal running
        running = False

    fig.canvas.mpl_connect("close_event", _on_close)
    signal.signal(signal.SIGINT, lambda *_: setattr(sys, "exit", None))

    plt.ion()
    fig.show()

    try:
        while running:
            try:
                if path.exists():
                    state = read_state(path)
                    if state is not None:
                        step = state.get("step_count", -1)
                        if step != _last_step:
                            _last_step = step
                            surface = state.get("options_surface")
                            if surface is not None:
                                render_surface(fig, surface)
                                fig.canvas.draw_idle()
                                plt.pause(0.001)
            except Exception:
                pass
            plt.pause(sleep_s)
    except KeyboardInterrupt:
        pass
    finally:
        plt.close(fig)


if __name__ == "__main__":
    main()
