import torch
import torch.nn as nn


class MinuteBarTransformer(nn.Module):
    """
    Transformer encoder over a window of minute bars.

    Input:  (batch, seq_len, input_dim)
    Output: (batch,) — raw logit for next-bar up/down
    """

    def __init__(
        self,
        input_dim: int = 4,
        d_model: int = 64,
        nhead: int = 4,
        num_layers: int = 2,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.input_proj = nn.Linear(input_dim, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.head = nn.Linear(d_model, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.input_proj(x)           # (B, T, d_model)
        x = self.encoder(x)              # (B, T, d_model)
        x = x[:, -1, :]                  # last token as sequence summary
        return self.head(x).squeeze(-1)  # (B,)
