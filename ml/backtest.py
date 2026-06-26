"""
Backtest the trained model on held-out minute-bar data.

Scans all tickers at 10:00 and 13:00 ET each day. At each scan, scores every
ticker using the preceding 60 bars, takes the top 10 predictions as longs,
and holds each position for exactly 60 bars (1 hour).

Usage:
    python -m ml.backtest --checkpoint ml/checkpoints/model_step00010000.pt
"""

import argparse
import gzip
import queue
import threading
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv(Path(__file__).parent.parent / ".env")

from data.streaming_dataset import _get_s3, _make_features, list_keys
from ml.model import MinuteBarTransformer

BUCKET        = "flatfiles"
SEQ_LEN       = 60
HORIZON       = 60
TRADES_PER_SCAN = 10
POSITION_SIZE = 1_000.0
COST_RT       = 0.0002

# Scan times: bar index within the session (9:30 = bar 0)
# 10:00 = bar 30, 13:00 = bar 210
SCAN_BARS = [30, 210]


# ── Data ──────────────────────────────────────────────────────────────────────

def _load_day(key: str) -> pd.DataFrame:
    obj = _get_s3().get_object(Bucket=BUCKET, Key=key)
    with gzip.open(obj["Body"], "rt") as f:
        df = pd.read_csv(f)
    df["dt"] = (
        pd.to_datetime(df["window_start"], unit="ns", utc=True)
        .dt.tz_convert("America/New_York")
    )
    t = df["dt"].dt
    df = df[((t.hour == 9) & (t.minute >= 30)) | (t.hour.between(10, 15))]
    return df.sort_values(["ticker", "dt"]).reset_index(drop=True)


# ── Simulation ────────────────────────────────────────────────────────────────

def _simulate_day(
    df: pd.DataFrame,
    model: MinuteBarTransformer,
    device: str,
) -> list[dict]:
    # Build per-ticker feature arrays and price arrays
    ticker_data = {}
    for ticker, group in df.groupby("ticker", sort=False):
        group = group.reset_index(drop=True)
        if len(group) < SEQ_LEN + HORIZON:
            continue
        ticker_data[ticker] = {
            "features": _make_features(group),
            "opens":    group["open"].values,
        }

    trades = []

    for scan_bar in SCAN_BARS:
        windows = []
        meta    = []

        for ticker, data in ticker_data.items():
            features = data["features"]
            opens    = data["opens"]

            # Window ends at scan_bar — need seq_len bars before it
            window_start = scan_bar - SEQ_LEN
            if window_start < 0:
                continue

            entry_idx = scan_bar          # enter at open of scan bar
            exit_idx  = scan_bar + HORIZON

            if exit_idx >= len(opens):
                continue

            windows.append(features[window_start:scan_bar])
            meta.append((ticker, opens[entry_idx], opens[exit_idx]))

        if not windows:
            continue

        # Single batched forward pass for this scan
        batch = torch.from_numpy(np.stack(windows)).to(device)
        with torch.no_grad():
            preds = model(batch).cpu().numpy()

        # Rank by prediction, take top N as longs
        ranked = sorted(zip(preds, meta), key=lambda x: x[0], reverse=True)
        top    = ranked[:TRADES_PER_SCAN]

        for pred, (ticker, entry_price, exit_price) in top:
            if entry_price <= 0 or exit_price <= 0:
                continue
            raw_return = np.log(exit_price / entry_price)
            net_return = raw_return - COST_RT
            trades.append({
                "ticker":       ticker,
                "scan_bar":     scan_bar,
                "entry_price":  entry_price,
                "exit_price":   exit_price,
                "prediction":   float(pred),
                "raw_return":   raw_return,
                "net_return":   net_return,
                "pnl":          POSITION_SIZE * net_return,
            })

    return trades


# ── Metrics ───────────────────────────────────────────────────────────────────

def _report(trades: list[dict]) -> None:
    if not trades:
        print("No trades generated.")
        return

    df = pd.DataFrame(trades)
    total_pnl  = df["pnl"].sum()
    total_cost = POSITION_SIZE * len(df) * COST_RT
    win_rate   = (df["net_return"] > 0).mean()
    avg_ret    = df["net_return"].mean() * 100
    avg_win    = df.loc[df["net_return"] > 0, "net_return"].mean() * 100
    avg_loss   = df.loc[df["net_return"] < 0, "net_return"].mean() * 100

    cumulative = df["pnl"].cumsum()
    max_dd     = (cumulative - cumulative.cummax()).min()

    daily_pnl  = df.groupby("date_str")["pnl"].sum()
    sharpe     = (daily_pnl.mean() / (daily_pnl.std() + 1e-8)) * np.sqrt(252)

    print("\n" + "=" * 50)
    print("BACKTEST RESULTS — May 2026")
    print("=" * 50)
    print(f"Trades:        {len(df):,}  ({len(df)//len(daily_pnl)} per day)")
    print(f"Scans:         10:00 + 13:00 ET, top {TRADES_PER_SCAN} longs each")
    print(f"Win rate:      {win_rate:.1%}")
    print(f"Avg return:    {avg_ret:.3f}%")
    print(f"Avg win:       {avg_win:.3f}%")
    print(f"Avg loss:      {avg_loss:.3f}%")
    print(f"Total P&L:     ${total_pnl:,.2f}")
    print(f"Total costs:   ${total_cost:,.2f}")
    print(f"Max drawdown:  ${max_dd:,.2f}")
    print(f"Sharpe:        {sharpe:.2f}")
    print("=" * 50)

    df.to_csv("ml/backtest_trades.csv", index=False)
    print("Trade log → ml/backtest_trades.csv")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(checkpoint_path: str) -> None:
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Device: {device}")

    model = MinuteBarTransformer(input_dim=7, d_model=128, nhead=8, num_layers=4).to(device)
    ckpt  = torch.load(checkpoint_path, map_location=device)
    state = ckpt["model"] if "model" in ckpt else ckpt
    model.load_state_dict(state)
    model.eval()
    print(f"Loaded: {checkpoint_path}")

    keys       = list_keys(date(2026, 5, 1), date(2026, 6, 1))
    all_trades = []
    _SENTINEL  = object()
    buf: queue.Queue = queue.Queue(maxsize=4)

    def _producer():
        for key in keys:
            try:
                buf.put((key, _load_day(key)))
            except Exception as e:
                tqdm.write(f"Warning: skipping {key} — {e}")
        buf.put(_SENTINEL)

    threading.Thread(target=_producer, daemon=True).start()

    with tqdm(total=len(keys), desc="days", unit="day") as bar:
        while True:
            item = buf.get()
            if item is _SENTINEL:
                break
            key, df   = item
            date_str  = key.split("/")[-1].replace(".csv.gz", "")
            trades    = _simulate_day(df, model, device)
            for t in trades:
                t["date_str"] = date_str
            all_trades.extend(trades)
            tqdm.write(f"{date_str}  trades={len(trades)}  cumulative={len(all_trades)}")
            bar.update(1)

    _report(all_trades)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default="ml/checkpoints/model_step00010000.pt")
    args = parser.parse_args()
    run(args.checkpoint)
