"""
Refresh all flat-file data sources.

Usage:
    uv run python refresh.py            # all three
    uv run python refresh.py stocks     # stocks only
    uv run python refresh.py options    # options only
    uv run python refresh.py market_cap # market cap only
"""

import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = {
    "market_cap": Path("data/download/market_cap.py"),
    "stocks":     Path("data/download/stocks.py"),
    "options":    Path("data/download/options.py"),
    "insights":   Path("data/download/insights.py"),
}

# Options is slow — warn the user upfront
SLOW = {"options"}


def run(name: str, script: Path) -> bool:
    print(f"\n{'─' * 50}")
    print(f"  {name.upper().replace('_', ' ')}")
    if name in SLOW:
        print(f"  ⚠  This may take several minutes (large flat file)")
    print(f"{'─' * 50}")
    t0 = time.monotonic()
    result = subprocess.run([sys.executable, str(script)])
    elapsed = time.monotonic() - t0
    if result.returncode == 0:
        print(f"\n  ✓  Done in {elapsed:.0f}s")
        return True
    else:
        print(f"\n  ✗  Failed (exit code {result.returncode})")
        return False


def main():
    selected = sys.argv[1:] or list(SCRIPTS)
    unknown  = [s for s in selected if s not in SCRIPTS]
    if unknown:
        print(f"Unknown targets: {unknown}")
        print(f"Available: {list(SCRIPTS)}")
        sys.exit(1)

    print(f"Refreshing: {', '.join(selected)}")
    t_total = time.monotonic()
    failed  = []

    for name in selected:
        ok = run(name, SCRIPTS[name])
        if not ok:
            failed.append(name)

    print(f"\n{'═' * 50}")
    total = time.monotonic() - t_total
    if failed:
        print(f"  DONE with errors — failed: {', '.join(failed)}  ({total:.0f}s total)")
        sys.exit(1)
    else:
        print(f"  ALL DONE  ({total:.0f}s total)")


if __name__ == "__main__":
    main()
