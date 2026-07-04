"""End-to-end Phase 5: the delta-hedging feedback loop.

Runs the full market — retail, institution, two equity MMs, the options
dealer, and the options-demand flow — and asserts the Phase 5 definition
of done: option fills occurred, the dealer's hedges hit the equity book
(the book reacts), and after each hedge cycle the dealer's net delta is
within `max(delta_hedge_threshold, 0.5)` of zero (the E2 quantisation
bound recorded in CLAUDE.md).

`test_e2e_phase2.py` is frozen; this file owns the Phase 5 assertions.
"""

from __future__ import annotations

import pytest

from run_sim import run
from sim.agents.options_mm import OptionsMarketMaker
from sim.options.chain import spot_from_book


def make_config(max_steps=600, seed=42):
    return {
        "market": {
            "tick_size": 1,
            "lot_size": 100,
            "initial_price": 10_000,
            "initial_bid_size": 200,
            "initial_ask_size": 200,
            "max_steps": max_steps,
            "seed": seed,
            "vol_window": 20,
            "minutes_per_year": 525_600,
        },
        "agents": {
            "retail": {
                "n_agents": 10,
                "arrival_rate": 10.0,
                "order_size_mean": 2,
                "direction_bias": 0.0,
            },
            "institution": {
                "arrival_rate": 5.0,
                "signal_halflife": 30.0,
                "signal_sigma": 1.0,
                "threshold": 0.0,
                "position_limit": 500,
                "quote_offset_ticks": 1,
                "scale": 100,
                "signal_price_scale": 5,
            },
            "equity_mms": [
                {
                    "id": "mm_aggressive",
                    "arrival_rate": 100.0,
                    "spread_target": 3,
                    "inventory_limit": 2000,
                    "risk_aversion": 0.05,
                    "quote_size": 5,
                    "max_orders_per_side": 1,
                    "vol_window": 20,
                    "vol_multiplier": 2.0,
                    "baseline_vol_bps": 5.0,
                },
                {
                    "id": "mm_conservative",
                    "arrival_rate": 100.0,
                    "spread_target": 5,
                    "inventory_limit": 2000,
                    "risk_aversion": 0.1,
                    "quote_size": 5,
                    "max_orders_per_side": 1,
                    "vol_window": 20,
                    "vol_multiplier": 2.0,
                    "baseline_vol_bps": 5.0,
                },
            ],
            "options_mm": {
                "arrival_rate": 20.0,
                "vol_estimate": 0.20,
                "spread_vols": 2.0,
                "delta_hedge_threshold": 0.05,
                "gamma_limit": 500,
                "option_tick": 1,
            },
            "options_flow": {
                "arrival_rate": 5.0,
                "max_lots": 3,
            },
        },
        "options": {
            "strikes_pct": [-0.05, -0.025, 0.0, 0.025, 0.05],
            "expiries_days": [7, 14, 30],
            "risk_free_rate": 0.05,
        },
    }


@pytest.fixture(scope="module")
def result():
    return run(make_config())


def get_dealer(result):
    return next(a for a in result["agents"] if isinstance(a, OptionsMarketMaker))


class TestPhase5EndToEnd:
    def test_option_fills_occurred(self, result):
        dealer = get_dealer(result)
        assert len(dealer.trade_log) > 0
        assert all(t.qty >= 1 for t in dealer.trade_log)
        assert dealer.option_positions or dealer.trade_log  # book may net to flat

    def test_dealer_hedges_hit_the_equity_book(self, result):
        dealer = get_dealer(result)
        assert len(dealer.hedge_log) > 0
        hedge_fills = [
            f for f in result["tape"].fills if f.taker_agent_id == dealer.agent_id
        ]
        assert hedge_fills, "hedge market orders must generate equity fills"
        # The book reacted: the dealer holds real equity inventory from
        # those fills, exactly the sum of realized hedge quantities.
        filled = sum(r.filled_qty_lots for r in dealer.hedge_log)
        assert dealer.position == filled
        assert dealer.position != 0

    def test_net_delta_within_bound_after_each_hedge_cycle(self, result):
        """The Phase 5 DoD: post-hedge delta within the E2 bound."""
        dealer = get_dealer(result)
        bound = max(dealer.config.delta_hedge_threshold, 0.5)
        assert dealer.hedge_log, "the run must produce hedge cycles"
        for record in dealer.hedge_log:
            assert record.filled_qty_lots == record.intended_qty_lots, (
                f"hedge at t={record.timestamp} only filled "
                f"{record.filled_qty_lots}/{record.intended_qty_lots} lots"
            )
            post_delta = record.pre_delta_lots + record.filled_qty_lots
            assert abs(post_delta) <= bound

    def test_final_net_delta_is_flat_modulo_drift(self, result):
        """At run end the dealer is hedged at the final mid, within the
        quantisation floor plus a drift allowance (the underlying moves
        between the dealer's last hedge check and the final event).

        The allowance is a path-dependent heuristic, not the E2/E3
        contract (which `test_net_delta_within_bound_after_each_hedge_
        cycle` pins per cycle). It was originally tuned at 1.0 on a
        trajectory where the equity MMs carried phantom self-trade
        inventory; the Phase 6 F5 wash fix corrected their positions and
        shifted the path, so the same seed now ends with more late-run
        drift. 2.0 lots = the 0.5 quantisation floor + 1.5 lots of
        gamma-driven drift headroom.
        """
        dealer = get_dealer(result)
        mid = result["book"].mid()
        assert mid is not None
        spot = spot_from_book(float(mid), dealer.tick_size)
        net = dealer.net_delta_lots(spot, float(result["clock"].now))
        assert abs(net) <= 2.0

    def test_deterministic_under_fixed_seed(self):
        a = run(make_config(max_steps=300))
        b = run(make_config(max_steps=300))
        dealer_a, dealer_b = get_dealer(a), get_dealer(b)
        assert [
            (t.series, t.side, t.qty, t.price) for t in dealer_a.trade_log
        ] == [(t.series, t.side, t.qty, t.price) for t in dealer_b.trade_log]
        assert dealer_a.position == dealer_b.position

    def test_phase3_agents_unaffected(self, result):
        """The equity-only market still functions around the dealer."""
        fills = result["tape"].fills
        equity_agents = {f.taker_agent_id for f in fills} | {
            f.maker_agent_id for f in fills
        }
        assert any(a.startswith("retail") for a in equity_agents)
        assert any(a.startswith("mm_") for a in equity_agents)
