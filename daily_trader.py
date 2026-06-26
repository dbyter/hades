"""
Daily momentum options trader.

Flow:
  1. Flat file → momentum signal → top-N tickers + weights
  2. Live API  → stock prices, option chains
  3. Flat file → RSI, 52w-high, IV rank (BSM)
  4. HTML table ranked by composite score → open in browser
"""

import json
import math
import os
import re
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import urlopen

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from strategy.momentum import add_momentum_rank
from strategy.pricing import bsm_call_delta, implied_vol as _implied_vol
from strategy.spreads import compute_spread, target_short_strike

load_dotenv(Path(__file__).parent / ".env")


# ─── Config ────────────────────────────────────────────────────────────────────

API_KEY     = os.environ["MASSIVE_API_KEY"]
BASE_URL    = "https://api.massive.com"
OUTPUT_HTML = Path(__file__).parent / "daily_output.html"

EXCLUDED    = {"GOOG", "BRK.A", "NWS"}
TOP_N       = 50
MIN_DTE     = 20
MAX_DTE     = 55

# OI thresholds relax for outer expirations — weeklies far out have thinner books
# Each entry: (dte_ceiling, min_oi_long, min_oi_short)
OI_TIERS = [
    (45,  500, 100),
    (55,  300,  50),
]
RSI_PERIOD   = 14
# WIDTH_FACTOR (short-strike calibration) lives in strategy/spreads.py — shared with the backtest.

# Composite score weights (must sum to 1.0)
# Each metric is independently percentile-ranked 0→1 across the universe,
# then combined as a weighted average → final score 0–100.
SCORE_WEIGHTS = {
    "rr":  0.50,   # reward-to-risk ratio of the spread
    "iv":  0.15,   # IV rank inverted (low rank = cheap options = good)
    "rsi": 0.35,   # RSI proximity to ideal momentum zone (50–75)
}

# Long calls have no R/R, so rank on momentum + cheapness instead.
LONGCALL_TARGET_DTE = 45     # pick the call at the expiry nearest this DTE
LONGCALL_MIN_OI     = 200    # single-leg liquidity floor
# Moneyness variants: ATM = nearest strike to spot; ITM/OTM = nearest target delta.
LONGCALL_DELTAS = {"itm": 0.75, "otm": 0.35}
LONGCALL_WEIGHTS = {
    "momentum":  0.35,   # momentum rank (stronger = better)
    "breakeven": 0.30,   # % move to breakeven (closer = better)
    "iv":        0.20,   # IV rank inverted (cheaper premium = better)
    "rsi":       0.15,   # RSI proximity to ideal zone
}


# ─── Massive REST helpers ───────────────────────────────────────────────────────

def api_get(path: str, params: dict = {}) -> dict:
    url = f"{BASE_URL}{path}?{urlencode({**params, 'apiKey': API_KEY})}"
    with urlopen(url) as r:
        return json.loads(r.read())


def fetch_stock_prices(tickers: list[str]) -> dict[str, float]:
    """Batch-fetch live prices for a list of tickers."""
    data = api_get(
        "/v2/snapshot/locale/us/markets/stocks/tickers",
        {"tickers": ",".join(tickers)},
    )
    out = {}
    for t in data.get("tickers", []):
        day   = t.get("day", {})
        price = day.get("c") or day.get("vw") or t.get("lastTrade", {}).get("p")
        if price:
            out[t["ticker"]] = float(price)
    return out


_NAMES_CACHE = Path(__file__).parent / "data" / "names_cache.json"


def fetch_company_names(tickers: list[str]) -> dict[str, str | None]:
    """Company name per ticker via /v3/reference/tickers. Cached to disk forever
    (names rarely change) — only unseen tickers trigger an API call."""
    cache: dict = {}
    if _NAMES_CACHE.exists():
        try:
            cache = json.loads(_NAMES_CACHE.read_text())
        except Exception:
            cache = {}

    missing = [t for t in tickers if t not in cache]

    def _one(ticker):
        try:
            d = api_get(f"/v3/reference/tickers/{ticker}").get("results", {})
            return ticker, d.get("name")
        except Exception:
            return ticker, None

    if missing:
        got = {}
        with ThreadPoolExecutor(max_workers=10) as pool:
            for ticker, n in [f.result() for f in as_completed(
                {pool.submit(_one, t): t for t in missing}
            )]:
                if n:
                    got[ticker] = n
        if got:
            cache.update(got)
            try:
                _NAMES_CACHE.write_text(json.dumps(cache))
            except Exception:
                pass

    return {t: cache.get(t) for t in tickers}


_RATINGS_CACHE = Path(__file__).parent / "data" / "ratings_cache.json"


def fetch_analyst_ratings(tickers: list[str]) -> dict[str, dict | None]:
    """Analyst Buy/Hold/Sell consensus per ticker via FMP grades-consensus.

    Buy = strongBuy + buy; Sell = sell + strongSell.
    Returns {ticker: {buy, hold, sell, total, consensus}} (None if unavailable).

    Cached to disk per day: ratings move slowly and FMP's free tier has a low
    daily request cap (HTTP 402 when exhausted), so repeat refreshes the same
    day reuse the cache and only fetch tickers we don't already have.
    """
    key = os.environ.get("FMP_API_KEY")
    if not key:
        return {}

    today = str(date.today())
    cache: dict = {}
    if _RATINGS_CACHE.exists():
        try:
            blob = json.loads(_RATINGS_CACHE.read_text())
            if blob.get("date") == today:
                cache = blob.get("data", {})
        except Exception:
            cache = {}

    missing = [t for t in tickers if t not in cache]
    base = "https://financialmodelingprep.com/stable/grades-consensus"

    def _one(ticker):
        try:
            url = f"{base}?{urlencode({'symbol': ticker, 'apikey': key})}"
            with urlopen(url) as r:
                data = json.loads(r.read())
            row = data[0] if isinstance(data, list) and data else None
            if not row:
                return ticker, None
            buy  = (row.get("strongBuy") or 0) + (row.get("buy") or 0)
            hold = row.get("hold") or 0
            sell = (row.get("sell") or 0) + (row.get("strongSell") or 0)
            total = buy + hold + sell
            if total == 0:
                return ticker, None
            return ticker, {"buy": buy, "hold": hold, "sell": sell,
                            "total": total, "consensus": row.get("consensus")}
        except Exception:
            return ticker, None   # 402/quota or missing → graceful None

    if missing:
        # Gentle concurrency to avoid tripping FMP rate limits.
        got = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            for ticker, v in [f.result() for f in as_completed(
                {pool.submit(_one, t): t for t in missing}
            )]:
                if v is not None:        # only cache hits; blocked/empty ones retry next refresh
                    got[ticker] = v
        if got:
            cache.update(got)
            try:
                _RATINGS_CACHE.write_text(json.dumps({"date": today, "data": cache}))
            except Exception:
                pass

    return {t: cache.get(t) for t in tickers}


