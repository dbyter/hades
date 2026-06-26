"""
Performance + trade metrics and the side-by-side comparison report.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from backtest.strategies import Position


def perf_metrics(equity: pd.Series) -> dict:
    """Portfolio metrics from an equity curve (base-invariant — uses ratios)."""
    eq = equity.dropna()
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return {"total": float("nan"), "cagr": float("nan"), "sharpe": float("nan"), "maxdd": float("nan")}
    rets = eq.pct_change().dropna()
    idx  = pd.to_datetime(pd.Series(list(eq.index)))
    yrs  = max((idx.iloc[-1] - idx.iloc[0]).days / 365.25, 1e-9)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr  = (1 + total) ** (1 / yrs) - 1
    sharpe = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else float("nan")
    cum   = eq / eq.iloc[0]
    maxdd = ((cum - cum.cummax()) / cum.cummax()).min()
    return {"total": total, "cagr": cagr, "sharpe": sharpe, "maxdd": maxdd}


def trade_metrics(positions: list[Position]) -> dict:
    """Trade-level stats from closed positions."""
    closed = [p for p in positions if p.exit_value is not None]
    if not closed:
        return {"n": 0, "win_rate": float("nan"), "avg_win": float("nan"),
                "avg_loss": float("nan"), "worthless": float("nan"),
                "hit_max": float("nan"), "avg_hold": float("nan"), "reasons": {}}
    pnls    = [p.pnl for p in closed]
    wins    = [x for x in pnls if x > 0]
    losses  = [x for x in pnls if x <= 0]
    worthless = sum(1 for p in closed if p.exit_value <= 0.01 * p.entry_debit)
    hit_max = sum(
        1 for p in closed
        if p.max_value is not None and p.exit_value >= 0.99 * p.max_value
    )
    holds   = [(p.exit_date - p.entry_date).days for p in closed if p.exit_date]
    reasons: dict[str, int] = {}
    for p in closed:
        reasons[p.exit_reason] = reasons.get(p.exit_reason, 0) + 1
    return {
        "n":        len(closed),
        "win_rate": len(wins) / len(closed),
        "avg_win":  float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        "worthless": worthless / len(closed),
        "hit_max":  hit_max / len(closed),
        "avg_hold": float(np.mean(holds)) if holds else float("nan"),
        "reasons":  reasons,
    }


def spy_equity(spy_returns: pd.Series, days: list[date]) -> pd.Series:
    """Compound SPY daily returns over the backtest days → equity (base 1.0)."""
    eq, out = 1.0, {}
    for d in days:
        r = spy_returns.get(d)
        if r is not None and pd.notna(r):
            eq *= (1 + r)
        out[d] = eq
    return pd.Series(out).sort_index()


def stock_momentum_equity(close_wide: pd.DataFrame, momentum: pd.DataFrame,
                          days: list[date], top_n: int = 50) -> pd.Series:
    """Benchmark: monthly top-N momentum, equal-weight, daily-compounded (base 1.0).

    The 'just own the momentum stocks' baseline the options overlay must beat.
    """
    daily_ret = close_wide.pct_change()
    mom_by_date = {d: g for d, g in momentum.dropna(subset=["momentum_rank_prev"]).groupby("date")}

    month_starts, seen = set(), set()
    for d in days:
        k = (d.year, d.month)
        if k not in seen:
            seen.add(k)
            month_starts.add(d)

    eq, out, current = 1.0, {}, []
    for d in days:
        if d in month_starts:
            g = mom_by_date.get(d)
            if g is not None:
                current = list(g.nlargest(top_n, "momentum_rank_prev")["ticker"])
        if current and d in daily_ret.index:
            cols = [t for t in current if t in daily_ret.columns]
            r = daily_ret.loc[d, cols].mean() if cols else np.nan
            if pd.notna(r):
                eq *= (1 + r)
        out[d] = eq
    return pd.Series(out).sort_index()


# ─── Reporting ────────────────────────────────────────────────────────────────

def _pct(x) -> str:
    return f"{x*100:6.1f}%" if x == x else "    n/a"   # x!=x → NaN


def print_comparison(rows: list[dict], title: str):
    """rows: list of {label, equity, positions(optional)}."""
    print(f"\n{'═'*108}")
    print(f"  {title}")
    print(f"{'═'*108}")
    print(f"  {'Strategy':<22}{'Total':>9}{'CAGR':>9}{'Sharpe':>8}{'MaxDD':>9}"
          f"{'Win%':>8}{'AvgWin':>9}{'AvgLoss':>9}{'Worthl':>8}{'Hold':>7}{'Trades':>8}")
    print(f"  {'-'*104}")
    for r in rows:
        m = perf_metrics(r["equity"])
        pos = r.get("positions")
        if pos is not None:
            t = trade_metrics(pos)
            tail = (f"{_pct(t['win_rate'])}{t['avg_win']:>9.0f}{t['avg_loss']:>9.0f}"
                    f"{_pct(t['worthless'])}{t['avg_hold']:>6.0f}d{t['n']:>8}")
        else:
            tail = f"{'—':>8}{'—':>9}{'—':>9}{'—':>8}{'—':>7}{'—':>8}"
        print(f"  {r['label']:<22}{_pct(m['total'])}{_pct(m['cagr'])}"
              f"{m['sharpe']:>8.2f}{_pct(m['maxdd'])}{tail}")
    print(f"  {'-'*104}")


def print_exit_reasons(results: list):
    print("\n  Exit-reason breakdown (closed trades):")
    for res in results:
        t = trade_metrics(res.positions)
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(t["reasons"].items()))
        print(f"    {res.name:<16} {reasons}")
