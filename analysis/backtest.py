import numpy as np
import pandas as pd


def run_backtest(df: pd.DataFrame, spy_returns: pd.Series | None = None, label: str = "Portfolio") -> None:
    """
    Generic backtest utility. Computes portfolio returns and prints performance metrics.

    df must have: date, daily_return, weight
    spy_returns: optional date-indexed Series of SPY daily returns for benchmark comparison
    """
    df = df.copy()
    df["daily_return"] = df["daily_return"].fillna(0)

    daily = (
        (df["weight"] * df["daily_return"])
        .groupby(df["date"])
        .sum()
    )
    daily.index = pd.to_datetime(daily.index)
    daily = daily.sort_index()

    spy = (spy_returns if spy_returns is not None else pd.Series(dtype=float)).reindex(daily.index).fillna(0)

    cum = (1 + daily).cumprod()
    cum_spy = (1 + spy).cumprod()

    def _metrics(r: pd.Series, cum: pd.Series) -> tuple:
        n_years = (r.index[-1] - r.index[0]).days / 365.25
        total = cum.iloc[-1] - 1
        cagr = (1 + total) ** (1 / n_years) - 1
        sharpe = r.mean() / r.std() * np.sqrt(252)
        max_dd = ((cum - cum.cummax()) / cum.cummax()).min()
        return cagr, sharpe, max_dd, total

    port_cagr, port_sharpe, port_dd, port_total = _metrics(daily, cum)
    spy_cagr,  spy_sharpe,  spy_dd,  spy_total  = _metrics(spy, cum_spy)

    annual_port = daily.groupby(daily.index.year).apply(lambda r: (1 + r).prod() - 1)
    annual_spy  = spy.groupby(spy.index.year).apply(lambda r: (1 + r).prod() - 1)

    if "ticker" in df.columns:
        last_month = df["date"].max()
        holdings = (
            df[df["date"] == last_month][["ticker", "weight"]]
            .query("weight > 0")
            .sort_values("weight", ascending=False)
        )
        print(f"\n=== Current Holdings ({last_month.date()}) ===")
        for _, row in holdings.iterrows():
            print(f"  {row['ticker']:<10}{row['weight']:.1%}")

    print(f"\n{'Year':<8}{label:>16}{'SPY':>10}")
    print("-" * 34)
    for year in sorted(set(annual_port.index) | set(annual_spy.index)):
        p = f"{annual_port.get(year, float('nan')):.1%}"
        s = f"{annual_spy.get(year, float('nan')):.1%}"
        print(f"{year:<8}{p:>16}{s:>10}")
    print("-" * 34)
    print(f"\n{'':24}{label:>6}{'SPY':>10}")
    print(f"{'CAGR':<24}{port_cagr:>5.1%}{spy_cagr:>10.1%}")
    print(f"{'Sharpe Ratio':<24}{port_sharpe:>5.2f}{spy_sharpe:>10.2f}")
    print(f"{'Max Drawdown':<24}{port_dd:>5.1%}{spy_dd:>10.1%}")
    print(f"{'Total Return':<24}{port_total:>5.1%}{spy_total:>10.1%}")