def fetch_earnings_dates(tickers: list[str], today: date) -> dict[str, str | None]:
    """Return next earnings date per ticker (None if not found)."""
    def _fetch(ticker):
        try:
            data = api_get(f"/v3/reference/tickers/{ticker}")
            r = data.get("results", {})
            for field in ("next_earnings_date", "earnings_date", "next_earnings"):
                d = r.get(field)
                if d and str(d) >= str(today):
                    return ticker, str(d)
        except Exception:
            pass
        return ticker, None

    out = {}
    with ThreadPoolExecutor(max_workers=20) as pool:
        for ticker, d in [f.result() for f in as_completed(
            {pool.submit(_fetch, t): t for t in tickers}
        )]:
            out[ticker] = d
    return out


# ─── Option chain fetching ──────────────────────────────────────────────────────

def _fetch_raw_calls(ticker: str, today: date) -> list[dict]:
    """Paginate /v3/snapshot/options to get all calls in the DTE window."""
    params = {
        "expiration_date.gte": str(today + timedelta(days=MIN_DTE)),
        "expiration_date.lte": str(today + timedelta(days=MAX_DTE)),
        "contract_type":       "call",
        "limit":               250,
    }
    path    = f"/v3/snapshot/options/{ticker}"
    results = []
    while path:
        try:
            data = api_get(path, params)
        except Exception as e:
            print(f"  [options API] {ticker}: {e}")
            break
        results.extend(data.get("results", []))
        next_url = data.get("next_url")
        if next_url:
            parsed = urlparse(next_url)
            cursor = parse_qs(parsed.query).get("cursor", [None])[0]
            params = {"cursor": cursor} if cursor else {}
            path   = parsed.path
        else:
            path = None
    return results


def _parse_leg(r: dict) -> dict | None:
    """Extract a clean leg dict from one API result row. Returns None on bad data."""
    details = r.get("details", {})
    exp     = details.get("expiration_date")
    strike  = details.get("strike_price")
    if not exp or strike is None:
        return None

    day    = r.get("day", {})
    greeks = r.get("greeks", {})

    # Use close if it exists and the contract traded today (volume > 0)
    # Otherwise fall back to open. If neither exists, price = None.
    close  = day.get("close")
    volume = day.get("volume") or 0
    open_  = day.get("open")
    if close and volume > 0:
        price      = float(close)
        price_type = "close"
    elif open_:
        price      = float(open_)
        price_type = "open"
    else:
        price      = None
        price_type = None

    return {
        "expiry":     exp,
        "strike":     float(strike),
        "price":      price,
        "price_type": price_type,   # "close" | "open" | None
        "iv":         r.get("implied_volatility"),
        "delta":      greeks.get("delta"),
        "theta":      greeks.get("theta"),
        "oi":         int(r.get("open_interest") or 0),
        "volume":     int(volume),
    }


# ─── Spread construction + math ────────────────────────────────────────────────

def _oi_thresholds(dte: int) -> tuple[int, int]:
    for ceiling, min_long, min_short in OI_TIERS:
        if dte <= ceiling:
            return min_long, min_short
    return OI_TIERS[-1][1], OI_TIERS[-1][2]


