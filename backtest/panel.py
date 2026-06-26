"""
Options panel — preprocess data/options_daily.csv into a fast, queryable form.

The raw flat file is one row per (date, OPRA-contract) with OHLCV but no
open-interest, no bid/ask, no greeks. This module does a single chunked pass to:
  - keep CALLS only on the momentum-eligible large-cap universe
  - parse the OPRA symbol → underlying, expiry, strike
  - cache the result to data/options_panel.parquet

`OptionsPanel` then serves point-in-time queries the engine needs:
  - chain(underlying, asof, min_dte, max_dte) → candidate contracts that day
  - quote(underlying, expiry, strike, on_date) → that contract's OHLCV that day

Build once:   uv run python -m backtest.panel
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

DATA_DIR      = Path(__file__).parent.parent / "data"
OPTIONS_CSV   = DATA_DIR / "options_daily.csv"
STOCKS_CSV    = DATA_DIR / "stocks_daily.csv"
MARKET_CAP    = DATA_DIR / "market_cap.csv"
PANEL_PARQUET = DATA_DIR / "options_panel.parquet"

EXCLUDED = {"GOOG", "BRK.A", "NWS"}
MIN_MCAP = 20_000_000_000

# We enter at 30–90 DTE and hold to expiry, so a held contract never exceeds
# ~90 DTE. Drop everything outside this band at build time (kills LEAPS bloat).
MAX_PANEL_DTE = 100

# O:<underlying><yy><mm><dd><C|P><strike*1000>
_SYM_RE = r"^O:([A-Z./]+)(\d{2})(\d{2})(\d{2})([CP])(\d+)$"


def large_cap_universe() -> set[str]:
    """Tickers with market cap ≥ MIN_MCAP, de-duped by composite_figi, minus excluded classes."""
    mc = pd.read_csv(MARKET_CAP)
    if "composite_figi" in mc.columns:
        mc = mc.sort_values("market_cap", ascending=False).drop_duplicates(
            subset="composite_figi", keep="first"
        )
    return set(mc.loc[mc["market_cap"] >= MIN_MCAP, "ticker"]) - EXCLUDED


def build_panel(universe: set[str] | None = None, chunksize: int = 1_000_000) -> pd.DataFrame:
    """One chunked pass over the options flat file → tidy call-options panel, cached to parquet."""
    universe = universe or large_cap_universe()
    print(f"Building options panel for {len(universe)} tickers from {OPTIONS_CSV.name}...")

    keep = []
    for chunk in pd.read_csv(
        OPTIONS_CSV,
        chunksize=chunksize,
        usecols=["date", "ticker", "open", "close", "volume", "transactions"],
        parse_dates=["date"],
    ):
        underlying = chunk["ticker"].str.extract(r"^O:([A-Z./]+)\d", expand=False)
        mask = underlying.isin(universe)
        if not mask.any():
            continue
        chunk = chunk[mask].copy()

        parts = chunk["ticker"].str.extract(_SYM_RE)
        parts.columns = ["underlying", "yy", "mm", "dd", "opt_type", "strike_str"]
        chunk = pd.concat([chunk.reset_index(drop=True), parts.reset_index(drop=True)], axis=1)
        chunk = chunk[chunk["opt_type"] == "C"].dropna(subset=["yy"])
        if chunk.empty:
            continue

        chunk["strike"] = chunk["strike_str"].astype(float) / 1000.0
        chunk["expiry"] = pd.to_datetime(
            "20" + chunk["yy"] + "-" + chunk["mm"] + "-" + chunk["dd"]
        )
        dte = (chunk["expiry"] - chunk["date"]).dt.days
        chunk = chunk[(dte >= 0) & (dte <= MAX_PANEL_DTE)]
        if chunk.empty:
            continue
        keep.append(chunk[["date", "underlying", "expiry", "strike",
                           "open", "close", "volume", "transactions"]])

    if not keep:
        raise RuntimeError("No matching option rows found — check universe / data file.")

    panel = pd.concat(keep, ignore_index=True)
    panel = panel.rename(columns={"date": "trade_date"}).sort_values(
        ["underlying", "trade_date", "expiry", "strike"]
    ).reset_index(drop=True)

    panel.to_parquet(PANEL_PARQUET, index=False)
    print(f"  → {len(panel):,} call-option rows  "
          f"({panel['trade_date'].min().date()} … {panel['trade_date'].max().date()})  "
          f"{panel['underlying'].nunique()} tickers")
    print(f"  → cached to {PANEL_PARQUET}")
    return panel


def load_panel() -> pd.DataFrame:
    if not PANEL_PARQUET.exists():
        return build_panel()
    return pd.read_parquet(PANEL_PARQUET)


class OptionsPanel:
    """In-memory query layer over the cached panel. Build once per backtest run."""

    def __init__(self, df: pd.DataFrame):
        df = df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["expiry"]     = pd.to_datetime(df["expiry"]).dt.date
        self._df = df

        # Per-underlying frame for chain() scans (each is small).
        self._by_underlying = {u: g for u, g in df.groupby("underlying", sort=False)}

        # Quote maps are built lazily per underlying (only tickers we actually
        # trade get materialized) → bounded memory instead of one 18M-key dict.
        self._quote_cache: dict[str, dict] = {}

    def _quote_map(self, underlying: str) -> dict:
        m = self._quote_cache.get(underlying)
        if m is None:
            g = self._by_underlying.get(underlying)
            m = {} if g is None else {
                (r.expiry, r.strike, r.trade_date): (r.open, r.close, r.volume)
                for r in g.itertuples(index=False)
            }
            self._quote_cache[underlying] = m
        return m

    def trading_days(self) -> list[date]:
        return sorted(self._df["trade_date"].unique())

    def underlying_frame(self, underlying: str) -> pd.DataFrame | None:
        """All call rows for one underlying (sorted), or None if not present."""
        return self._by_underlying.get(underlying)

    def underlyings(self) -> set[str]:
        return set(self._by_underlying)

    def chain(self, underlying: str, asof: date, min_dte: int, max_dte: int) -> pd.DataFrame:
        """Calls quoted on `asof` whose expiry falls in [asof+min_dte, asof+max_dte]."""
        g = self._by_underlying.get(underlying)
        if g is None:
            return pd.DataFrame(columns=self._df.columns)
        lo, hi = asof + timedelta(days=min_dte), asof + timedelta(days=max_dte)
        return g[(g["trade_date"] == asof) & (g["expiry"] >= lo) & (g["expiry"] <= hi)]

    def quote(self, underlying: str, expiry: date, strike: float, on_date: date) -> dict | None:
        """OHLCV for one contract on one day. None if it did not trade/quote that day."""
        v = self._quote_map(underlying).get((expiry, strike, on_date))
        if v is None:
            return None
        return {"open": v[0], "close": v[1], "volume": v[2]}


if __name__ == "__main__":
    build_panel()
