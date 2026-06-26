"""
Minimal training loop for MinuteBarTransformer.

Usage:
    python -m ml.trainer
"""

import math
from datetime import date
from pathlib import Path

import torch
import torch.nn as nn
from dotenv import load_dotenv
from torch.utils.data import DataLoader
from tqdm import tqdm

load_dotenv(Path(__file__).parent.parent / ".env")

from data.streaming_dataset import MinuteBarDataset
from ml.model import MinuteBarTransformer


def _device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _lr_lambda(step: int, warmup_steps: int, total_steps: int) -> float:
    if step < warmup_steps:
        return step / max(1, warmup_steps)
    progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
    return 0.5 * (1.0 + math.cos(math.pi * progress))


def train(
    start: date = date(2024, 1, 1),
    end: date = date(2024, 2, 1),
    seq_len: int = 60,
    batch_size: int = 256,
    epochs: int = 5,
    lr: float = 3e-4,
    warmup_steps: int = 500,
    tickers: list[str] | None = None,
    save_path: str = "ml/model.pt",
) -> MinuteBarTransformer:
    device = _device()
    print(f"Device: {device}")

    dataset = MinuteBarDataset(start=start, end=end, seq_len=seq_len, tickers=tickers)
    loader  = DataLoader(dataset, batch_size=batch_size, num_workers=0)

    model     = MinuteBarTransformer(input_dim=7, d_model=128, nhead=8, num_layers=4).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    criterion = nn.MSELoss()

    # Estimate total steps for cosine decay — ~2800 batches/day, ~21 trading days/month
    trading_days = max(1, (end - start).days * 5 // 7)
    total_steps  = epochs * trading_days * 2800
    scheduler    = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lr_lambda=lambda step: _lr_lambda(step, warmup_steps, total_steps)
    )

    tqdm.write(f"LR schedule: warmup {warmup_steps} steps → cosine decay over ~{total_steps:,} steps")

    global_step = 0
    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = total_mae = total = 0

        bar = tqdm(loader, desc=f"Epoch {epoch}/{epochs}", unit="batch", leave=False)
        for batch_x, batch_y in bar:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)

            optimizer.zero_grad()
            preds = model(batch_x)
            loss  = criterion(preds, batch_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            global_step += 1

            total_loss += loss.item() * len(batch_y)
            total_mae  += (preds - batch_y).abs().sum().item()
            total      += len(batch_y)

            bar.set_postfix(
                mse=f"{total_loss/total:.6f}",
                mae=f"{total_mae/total:.4f}",
                lr=f"{scheduler.get_last_lr()[0]:.2e}",
            )

        tqdm.write(
            f"Epoch {epoch}/{epochs}  mse={total_loss/total:.6f}  "
            f"mae={total_mae/total:.4f}  lr={scheduler.get_last_lr()[0]:.2e}"
        )

    torch.save(model.state_dict(), save_path)
    tqdm.write(f"Saved → {save_path}")
    return model


if __name__ == "__main__":
    train()
