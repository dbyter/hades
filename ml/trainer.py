"""
Single-pass trainer for MinuteBarTransformer.

Streams data once across the full training date range, checkpointing every
`checkpoint_every` steps. No epochs — the dataset is large enough that a
single pass is sufficient and avoids overfitting to early data.

Usage:
    python -m ml.trainer
"""

import math
import os
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

CHECKPOINT_DIR = Path("ml/checkpoints")


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
    train_start: date = date(2026, 4, 1),
    train_end: date   = date(2026, 5, 1),
    seq_len: int      = 60,
    horizon: int      = 60,
    batch_size: int   = 256,
    lr: float         = 3e-4,
    warmup_steps: int = 1000,
    total_steps: int = 72_000,
    checkpoint_every: int = 10_000,
    tickers: list[str] | None = None,
    save_path: str = "ml/model_final.pt",
) -> MinuteBarTransformer:
    device = _device()
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    tqdm.write(f"Device: {device}")
    tqdm.write(f"Training {train_start} → {train_end}")

    dataset = MinuteBarDataset(
        start=train_start, end=train_end,
        seq_len=seq_len, horizon=horizon,
        tickers=tickers,
    )
    loader = DataLoader(dataset, batch_size=batch_size, num_workers=0)

    model     = MinuteBarTransformer(input_dim=7, d_model=128, nhead=8, num_layers=4).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lr_lambda=lambda step: _lr_lambda(step, warmup_steps, total_steps)
    )
    def criterion(preds, labels):
        preds_c  = preds  - preds.mean()
        labels_c = labels - labels.mean()
        corr = (preds_c * labels_c).sum() / (preds_c.norm() * labels_c.norm() + 1e-8)
        return -corr

    tqdm.write(f"LR: warmup {warmup_steps} steps → cosine decay over {total_steps:,} steps")

    step = 0
    total_loss = total_mae = total = 0
    total_corr = 0

    bar = tqdm(loader, desc="Training", unit="batch")
    for batch_x, batch_y in bar:
        batch_x, batch_y = batch_x.to(device), batch_y.to(device)

        optimizer.zero_grad()
        preds = model(batch_x)
        loss  = criterion(preds, batch_y)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        scheduler.step()
        step += 1

        total_corr += -loss.item()
        total_mae  += (preds - batch_y).abs().sum().item()
        total      += len(batch_y)

        bar.set_postfix(
            step=step,
            corr=f"{total_corr/step:.4f}",
            mae=f"{total_mae/total:.4f}",
            lr=f"{scheduler.get_last_lr()[0]:.2e}",
        )

        if step % checkpoint_every == 0:
            ckpt = CHECKPOINT_DIR / f"model_step{step:08d}.pt"
            torch.save({
                "step":      step,
                "model":     model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict(),
                "corr":      total_corr / step,
                "mae":       total_mae / total,
            }, ckpt)
            tqdm.write(
                f"[step {step:,}] checkpoint saved → {ckpt}  "
                f"corr={total_corr/step:.4f}  mae={total_mae/total:.4f}"
            )

    torch.save(model.state_dict(), save_path)
    tqdm.write(f"Done. Final model saved → {save_path}")
    return model


if __name__ == "__main__":
    train()
