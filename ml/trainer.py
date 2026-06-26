"""
Minimal training loop for MinuteBarTransformer.

Usage:
    python -m ml.trainer
"""

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


def train(
    start: date = date(2024, 1, 1),
    end: date = date(2024, 2, 1),
    seq_len: int = 60,
    batch_size: int = 256,
    epochs: int = 5,
    lr: float = 1e-3,
    tickers: list[str] | None = None,
    save_path: str = "ml/model.pt",
) -> MinuteBarTransformer:
    device = _device()
    print(f"Device: {device}")

    dataset = MinuteBarDataset(start=start, end=end, seq_len=seq_len, tickers=tickers)
    loader  = DataLoader(dataset, batch_size=batch_size, num_workers=0)

    model     = MinuteBarTransformer(input_dim=4, d_model=64, nhead=4, num_layers=2).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.BCEWithLogitsLoss()

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = correct = total = 0

        bar = tqdm(loader, desc=f"Epoch {epoch}/{epochs}", unit="batch", leave=False)
        for batch_x, batch_y in bar:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)

            optimizer.zero_grad()
            logits = model(batch_x)
            loss   = criterion(logits, batch_y)
            loss.backward()
            optimizer.step()

            total_loss += loss.item() * len(batch_y)
            correct    += ((logits > 0).float() == batch_y).sum().item()
            total      += len(batch_y)

            bar.set_postfix(loss=f"{total_loss/total:.4f}", acc=f"{correct/total:.4f}")

        tqdm.write(f"Epoch {epoch}/{epochs}  loss={total_loss/total:.4f}  acc={correct/total:.4f}")

    torch.save(model.state_dict(), save_path)
    print(f"Saved → {save_path}")
    return model


if __name__ == "__main__":
    train()