def build_for_ticker(
    ticker: str, stock_price: float, today: date, vol_63d: float | None,
    opt_vol_5d: dict[str, float] | None = None,
) -> dict:
    """
    One chain fetch → both a list of bull spreads (one per expiry) AND a single
    long-call candidate (ATM call at the expiry nearest LONGCALL_TARGET_DTE).

    Returns {"spreads": [...], "long_call": {...}|None}.
    """
    raw = _fetch_raw_calls(ticker, today)

    by_expiry: dict[str, list[dict]] = {}
    for r in raw:
        leg = _parse_leg(r)
        if leg:
            by_expiry.setdefault(leg["expiry"], []).append(leg)

    # Replace today-only option volume with 5-day avg from flat file
    if opt_vol_5d:
        for legs in by_expiry.values():
            for leg in legs:
                sym = opt_symbol(ticker, leg["expiry"], leg["strike"])
                avg = opt_vol_5d.get(sym)
                if avg is not None:
                    leg["volume"] = int(round(avg))

    # ── Bull spreads: one per expiry ──────────────────────────────────────────
    spreads = []
    for exp, legs in sorted(by_expiry.items()):
        legs.sort(key=lambda l: l["strike"])
        dte = (date.fromisoformat(exp) - today).days

        long_leg = min(legs, key=lambda l: abs(l["strike"] - stock_price))
        above    = [l for l in legs if l["strike"] > long_leg["strike"]]
        if not above:
            continue

        target = target_short_strike(long_leg["strike"], vol_63d, dte)
        short_leg = (min(above, key=lambda l: abs(l["strike"] - target))
                     if target is not None else above[0])

        min_oi_long, min_oi_short = _oi_thresholds(dte)
        spread = compute_spread(long_leg, short_leg, stock_price, min_oi_long, min_oi_short,
                                vol=vol_63d, dte=dte)
        spreads.append({"ticker": ticker, "expiry": exp, "dte": dte,
                        "long": long_leg, "short": short_leg, "spread": spread})

    # ── Long calls: ATM / ITM / OTM at the expiry nearest the target DTE ──────
    long_calls = {"atm": None, "itm": None, "otm": None}
    for exp in sorted(by_expiry, key=lambda e: abs((date.fromisoformat(e) - today).days - LONGCALL_TARGET_DTE)):
        legs = by_expiry[exp]
        dte  = (date.fromisoformat(exp) - today).days
        tradeable = [l for l in legs if l["price"] and l["price"] > 0 and l["oi"] >= LONGCALL_MIN_OI]
        if not tradeable:
            continue

        def _eff_delta(l):
            d = l.get("delta")
            if d is None and vol_63d:
                d = bsm_call_delta(stock_price, l["strike"], max(dte, 1) / 252, vol_63d)
            return d

        move_pct = vol_63d * math.sqrt(dte / 252) if vol_63d else None

        def _make(leg):
            be = leg["strike"] + leg["price"]
            d  = _eff_delta(leg)
            return {
                "ticker": ticker, "expiry": exp, "dte": dte, "spot": round(stock_price, 2),
                "strike": leg["strike"], "premium": round(leg["price"], 2),
                "breakeven": round(be, 2), "be_pct": round((be - stock_price) / stock_price * 100, 2),
                "exp_move_pct": round(move_pct * 100, 1) if move_pct else None,
                "exp_move":     round(stock_price * move_pct, 2) if move_pct else None,
                "delta": round(d, 2) if d is not None else None,
                "iv": leg["iv"], "oi": leg["oi"], "volume": leg["volume"],
            }

        # ATM = nearest strike to spot
        long_calls["atm"] = _make(min(tradeable, key=lambda l: abs(l["strike"] - stock_price)))
        # ITM = strike below spot, delta nearest target
        itm = [l for l in tradeable if l["strike"] < stock_price]
        if itm:
            long_calls["itm"] = _make(min(itm, key=lambda l: abs((_eff_delta(l) or 0) - LONGCALL_DELTAS["itm"])))
        # OTM = strike above spot, delta nearest target
        otm = [l for l in tradeable if l["strike"] > stock_price]
        if otm:
            long_calls["otm"] = _make(min(otm, key=lambda l: abs((_eff_delta(l) or 0) - LONGCALL_DELTAS["otm"])))
        break

    return {"spreads": spreads, "long_calls": long_calls}


# ─── Stock metrics from flat file ──────────────────────────────────────────────

def compute_stock_metrics(df: pd.DataFrame, tickers: list[str]) -> dict[str, dict]:
    """RSI(14), % from 52w high, 63d vol, plus YTD and 1w/1m/3m returns per ticker."""
    subset = df[df["ticker"].isin(tickers)].sort_values(["ticker", "date"])

    def _ret(s: pd.Series, n: int) -> float | None:
        if len(s) <= n:
            return None
        return float((s.iloc[-1] / s.iloc[-1 - n] - 1) * 100)

    out = {}
    for ticker, grp in subset.groupby("ticker"):
        s = grp.dropna(subset=["close"]).set_index("date")["close"]
        if len(s) < RSI_PERIOD + 1:
            out[ticker] = {"rsi": None, "from_52w_high": None, "vol_63d": None,
                           "ytd": None, "ret_1w": None, "ret_1m": None, "ret_3m": None}
            continue

        delta  = s.diff()
        gain   = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
        loss   = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
        rs     = gain / loss.replace(0, np.nan)
        rsi    = float((100 - 100 / (1 + rs)).iloc[-1])

        high_252  = s.iloc[-252:].max() if len(s) >= 252 else s.max()
        from_high = float((s.iloc[-1] - high_252) / high_252 * 100)

        vol_63 = float(s.iloc[-63:].pct_change().std() * np.sqrt(252)) if len(s) >= 64 else None

        # YTD: last close vs first close of the latest year present
        year      = s.index.max().year
        ytd_slice = s[s.index >= pd.Timestamp(year, 1, 1)]
        ytd = float((s.iloc[-1] / ytd_slice.iloc[0] - 1) * 100) if len(ytd_slice) else None

        out[ticker] = {
            "rsi":          round(rsi, 1) if not math.isnan(rsi) else None,
            "from_52w_high": round(from_high, 1),
            "vol_63d":      round(vol_63, 4) if vol_63 else None,
            "ytd":          round(ytd, 1) if ytd is not None else None,
            "ret_1w":       round(r, 1) if (r := _ret(s, 5))  is not None else None,
            "ret_1m":       round(r, 1) if (r := _ret(s, 21)) is not None else None,
            "ret_3m":       round(r, 1) if (r := _ret(s, 63)) is not None else None,
        }
    return out


# ─── IV Rank from options flat file (Black-Scholes) ────────────────────────────

_OPT_SYM_RE = re.compile(r"^O:([A-Z./]+)(\d{2})(\d{2})(\d{2})([CP])(\d+)$")


def opt_symbol(ticker: str, expiry: str, strike: float) -> str:
    """Reconstruct the Polygon option symbol, e.g. O:AAPL250718C00150000."""
    yy, mm, dd = expiry[2:4], expiry[5:7], expiry[8:10]
    return f"O:{ticker}{yy}{mm}{dd}C{int(round(strike * 1000)):08d}"


