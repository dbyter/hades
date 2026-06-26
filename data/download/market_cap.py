"""
Download market cap for all active US stocks from Massive REST API.

Output:
    ~/dev/hades/stockdata/market_cap.csv  (ticker, market_cap)
"""

import csv
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode
import json

from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv(Path(__file__).parent.parent.parent / ".env")

API_KEY   = os.environ["MASSIVE_API_KEY"]
BASE_URL  = "https://api.massive.com"
OUTPUT    = Path(__file__).parent.parent / "market_cap.csv"
WORKERS   = 50


def api_get(path: str, params: dict = {}) -> dict:
    url = f"{BASE_URL}{path}?{urlencode({**params, 'apiKey': API_KEY})}"
    with urlopen(url) as r:
        return json.loads(r.read())


def all_tickers() -> list[str]:
    """Page through /v3/reference/tickers and return all active US stock symbols."""
    tickers = []
    params = {"market": "stocks", "active": "true", "limit": 1000}
    path = "/v3/reference/tickers"
    while True:
        data = api_get(path, params)
        for r in data.get("results", []):
            tickers.append(r["ticker"])
        next_url = data.get("next_url")
        if not next_url:
            break
        # next_url contains cursor param — extract it
        cursor = next_url.split("cursor=")[-1].split("&")[0]
        params = {"cursor": cursor}
        path = "/v3/reference/tickers"
    return tickers


def fetch_ticker_info(ticker: str) -> dict:
    try:
        data = api_get(f"/v3/reference/tickers/{ticker}").get("results", {})
        return {
            "ticker": ticker,
            "market_cap": data.get("market_cap"),
            "composite_figi": data.get("composite_figi"),
        }
    except Exception:
        return {"ticker": ticker, "market_cap": None, "composite_figi": None}


def main():
    print("Fetching ticker list...")
    tickers = all_tickers()
    print(f"Found {len(tickers)} tickers. Fetching market caps...")

    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_ticker_info, t): t for t in tickers}
        with tqdm(total=len(tickers), unit="ticker") as bar:
            for future in as_completed(futures):
                rows.append(future.result())
                bar.update(1)

    rows.sort(key=lambda r: r["ticker"])
    with open(OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "market_cap", "composite_figi"])
        writer.writeheader()
        writer.writerows(rows)

    filled = sum(1 for r in rows if r["market_cap"] is not None)
    print(f"\nDone! {filled}/{len(rows)} tickers have market cap data.")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
