"""
Black-Scholes pricing + implied-vol solver.

Shared by the live trader (daily_trader.py) and the backtest engine so both
price options with identical math. No external state — pure functions.
"""

import math


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x * 0.7071067811865476))


def bsm_call_price(S: float, K: float, T: float, sigma: float, r: float = 0.045) -> float:
    """Black-Scholes price of a European call."""
    if sigma <= 0 or T <= 0:
        return max(S - K, 0.0)
    sqT = math.sqrt(T)
    d1  = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqT)
    return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d1 - sqT * sigma)


def bsm_call_delta(S: float, K: float, T: float, sigma: float, r: float = 0.045) -> float:
    """Delta (N(d1)) of a European call. Used to target a strike by moneyness."""
    if sigma <= 0 or T <= 0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1)


def implied_vol(C: float, S: float, K: float, T: float, r: float = 0.045) -> float | None:
    """Bisection solver for implied vol. Returns None if unsolvable."""
    intrinsic = max(S - K * math.exp(-r * T), 0.0)
    if T <= 0 or C <= intrinsic + 0.01:
        return None
    lo, hi = 0.01, 4.0
    for _ in range(60):
        mid = (lo + hi) * 0.5
        if bsm_call_price(S, K, T, mid, r) >= C:
            hi = mid
        else:
            lo = mid
    iv = (lo + hi) * 0.5
    return iv if 0.05 <= iv <= 3.5 else None
