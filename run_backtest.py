"""
Options-overlay backtest — compare trade structures on the same momentum signal.

Replays monthly bull-call-spread vs. long-call vs. deep-ITM-call over the
options flat-file history, against SPY and the underlying stock-momentum
portfolio, at ideal and pessimistic execution costs.

Usage:
  uv run python run_backtest.py
"""

from backtest.costs import IDEAL, PESSIMISTIC
from backtest.engine import Engine, build_inputs
from backtest.metrics import (
    print_comparison,
    print_exit_reasons,
    spy_equity,
    stock_momentum_equity,
)
from backtest.strategies import MonthlyBullSpread, MonthlyDeepITMCall, MonthlyLongCall

STRATEGIES   = [MonthlyBullSpread, MonthlyLongCall, MonthlyDeepITMCall]
COST_SCENARIOS = [("Ideal fills (cost=0)", IDEAL), ("Pessimistic fills (3% + 2¢)", PESSIMISTIC)]
POOL_N    = 50          # momentum universe each month
SELECT_NS = [5, 10]     # how many to actually trade, ranked by composite score


def main():
    inputs = build_inputs()
    days   = inputs.panel.trading_days()
    print(f"Backtest window: {days[0]} … {days[-1]}  ({len(days)} trading days)")
    print(f"Pool: top {POOL_N} momentum; trade top {SELECT_NS} by composite score (R/R·IV·RSI)\n")

    # Benchmarks (cost-independent)
    spy   = spy_equity(inputs.spy, days)
    stock = stock_momentum_equity(inputs.close_wide, inputs.momentum, days, top_n=POOL_N)
    benchmarks = [
        {"label": "SPY (buy & hold)",     "equity": spy},
        {"label": f"Stock momentum {POOL_N}", "equity": stock},
    ]

    reason_sample = []
    for select_n in SELECT_NS:
        for label, cost in COST_SCENARIOS:
            engine  = Engine(inputs, cost=cost)
            results = [engine.run(S(pool_n=POOL_N, select_n=select_n)) for S in STRATEGIES]
            if not reason_sample:
                reason_sample = results

            rows = [{"label": r.name, "equity": r.equity, "positions": r.positions} for r in results]
            rows += benchmarks
            print_comparison(rows, f"Top {select_n} by score — {label}")

    print_exit_reasons(reason_sample)

    print(f"\n{'─'*108}")
    print("  CAVEATS — read before trusting any number above:")
    print("  • ~1 year of options data, a single trending regime. Suggestive, NOT conclusive.")
    print("  • No historical open-interest → liquidity proxied by entry-day volume (weaker gate).")
    print("  • No bid/ask → synthetic slippage. Trust the ideal-vs-pessimistic RANGE, not a point.")
    print("  • Mid-life marks use last trade (or BSM fallback when a contract didn't trade).")
    print("  • Sizing is fractional-contract equal-risk (idealized); real fills need round lots.")
    print(f"{'─'*108}")


if __name__ == "__main__":
    main()
