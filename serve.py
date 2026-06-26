"""
Launch the momentum-options trading-system dashboard.

  uv run python serve.py            # opens http://127.0.0.1:8000 in your browser
  uv run python serve.py --no-open  # don't auto-open the browser
"""

import sys
import threading
import webbrowser

import uvicorn

HOST, PORT = "127.0.0.1", 8000


def main():
    url = f"http://{HOST}:{PORT}"
    if "--no-open" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\n  Momentum Options dashboard → {url}\n  (Ctrl-C to stop)\n")
    uvicorn.run("app.server:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