def compute_options_data(
    tickers: list[str], stock_df: pd.DataFrame, today: date
) -> tuple[dict[str, dict], dict[str, float]]:
    """Single pass through options_daily.csv to compute both IV rank and 5-day avg volume.

    Returns (iv_rank_data, opt_vol_5d).
    """
    print("Computing IV rank + 5-day avg option volume from flat file (single pass)...")
    ticker_set = set(tickers)

    # Build {(ticker, date): close_price} lookup for IV rank computation
    px = stock_df[stock_df["ticker"].isin(ticker_set)][["ticker", "date", "close"]].copy()
    px["d"] = pd.to_datetime(px["date"]).dt.date
    price_lookup: dict[tuple, float] = {
        (r.ticker, r.d): r.close for r in px.itertuples(index=False)
    }

    iv_history: dict[str, list[tuple[date, float]]] = {t: [] for t in tickers}
    vol_frames: list[pd.DataFrame] = []

    for chunk in pd.read_csv(
        "data/options_daily.csv",
        chunksize=500_000,
        parse_dates=["date"],
        usecols=["date", "ticker", "volume", "close"],
        dtype={"volume": "float64", "close": "float64"},
    ):
        # Accumulate volume data (all tickers, filtered to cutoff dates later)
        vol_frames.append(chunk[["date", "ticker", "volume"]].copy())

        # IV rank: only symbols belonging to our tickers
        underlying = chunk["ticker"].str.extract(r"^O:([A-Z./]+)\d", expand=False)
        mask = underlying.isin(ticker_set)
        if not mask.any():
            continue

        iv_chunk = chunk[mask].copy()
        iv_chunk["underlying"] = underlying[mask]

        parsed = iv_chunk["ticker"].str.extract(
            r"^O:[A-Z./]+(\d{2})(\d{2})(\d{2})([CP])(\d+)$"
        )
        parsed.columns = ["yy", "mm", "dd", "opt_type", "strike_str"]
        iv_chunk = pd.concat(
            [iv_chunk.reset_index(drop=True), parsed.reset_index(drop=True)], axis=1
        ).dropna(subset=["yy"])

        iv_chunk = iv_chunk[iv_chunk["opt_type"] == "C"]
        if iv_chunk.empty:
            continue

        iv_chunk["strike"]      = iv_chunk["strike_str"].astype(float) / 1000.0
        iv_chunk["trade_date"]  = iv_chunk["date"].dt.date
        iv_chunk["expiry_date"] = pd.to_datetime(
            "20" + iv_chunk["yy"] + "-" + iv_chunk["mm"] + "-" + iv_chunk["dd"]
        ).dt.date
        iv_chunk["dte"] = (
            iv_chunk["expiry_date"].map(lambda d: d.toordinal()) -
            iv_chunk["trade_date"].map(lambda d: d.toordinal())
        )

        iv_chunk = iv_chunk[(iv_chunk["dte"] >= 25) & (iv_chunk["dte"] <= 45)]
        iv_chunk = iv_chunk[iv_chunk["close"] > 0].dropna(subset=["close"])
        if iv_chunk.empty:
            continue

        iv_chunk["S"] = iv_chunk.apply(
            lambda r: price_lookup.get((r["underlying"], r["trade_date"])), axis=1
        )
        iv_chunk = iv_chunk.dropna(subset=["S"])
        iv_chunk = iv_chunk[iv_chunk["S"] > 0]

        iv_chunk["moneyness"] = (iv_chunk["strike"] - iv_chunk["S"]).abs() / iv_chunk["S"]
        iv_chunk = iv_chunk[iv_chunk["moneyness"] < 0.05]
        if iv_chunk.empty:
            continue

        iv_chunk = iv_chunk.sort_values("moneyness").drop_duplicates(
            subset=["underlying", "trade_date"], keep="first"
        )

        for row in iv_chunk.itertuples(index=False):
            iv = _implied_vol(row.close, row.S, row.strike, row.dte / 365.0)
            if iv is not None:
                iv_history[row.underlying].append((row.trade_date, iv))

    # Build IV rank results
    iv_rank_data: dict[str, dict] = {}
    for ticker in tickers:
        pairs = sorted(iv_history[ticker])
        if len(pairs) < 20:
            iv_rank_data[ticker] = {
                "iv_current": None, "iv_52w_low": None,
                "iv_52w_high": None, "iv_rank": None,
            }
            continue
        ivs  = [iv for _, iv in pairs]
        lo   = min(ivs)
        hi   = max(ivs)
        cur  = ivs[-1]
        rank = (cur - lo) / (hi - lo) * 100 if hi > lo else 50.0
        iv_rank_data[ticker] = {
            "iv_current":  round(cur * 100, 1),
            "iv_52w_low":  round(lo  * 100, 1),
            "iv_52w_high": round(hi  * 100, 1),
            "iv_rank":     round(rank, 1),
        }

    # Build 5-day avg volume results
    opt_vol_5d: dict[str, float] = {}
    if vol_frames:
        df_vol = pd.concat(vol_frames, ignore_index=True)
        dates = sorted(df_vol["date"].dt.date.unique())
        cutoff_dates = [d for d in dates if d < today][-5:]
        if cutoff_dates:
            df_vol = df_vol[df_vol["date"].dt.date.isin(cutoff_dates)]
            opt_vol_5d = df_vol.groupby("ticker")["volume"].mean().to_dict()

    return iv_rank_data, opt_vol_5d


# ─── Scoring ────────────────────────────────────────────────────────────────────

def _rsi_to_factor(rsi: float | None) -> float:
    """Map RSI to 0–1 proximity score. Ideal zone is 50–75."""
    if rsi is None:  return 0.8
    if rsi >= 50 and rsi < 75: return 1.0
    if rsi >= 75:    return max(0.0, 1.0 - (rsi - 75) / 25)   # linear decay above 75
    return max(0.0, rsi / 50)                                   # linear decay below 50


def _percentile_rank(vals: list[float]) -> list[float]:
    """Map each value to its 0–1 percentile rank within the list (0=min, 1=max)."""
    n = len(vals)
    if n <= 1:
        return [1.0] * n
    arr = np.array(vals, dtype=float)
    ranks = np.zeros(n)
    for pos, idx in enumerate(np.argsort(arr)):
        ranks[idx] = pos / (n - 1)
    return ranks.tolist()


