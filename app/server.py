"""
FastAPI server for the trading-system dashboard.

Routes:
  GET  /                       → dashboard page
  GET  /api/candidates         → cached scored candidates (or {status: computing})
  POST /api/candidates/refresh → recompute candidates in the background
  GET  /api/positions          → open positions, re-priced, with exit actions
  POST /api/open               → record a new position in the ledger
  POST /api/close              → close a position in the ledger
"""

import json
import threading
from pathlib import Path

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from app.dashboard import render_dashboard
from app.pricing_live import price_all, price_stock_positions, rotation
from daily_trader import build_candidates

_INSIGHTS_PATH = Path("data/insights_cache.json")


def _load_insights() -> dict:
    if not _INSIGHTS_PATH.exists():
        return {}
    try:
        return json.loads(_INSIGHTS_PATH.read_text()).get("tickers", {})
    except Exception:
        return {}
from portfolio import ledger, nav, performance

app = FastAPI(title="Momentum Options Trading System")

# Candidate computation is slow (~30–60s) → cache + background refresh.
_cand = {"status": "idle", "data": None}
_lock = threading.Lock()


def _flatten(c: dict, insights: dict, n_top: int = 5) -> list[dict]:
    sm, ivr = c["stock_metrics"], c["iv_rank_data"]
    out = []
    for i, r in enumerate(c["rows"]):
        sp, lg, sh = r["spread"], r["long"], r["short"]
        m, iv = sm.get(r["ticker"], {}), ivr.get(r["ticker"], {})
        out.append({
            "rank": i + 1, "is_top5": i < n_top,
            "ticker": r["ticker"], "score": r["score"],
            "expiry": r["expiry"], "dte": r["dte"],
            "long_strike": lg["strike"], "short_strike": sh["strike"],
            "long_price": lg["price"], "short_price": sh["price"],
            "width": sp["width"], "debit": sp["debit"], "cost": sp["cost"],
            "max_profit": sp["max_profit"], "rr": sp["rr"], "win_prob": sp["win_prob"],
            "breakeven": sp["breakeven"], "be_pct": sp["be_pct"],
            "iv_rank": iv.get("iv_rank"), "rsi": m.get("rsi"),
            "long_oi": lg["oi"], "long_vol": lg["volume"],
            "short_oi": sh["oi"], "short_vol": sh["volume"],
            "insights": insights.get(r["ticker"]),
            "name": (c.get("names") or {}).get(r["ticker"]),
        })
    return out


def _stocks(c: dict) -> list[dict]:
    sm = c["stock_metrics"]
    ratings = c.get("ratings", {})
    rows = []
    for h in c["holdings"]:
        m = sm.get(h["ticker"], {})
        rows.append({
            "rank": h.get("rank"), "ticker": h["ticker"],
            "weight": h["weight"], "price": h["price"], "market_cap": h.get("market_cap"),
            "rsi": m.get("rsi"), "from_52w_high": m.get("from_52w_high"),
            "ret_1w": m.get("ret_1w"), "ret_1m": m.get("ret_1m"),
            "ret_3m": m.get("ret_3m"), "ytd": m.get("ytd"),
            "rating": ratings.get(h["ticker"]),
            "name": (c.get("names") or {}).get(h["ticker"]),
        })
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"]))
    return rows


def _longcalls(c: dict, insights: dict, n_top: int = 5) -> dict:
    ivr = c["iv_rank_data"]
    sm = c["stock_metrics"]
    names = c.get("names", {})
    rank_map = {h["ticker"]: h.get("rank") for h in c["holdings"]}

    def _flat(rows):
        out = []
        for i, r in enumerate(rows):
            t = r["ticker"]
            out.append({
                "rank": i + 1, "is_top5": i < n_top, "score": r["score"],
                "ticker": t, "name": names.get(t),
                "expiry": r["expiry"], "dte": r["dte"], "strike": r["strike"],
                "premium": r["premium"], "breakeven": r["breakeven"], "be_pct": r["be_pct"],
                "exp_move_pct": r.get("exp_move_pct"), "exp_move": r.get("exp_move"),
                "sig_low":  round(r["spot"] - r["exp_move"], 2) if r.get("exp_move") is not None else None,
                "sig_high": round(r["spot"] + r["exp_move"], 2) if r.get("exp_move") is not None else None,
                "delta": r.get("delta"),
                "iv": round(r["iv"] * 100, 1) if r.get("iv") else None,
                "iv_rank": ivr.get(t, {}).get("iv_rank"),
                "iv_52w_low": ivr.get(t, {}).get("iv_52w_low"),
                "iv_52w_high": ivr.get(t, {}).get("iv_52w_high"),
                "rsi": sm.get(t, {}).get("rsi"),
                "oi": r["oi"], "volume": r["volume"], "mom_rank": rank_map.get(t),
                "insights": insights.get(t),
            })
        return out

    lc = c.get("long_calls", {})
    return {m: _flat(lc.get(m, [])) for m in ("atm", "itm", "otm")}


def _compute():
    with _lock:
        if _cand["status"] == "computing":
            return
        _cand["status"] = "computing"
    try:
        c = build_candidates()
        insights = _load_insights()
        _cand["data"] = {"as_of": c["as_of"], "rows": _flatten(c, insights),
                         "stocks": _stocks(c), "longcalls": _longcalls(c, insights)}
        _cand["status"] = "ready"
    except Exception as e:
        print(f"[candidates] compute failed: {e}")
        _cand["status"] = "error"


