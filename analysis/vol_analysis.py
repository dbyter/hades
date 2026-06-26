"""
Volatility analysis for top-20 momentum stocks.

Shows realized vol at multiple lookbacks and the implied spread width
(as % of stock price) at different DTE horizons.

Expected move:  annualized_vol × √(DTE / 252)  — expressed as % of price
Spread width:   expected_move_pct × WIDTH_FACTOR

Usage:
  uv run python analysis/vol_analysis.py
"""

import math
import dotenv
import numpy as np
import pandas as pd
from strategy.momentum import add_momentum_rank

dotenv.load_dotenv()

EXCLUDED    = {"GOOG", "BRK.A", "NWS"}
TOP_N       = 20
MIN_MCAP    = 20_000_000_000
DTES        = [30, 45, 60]
VOL_WINDOWS = [21, 63, 126, 252]


def realized_vol(closes: pd.Series, window: int) -> float | None:
    if len(closes) < window + 1:
        return None
    log_ret = np.log(closes / closes.shift(1)).dropna()
    return float(log_ret.iloc[-window:].std() * np.sqrt(252))


def expected_move_pct(ann_vol: float, dte: int) -> float:
    """1σ expected move as % of stock price."""
    return ann_vol * np.sqrt(dte / 252)


def main():
    market_cap = pd.read_csv("data/market_cap.csv")
    if "composite_figi" in market_cap.columns:
        market_cap = (
            market_cap.sort_values("market_cap", ascending=False)
            .drop_duplicates(subset="composite_figi", keep="first")
        )
    large_caps = (
        set(market_cap.loc[market_cap["market_cap"] >= MIN_MCAP, "ticker"])
        - EXCLUDED
    )

    print("Loading stocks flat file...")
    df = pd.read_csv(
        "data/stocks_daily.csv",
        dtype={"close": "float64", "open": "float64", "high": "float64",
               "low": "float64", "volume": "float64", "transactions": "float64"},
        parse_dates=["date"],
    )
    df = df.dropna(subset=["ticker"])
    df = df[df["ticker"].isin(large_caps)]
    df = add_momentum_rank(df)
    df["momentum_rank_prev"] = df.groupby("ticker")["momentum_rank"].shift(1)

    latest = df["date"].max()
    print(f"Latest data: {latest.date()}\n")

    snap = (
        df[df["date"] == latest][["ticker", "close", "momentum_rank_prev"]]
        .dropna(subset=["momentum_rank_prev"])
        .copy()
    )
    snap["_rank"] = snap["momentum_rank_prev"].rank(ascending=False, method="first")
    top = snap[snap["_rank"] <= TOP_N].sort_values("_rank").reset_index(drop=True)

    rows = []
    for _, t_row in top.iterrows():
        ticker = t_row["ticker"]
        closes = df[df["ticker"] == ticker].sort_values("date")["close"]
        vols   = {w: realized_vol(closes, w) for w in VOL_WINDOWS}
        primary = vols[63] or vols[21] or vols[126] or vols[252]
        rows.append({"ticker": ticker, "price": t_row["close"], **{f"vol_{w}": vols[w] for w in VOL_WINDOWS}, "_vol": primary})

    analysis = pd.DataFrame(rows)

    # ── Table 1: Realized vol at each lookback ─────────────────────────────────
    print("=" * 60)
    print("REALIZED VOL (annualized, % of price)")
    print("=" * 60)
    print(f"{'Ticker':<8} {'Price':>8}   {'21d':>6} {'63d':>6} {'126d':>6} {'252d':>6}")
    print("-" * 60)
    def fv(v): return f"{v*100:5.1f}%" if v else "   n/a"
    for _, r in analysis.iterrows():
        print(f"{r['ticker']:<8} ${r['price']:>7.2f}   {fv(r['vol_21'])} {fv(r['vol_63'])} {fv(r['vol_126'])} {fv(r['vol_252'])}")
    print()

    # ── Table 2: 1σ expected move % by DTE ────────────────────────────────────
    print("=" * 60)
    print("1σ EXPECTED MOVE % by DTE  (using 63d vol)")
    print("  = the % the stock needs to move to reach 1σ above ATM")
    print("=" * 60)
    print(f"{'Ticker':<8} {'63d vol':>7}   " + "   ".join(f"DTE={d}" for d in DTES))
    print("-" * 60)
    for _, r in analysis.iterrows():
        vol = r["_vol"]
        if vol is None:
            print(f"{r['ticker']:<8}  no data")
            continue
        moves = "   ".join(f"{expected_move_pct(vol, d)*100:5.1f}%" for d in DTES)
        print(f"{r['ticker']:<8} {vol*100:6.1f}%   {moves}")
    print()

    # ── Table 3: Implied spread width % at different factors ──────────────────
    print("=" * 60)
    print("IMPLIED SPREAD WIDTH % at DTE=30  (using 63d vol)")
    print("  WIDTH_FACTOR × 1σ expected move")
    print("=" * 60)
    factors = [0.25, 0.50, 0.75, 1.00]
    print(f"{'Ticker':<8} {'1σ move':>7}   " + "  ".join(f"f={f}" for f in factors))
    print("-" * 60)
    for _, r in analysis.iterrows():
        vol = r["_vol"]
        if vol is None:
            continue
        move = expected_move_pct(vol, 30)
        widths = "  ".join(f"{move*f*100:6.1f}%" for f in factors)
        print(f"{r['ticker']:<8} {move*100:6.1f}%   {widths}")
    print()

    # ── Summary ────────────────────────────────────────────────────────────────
    vols = analysis["vol_63"].dropna()
    print("=" * 60)
    print("SUMMARY (63d vol)")
    print("=" * 60)
    print(f"  Median : {vols.median()*100:.1f}%")
    print(f"  Mean   : {vols.mean()*100:.1f}%")
    print(f"  Range  : {vols.min()*100:.1f}% – {vols.max()*100:.1f}%")
    print()
    for dte in DTES:
        m = expected_move_pct(vols.median(), dte)
        print(f"  DTE={dte}: median 1σ = {m*100:.1f}%  →  "
              f"0.5σ width = {m*0.5*100:.1f}%,  1.0σ width = {m*100:.1f}%")
    print()
    print("  WIDTH_FACTOR guide (probability of hitting max profit):")
    print("    0.25 → short strike at 0.25σ above ATM → ~40% max profit")
    print("    0.50 → short strike at 0.50σ above ATM → ~31% max profit")
    print("    0.75 → short strike at 0.75σ above ATM → ~23% max profit")
    print("    1.00 → short strike at 1.0σ above ATM  → ~16% max profit")
    print("  Higher factor = better R/R but lower win rate.")

    # ── Table 4: R/R vs f (theoretical, Black-Scholes) ────────────────────────
    print()
    print("=" * 72)
    print("R/R vs WIDTH_FACTOR  (Black-Scholes, ATM bull call spread, DTE=30)")
    print("  Shown at low / median / high vol for this portfolio.")
    print("  Win prob = probability stock closes above short strike at expiry.")
    print("  EV/$ = expected value per $1 risked (assumes stock drifts at +vol/yr)")
    print("=" * 72)

    sample_vols = {
        "low (RY 17%)":    0.17,
        "median (55%)":    vols.median(),
        "high (BE 105%)":  vols.max(),
    }
    f_values = [0.10, 0.25, 0.50, 0.75, 1.00, 1.25, 1.50]
    T = 30 / 252

    def norm_cdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))

    def bsm_call(S, K, vol, T, r=0.0):
        if vol <= 0 or T <= 0:
            return max(S - K, 0)
        d1 = (math.log(S / K) + (r + 0.5 * vol**2) * T) / (vol * math.sqrt(T))
        d2 = d1 - vol * math.sqrt(T)
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)

    for label, vol in sample_vols.items():
        print(f"\n  Vol = {label}")
        print(f"  {'f':>5}  {'width%':>7}  {'R/R':>6}  {'win%':>6}  {'EV/$risk':>9}")
        print(f"  {'-'*5}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*9}")
        S = 1.0   # normalise to 1 so everything is in % terms
        long_price = bsm_call(S, S, vol, T)   # ATM call

        for f in f_values:
            width_pct   = f * vol * math.sqrt(T)
            K_short     = S * (1 + width_pct)
            short_price = bsm_call(S, K_short, vol, T)
            debit       = long_price - short_price

            if debit <= 0 or debit >= width_pct:
                print(f"  {f:>5.2f}  {width_pct*100:>6.1f}%  {'n/a':>6}  {'n/a':>6}  {'n/a':>9}")
                continue

            max_profit  = width_pct - debit
            rr          = max_profit / debit

            # P(stock > short strike at expiry) — under risk-neutral measure (r=0)
            d2      = (math.log(S / K_short) - 0.5 * vol**2 * T) / (vol * math.sqrt(T))
            win_prob = norm_cdf(d2)

            # EV assuming stock has upward drift = vol (momentum assumption)
            # i.e. expected log return = vol²/2 × T  →  drift ≈ vol × √T over horizon
            drift_d2 = (math.log(S / K_short) + (vol + 0.5 * vol**2) * T) / (vol * math.sqrt(T))
            win_prob_with_drift = norm_cdf(drift_d2)
            ev_per_risk = win_prob_with_drift * rr - (1 - win_prob_with_drift)

            print(f"  {f:>5.2f}  {width_pct*100:>6.1f}%  {rr:>6.1f}x  {win_prob*100:>5.1f}%  {ev_per_risk:>+9.2f}")

    print()
    print("  Notes:")
    print("  - R/R and win% are in tension: you can't improve both simultaneously.")
    print("  - EV/$ > 0 means the trade has positive expected value given momentum drift.")
    print("  - At high vol, even f=0.25 gives decent R/R because the short strike")
    print("    is already far OTM in absolute terms.")
    print("  - EV/$ peaks around f=0.5–0.75 for most vol levels (best use of drift).")


if __name__ == "__main__":
    main()