def compute_scores(rows: list[dict], stock_metrics: dict, iv_rank_data: dict) -> None:
    """
    Assign composite scores (0–100) to all rows in-place.

    Each metric is independently percentile-ranked across the full universe
    of valid rows (0 = worst, 1 = best), then combined as a weighted average
    scaled to 0–100. This ensures no single metric can dominate just because
    it happens to be on a larger scale.

    Rows with no valid spread get score = 0.0.
    """
    valid = [
        r for r in rows
        if r["spread"] is not None
        and r["spread"]["rr"] is not None
        and not r["spread"]["impossible"]
    ]

    for r in rows:
        r["score"] = 0.0

    if not valid:
        return

    rr_vals  = [r["spread"]["rr"] for r in valid]
    # IV rank: lower = cheaper options = better → invert so higher = better
    iv_vals  = [100 - (iv_rank_data.get(r["ticker"], {}).get("iv_rank") or 50) for r in valid]
    rsi_vals = [_rsi_to_factor(stock_metrics.get(r["ticker"], {}).get("rsi")) for r in valid]

    rr_ranks  = _percentile_rank(rr_vals)
    iv_ranks  = _percentile_rank(iv_vals)
    rsi_ranks = _percentile_rank(rsi_vals)

    w = SCORE_WEIGHTS
    for i, r in enumerate(valid):
        r["score"] = round(
            100 * (
                w["rr"]  * rr_ranks[i] +
                w["iv"]  * iv_ranks[i] +
                w["rsi"] * rsi_ranks[i]
            ),
            1,
        )


def compute_longcall_scores(rows: list[dict], rank_map, stock_metrics: dict, iv_rank_data: dict) -> None:
    """
    Score long calls (0–100) in-place. No R/R, so rank on momentum strength,
    % move to breakeven (closer = better), IV rank (cheaper = better), and RSI.
    """
    for r in rows:
        r["score"] = 0.0
    if not rows:
        return

    mom_vals = [-(rank_map.get(r["ticker"], 9999)) for r in rows]   # stronger rank → higher
    be_vals  = [-r["be_pct"] for r in rows]                          # closer breakeven → higher
    iv_vals  = [100 - (iv_rank_data.get(r["ticker"], {}).get("iv_rank") or 50) for r in rows]
    rsi_vals = [_rsi_to_factor(stock_metrics.get(r["ticker"], {}).get("rsi")) for r in rows]

    mom_r, be_r, iv_r, rsi_r = (_percentile_rank(v) for v in (mom_vals, be_vals, iv_vals, rsi_vals))
    w = LONGCALL_WEIGHTS
    for i, r in enumerate(rows):
        r["score"] = round(
            100 * (
                w["momentum"]  * mom_r[i] +
                w["breakeven"] * be_r[i] +
                w["iv"]        * iv_r[i] +
                w["rsi"]       * rsi_r[i]
            ),
            1,
        )


# ─── HTML rendering ─────────────────────────────────────────────────────────────

def build_html(
    holdings:      list[dict],
    rows:          list[dict],
    stock_metrics: dict,
    iv_rank_data:  dict,
    earnings:      dict,
    as_of:         str,
) -> str:
    import json as _json

    weight_map = {h["ticker"]: h["weight"] for h in holdings}
    price_map  = {h["ticker"]: h["price"]  for h in holdings}

    # Build per-row JSON for Alpine
    row_data = []
    for row in rows:
        ticker = row["ticker"]
        sp     = row["spread"]
        long_  = row["long"]
        short_ = row["short"]
        sm     = stock_metrics.get(ticker, {})
        ivrd   = iv_rank_data.get(ticker, {})
        earn   = earnings.get(ticker)
        row_data.append({
            "score":            row["score"],
            "ticker":           ticker,
            "weight":           weight_map.get(ticker, 0),
            "price":            price_map.get(ticker),
            "rsi":              sm.get("rsi"),
            "from_52w_high":    sm.get("from_52w_high"),
            "earnings":         earn,
            "earnings_in_window": bool(earn and str(date.today()) <= earn <= row["expiry"]),
            "expiry":           row["expiry"],
            "dte":              row["dte"],
            "long_strike":      long_["strike"],
            "long_ask":         long_["price"],
            "long_iv":          round(long_["iv"] * 100, 1) if long_["iv"] else None,
            "long_iv_rank":     ivrd.get("iv_rank"),
            "long_iv_52w_low":  ivrd.get("iv_52w_low"),
            "long_iv_52w_high": ivrd.get("iv_52w_high"),
            "long_oi":          long_["oi"],
            "short_strike":     short_["strike"],
            "width":            sp["width"],
            "cost":             sp["cost"],
            "max_profit":       sp["max_profit"],
            "breakeven":        sp["breakeven"],
            "be_pct":           sp["be_pct"],
            "rr":               sp["rr"],
            "win_prob":         sp["win_prob"],
            "adj_rr":           sp["adj_rr"],
            "stale":            sp["stale"],
            "impossible":       sp["impossible"],
        })

    data_json = _json.dumps(row_data)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daily Trader — {as_of}</title>
<script src="https://cdn.tailwindcss.com"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js"></script>
<script>
  tailwind.config = {{
    theme: {{
      extend: {{
        fontFamily: {{
          sans: ['-apple-system','BlinkMacSystemFont','SF Pro Display','Segoe UI','system-ui','sans-serif'],
          mono: ['SF Mono','ui-monospace','Fira Code','monospace'],
        }}
      }}
    }}
  }}
</script>
<style>
  [x-cloak] {{ display: none !important; }}
  .num      {{ font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }}
  .col-sep  {{ border-left: 1px solid rgb(30 41 59 / 0.6); }}
  thead th  {{ position: sticky; top: 0; z-index: 10; backdrop-filter: blur(8px); }}
</style>
</head>
<body class="bg-slate-950 text-slate-200 min-h-screen" style="font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',system-ui,sans-serif">

