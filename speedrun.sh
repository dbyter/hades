#!/usr/bin/env bash
set -euo pipefail

echo "=== Hades speedrun ==="

# ── Credentials ───────────────────────────────────────────────────────────────

if [ -f .env ] && grep -q "MASSIVE_ACCESS_KEY" .env; then
    echo "Found existing .env — using stored credentials."
else
    read -rp "Massive access key: " MASSIVE_ACCESS_KEY
    read -rsp "Massive secret key: " MASSIVE_SECRET_KEY
    echo
    cat > .env <<EOF
MASSIVE_ACCESS_KEY=${MASSIVE_ACCESS_KEY}
MASSIVE_SECRET_KEY=${MASSIVE_SECRET_KEY}
EOF
    echo "Credentials saved to .env"
fi

# ── uv ────────────────────────────────────────────────────────────────────────

if ! command -v uv &>/dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── Dependencies ──────────────────────────────────────────────────────────────

echo "Syncing dependencies..."
uv sync --quiet

# ── Train ─────────────────────────────────────────────────────────────────────

echo "Starting training..."
uv run python -m ml.trainer
