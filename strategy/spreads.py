"""
Bull-call-spread construction + metrics.

Shared by the live trader (daily_trader.py) and the backtest engine so both
build and evaluate spreads with identical logic. A "leg" is a plain dict:
    {strike, price, price_type, oi?, ...}

Keeping this here (not in daily_trader) guarantees the backtest measures the
exact spread we trade live.
"""

import math

from strategy.pricing import norm_cdf

# Short strike sits at f × 1σ expected move above the ATM long strike.
WIDTH_FACTOR = 0.5


def target_short_strike(long_strike: float, vol: float | None, dte: int, width_factor: float = WIDTH_FACTOR) -> float | None:
    """Vol-calibrated short-strike target: atm × (1 + f × vol × √(dte/252)).

    Returns None when vol is unavailable (caller should fall back to the next
    strike above ATM).
    """
    if not vol or vol <= 0 or dte <= 0:
        return None
    return long_strike * (1 + width_factor * vol * math.sqrt(dte / 252))


def compute_spread(
    long: dict, short: dict, stock_price: float,
    min_oi_long: int = 0, min_oi_short: int = 0,
    vol: float | None = None, dte: int | None = None,
) -> dict | None:
    """
    Compute bull call spread metrics from two parsed legs.

    Returns None when: either leg has no price, OI below threshold (when an "oi"
    field is present and a threshold is set), debit <= 0 (stale), or
    debit >= width (paying more than max payoff).

    win_prob  — BSM P(stock closes above breakeven at expiry); requires vol + dte
    adj_rr    — EV per $1 risked = win_prob × R/R − (1 − win_prob);
                positive = positive expected value under the vol estimate
    """
    if long["price"] is None or short["price"] is None:
        return None
    if long.get("oi", math.inf) < min_oi_long or short.get("oi", math.inf) < min_oi_short:
        return None

    width  = short["strike"] - long["strike"]
    debit  = long["price"] - short["price"]

    if not (0 < debit < width):
        return None

    debit_total = round(debit * 100)
    max_profit  = round((width - debit) * 100)
    breakeven   = long["strike"] + debit
    be_pct      = (breakeven - stock_price) / stock_price * 100
    rr          = max_profit / debit_total if debit_total > 0 else None

    win_prob = adj_rr = None
    if vol and dte and vol > 0 and dte > 0 and rr:
        T  = dte / 252
        d2 = (math.log(stock_price / breakeven) - 0.5 * vol ** 2 * T) / (vol * math.sqrt(T))
        win_prob = round(norm_cdf(d2) * 100, 1)
        adj_rr   = round(win_prob / 100 * rr - (1 - win_prob / 100), 2)

    return {
        "width":      width,
        "debit":      round(debit, 2),
        "cost":       debit_total,
        "max_profit": max_profit,
        "max_loss":   debit_total,
        "breakeven":  round(breakeven, 2),
        "be_pct":     round(be_pct, 2),
        "rr":         round(rr, 2) if rr else None,
        "win_prob":   win_prob,
        "adj_rr":     adj_rr,
        "stale":      (long.get("price_type") != "close" or short.get("price_type") != "close"),
        "impossible": breakeven >= short["strike"],
    }
