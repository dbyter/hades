"""
Strategy layer for the backtest engine.

A Strategy is fully responsible for: WHEN it trades (rebalance cadence), WHAT it
selects (signal → universe), HOW it structures each trade (legs), and WHEN it
exits. The engine is strategy-agnostic — it just calls these three methods.

This module also defines the shared data types (Leg/TradeSpec/Position/…) and
the MarketContext protocol the engine implements.

The three concrete strategies share identical monthly momentum selection and
differ ONLY in trade structure — that's the apples-to-apples comparison:
  MonthlyBullSpread  — ATM long + vol-calibrated short (caps the upside)
  MonthlyLongCall    — ATM long call (full upside, more premium/theta)
  MonthlyDeepITMCall — ~0.80-delta call (leveraged-stock-like, full upside)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import pandas as pd

from strategy.exits import ExitDecision, ExitRules, evaluate_exit as _evaluate_exit_core
from strategy.pricing import bsm_call_delta
from strategy.spreads import compute_spread, target_short_strike

TARGET_DTE = 45   # among eligible expiries, pick the one closest to this DTE

# Composite-score weights — mirror daily_trader.py's live scoring.
SCORE_WEIGHTS = {"rr": 0.50, "iv": 0.15, "rsi": 0.35}


def _rsi_to_factor(rsi: float | None) -> float:
    """RSI → 0–1 desirability (ideal momentum zone 50–75). Mirrors daily_trader."""
    if rsi is None:           return 0.8
    if 50 <= rsi < 75:        return 1.0
    if rsi >= 75:             return max(0.0, 1.0 - (rsi - 75) / 25)
    return max(0.0, rsi / 50)


def _percentile_rank(vals: list[float]) -> list[float]:
    """Each value → its 0–1 percentile within the list (0=worst, 1=best)."""
    import numpy as np
    n = len(vals)
    if n <= 1:
        return [1.0] * n
    order = np.argsort(np.argsort(np.array(vals, dtype=float)))
    return [p / (n - 1) for p in order]


def composite_score_rank(items: list[dict], weights: dict = SCORE_WEIGHTS) -> list[str]:
    """
    Rank candidate trades by the live composite score and return tickers best-first.

    items: list of {ticker, rr, iv_rank, rsi}. Each metric is percentile-ranked
    across the candidate set, then combined as a weighted average — identical in
    spirit to daily_trader.compute_scores (which is what we display live).
    """
    items = [it for it in items if it.get("rr") is not None]
    if not items:
        return []
    rr_vals  = [it["rr"] for it in items]
    iv_vals  = [100 - (it["iv_rank"] if it["iv_rank"] is not None else 50) for it in items]
    rsi_vals = [_rsi_to_factor(it["rsi"]) for it in items]

    rr_r, iv_r, rsi_r = (_percentile_rank(v) for v in (rr_vals, iv_vals, rsi_vals))
    scored = [
        (it["ticker"], weights["rr"] * rr_r[i] + weights["iv"] * iv_r[i] + weights["rsi"] * rsi_r[i])
        for i, it in enumerate(items)
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored]


# ─── Shared data types ───────────────────────────────────────────────────────

@dataclass
class Leg:
    side: int          # +1 long, -1 short
    expiry: date
    strike: float
    ref_price: float   # entry-day close, per share (pre-cost)


@dataclass
class TradeSpec:
    underlying: str
    structure: str               # 'bull_spread' | 'long_call' | 'deep_itm_call'
    expiry: date
    dte: int
    legs: list[Leg]
    spot: float
    width: float | None          # per-share spread width; None for single-leg
    max_value: float | None      # per-contract max value ($); None = uncapped
    meta: dict = field(default_factory=dict)


@dataclass
class Position:
    spec: TradeSpec
    contracts: int
    entry_debit: float            # per-contract $ paid (incl entry cost)
    max_value: float | None       # per-contract $ cap (= width×100) or None
    entry_date: date
    mark: float | None = None     # per-contract current value, set daily by engine
    exit_date: date | None = None
    exit_value: float | None = None    # per-contract proceeds (incl exit cost)
    exit_reason: str | None = None

    @property
    def closed(self) -> bool:
        return self.exit_date is not None

    @property
    def pnl(self) -> float | None:
        if self.exit_value is None:
            return None
        return (self.exit_value - self.entry_debit) * self.contracts


class MarketContext(Protocol):
    """Point-in-time view of the market on `today`. Implemented by the engine.

    Every accessor returns only data observable on or before `today` — this is
    the single chokepoint that enforces no look-ahead.
    """
    today: date
    def momentum_top(self, n: int) -> list[str]: ...
    def ranked_names(self, pool_n: int, select_n: int) -> list[str]: ...
    def spot(self, underlying: str) -> float | None: ...
    def vol_63d(self, underlying: str) -> float | None: ...
    def chain(self, underlying: str, min_dte: int, max_dte: int) -> pd.DataFrame: ...


class Strategy(Protocol):
    name: str
    def rebalance_dates(self, trading_days: list[date]) -> list[date]: ...
    def select(self, ctx: MarketContext) -> list[TradeSpec]: ...
    def should_exit(self, pos: Position, ctx: MarketContext) -> ExitDecision | None: ...


# ─── Shared helpers ──────────────────────────────────────────────────────────

def evaluate_exit(pos: Position, today: date, rules: ExitRules) -> ExitDecision | None:
    """Adapter: unpack a backtest Position and delegate to the shared exit core."""
    return _evaluate_exit_core(
        mark_pc=pos.mark,
        entry_debit_pc=pos.entry_debit,
        max_value_pc=pos.max_value,
        dte=(pos.spec.expiry - today).days,
        rules=rules,
    )


def _nearest_expiry(chain: pd.DataFrame, today: date, target_dte: int) -> date:
    expiries = sorted(chain["expiry"].unique())
    return min(expiries, key=lambda e: abs((e - today).days - target_dte))


def _tradable(chain: pd.DataFrame, expiry: date, min_volume: int) -> pd.DataFrame:
    legs = chain[(chain["expiry"] == expiry) & (chain["close"] > 0) & (chain["volume"] >= min_volume)]
    return legs


# ─── Base: monthly momentum selection (structure-agnostic) ────────────────────

class _MonthlyMomentum:
    structure = "abstract"

    def __init__(self, pool_n: int = 50, select_n: int | None = None,
                 min_dte: int = 30, max_dte: int = 90,
                 target_dte: int = TARGET_DTE, min_volume: int = 10,
                 exits: ExitRules | None = None):
        # pool_n   = momentum universe to consider each month
        # select_n = how many to actually trade, ranked by composite score
        #            (None → trade the whole pool, no narrowing)
        self.pool_n     = pool_n
        self.select_n   = select_n
        self.min_dte    = min_dte
        self.max_dte    = max_dte
        self.target_dte = target_dte
        self.min_volume = min_volume
        self.exits      = exits or ExitRules()

    def rebalance_dates(self, trading_days: list[date]) -> list[date]:
        """First trading day of each month."""
        out, seen = [], set()
        for d in trading_days:
            key = (d.year, d.month)
            if key not in seen:
                seen.add(key)
                out.append(d)
        return out

    def select(self, ctx: MarketContext) -> list[TradeSpec]:
        if self.select_n is None:
            names = ctx.momentum_top(self.pool_n)
        else:
            names = ctx.ranked_names(self.pool_n, self.select_n)
        specs = []
        for u in names:
            spec = self._build(ctx, u)
            if spec is not None:
                specs.append(spec)
        return specs

    def should_exit(self, pos: Position, ctx: MarketContext) -> ExitDecision | None:
        return evaluate_exit(pos, ctx.today, self.exits)

    # Shared: locate the chosen expiry and its tradable legs + the ATM long row.
    def _atm_setup(self, ctx: MarketContext, u: str):
        spot = ctx.spot(u)
        if spot is None:
            return None
        chain = ctx.chain(u, self.min_dte, self.max_dte)
        if chain.empty:
            return None
        expiry = _nearest_expiry(chain, ctx.today, self.target_dte)
        legs   = _tradable(chain, expiry, self.min_volume)
        if legs.empty:
            return None
        dte      = (expiry - ctx.today).days
        long_row = legs.loc[(legs["strike"] - spot).abs().idxmin()]
        return spot, chain, expiry, legs, dte, long_row

    def _build(self, ctx: MarketContext, u: str) -> TradeSpec | None:
        raise NotImplementedError


# ─── Concrete structures ──────────────────────────────────────────────────────

class MonthlyBullSpread(_MonthlyMomentum):
    name      = "Bull Spread"
    structure = "bull_spread"

    def _build(self, ctx, u):
        setup = self._atm_setup(ctx, u)
        if setup is None:
            return None
        spot, _chain, expiry, legs, dte, long_row = setup
        vol = ctx.vol_63d(u)

        above = legs[legs["strike"] > long_row["strike"]]
        if above.empty:
            return None
        target = target_short_strike(long_row["strike"], vol, dte)
        if target is not None:
            short_row = above.loc[(above["strike"] - target).abs().idxmin()]
        else:
            short_row = above.sort_values("strike").iloc[0]

        sp = compute_spread(
            {"strike": long_row["strike"],  "price": long_row["close"],  "price_type": "close"},
            {"strike": short_row["strike"], "price": short_row["close"], "price_type": "close"},
            spot, vol=vol, dte=dte,
        )
        if sp is None:
            return None

        return TradeSpec(
            u, self.structure, expiry, dte,
            [Leg(+1, expiry, float(long_row["strike"]),  float(long_row["close"])),
             Leg(-1, expiry, float(short_row["strike"]), float(short_row["close"]))],
            spot=spot, width=sp["width"], max_value=sp["width"] * 100,
            meta={"vol": vol, "rr": sp["rr"], "be_pct": sp["be_pct"], "win_prob": sp["win_prob"]},
        )


class MonthlyLongCall(_MonthlyMomentum):
    name      = "Long Call"
    structure = "long_call"

    def _build(self, ctx, u):
        setup = self._atm_setup(ctx, u)
        if setup is None:
            return None
        spot, _chain, expiry, _legs, dte, long_row = setup
        if long_row["close"] <= 0:
            return None
        return TradeSpec(
            u, self.structure, expiry, dte,
            [Leg(+1, expiry, float(long_row["strike"]), float(long_row["close"]))],
            spot=spot, width=None, max_value=None,
            meta={"vol": ctx.vol_63d(u)},
        )


class MonthlyDeepITMCall(_MonthlyMomentum):
    name      = "Deep ITM Call"
    structure = "deep_itm_call"
    TARGET_DELTA = 0.80

    def _build(self, ctx, u):
        setup = self._atm_setup(ctx, u)
        if setup is None:
            return None
        spot, _chain, expiry, legs, dte, _long_row = setup
        vol = ctx.vol_63d(u) or 0.4
        T   = max(dte, 1) / 252

        # Pick the strike whose BSM delta is closest to TARGET_DELTA (ITM → strike < spot).
        itm = legs[legs["strike"] < spot]
        if itm.empty:
            return None
        deltas = itm["strike"].map(lambda k: bsm_call_delta(spot, float(k), T, vol))
        pick   = itm.loc[(deltas - self.TARGET_DELTA).abs().idxmin()]
        if pick["close"] <= 0:
            return None
        return TradeSpec(
            u, self.structure, expiry, dte,
            [Leg(+1, expiry, float(pick["strike"]), float(pick["close"]))],
            spot=spot, width=None, max_value=None,
            meta={"vol": vol, "target_delta": self.TARGET_DELTA},
        )
