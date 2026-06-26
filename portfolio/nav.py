"""Daily portfolio NAV history → data/nav_history.json (one snapshot per day)."""

import json
from datetime import date
from pathlib import Path

NAV_PATH = Path(__file__).parent.parent / "data" / "nav_history.json"


def load() -> list[dict]:
    if not NAV_PATH.exists():
        return []
    try:
        return json.loads(NAV_PATH.read_text())
    except json.JSONDecodeError:
        return []


def record(snapshot: dict) -> list[dict]:
    """Upsert today's snapshot {total, core, momentum, options}. Returns full history."""
    today = str(date.today())
    hist = [h for h in load() if h.get("date") != today]
    hist.append({"date": today, **snapshot})
    hist.sort(key=lambda h: h["date"])
    try:
        NAV_PATH.parent.mkdir(parents=True, exist_ok=True)
        NAV_PATH.write_text(json.dumps(hist))
    except Exception:
        pass
    return hist
