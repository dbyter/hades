"""
Portfolio performance — period (WTD/MTD/YTD) price returns + bucket aggregation.

Period returns are computed from each holding's close history (data/stocks_daily.csv)
weighted by current market value — i.e. "how did my current basket do this period."
This assumes current share counts were held for the whole window (no mid-period
trades) and excludes options (no clean calendar price history).
"""

from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data"
STOCKS_CSV = DATA_DIR / "stocks_daily.csv"

_closes: dict[str, pd.Series] = {}   # ticker -> date-indexed close Series (in-process cache)


def _load_closes(tickers: list[str]) -> dict[str, pd.Series]:
    need = [t for t in tickers if t not in _closes]
    if need:
        need_set = set(need)
        frames = []
        for chunk in pd.read_csv(
            STOCKS_CSV, chunksize=1_000_000,
            usecols=["date", "ticker", "close"], parse_dates=["date"],
        ):
            m = chunk["ticker"].isin(need_set)
            if m.any():
                frames.append(chunk[m])
        if frames:
            df = pd.concat(frames)
            for t, g in df.groupby("ticker"):
                _closes[t] = g.dropna(subset=["close"]).set_index("date")["close"].sort_index()
        for t in need:
            _closes.setdefault(t, pd.Series(dtype=float))
    return {t: _closes[t] for t in tickers}


def anchors(tickers: list[str]) -> dict[str, dict]:
    """Per-ticker anchor close prices for WTD/MTD/YTD (None if no history)."""
    out = {}
    for t, s in _load_closes(tickers).items():
        if len(s) == 0:
            out[t] = {"wtd": None, "mtd": None, "ytd": None}
            continue
        last = s.index.max()
        monday    = (last - pd.Timedelta(days=int(last.weekday()))).normalize()
        month_1st = pd.Timestamp(last.year, last.month, 1)
        year_1st  = pd.Timestamp(last.year, 1, 1)

        def before(cutoff):
            sub = s[s.index < cutoff]
            return float(sub.iloc[-1]) if len(sub) else None

        out[t] = {"wtd": before(monday), "mtd": before(month_1st), "ytd": before(year_1st)}
    return out


def _bucket():
    return {"value": 0.0, "cost": 0.0, "pnl": 0.0, "positions": [],
            "_w": {"wtd": 0.0, "mtd": 0.0, "ytd": 0.0},
            "_wv": {"wtd": 0.0, "mtd": 0.0, "ytd": 0.0}}


def aggregate(stocks_priced: list[dict], options_priced: list[dict], anch: dict,
              long_calls_priced: list[dict] | None = None) -> dict:
    """Roll up priced positions into total + per-bucket value/PNL and weighted period returns."""
    buckets: dict[str, dict] = {}

    for p in stocks_priced:
        if p.get("current_price") is None:
            continue
        mv   = p["current_price"] * p["shares"]
        cost = p["entry_price"] * p["shares"]
        b = buckets.setdefault(p["tracker"], _bucket())
        b["value"] += mv; b["cost"] += cost; b["pnl"] += (p.get("pnl") or 0)
        a, rets = anch.get(p["ticker"], {}), {}
        for w in ("wtd", "mtd", "ytd"):
            an = a.get(w)
            if an:
                r = p["current_price"] / an - 1
                rets[w] = round(r * 100, 2)
                b["_w"][w] += r * mv; b["_wv"][w] += mv
            else:
                rets[w] = None
        b["positions"].append({**p, "mv": round(mv, 2), "ret": rets})

    for bucket_name, plist in (("options", options_priced), ("long_calls", long_calls_priced or [])):
        for p in plist:
            mv   = (p.get("current_mark") or 0) * p["contracts"] * 100
            cost = p["entry_debit"] * p["contracts"] * 100
            b = buckets.setdefault(bucket_name, _bucket())
            b["value"] += mv; b["cost"] += cost; b["pnl"] += (p.get("pnl") or 0)
            b["positions"].append({**p, "mv": round(mv, 2), "ret": {"wtd": None, "mtd": None, "ytd": None}})

    total = sum(b["value"] for b in buckets.values())
    cost_total = sum(b["cost"] for b in buckets.values())
    pnl_total = sum(b["pnl"] for b in buckets.values())
    overall_w = {"wtd": 0.0, "mtd": 0.0, "ytd": 0.0}
    overall_wv = {"wtd": 0.0, "mtd": 0.0, "ytd": 0.0}
    order = {"momentum": 0, "core": 1, "long_calls": 2, "options": 3}

    out_buckets = []
    for name, b in buckets.items():
        per = {}
        for w in ("wtd", "mtd", "ytd"):
            per[w] = round(b["_w"][w] / b["_wv"][w] * 100, 2) if b["_wv"][w] > 0 else None
            overall_w[w] += b["_w"][w]; overall_wv[w] += b["_wv"][w]
        out_buckets.append({
            "name": name,
            "value": round(b["value"], 2),
            "weight": round(b["value"] / total * 100, 1) if total else 0,
            "pnl": round(b["pnl"], 2),
            "pnl_pct": round(b["pnl"] / b["cost"] * 100, 2) if b["cost"] else None,
            "wtd": per["wtd"], "mtd": per["mtd"], "ytd": per["ytd"],
            "positions": sorted(b["positions"], key=lambda x: -x["mv"]),
        })
    out_buckets.sort(key=lambda x: order.get(x["name"], 9))

    periods = {w: (round(overall_w[w] / overall_wv[w] * 100, 2) if overall_wv[w] > 0 else None)
               for w in ("wtd", "mtd", "ytd")}

    return {
        "total": round(total, 2),
        "cost": round(cost_total, 2),
        "pnl": round(pnl_total, 2),
        "pnl_pct": round(pnl_total / cost_total * 100, 2) if cost_total else None,
        "buckets": out_buckets,
        "periods": periods,
    }
