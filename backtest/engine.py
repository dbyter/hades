"""
Backtest engine — strategy-agnostic replay over the options panel.

Owns everything the strategy doesn't: point-in-time data access (no look-ahead),
position lifecycle, daily mark-to-market with a BSM fallback for missing quotes,
execution costs, equal-defined-risk sizing, and the equity curve.

The event loop, per trading day:
  1. mark / exit / settle open positions
  2. open new positions on the strategy's rebalance dates
  3. record portfolio equity (cash + mark-to-market of open positions)

No-look-ahead is enforced in one place: `_Ctx` only exposes data dated ≤ today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from backtest.costs import CostModel
from backtest.panel import DATA_DIR, OptionsPanel, large_cap_universe, load_panel
from backtest.strategies import (
    SCORE_WEIGHTS,
    MarketContext,
    MonthlyBullSpread,
    Position,
    Strategy,
    TradeSpec,
    composite_score_rank,
)
from strategy.momentum import add_momentum_rank
from strategy.pricing import bsm_call_price, implied_vol

PER_COHORT_RISK  = 50_000.0    # total $ risk deployed each month, split equally across that
                               # month's trades → total deployment is constant whether we
                               # trade 5 names or 50 (makes concentration levels comparable)
INITIAL_CAPITAL  = 250_000.0
VOL_FALLBACK     = 0.40        # used to BSM-price a leg when realized vol is unavailable


@dataclass
class BacktestResult:
    name: str
    equity: pd.Series              # date → portfolio equity
    positions: list[Position]
    spy_returns: pd.Series         # daily SPY returns aligned to equity dates
    cost: CostModel


# ─── Inputs ──────────────────────────────────────────────────────────────────

@dataclass
class Inputs:
    panel: OptionsPanel
    close_wide: pd.DataFrame       # date × ticker → close (universe only)
    momentum: pd.DataFrame         # ticker, date, momentum_rank_prev
    spy: pd.Series                 # date → daily return


def build_inputs() -> Inputs:
    """Load + precompute everything the engine needs (call once, reuse across strategies)."""
    universe = large_cap_universe()
    print("Loading panel + stocks...")
    panel = OptionsPanel(load_panel())

    stocks = pd.read_csv(
        DATA_DIR / "stocks_daily.csv",
        usecols=["date", "ticker", "close"],
        dtype={"close": "float64"},
        parse_dates=["date"],
    ).dropna(subset=["ticker"])

    spy = stocks[stocks["ticker"] == "SPY"].set_index("date")["close"].sort_index().pct_change()
    spy.index = spy.index.date

    uni = stocks[stocks["ticker"].isin(universe)].copy()

    print("Computing momentum signal...")
    mom = add_momentum_rank(uni)
    mom["momentum_rank_prev"] = mom.groupby("ticker")["momentum_rank"].shift(1)
    momentum = mom[["ticker", "date", "momentum_rank_prev"]].copy()
    momentum["date"] = momentum["date"].dt.date

    close_wide = uni.pivot_table(index="date", columns="ticker", values="close")
    close_wide.index = close_wide.index.date

    return Inputs(panel=panel, close_wide=close_wide, momentum=momentum, spy=spy)


# ─── Point-in-time market context ─────────────────────────────────────────────

class _Ctx(MarketContext):
    def __init__(self, engine: "Engine", today: date):
        self._e = engine
        self.today = today

    def momentum_top(self, n: int) -> list[str]:
        return self._e.momentum_top(self.today, n)

    def ranked_names(self, pool_n: int, select_n: int) -> list[str]:
        return self._e.ranked_names(self.today, pool_n, select_n)

    def spot(self, underlying: str) -> float | None:
        return self._e.spot(underlying, self.today)

    def vol_63d(self, underlying: str) -> float | None:
        return self._e.vol_63d(underlying, self.today)

    def chain(self, underlying: str, min_dte: int, max_dte: int) -> pd.DataFrame:
        return self._e.panel.chain(underlying, self.today, min_dte, max_dte)


# ─── Engine ───────────────────────────────────────────────────────────────────

class Engine:
    def __init__(self, inputs: Inputs, cost: CostModel = CostModel(),
                 per_cohort_risk: float = PER_COHORT_RISK,
                 initial_capital: float = INITIAL_CAPITAL):
        self.panel        = inputs.panel
        self.close_wide   = inputs.close_wide
        self.momentum     = inputs.momentum
        self.spy          = inputs.spy
        self.cost         = cost
        self.per_cohort_risk = per_cohort_risk
        self.initial_capital = initial_capital
        # date → ticker list (top by prior-day momentum), cached per date
        self._mom_by_date: dict[date, pd.DataFrame] = {
            d: g for d, g in self.momentum.dropna(subset=["momentum_rank_prev"]).groupby("date")
        }
        self._skipped_funding = 0
        # Reference bull-spread builder used to score candidates for ranking,
        # plus a lazy per-ticker ATM-IV history cache for IV rank.
        self._ranker_bull = MonthlyBullSpread()
        self._iv_series_cache: dict[str, pd.Series] = {}

    # ── point-in-time accessors (date ≤ today only) ──
    def momentum_top(self, today: date, n: int) -> list[str]:
        g = self._mom_by_date.get(today)
        if g is None:
            return []
        return list(g.nlargest(n, "momentum_rank_prev")["ticker"])

    def spot(self, u: str, today: date) -> float | None:
        if u not in self.close_wide.columns:
            return None
        s = self.close_wide[u]
        s = s[s.index <= today].dropna()
        return float(s.iloc[-1]) if len(s) else None

    def close_on_or_before(self, u: str, d: date) -> float | None:
        return self.spot(u, d)

    def vol_63d(self, u: str, today: date) -> float | None:
        if u not in self.close_wide.columns:
            return None
        s = self.close_wide[u]
        s = s[s.index <= today].dropna()
        if len(s) < 64:
            return None
        return float(s.iloc[-64:].pct_change().std() * np.sqrt(252))

    def rsi(self, u: str, today: date, period: int = 14) -> float | None:
        if u not in self.close_wide.columns:
            return None
        s = self.close_wide[u]
        s = s[s.index <= today].dropna()
        if len(s) < period + 1:
            return None
        delta = s.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = (100 - 100 / (1 + rs)).iloc[-1]
        return float(rsi) if pd.notna(rsi) else None

    def _atm_iv_series(self, u: str) -> pd.Series:
        """Lazy per-ticker daily ATM implied-vol history (BSM), cached."""
        cached = self._iv_series_cache.get(u)
        if cached is not None:
            return cached
        g = self.panel.underlying_frame(u)
        out: dict[date, float] = {}
        if g is not None:
            for tdate, day in g.groupby("trade_date"):
                spot = self.spot(u, tdate)
                if not spot or spot <= 0:
                    continue
                dte = day["expiry"].map(lambda e, t=tdate: (e - t).days)
                cand = day[(dte >= 25) & (dte <= 45) & (day["close"] > 0)]
                if cand.empty:
                    continue
                row = cand.loc[(cand["strike"] - spot).abs().idxmin()]
                d   = (row["expiry"] - tdate).days
                iv  = implied_vol(row["close"], spot, row["strike"], d / 365.0)
                if iv is not None:
                    out[tdate] = iv
        s = pd.Series(out).sort_index()
        self._iv_series_cache[u] = s
        return s

    def iv_rank(self, u: str, today: date) -> float | None:
        """Where today's ATM IV sits in its trailing range (0=cheap … 100=rich)."""
        s = self._atm_iv_series(u)
        s = s[s.index <= today]
        if len(s) < 20:
            return None
        lo, hi, cur = s.min(), s.max(), s.iloc[-1]
        return float((cur - lo) / (hi - lo) * 100) if hi > lo else 50.0

    def ranked_names(self, today: date, pool_n: int, select_n: int) -> list[str]:
        """Composite-score the momentum pool (R/R via bull spread + IV rank + RSI) → top names."""
        ctx = _Ctx(self, today)
        items = []
        for u in self.momentum_top(today, pool_n):
            spec = self._ranker_bull._build(ctx, u)
            if spec is None or spec.meta.get("rr") is None:
                continue
            items.append({"ticker": u, "rr": spec.meta["rr"],
                          "iv_rank": self.iv_rank(u, today), "rsi": self.rsi(u, today)})
        return composite_score_rank(items, SCORE_WEIGHTS)[:select_n]

    # ── leg valuation ──
    def _leg_value(self, u: str, expiry: date, strike: float, today: date,
                   spot_today: float | None, vol: float | None) -> float:
        """Per-share market value of one call leg on `today` (panel close, BSM fallback)."""
        q = self.panel.quote(u, expiry, strike, today)
        if q is not None and q["close"] and q["close"] > 0:
            return float(q["close"])
        if spot_today is None:
            return 0.0
        dte = (expiry - today).days
        if dte <= 0:
            return max(spot_today - strike, 0.0)
        return bsm_call_price(spot_today, strike, dte / 252, vol or VOL_FALLBACK)

    def _open(self, spec: TradeSpec, today: date, trade_risk: float) -> Position | None:
        slip = self.cost.slip
        base = sum(leg.side * leg.ref_price for leg in spec.legs)          # long − short
        fees = sum(slip(leg.ref_price) for leg in spec.legs)
        debit_ps = base + fees
        entry_debit = debit_ps * 100
        if entry_debit <= 0:
            return None
        contracts = trade_risk / entry_debit                              # fractional, equal $ risk
        return Position(spec=spec, contracts=contracts, entry_debit=entry_debit,
                        max_value=spec.max_value, entry_date=today, mark=entry_debit)

    def _mark(self, pos: Position, today: date) -> float:
        """Per-contract mid value (no cost) for MTM + exit checks."""
        u = pos.spec.underlying
        spot_today = self.spot(u, today)
        vol = self.vol_63d(u, today)
        val_ps = sum(
            leg.side * self._leg_value(u, leg.expiry, leg.strike, today, spot_today, vol)
            for leg in pos.spec.legs
        )
        return val_ps * 100

    def _settle_expiry(self, pos: Position) -> float:
        """Per-contract proceeds at expiration (intrinsic; no exit slippage)."""
        u = pos.spec.underlying
        spot_exp = self.close_on_or_before(u, pos.spec.expiry) or 0.0
        val_ps = sum(leg.side * max(spot_exp - leg.strike, 0.0) for leg in pos.spec.legs)
        return val_ps * 100

    def _exit_early(self, pos: Position, today: date) -> float:
        """Per-contract proceeds closing now (mid value minus slippage on every leg)."""
        u = pos.spec.underlying
        spot_today = self.spot(u, today)
        vol = self.vol_63d(u, today)
        slip = self.cost.slip
        proceeds_ps = 0.0
        for leg in pos.spec.legs:
            v = self._leg_value(u, leg.expiry, leg.strike, today, spot_today, vol)
            proceeds_ps += leg.side * v - slip(v)
        return proceeds_ps * 100

    # ── main loop ──
    def run(self, strategy: Strategy) -> BacktestResult:
        self._skipped_funding = 0
        days = self.panel.trading_days()
        entry_dates = set(strategy.rebalance_dates(days))

        cash = self.initial_capital
        open_positions: list[Position] = []
        all_positions: list[Position] = []
        equity: dict[date, float] = {}

        for today in days:
            ctx = _Ctx(self, today)

            # 1. exits / settlements
            still_open = []
            for pos in open_positions:
                if today >= pos.spec.expiry:
                    pos.exit_value  = self._settle_expiry(pos)
                    pos.exit_date   = pos.spec.expiry
                    pos.exit_reason = "expiry"
                    cash += pos.exit_value * pos.contracts
                    continue
                pos.mark = self._mark(pos, today)
                decision = strategy.should_exit(pos, ctx)
                if decision is not None:
                    pos.exit_value  = self._exit_early(pos, today)
                    pos.exit_date   = today
                    pos.exit_reason = decision.reason
                    cash += pos.exit_value * pos.contracts
                else:
                    still_open.append(pos)
            open_positions = still_open

            # 2. entries — split the month's risk budget equally across the cohort
            if today in entry_dates:
                specs = strategy.select(ctx)
                if specs:
                    trade_risk = self.per_cohort_risk / len(specs)
                    for spec in specs:
                        pos = self._open(spec, today, trade_risk)
                        if pos is None:
                            continue
                        need = pos.entry_debit * pos.contracts
                        if cash < need:
                            self._skipped_funding += 1
                            continue
                        cash -= need
                        open_positions.append(pos)
                        all_positions.append(pos)

            # 3. mark-to-market equity
            mtm = 0.0
            for pos in open_positions:
                pos.mark = self._mark(pos, today)
                mtm += pos.mark * pos.contracts
            equity[today] = cash + mtm

        if self._skipped_funding:
            print(f"  [{strategy.name}] skipped {self._skipped_funding} trades (insufficient cash)")

        return BacktestResult(
            name=strategy.name,
            equity=pd.Series(equity).sort_index(),
            positions=all_positions,
            spy_returns=self.spy,
            cost=self.cost,
        )
