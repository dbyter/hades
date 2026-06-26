"""
Streaming PyTorch IterableDataset over Massive.com minute-bar flat files.

Streams daily .csv.gz files directly from S3 without writing to disk.
Yields (window, label) pairs where:
  - window: float32 tensor of shape (seq_len, 4) — [log_ret, high_ret, low_ret, vol_z]
  - label:  float32 scalar — 1.0 if next bar close > current bar close, else 0.0

Requires MASSIVE_ACCESS_KEY and MASSIVE_SECRET_KEY in .env.
"""

import gzip
import os
import queue
import threading
from datetime import date
from pathlib import Path

import boto3
import numpy as np
import pandas as pd
import torch
from botocore.client import Config
from dotenv import load_dotenv
from torch.utils.data import IterableDataset
from tqdm import tqdm

load_dotenv(Path(__file__).parent.parent / ".env")

ENDPOINT = "https://files.massive.com"
BUCKET   = "flatfiles"
PREFIX   = "us_stocks_sip/minute_aggs_v1/"

_thread_local = threading.local()


def _get_s3():
    if not hasattr(_thread_local, "client"):
        _thread_local.client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT,
            aws_access_key_id=os.environ["MASSIVE_ACCESS_KEY"],
            aws_secret_access_key=os.environ["MASSIVE_SECRET_KEY"],
            config=Config(signature_version="s3v4"),
        )
    return _thread_local.client


def list_keys(start: date, end: date) -> list[str]:
    print(f"Listing S3 keys ({start} → {end})...")
    paginator = _get_s3().get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            date_str = key.split("/")[-1].replace(".csv.gz", "").replace(".csv", "")
            try:
                file_date = date.fromisoformat(date_str)
            except ValueError:
                continue
            if start <= file_date <= end:
                keys.append(key)
    tqdm.write(f"Found {len(keys)} daily files.")
    return sorted(keys)


def _stream_day(key: str) -> pd.DataFrame:
    obj = _get_s3().get_object(Bucket=BUCKET, Key=key)
    with gzip.open(obj["Body"], "rt") as f:
        df = pd.read_csv(f)
    df["dt"] = (
        pd.to_datetime(df["window_start"], unit="ns", utc=True)
        .dt.tz_convert("America/New_York")
    )
    # Regular market hours only: 9:30 – 16:00 ET
    t = df["dt"].dt
    df = df[((t.hour == 9) & (t.minute >= 30)) | (t.hour.between(10, 15))]
    return df.sort_values(["ticker", "dt"])


def _make_features(group: pd.DataFrame) -> np.ndarray:
    closes  = group["close"].values
    opens   = group["open"].values
    highs   = group["high"].values
    lows    = group["low"].values
    volumes = group["volume"].values

    log_ret  = np.concatenate([[0.0], np.log(closes[1:] / np.maximum(closes[:-1], 1e-8))])
    high_ret = np.log(np.maximum(highs / np.maximum(opens, 1e-8), 1e-8))
    low_ret  = np.log(np.maximum(lows  / np.maximum(opens, 1e-8), 1e-8))

    vol_z = (volumes - volumes.mean()) / (volumes.std() + 1e-8)

    return np.column_stack([log_ret, high_ret, low_ret, vol_z]).astype(np.float32)


_SENTINEL = object()


class MinuteBarDataset(IterableDataset):
    """
    Streams minute-bar windows from Massive S3 flat files.

    A background thread prefetches the next `prefetch` daily files while
    the main thread trains, overlapping S3 IO with GPU compute.

    Args:
        start:     First date (inclusive).
        end:       Last date (inclusive).
        seq_len:   Number of bars per input window (default 60 = 1 hour).
        tickers:   Optional allowlist; None streams all tickers.
        prefetch:  Number of daily files to buffer ahead (default 4).
    """

    def __init__(
        self,
        start: date,
        end: date,
        seq_len: int = 60,
        tickers: list[str] | None = None,
        prefetch: int = 4,
    ):
        self.start    = start
        self.end      = end
        self.seq_len  = seq_len
        self.tickers  = set(tickers) if tickers else None
        self.prefetch = prefetch
        self._keys: list[str] | None = None

    def _get_keys(self) -> list[str]:
        if self._keys is None:
            self._keys = list_keys(self.start, self.end)
        return self._keys

    def __iter__(self):
        keys = self._get_keys()
        buf: queue.Queue = queue.Queue(maxsize=self.prefetch)

        def _producer():
            for key in keys:
                try:
                    buf.put(_stream_day(key))
                except Exception as e:
                    tqdm.write(f"Warning: skipping {key} — {e}")
            buf.put(_SENTINEL)

        t = threading.Thread(target=_producer, daemon=True)
        t.start()

        with tqdm(total=len(keys), desc="days", unit="day") as bar:
            while True:
                df = buf.get()
                if df is _SENTINEL:
                    break

                for ticker, group in df.groupby("ticker", sort=False):
                    if self.tickers and ticker not in self.tickers:
                        continue
                    if len(group) < self.seq_len + 1:
                        continue

                    features = _make_features(group.reset_index(drop=True))
                    closes   = group["close"].values

                    for i in range(len(features) - self.seq_len):
                        window = features[i : i + self.seq_len]
                        label  = float(closes[i + self.seq_len] > closes[i + self.seq_len - 1])
                        yield torch.from_numpy(window), torch.tensor(label, dtype=torch.float32)

                bar.update(1)
