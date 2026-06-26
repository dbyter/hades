"""
Generate per-ticker qualitative insights: news sentiment + fundamentals → OpenAI synthesis.

For each top-N momentum ticker:
  1. Fetch last 7 days of news from Massive (pre-scored per-ticker sentiment)
  2. Fetch last 4 quarters of financials from Massive (revenue, EPS, margins)
  3. Call OpenAI gpt-4o-mini to synthesize into trade-relevant structured insights

Output: data/insights_cache.json
  {
    "date": "YYYY-MM-DD",
    "tickers": {
      "AAPL": {
        "as_of": "YYYY-MM-DD",
        "news": [...],           # raw articles with sentiment
        "financials": [...],     # last 4 quarters
        "fundamental_trend": {   # computed growth metrics
            "rev_yoy_pct": ...,
            "eps_yoy_pct": ...,
            ...
        },
        "insights": {            # OpenAI output
            "positive": [...],
            "negative": [...],
            "fundamental": "...",
            "bias": "bullish|bearish|neutral",
            "key_risk": "..."
        }
      }
    }
  }
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT))
from strategy.momentum import add_momentum_rank

CACHE          = ROOT / "data" / "insights_cache.json"
API_KEY        = os.environ["MASSIVE_API_KEY"]
BASE_URL       = "https://api.massive.com"
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TOP_N          = 50
NEWS_DAYS      = 7
EXCLUDED       = {"GOOG", "BRK.A", "NWS"}


# ─── Massive helpers ───────────────────────────────────────────────────────────

def _get(path: str, params: dict = {}) -> dict:
    url = f"{BASE_URL}{path}?{urlencode({**params, 'apiKey': API_KEY})}"
    with urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def fetch_news(ticker: str) -> list[dict]:
    """Last 7 days of news; only articles where this ticker has non-neutral sentiment."""
    since = str(date.today() - timedelta(days=NEWS_DAYS))
    try:
        data = _get("/v2/reference/news", {
            "ticker": ticker, "limit": 20, "published_utc.gte": since,
        })
        out = []
        for r in data.get("results", []):
            insight = next(
                (i for i in r.get("insights", []) if i.get("ticker") == ticker), None
            )
            out.append({
                "title":       r.get("title", ""),
                "published":   r.get("published_utc", "")[:10],
                "url":         r.get("article_url", ""),
                "publisher":   r.get("publisher", {}).get("name", ""),
                "description": r.get("description", ""),
                "sentiment":   insight.get("sentiment")           if insight else "neutral",
                "reasoning":   insight.get("sentiment_reasoning") if insight else "",
            })
        return out
    except Exception as e:
        print(f"  [news] {ticker}: {e}")
        return []


def fetch_financials(ticker: str) -> list[dict]:
    """Last 4 quarters: revenue, EPS, operating income, gross profit."""
    try:
        data = _get("/vX/reference/financials", {
            "ticker": ticker, "timeframe": "quarterly",
            "limit": 4, "sort": "period_of_report_date", "order": "desc",
        })
        out = []
        for r in data.get("results", []):
            inc = r.get("financials", {}).get("income_statement", {})
            def _val(key):
                return inc.get(key, {}).get("value")
            out.append({
                "period":           r.get("end_date", "")[:7],
                "fiscal_period":    r.get("fiscal_period", ""),
                "revenue":          _val("revenues"),
                "eps":              _val("basic_earnings_per_share"),
                "eps_diluted":      _val("diluted_earnings_per_share"),
                "operating_income": _val("operating_income_loss"),
                "gross_profit":     _val("gross_profit"),
                "net_income":       _val("net_income_loss"),
                "rd_expense":       _val("research_and_development"),
            })
        return out
    except Exception as e:
        print(f"  [financials] {ticker}: {e}")
        return []


def compute_fundamental_trend(quarters: list[dict]) -> dict:
    """Derive YoY growth rates and margin trends from the 4-quarter history."""
    if len(quarters) < 2:
        return {}

    trend = {}

    # YoY requires comparing quarter[0] vs quarter[2] (same quarter last year)
    if len(quarters) >= 4:
        q_now  = quarters[0]
        q_year = quarters[3]  # same quarter ~1 year ago (approx; exact if fiscal)

        if q_now.get("revenue") and q_year.get("revenue") and q_year["revenue"] != 0:
            trend["rev_yoy_pct"] = round(
                (q_now["revenue"] - q_year["revenue"]) / abs(q_year["revenue"]) * 100, 1
            )
        if q_now.get("eps") and q_year.get("eps") and q_year["eps"] != 0:
            trend["eps_yoy_pct"] = round(
                (q_now["eps"] - q_year["eps"]) / abs(q_year["eps"]) * 100, 1
            )

    # QoQ sequential
    q0, q1 = quarters[0], quarters[1]
    if q0.get("revenue") and q1.get("revenue") and q1["revenue"] != 0:
        trend["rev_qoq_pct"] = round(
            (q0["revenue"] - q1["revenue"]) / abs(q1["revenue"]) * 100, 1
        )

    # Operating margin (most recent quarter)
    if q0.get("operating_income") and q0.get("revenue") and q0["revenue"] != 0:
        trend["op_margin_pct"] = round(q0["operating_income"] / q0["revenue"] * 100, 1)

    # Revenue trend direction over 4Q (simple linear slope sign)
    revs = [q.get("revenue") for q in quarters if q.get("revenue")]
    if len(revs) >= 3:
        xs = list(range(len(revs)))
        slope = float(np.polyfit(xs, revs, 1)[0])
        trend["rev_trend"] = "accelerating" if slope > 0 else "decelerating"

    return trend


# ─── OpenAI synthesis ─────────────────────────────────────────────────────────

def _fmt_financials(quarters: list[dict], trend: dict) -> str:
    lines = []
    for q in quarters:
        rev = f"${q['revenue']/1e9:.1f}B" if q.get("revenue") else "—"
        eps = f"${q['eps']:.2f}"          if q.get("eps")     else "—"
        op  = (f", OpInc ${q['operating_income']/1e9:.1f}B"
               if q.get("operating_income") else "")
        lines.append(f"  {q['period']} ({q['fiscal_period']}): Rev {rev}, EPS {eps}{op}")

    growth_parts = []
    if "rev_yoy_pct" in trend:
        growth_parts.append(f"Revenue YoY: {trend['rev_yoy_pct']:+.1f}%")
    if "eps_yoy_pct" in trend:
        growth_parts.append(f"EPS YoY: {trend['eps_yoy_pct']:+.1f}%")
    if "op_margin_pct" in trend:
        growth_parts.append(f"Op margin: {trend['op_margin_pct']:.1f}%")
    if "rev_trend" in trend:
        growth_parts.append(f"Revenue trend: {trend['rev_trend']}")

    return "\n".join(lines) + ("\n  Growth: " + " | ".join(growth_parts) if growth_parts else "")


def _fmt_news(articles: list[dict]) -> str:
    lines = []
    for a in articles:
        if a["sentiment"] != "neutral" and a["reasoning"]:
            lines.append(
                f"  [{a['published']}] {a['sentiment'].upper()} — {a['title']}\n"
                f"    {a['reasoning'][:200]}"
            )
    return "\n".join(lines) if lines else "  No significant non-neutral news this week."


def synthesize(ticker: str, news: list[dict], financials: list[dict],
               trend: dict) -> dict:
    """Call OpenAI to produce structured trade-relevant insights."""
    import urllib.request

    fin_text  = _fmt_financials(financials, trend)
    news_text = _fmt_news(news)

    prompt = f"""You are a sell-side analyst reviewing {ticker} for a client considering a BULLISH options trade (bull call spread or long call, ~30-55 DTE).

