import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Allow running as a standalone script from any working directory
sys.path.insert(0, str(Path(__file__).parent.parent))


def add_momentum_rank(df: pd.DataFrame, lookback: int = 252, skip: int = 21, winsor: float = 0.01) -> pd.DataFrame:
    """
    Adds a cross-sectional momentum rank to a daily OHLCV dataframe.

    Steps:
      1. Compute cumulative log return over [t-lookback, t-skip] (skips the most
         recent `skip` days to avoid short-term reversal contamination).
      2. Divide by the annualised realised volatility of daily log-returns over
         the same window to get a risk-adjusted momentum score.
      3. Winsorise the cross-sectional score at the `winsor` / (1-winsor) percentiles.
      4. Rank stocks cross-sectionally (0-100) at each date.

    Args:
        df:       Daily dataframe sorted by (ticker, date) with a 'close' column.
        lookback: Formation window in trading days (default 252 ≈ 1 year).
        skip:     Days to skip at the near end (default 21 ≈ 1 month).
        winsor:   Tail fraction to winsorise (default 1 %).

    Returns:
        df with added columns: 'log_return', 'momentum_raw', 'momentum_score', 'momentum_rank'.
    """
    df = df.copy()
    df = df.sort_values(['ticker', 'date'])

    df['log_return'] = df.groupby('ticker')['close'].transform(
        lambda s: np.log(s / s.shift(1))
    )

    def _score(s: pd.Series) -> pd.Series:
        cum_ret = np.log(s.shift(skip) / s.shift(lookback))
        log_ret = np.log(s / s.shift(1))
        vol = log_ret.shift(skip).rolling(lookback - skip).std() * np.sqrt(252)
        return cum_ret / vol

    df['momentum_raw'] = df.groupby('ticker')['close'].transform(_score)

    def _winsorise(s: pd.Series) -> pd.Series:
        lo, hi = s.quantile(winsor), s.quantile(1 - winsor)
        return s.clip(lo, hi)

    df['momentum_score'] = df.groupby('date')['momentum_raw'].transform(_winsorise)

    df['momentum_rank'] = (
        df.groupby('date')['momentum_score']
        .rank(pct=True, na_option='keep') * 100
    )

    return df


def assign_monthly_weights(
    df: pd.DataFrame,
    top_n: int = 20,
    weighting: str = 'equal',
    market_cap: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    At the first trading day of each month, rank tickers by the prior day's
    momentum signal and assign weights to the top N. Holds weights for the
    full month. Drops the first month (no prior signal exists yet).
    """
    df = df.sort_values(['date', 'ticker']).copy()
    df['_ym'] = df['date'].dt.to_period('M')

    first_days = df.groupby('_ym')['date'].min()
    month_starts = df[df['date'].isin(first_days)][['_ym', 'ticker', 'momentum_rank_prev']].copy()
    month_starts['_rank'] = month_starts.groupby('_ym')['momentum_rank_prev'].rank(
        ascending=False, na_option='keep', method='first'
    )
    month_starts = month_starts[month_starts['_rank'] <= top_n]

    if weighting == 'mcap' and market_cap is not None:
        month_starts = month_starts.merge(market_cap[['ticker', 'market_cap']], on='ticker', how='left')
        month_starts['weight'] = month_starts.groupby('_ym')['market_cap'].transform(
            lambda x: x / x.sum()
        )
    else:
        month_starts['weight'] = 1.0 / top_n

    month_starts = month_starts[['_ym', 'ticker', 'weight']]
    df = df.merge(month_starts, on=['_ym', 'ticker'], how='left')
    df['weight'] = df['weight'].fillna(0)

    first_rebalance = month_starts['_ym'].min()
    df = df[df['_ym'] > first_rebalance].drop(columns=['_ym'])

    return df


if __name__ == "__main__":
    import dotenv
    dotenv.load_dotenv()

    from analysis.backtest import run_backtest
    import pandas as pd

    TOP_N = 20
    market_cap = pd.read_csv("data/market_cap.csv")
    if "composite_figi" in market_cap.columns:
        market_cap = (
            market_cap.sort_values("market_cap", ascending=False)
            .drop_duplicates(subset="composite_figi", keep="first")
        )

    EXCLUDED = {"GOOG", "BRK.A", "NWS"}
    large_caps = set(market_cap.loc[market_cap["market_cap"] >= 20_000_000_000, "ticker"]) - EXCLUDED

    df = pd.read_csv(
        "data/stocks_daily.csv",
        dtype={"low": "float64", "high": "float64", "open": "float64",
               "close": "float64", "volume": "float64", "transactions": "float64"},
        parse_dates=["date"],
    )
    df = df.dropna(subset=["ticker"])

    spy_returns = df[df["ticker"] == "SPY"].set_index("date")["close"].pct_change()

    df = df[df["ticker"].isin(large_caps)]
    df['daily_return'] = df.groupby('ticker')['close'].pct_change()
    df = add_momentum_rank(df)
    df = df[['ticker', 'date', 'daily_return', 'momentum_rank']]
    df['momentum_rank_prev'] = df.groupby('ticker')['momentum_rank'].shift(1)

    base = df.copy()
    equal_df = assign_monthly_weights(base, top_n=TOP_N, weighting='equal')
    mcap_df  = assign_monthly_weights(base, top_n=TOP_N, weighting='mcap', market_cap=market_cap)

    print("\n=== Equal Weight ===")
    run_backtest(equal_df[['date', 'ticker', 'daily_return', 'weight']], spy_returns=spy_returns, label="Equal Weight")

    print("\n=== Market Cap Weight ===")
    run_backtest(mcap_df[['date', 'ticker', 'daily_return', 'weight']], spy_returns=spy_returns, label="MCap Weight")