<div class="max-w-[1900px] mx-auto px-6 py-8">

  <!-- Header -->
  <div class="mb-7">
    <h1 class="text-lg font-bold text-white tracking-tight">Daily Trader</h1>
    <p class="text-slate-600 text-xs mt-0.5">{as_of} &nbsp;·&nbsp; Top {TOP_N} momentum large-caps &nbsp;·&nbsp; ATM bull call spreads {MIN_DTE}–{MAX_DTE} DTE &nbsp;·&nbsp; score = 50% R/R · 15% IV rank · 35% RSI (percentile-ranked)</p>
  </div>

  <!-- Table -->
  <div x-data="app()" x-cloak>

    <!-- Controls -->
    <div class="flex items-center gap-4 mb-3">
      <div class="relative">
        <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-600 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
        </svg>
        <input x-model="search" type="text" placeholder="Filter ticker…"
          class="bg-slate-900 border border-slate-800 rounded-lg pl-7 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-600 w-36 transition-colors">
      </div>
      <label class="flex items-center gap-2 text-xs text-slate-600">
        Min score
        <input x-model.number="minScore" type="range" min="0" max="100" step="5" class="w-24 accent-emerald-500 cursor-pointer">
        <span x-text="minScore" class="text-slate-400 num w-5 text-xs"></span>
      </label>
      <span class="text-slate-700 text-xs ml-auto num" x-text="filteredRows.length + ' spreads'"></span>
    </div>

    <!-- Table container -->
    <div class="rounded-xl border border-slate-800/70 overflow-hidden overflow-x-auto">
      <table class="w-full border-collapse" style="font-size:11.5px">
        <thead>
          <tr class="bg-slate-900/90 border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-600">
            <th @click="sort('score')"        class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none whitespace-nowrap transition-colors">Score<span x-text="si('score')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('ticker')"       class="px-3 py-2.5 text-left   cursor-pointer hover:text-slate-400 select-none transition-colors">Ticker<span x-text="si('ticker')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('weight')"       class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none transition-colors">Wt<span x-text="si('weight')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('price')"        class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none transition-colors">Price<span x-text="si('price')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('rsi')"          class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none transition-colors">RSI<span x-text="si('rsi')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('from_52w_high')" class="px-3 py-2.5 text-right cursor-pointer hover:text-slate-400 select-none whitespace-nowrap transition-colors">52w Hi<span x-text="si('from_52w_high')" class="ml-0.5 text-slate-700"></span></th>
            <th                               class="px-3 py-2.5 text-right whitespace-nowrap">Earnings</th>
            <th @click="sort('dte')"          class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none col-sep transition-colors">DTE<span x-text="si('dte')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('long_strike')"  class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none whitespace-nowrap transition-colors">Long K<span x-text="si('long_strike')" class="ml-0.5 text-slate-700"></span></th>
            <th                               class="px-3 py-2.5 text-right whitespace-nowrap">Ask</th>
            <th @click="sort('long_iv')"      class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none transition-colors">IV<span x-text="si('long_iv')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('long_iv_rank')" class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none whitespace-nowrap transition-colors">IV Rank<span x-text="si('long_iv_rank')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('long_oi')"      class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none transition-colors">OI<span x-text="si('long_oi')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('short_strike')" class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none whitespace-nowrap col-sep transition-colors">Short K<span x-text="si('short_strike')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('width')"        class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none transition-colors">Width<span x-text="si('width')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('cost')"         class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none whitespace-nowrap transition-colors">Cost/ct<span x-text="si('cost')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('max_profit')"   class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none whitespace-nowrap transition-colors">Max Profit<span x-text="si('max_profit')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('rr')"           class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none transition-colors">R/R<span x-text="si('rr')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('win_prob')"     class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none whitespace-nowrap transition-colors">Win%<span x-text="si('win_prob')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('adj_rr')"       class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none whitespace-nowrap transition-colors">Adj R/R<span x-text="si('adj_rr')" class="ml-0.5 text-slate-700"></span></th>
            <th @click="sort('be_pct')"       class="px-3 py-2.5 text-right  cursor-pointer hover:text-slate-400 select-none whitespace-nowrap transition-colors">Breakeven<span x-text="si('be_pct')" class="ml-0.5 text-slate-700"></span></th>
          </tr>
        </thead>
        <tbody>
          <template x-for="row in filteredRows" :key="row.ticker + row.expiry">
            <tr class="border-b border-slate-800/40 hover:bg-white/[0.02] transition-colors">

              <!-- Score badge -->
              <td class="px-3 py-2.5 text-right">
                <span class="inline-flex items-center justify-center rounded-md px-2 py-0.5 text-xs font-bold num min-w-[2.2rem]"
                      :class="scoreCls(row.score)" x-text="row.score"></span>
              </td>

              <!-- Ticker -->
              <td class="px-3 py-2.5 text-left font-bold text-white tracking-wide" x-text="row.ticker"></td>

              <!-- Weight -->
              <td class="px-3 py-2.5 text-right text-slate-600 num" x-text="(row.weight*100).toFixed(1)+'%'"></td>

              <!-- Price -->
              <td class="px-3 py-2.5 text-right text-slate-300 num" x-text="row.price!=null ? '$'+row.price.toFixed(2) : '—'"></td>

              <!-- RSI -->
              <td class="px-3 py-2.5 text-right num" :class="rsiCls(row.rsi)" x-text="row.rsi ?? '—'"></td>

              <!-- 52w High -->
              <td class="px-3 py-2.5 text-right num"
                  :class="row.from_52w_high!=null && row.from_52w_high < -10 ? 'text-red-400' : 'text-emerald-400'"
                  x-text="row.from_52w_high!=null ? row.from_52w_high.toFixed(1)+'%' : '—'"></td>

              <!-- Earnings -->
              <td class="px-3 py-2.5 text-right">
                <span :class="row.earnings_in_window ? 'text-red-400' : 'text-slate-600'"
                      x-text="row.earnings ?? '—'"></span>
                <template x-if="row.earnings_in_window"><span class="text-red-400"> ⚠</span></template>
              </td>

              <!-- DTE -->
              <td class="px-3 py-2.5 text-right num col-sep">
                <span class="text-slate-300" x-text="row.dte"></span><span class="text-slate-700">d</span>
                <span class="text-slate-700 ml-1 text-[10px]" x-text="row.expiry.slice(5)"></span>
              </td>

              <!-- Long Strike -->
              <td class="px-3 py-2.5 text-right text-emerald-400 font-semibold num" x-text="row.long_strike"></td>

              <!-- Ask -->
              <td class="px-3 py-2.5 text-right text-slate-400 num">
                <span x-text="row.long_ask!=null ? '$'+row.long_ask.toFixed(2) : '—'"></span>
                <template x-if="row.stale"><span class="text-slate-700">~</span></template>
              </td>

              <!-- IV -->
              <td class="px-3 py-2.5 text-right num" :class="ivCls(row.long_iv)"
                  x-text="row.long_iv!=null ? row.long_iv.toFixed(1)+'%' : '—'"></td>

              <!-- IV Rank -->
              <td class="px-3 py-2.5 text-right num">
                <template x-if="row.long_iv_rank!=null">
                  <span>
                    <span :class="ivRankCls(row.long_iv_rank)" x-text="Math.round(row.long_iv_rank)"></span>
                    <span class="text-slate-700 text-[10px] ml-0.5"
                          x-text="'('+row.long_iv_52w_low+'–'+row.long_iv_52w_high+')'"></span>
                  </span>
                </template>
                <template x-if="row.long_iv_rank==null"><span class="text-slate-700">—</span></template>
              </td>

              <!-- OI -->
              <td class="px-3 py-2.5 text-right text-slate-500 num" x-text="fmtK(row.long_oi)"></td>

              <!-- Short Strike -->
              <td class="px-3 py-2.5 text-right text-slate-300 font-semibold num col-sep" x-text="row.short_strike"></td>

              <!-- Width -->
              <td class="px-3 py-2.5 text-right text-slate-600 num" x-text="'$'+row.width"></td>

              <!-- Cost/ct -->
              <td class="px-3 py-2.5 text-right text-slate-300 num" x-text="'$'+row.cost"></td>

              <!-- Max Profit -->
              <td class="px-3 py-2.5 text-right text-emerald-400 num" x-text="'$'+row.max_profit"></td>

              <!-- R/R -->
              <td class="px-3 py-2.5 text-right text-yellow-400 font-semibold num" x-text="row.rr.toFixed(1)+'x'"></td>

              <!-- Win% -->
              <td class="px-3 py-2.5 text-right num">
                <span x-text="row.win_prob != null ? row.win_prob.toFixed(1)+'%' : '—'"
                      :class="row.win_prob >= 50 ? 'text-emerald-400' : row.win_prob >= 35 ? 'text-yellow-400' : 'text-red-400'"></span>
              </td>

              <!-- Adj R/R -->
              <td class="px-3 py-2.5 text-right font-semibold num">
                <span x-text="row.adj_rr != null ? (row.adj_rr > 0 ? '+' : '') + row.adj_rr.toFixed(2) : '—'"
                      :class="row.adj_rr > 0 ? 'text-emerald-400' : 'text-red-400'"></span>
              </td>

              <!-- Breakeven -->
              <td class="px-3 py-2.5 text-right num">
                <span class="text-slate-400" x-text="row.breakeven.toFixed(2)"></span>
                <span class="ml-1.5 rounded px-1.5 py-0.5 text-[10px] font-medium num"
                      :class="beCls(row.be_pct)" x-text="'+'+row.be_pct.toFixed(1)+'%'"></span>
                <template x-if="row.impossible"><span class="text-red-400 ml-0.5">⚠</span></template>
              </td>

            </tr>
          </template>
          <template x-if="filteredRows.length === 0">
            <tr><td colspan="21" class="px-6 py-14 text-center text-slate-700">No spreads match your filter.</td></tr>
          </template>
        </tbody>
      </table>
    </div>

    <p class="text-[10px] text-slate-700 mt-3">
      ~ ask from open (may be stale) &nbsp;·&nbsp;
      ⚠ breakeven = can never reach max profit &nbsp;·&nbsp;
      ⚠ earnings = event falls within option window
    </p>
  </div>