RECENT NEWS (last {NEWS_DAYS} days, sentiment pre-scored per-article):
{news_text}

LAST 4 QUARTERS:
{fin_text}

Return a JSON object with EXACTLY these fields — be specific, concise, and trade-relevant:
{{
  "positive": ["<2-3 bullet strings: tailwinds / catalysts that support a near-term move up>"],
  "negative": ["<2-3 bullet strings: risks / headwinds that could hurt the trade>"],
  "fundamental": "<1 sentence: revenue + EPS trend in plain English, e.g. 'Revenue grew 12% YoY with expanding margins'>",
  "bias": "<one of: bullish | bearish | neutral — your overall read given news + fundamentals>",
  "key_risk": "<single biggest risk to a bullish position in ≤ 15 words>"
}}"""

    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "max_tokens": 500,
        "temperature": 0.2,
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())

    return json.loads(resp["choices"][0]["message"]["content"])


# ─── Ticker selection ──────────────────────────────────────────────────────────

def get_top_tickers() -> list[str]:
    market_cap = pd.read_csv(ROOT / "data" / "market_cap.csv")
    if "composite_figi" in market_cap.columns:
        market_cap = (
            market_cap.sort_values("market_cap", ascending=False)
            .drop_duplicates(subset="composite_figi", keep="first")
        )
    large_caps = (
        set(market_cap.loc[market_cap["market_cap"] >= 20_000_000_000, "ticker"])
        - EXCLUDED
    )

    df = pd.read_csv(
        ROOT / "data" / "stocks_daily.csv",
        dtype={"close": "float64"},
        parse_dates=["date"],
        usecols=["date", "ticker", "close", "volume"],
    )
    df = df.dropna(subset=["ticker"])
    df = df[df["ticker"].isin(large_caps)]
    df = add_momentum_rank(df)
    df["momentum_rank_prev"] = df.groupby("ticker")["momentum_rank"].shift(1)

    latest = df["date"].max()
    snap = (
        df[df["date"] == latest][["ticker", "momentum_rank_prev"]]
        .dropna()
        .copy()
    )
    snap["_rank"] = snap["momentum_rank_prev"].rank(ascending=False, method="first")
    top = snap[snap["_rank"] <= TOP_N].sort_values("_rank")
    return list(top["ticker"])


# ─── Per-ticker worker ────────────────────────────────────────────────────────

def process_ticker(ticker: str) -> tuple[str, dict | None]:
    print(f"  {ticker}...")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            news_f = pool.submit(fetch_news, ticker)
            fin_f  = pool.submit(fetch_financials, ticker)
            news       = news_f.result()
            financials = fin_f.result()

        trend = compute_fundamental_trend(financials)

        try:
            ai = synthesize(ticker, news, financials, trend)
        except Exception as e:
            print(f"  [openai] {ticker}: {e}")
            ai = {"positive": [], "negative": [], "fundamental": "", "bias": "neutral", "key_risk": ""}

        return ticker, {
            "as_of":              str(date.today()),
            "news":               news,
            "financials":         financials,
            "fundamental_trend":  trend,
            "insights":           ai,
        }
    except Exception as e:
        print(f"  [error] {ticker}: {e}")
        return ticker, None


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Selecting top tickers...")
    tickers = get_top_tickers()
    print(f"  {len(tickers)} tickers: {', '.join(tickers[:10])}...")

    # Load existing cache — only re-fetch tickers that are stale or missing
    today = str(date.today())
    cache: dict = {}
    if CACHE.exists():
        try:
            blob = json.loads(CACHE.read_text())
            if blob.get("date") == today:
                cache = blob.get("tickers", {})
        except Exception:
            pass

    fresh    = [t for t in tickers if t not in cache]
    skipped  = len(tickers) - len(fresh)
    if skipped:
        print(f"  {skipped} tickers already cached for today — skipping")
    if not fresh:
        print("All tickers up to date.")
        return

    print(f"Fetching insights for {len(fresh)} tickers (parallel)...")
    results: dict[str, dict] = {}
    # Cap at 10 workers — OpenAI rate limits are the bottleneck
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(process_ticker, t): t for t in fresh}
        for f in as_completed(futures):
            ticker, data = f.result()
            if data:
                results[ticker] = data

    cache.update(results)
    CACHE.write_text(json.dumps({"date": today, "tickers": cache}, indent=2))
    print(f"\n✓ Insights cached for {len(cache)} tickers → {CACHE}")


if __name__ == "__main__":
    main()