def _topn_tickers() -> set[str] | None:
    if _cand["status"] == "ready":
        return {s["ticker"] for s in _cand["data"]["stocks"]}
    return None


@app.on_event("startup")
def _kickoff():
    threading.Thread(target=_compute, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def index():
    return render_dashboard()


@app.get("/api/candidates")
def get_candidates():
    if _cand["status"] == "ready":
        return {"status": "ready", **_cand["data"]}
    return {"status": _cand["status"]}


@app.post("/api/candidates/refresh")
def refresh_candidates():
    threading.Thread(target=_compute, daemon=True).start()
    return {"status": "computing"}


@app.get("/api/positions")
def get_positions():
    priced = price_all(ledger.open_positions("option_spread"))
    return {"positions": priced}


@app.get("/api/stocks/candidates")
def get_stock_candidates():
    if _cand["status"] == "ready":
        return {"status": "ready", "as_of": _cand["data"]["as_of"], "stocks": _cand["data"]["stocks"]}
    return {"status": _cand["status"]}


@app.get("/api/stocks/positions")
def get_stock_positions():
    held = ledger.open_positions("stock")
    topn = _topn_tickers()
    # Without a fresh top-N, don't raise false rotation alarms.
    effective_topn = topn if topn is not None else {p["ticker"] for p in held}
    priced = price_stock_positions(held, effective_topn)
    # Only MOMENTUM-tagged holdings count as drop-outs (core ETFs are held forever).
    mom_held = {p["ticker"] for p in priced if p["tracker"] == "momentum"}
    rot = rotation(mom_held, topn) if topn is not None else {"new_entrants": [], "dropouts": []}
    return {"positions": priced, "rotation": rot}


@app.post("/api/stocks/tag")
def tag_stock(body: dict = Body(...)):
    pos = ledger.set_tracker(body["id"], body["tracker"])
    return JSONResponse({"ok": pos is not None})


@app.get("/api/longcalls/candidates")
def get_longcall_candidates():
    if _cand["status"] == "ready":
        return {"status": "ready", "as_of": _cand["data"]["as_of"], "longcalls": _cand["data"]["longcalls"]}
    return {"status": _cand["status"]}


@app.get("/api/longcalls/positions")
def get_longcall_positions():
    return {"positions": price_all(ledger.open_positions("long_call"))}


@app.post("/api/longcalls/open")
def open_longcall(body: dict = Body(...)):
    pos = ledger.add_long_call(ticker=body["ticker"], expiry=body["expiry"], strike=body["strike"],
                               entry_premium=body["premium"], contracts=body["contracts"])
    return JSONResponse({"ok": True, "id": pos["id"]})


@app.post("/api/longcalls/close")
def close_longcall(body: dict = Body(...)):
    pos = ledger.close(body["id"], body["exit_premium"], reason=body.get("reason", "manual"))
    return JSONResponse({"ok": pos is not None})


@app.get("/api/portfolio")
def get_portfolio():
    stock_pos = ledger.open_positions("stock")
    opt_pos   = ledger.open_positions("option_spread")
    lc_pos    = ledger.open_positions("long_call")
    topn      = _topn_tickers() or set()
    stocks_priced  = price_stock_positions(stock_pos, topn)
    options_priced = price_all(opt_pos)
    lc_priced      = price_all(lc_pos)
    anch = performance.anchors([p["ticker"] for p in stock_pos])
    agg  = performance.aggregate(stocks_priced, options_priced, anch, long_calls_priced=lc_priced)

    bvals = {b["name"]: b["value"] for b in agg["buckets"]}
    hist  = nav.record({"total": agg["total"], "core": bvals.get("core", 0),
                        "momentum": bvals.get("momentum", 0), "options": bvals.get("options", 0),
                        "long_calls": bvals.get("long_calls", 0)})
    prev = hist[-2]["total"] if len(hist) >= 2 else None
    agg["day_change"]     = round(agg["total"] - prev, 2) if prev else None
    agg["day_change_pct"] = round((agg["total"] / prev - 1) * 100, 2) if prev else None
    agg["positions_count"] = len(stock_pos) + len(opt_pos) + len(lc_pos)
    agg["nav_history"] = hist
    return agg


@app.post("/api/stocks/open")
def open_stock(body: dict = Body(...)):
    pos = ledger.add_stock(ticker=body["ticker"], shares=body["shares"], fill_price=body["fill_price"])
    return JSONResponse({"ok": True, "id": pos["id"]})


@app.post("/api/stocks/close")
def close_stock(body: dict = Body(...)):
    pos = ledger.close(body["id"], body["exit_price"], reason=body.get("reason", "manual"))
    return JSONResponse({"ok": pos is not None})


@app.post("/api/open")
def open_position(body: dict = Body(...)):
    pos = ledger.add(
        ticker=body["ticker"], structure=body.get("structure", "bull_spread"),
        expiry=body["expiry"], long_strike=body["long_strike"],
        short_strike=body.get("short_strike"), entry_debit=body["entry_debit"],
        width=body.get("width"), contracts=body["contracts"],
    )
    return JSONResponse({"ok": True, "id": pos["id"]})


@app.post("/api/close")
def close_position(body: dict = Body(...)):
    pos = ledger.close(body["id"], body["exit_debit"], reason=body.get("reason", "manual"))
    return JSONResponse({"ok": pos is not None})
