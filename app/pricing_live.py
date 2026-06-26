"""
Live re-pricing of open positions + exit evaluation.

Held contracts age below the screener's 30-DTE window, so we fetch by the
position's OWN expiry/strikes (not the screener range). Reuses daily_trader's
API helpers and the SHARED exit rules in strategy/exits.py.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import parse_qs, urlparse

from daily_trader import _parse_leg, api_get, fetch_stock_prices
from strategy.exits import ExitRules, evaluate_exit

RULES = ExitRules()

ACTION_LABELS = {
    "hold":          "HOLD",
    "profit_target": "CLOSE · profit target",
    "stop":          "CLOSE · stop loss",
    "time":          "CLOSE · 7 DTE",
    "unknown":       "?",
}


def _fetch_calls_for_expiry(ticker: str, expiry: str) -> list[dict]:
    """All call snapshots for one underlying at one expiry (paginated)."""
    params = {"expiration_date": expiry, "contract_type": "call", "limit": 250}
    path, out = f"/v3/snapshot/options/{ticker}", []
    while path:
        try:
            data = api_get(path, params)
        except Exception:
            break
        out.extend(data.get("results", []))
        nxt = data.get("next_url")
        if nxt:
            cur = parse_qs(urlparse(nxt).query).get("cursor", [None])[0]
            params, path = ({"cursor": cur}, urlparse(nxt).path) if cur else ({}, None)
        else:
            path = None
    return out


def price_position(pos: dict) -> dict:
    """Re-price one ledger position and attach live value, P&L, and exit action."""
    legs = _fetch_calls_for_expiry(pos["ticker"], pos["expiry"])
    by_strike = {}
    for r in legs:
        leg = _parse_leg(r)
        if leg and leg["price"] is not None:
            by_strike[round(leg["strike"], 4)] = leg

    long_price = (by_strike.get(round(pos["long_strike"], 4)) or {}).get("price")
    if pos["short_strike"] is not None:
        short_price = (by_strike.get(round(pos["short_strike"], 4)) or {}).get("price")
        mark = (long_price - short_price) if (long_price is not None and short_price is not None) else None
    else:
        short_price, mark = None, long_price

    dte         = (date.fromisoformat(pos["expiry"]) - date.today()).days
    entry_debit = pos["entry_debit"]
    contracts   = pos["contracts"]
    max_value_pc = pos["width"] * 100 if pos["width"] is not None else None

    if mark is not None:
        pnl     = round((mark - entry_debit) * contracts * 100, 2)
        pnl_pct = round((mark / entry_debit - 1) * 100, 1) if entry_debit > 0 else None
        decision = evaluate_exit(mark * 100, entry_debit * 100, max_value_pc, dte, RULES)
        action   = decision.reason if decision else "hold"
    else:
        pnl = pnl_pct = None
        action = "unknown"

    return {
        **pos,
        "dte":          dte,
        "long_price":   round(long_price, 2) if long_price is not None else None,
        "short_price":  round(short_price, 2) if short_price is not None else None,
        "current_mark": round(mark, 2) if mark is not None else None,
        "pnl":          pnl,
        "pnl_pct":      pnl_pct,
        "action":       action,                       # hold | profit_target | stop | time | unknown
        "action_label": ACTION_LABELS.get(action, action),
        "priced":       mark is not None,
    }


def price_all(positions: list[dict]) -> list[dict]:
    if not positions:
        return []
    with ThreadPoolExecutor(max_workers=10) as pool:
        return list(pool.map(price_position, positions))


# ─── Stocks ───────────────────────────────────────────────────────────────────

def effective_tracker(pos: dict, topn_tickers: set[str]) -> str:
    """Resolve a stock position's strategy bucket: explicit override, else auto
    (momentum if currently in the top-N signal, else core)."""
    t = pos.get("tracker")
    if t in ("core", "momentum"):
        return t
    return "momentum" if pos["ticker"] in topn_tickers else "core"


def price_stock_positions(positions: list[dict], topn_tickers: set[str]) -> list[dict]:
    """Re-price held stocks. Only MOMENTUM-tagged names that left the top-N flag ROTATE OUT;
    core holdings never rotate."""
    if not positions:
        return []
    prices = fetch_stock_prices(sorted({p["ticker"] for p in positions}))
    out = []
    for p in positions:
        px      = prices.get(p["ticker"])
        shares  = p["shares"]
        entry   = p["entry_price"]
        pnl     = round((px - entry) * shares, 2) if px is not None else None
        pnl_pct = round((px / entry - 1) * 100, 1) if (px is not None and entry > 0) else None
        tracker = effective_tracker(p, topn_tickers)
        rotate  = tracker == "momentum" and p["ticker"] not in topn_tickers
        out.append({
            **p,
            "tracker":       tracker,
            "current_price": round(px, 2) if px is not None else None,
            "pnl":           pnl,
            "pnl_pct":       pnl_pct,
            "action":        "rotate" if rotate else "hold",
            "action_label":  "ROTATE OUT · left top-N" if rotate else "HOLD",
            "priced":        px is not None,
        })
    return out


def rotation(held_tickers: set[str], topn_tickers: set[str]) -> dict:
    """New names that entered the top-N (not held) and held names that dropped out."""
    return {
        "new_entrants": sorted(topn_tickers - held_tickers),
        "dropouts":     sorted(held_tickers - topn_tickers),
    }
