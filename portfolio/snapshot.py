"""
Headless daily NAV snapshot — records portfolio value to data/nav_history.json
without needing the dashboard open. Designed to be run once per weekday after
the close by a scheduler (launchd / cron).

  uv run python -m portfolio.snapshot

Bucket split uses your explicit Core/Momentum tags (untagged → core); the total
NAV — the thing the equity curve needs — is unaffected by tagging. Prices come
from the live snapshot feed, so schedule it ~15–20 min after the 4pm ET close
for settled marks.
"""

from app.pricing_live import price_all, price_stock_positions
from portfolio import ledger, nav, performance


def run() -> dict:
    stock_pos = ledger.open_positions("stock")
    opt_pos   = ledger.open_positions("option_spread")

    # No top-N here (would require the full momentum compute) → untagged stocks
    # default to 'core'. Period returns aren't needed for a NAV point, so pass
    # empty anchors and skip the price-history load entirely (keeps this fast).
    stocks_priced  = price_stock_positions(stock_pos, set())
    options_priced = price_all(opt_pos)
    agg = performance.aggregate(stocks_priced, options_priced, {})

    bvals = {b["name"]: b["value"] for b in agg["buckets"]}
    hist = nav.record({
        "total":    agg["total"],
        "core":     bvals.get("core", 0),
        "momentum": bvals.get("momentum", 0),
        "options":  bvals.get("options", 0),
    })
    print(f"[snapshot] {hist[-1]['date']}  total=${agg['total']:,.0f}  "
          f"({len(hist)} day(s) of history)")
    return agg


if __name__ == "__main__":
    run()