</div>

<script>
const DATA = {data_json};

function app() {{
  return {{
    rows: DATA,
    search: '',
    minScore: 0,
    sortKey: 'score',
    sortAsc: false,

    get filteredRows() {{
      const q = this.search.toUpperCase().trim();
      return [...this.rows]
        .filter(r => !q || r.ticker.includes(q))
        .filter(r => r.score >= this.minScore)
        .sort((a, b) => {{
          const av = a[this.sortKey] ?? (this.sortAsc ? Infinity : -Infinity);
          const bv = b[this.sortKey] ?? (this.sortAsc ? Infinity : -Infinity);
          const cmp = typeof av === 'string' ? av.localeCompare(bv) : av - bv;
          return this.sortAsc ? cmp : -cmp;
        }});
    }},

    sort(k) {{ this.sortKey === k ? (this.sortAsc = !this.sortAsc) : (this.sortKey = k, this.sortAsc = false); }},
    si(k)   {{ return this.sortKey !== k ? '' : (this.sortAsc ? ' ↑' : ' ↓'); }},

    scoreCls(s)  {{ return s >= 70 ? 'bg-emerald-500/15 text-emerald-400' : s >= 40 ? 'bg-yellow-500/15 text-yellow-400' : 'bg-slate-800 text-slate-600'; }},
    rsiCls(r)    {{ return r == null ? 'text-slate-700' : r >= 75 ? 'text-red-400' : r >= 50 ? 'text-emerald-400' : 'text-slate-500'; }},
    ivCls(iv)    {{ return iv == null ? 'text-slate-700' : iv >= 60 ? 'text-red-400' : iv >= 35 ? 'text-yellow-400' : 'text-emerald-400'; }},
    ivRankCls(r) {{ return r <= 30 ? 'text-emerald-400' : r <= 60 ? 'text-yellow-400' : 'text-red-400'; }},
    beCls(p)     {{ return p < 2 ? 'bg-emerald-500/10 text-emerald-500' : p < 5 ? 'bg-yellow-500/10 text-yellow-500' : 'bg-red-500/10 text-red-400'; }},
    fmtK(v)      {{ if (!v) return '—'; return v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1000 ? Math.round(v/1000)+'K' : v; }},
  }};
}}
</script>
</body>
</html>"""


# ─── Candidate pipeline ──────────────────────────────────────────────────────

def build_candidates() -> dict:
    """
    Run the full screener pipeline and return structured data (no HTML):
      {
        "as_of":         "YYYY-MM-DD",
        "holdings":      [{ticker, weight, price}, ...],
        "rows":          [scored spread rows, sorted by score desc],
        "stock_metrics": {ticker: {...}},
        "iv_rank_data":  {ticker: {...}},
        "earnings":      {ticker: date|None},
      }
    This is what both the standalone HTML report and the live web app consume.
    """
    # 1. Load market cap, filter universe
    market_cap = pd.read_csv("data/market_cap.csv")
    if "composite_figi" in market_cap.columns:
        market_cap = (
            market_cap.sort_values("market_cap", ascending=False)
            .drop_duplicates(subset="composite_figi", keep="first")
        )
    large_caps = (
        set(market_cap.loc[market_cap["market_cap"] >= 20_000_000_000, "ticker"])
        - EXCLUDED
    )
    mc_map = market_cap.set_index("ticker")["market_cap"]

    # 2. Compute momentum signal from flat file
    print("Loading stocks flat file + computing momentum...")
    df = pd.read_csv(
        "data/stocks_daily.csv",
        dtype={"open": "float64", "high": "float64", "low": "float64",
               "close": "float64", "volume": "float64", "transactions": "float64"},
        parse_dates=["date"],
    )
    df = df.dropna(subset=["ticker"])
    df = df[df["ticker"].isin(large_caps)]
    df = add_momentum_rank(df)
    df["momentum_rank_prev"] = df.groupby("ticker")["momentum_rank"].shift(1)

    latest = df["date"].max()
    snap = (
        df[df["date"] == latest][["ticker", "close", "momentum_rank_prev"]]
        .dropna(subset=["momentum_rank_prev"])
        .copy()
    )
    snap["_rank"] = snap["momentum_rank_prev"].rank(ascending=False, method="first")
    top = snap[snap["_rank"] <= TOP_N].copy()
    top["market_cap"] = top["ticker"].map(mc_map)
    top["weight"]     = top["market_cap"] / top["market_cap"].sum()
    top = top.sort_values("weight", ascending=False).reset_index(drop=True)
    tickers = list(top["ticker"])
    rank_map = top.set_index("ticker")["_rank"].astype(int)

    # 3. Compute stock metrics + IV rank + option vol 5d from flat files
    stock_metrics             = compute_stock_metrics(df, tickers)
    iv_rank_data, opt_vol_5d  = compute_options_data(tickers, df, date.today())

    print("Fetching analyst ratings (FMP)...")
    ratings = fetch_analyst_ratings(tickers)
    names   = fetch_company_names(tickers)

    # 4. Fetch live prices
    print("Fetching live stock prices...")
    live_prices = fetch_stock_prices(tickers)
    top["live_price"] = top["ticker"].map(live_prices).fillna(top["close"])

    holdings = [
        {"ticker": r["ticker"], "weight": r["weight"], "price": r["live_price"],
         "rank": int(rank_map[r["ticker"]]), "market_cap": float(r["market_cap"])}
        for _, r in top.iterrows()
    ]
    price_map = top.set_index("ticker")["live_price"]
    today     = date.today()

    # 5. Fetch option chains + earnings in parallel
    print("Fetching option chains + earnings (parallel)...")

    vol_map = {t: stock_metrics[t].get("vol_63d") for t in tickers}

    def _build(row):
        t = row["ticker"]
        p = price_map.get(t)
        if p is None:
            return {"spreads": [], "long_call": None}
        print(f"  options {t}...")
        return build_for_ticker(t, p, today, vol_map.get(t), opt_vol_5d)

    spread_rows = []
    lc_sets: dict[str, list] = {"atm": [], "itm": [], "otm": []}
    with ThreadPoolExecutor(max_workers=20) as pool:
        earn_future  = pool.submit(fetch_earnings_dates, tickers, today)
        opt_futures  = {pool.submit(_build, row): row["ticker"] for _, row in top.iterrows()}
        earnings     = earn_future.result()
        for f in as_completed(opt_futures):
            res = f.result()
            spread_rows.extend(res["spreads"])
            for m, lc in res["long_calls"].items():
                if lc is not None:
                    lc_sets[m].append(lc)

    # 6. Drop rows with no valid spread, score and sort the rest
    spread_rows = [r for r in spread_rows if r["spread"] is not None]
    compute_scores(spread_rows, stock_metrics, iv_rank_data)
    spread_rows.sort(key=lambda r: r["score"], reverse=True)

    # Long calls: score (momentum + cheapness) and sort each moneyness set
    for m in lc_sets:
        compute_longcall_scores(lc_sets[m], rank_map, stock_metrics, iv_rank_data)
        lc_sets[m].sort(key=lambda r: r["score"], reverse=True)

    return {
        "as_of":         str(today),
        "holdings":      holdings,
        "rows":          spread_rows,
        "long_calls":    lc_sets,
        "stock_metrics": stock_metrics,
        "iv_rank_data":  iv_rank_data,
        "earnings":      earnings,
        "ratings":       ratings,
        "names":         names,
    }


# ─── Main (standalone HTML report) ───────────────────────────────────────────────

def main():
    c = build_candidates()
    html = build_html(
        c["holdings"], c["rows"], c["stock_metrics"],
        c["iv_rank_data"], c["earnings"], c["as_of"],
    )
    OUTPUT_HTML.write_text(html)
    print(f"\nOutput: {OUTPUT_HTML}")
    webbrowser.open(OUTPUT_HTML.as_uri())


if __name__ == "__main__":
    main()
