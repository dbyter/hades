"""
Backtest the trained model on held-out minute-bar data.

For each day, slides the model across every ticker's bars, collects signals,
simulates entries at the next bar's open, exits 60 bars later.

Usage:
    python -m ml.backtest --checkpoint ml/checkpoints/model_step00010000.pt
"""

import argparse
import gzip
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv(Path(__file__).parent.parent / ".env")

from data.streaming_dataset import _get_s3, _make_features, list_keys, BARS_IN_SESSION
from ml.model import MinuteBarTransformer

BUCKET = "flatfiles"

# ── Config ────────────────────────────────────────────────────────────────────

SEQ_LEN       = 60
HORIZON       = 60
POSITION_SIZE = 1_000.0   # USD per trade
MAX_POSITIONS = 20        # max concurrent open trades
THRESHOLD     = 0.003     # min |predicted log return| to open a trade
COST_RT       = 0.0002    # round-trip cost as fraction (0.02%)


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


# ── Signal generation ─────────────────────────────────────────────────────────

def _simulate_day(
    df: pd.DataFrame,
    model: MinuteBarTransformer,
    device: str,
    open_positions: int,
) -> tuple[list[dict], int]:
    """Score all tickers in one batched GPU call, select top signals, simulate trades."""
    all_windows = []
    meta        = []  # (ticker, i, opens)

    for ticker, group in df.groupby("ticker", sort=False):
        group = group.reset_index(drop=True)
        if len(group) < SEQ_LEN + HORIZON:
            continue
        features  = _make_features(group)
        opens     = group["open"].values
        n_windows = len(features) - SEQ_LEN - HORIZON + 1
        for i in range(n_windows):
            all_windows.append(features[i : i + SEQ_LEN])
            meta.append((ticker, i, opens))

    if not all_windows:
        return [], open_positions

    # Single forward pass for the entire day
    batch = torch.from_numpy(np.stack(all_windows)).to(device)
    with torch.no_grad():
        preds = model(batch).cpu().numpy()

    all_candidates = []
    for pred, (ticker, i, opens) in zip(preds, meta):
        if abs(pred) < THRESHOLD:
            continue
        entry_idx = i + SEQ_LEN
        exit_idx  = i + SEQ_LEN + HORIZON
        if exit_idx >= len(opens):
            continue
        all_candidates.append({
            "ticker":      ticker,
            "entry_bar":   entry_idx,
            "exit_bar":    exit_idx,
            "entry_price": opens[entry_idx],
            "exit_price":  opens[exit_idx],
            "prediction":  float(pred),
            "direction":   1 if pred > 0 else -1,
        })


    # Sort by signal strength, take top signals respecting position cap
    all_candidates.sort(key=lambda c: abs(c["prediction"]), reverse=True)

    trades = []
    active: dict[str, int] = {}  # ticker -> entry_bar of open trade

    for c in all_candidates:
        ticker = c["ticker"]
        if open_positions >= MAX_POSITIONS:
            break
        if ticker in active:
            continue

        entry = c["entry_price"]
        exit_ = c["exit_price"]
        if entry <= 0 or exit_ <= 0:
            continue

        raw_return = c["direction"] * np.log(exit_ / entry)
        net_return = raw_return - COST_RT
        pnl        = POSITION_SIZE * net_return

        trades.append({
            "ticker":     ticker,
            "direction":  "long" if c["direction"] == 1 else "short",
            "entry_bar":  c["entry_bar"],
            "entry_price": entry,
            "exit_price":  exit_,
            "prediction":  c["prediction"],
            "raw_return":  raw_return,
            "net_return":  net_return,
            "pnl":         pnl,
        })
        active[ticker] = c["entry_bar"]
        open_positions += 1

    # All intraday trades close same day
    open_positions = max(0, open_positions - len(trades))
    return trades, open_positions


# ── Metrics ───────────────────────────────────────────────────────────────────

def _report(trades: list[dict]) -> None:
    if not trades:
        print("No trades generated.")
        return

    df = pd.DataFrame(trades)
    total_pnl    = df["pnl"].sum()
    total_cost   = POSITION_SIZE * len(df) * COST_RT
    win_rate     = (df["net_return"] > 0).mean()
    avg_ret      = df["net_return"].mean() * 100
    avg_win      = df.loc[df["net_return"] > 0, "net_return"].mean() * 100
    avg_loss     = df.loc[df["net_return"] < 0, "net_return"].mean() * 100

    # Cumulative P&L drawdown
    cumulative   = df["pnl"].cumsum()
    rolling_max  = cumulative.cummax()
    drawdown     = (cumulative - rolling_max)
    max_dd       = drawdown.min()

    # Sharpe (daily P&L)
    daily_pnl = df.groupby("date_str")["pnl"].sum()
    sharpe    = (daily_pnl.mean() / (daily_pnl.std() + 1e-8)) * np.sqrt(252)

    print("\n" + "=" * 50)
    print("BACKTEST RESULTS — May 2026")
    print("=" * 50)
    print(f"Trades:          {len(df):,}")
    print(f"Long / Short:    {(df['direction']=='long').sum()} / {(df['direction']=='short').sum()}")
    print(f"Win rate:        {win_rate:.1%}")
    print(f"Avg return:      {avg_ret:.3f}%")
    print(f"Avg win:         {avg_win:.3f}%")
    print(f"Avg loss:        {avg_loss:.3f}%")
    print(f"Total P&L:       ${total_pnl:,.2f}")
    print(f"Total costs:     ${total_cost:,.2f}")
    print(f"Max drawdown:    ${max_dd:,.2f}")
    print(f"Approx Sharpe:   {sharpe:.2f}")
    print("=" * 50)

    df.to_csv("ml/backtest_trades.csv", index=False)
    print("Trade log → ml/backtest_trades.csv")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(checkpoint_path: str) -> None:
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Device: {device}")

    # Load model
    model = MinuteBarTransformer(input_dim=7, d_model=128, nhead=8, num_layers=4).to(device)
    ckpt  = torch.load(checkpoint_path, map_location=device)
    state = ckpt["model"] if "model" in ckpt else ckpt
    model.load_state_dict(state)
    model.eval()
    print(f"Loaded checkpoint: {checkpoint_path}")

    import queue, threading
    keys = list_keys(date(2026, 5, 1), date(2026, 6, 1))
    all_trades     = []
    open_positions = 0
    _SENTINEL      = object()

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
            key, df = item
            date_str = key.split("/")[-1].replace(".csv.gz", "")
            trades, open_positions = _simulate_day(df, model, device, open_positions)
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
