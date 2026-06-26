"""
Position ledger — the system's memory of what you actually traded.

A flat JSON file at data/positions.json. Each record stores everything needed
to re-price the position live and evaluate exit rules. All prices are PER SHARE
(multiply by 100 × contracts for dollars), matching the screener's convention.
"""

import json
from datetime import date
from pathlib import Path

LEDGER_PATH = Path(__file__).parent.parent / "data" / "positions.json"


def _load_raw() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    try:
        return json.loads(LEDGER_PATH.read_text())
    except json.JSONDecodeError:
        return []


def _save_raw(items: list[dict]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(items, indent=2))


def load() -> list[dict]:
    return _load_raw()


def _asset(p: dict) -> str:
    return p.get("asset", "option_spread")   # back-compat: pre-asset records are spreads


def open_positions(asset: str | None = None) -> list[dict]:
    return [p for p in _load_raw()
            if p["status"] == "open" and (asset is None or _asset(p) == asset)]


def history(asset: str | None = None) -> list[dict]:
    return [p for p in _load_raw()
            if p["status"] == "closed" and (asset is None or _asset(p) == asset)]


def _next_id(items: list[dict]) -> int:
    return max((p["id"] for p in items), default=0) + 1


def add(*, ticker: str, structure: str, expiry: str,
        long_strike: float, short_strike: float | None,
        entry_debit: float, width: float | None,
        contracts: float, entry_date: str | None = None) -> dict:
    """Append a new open option-spread position. entry_debit/width are per share."""
    items = _load_raw()
    pos = {
        "id":           _next_id(items),
        "asset":        "option_spread",
        "ticker":       ticker,
        "structure":    structure,
        "expiry":       expiry,
        "long_strike":  long_strike,
        "short_strike": short_strike,
        "entry_debit":  entry_debit,
        "width":        width,
        "contracts":    contracts,
        "entry_date":   entry_date or str(date.today()),
        "status":       "open",
        "exit_date":    None,
        "exit_debit":   None,
        "exit_reason":  None,
    }
    items.append(pos)
    _save_raw(items)
    return pos


def add_stock(*, ticker: str, shares: float, fill_price: float,
              entry_date: str | None = None, tracker: str | None = None) -> dict:
    """Append a new open stock position. tracker = 'core'|'momentum' (None → auto)."""
    items = _load_raw()
    pos = {
        "id":          _next_id(items),
        "asset":       "stock",
        "ticker":      ticker,
        "shares":      shares,
        "entry_price": fill_price,
        "entry_date":  entry_date or str(date.today()),
        "tracker":     tracker,
        "status":      "open",
        "exit_date":   None,
        "exit_price":  None,
        "exit_reason": None,
    }
    items.append(pos)
    _save_raw(items)
    return pos


def add_long_call(*, ticker: str, expiry: str, strike: float,
                  entry_premium: float, contracts: float,
                  entry_date: str | None = None) -> dict:
    """Append a new open long-call position (single leg). Priced like an
    option spread with no short leg / no width, so pricing + uncapped exit
    rules work unchanged."""
    items = _load_raw()
    pos = {
        "id":           _next_id(items),
        "asset":        "long_call",
        "ticker":       ticker,
        "structure":    "long_call",
        "expiry":       expiry,
        "long_strike":  strike,
        "short_strike": None,
        "entry_debit":  entry_premium,
        "width":        None,
        "contracts":    contracts,
        "entry_date":   entry_date or str(date.today()),
        "status":       "open",
        "exit_date":    None,
        "exit_debit":   None,
        "exit_reason":  None,
    }
    items.append(pos)
    _save_raw(items)
    return pos


def set_tracker(pos_id: int, tracker: str) -> dict | None:
    """Override a stock position's strategy bucket ('core' | 'momentum')."""
    items = _load_raw()
    for p in items:
        if p["id"] == pos_id:
            p["tracker"] = tracker
            _save_raw(items)
            return p
    return None


def close(pos_id: int, exit_value: float, reason: str = "manual") -> dict | None:
    """Mark an open position closed at exit_value (per share). Returns it, or None.

    Stores into exit_price for stocks, exit_debit for option spreads.
    """
    items = _load_raw()
    for p in items:
        if p["id"] == pos_id and p["status"] == "open":
            p["status"]      = "closed"
            p["exit_date"]   = str(date.today())
            p["exit_reason"] = reason
            if _asset(p) == "stock":
                p["exit_price"] = exit_value
            else:
                p["exit_debit"] = exit_value
            _save_raw(items)
            return p
    return None
