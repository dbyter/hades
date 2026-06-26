"""
Export the options backtest to an Excel workbook: a Summary tab plus one tab
per structure listing every trade with construction, slippage, and P&L under
both ideal (0%) and pessimistic (3% + 2c) fills.

  uv run python export_trades.py            → backtest_trades.xlsx
"""

import statistics as st

import pandas as pd

from backtest.costs import IDEAL, PESSIMISTIC
from backtest.engine import Engine, build_inputs
from backtest.metrics import perf_metrics
from backtest.strategies import MonthlyBullSpread, MonthlyDeepITMCall, MonthlyLongCall

OUT     = "backtest_trades.xlsx"
SELECT  = 5
STRATS  = [("Bull Spread", MonthlyBullSpread),
           ("Long Call", MonthlyLongCall),
           ("Deep ITM Call", MonthlyDeepITMCall)]

# column name -> xlsxwriter number format
FMT_MONEY = "#,##0.00"
FMT_INT   = "#,##0"
FMT_PCT   = '0.0"%"'
COL_FMT = {
    "Long price": FMT_MONEY, "Short price": FMT_MONEY,
    "Exit long price": FMT_MONEY, "Exit short price": FMT_MONEY,
    "Net debit/sh": FMT_MONEY, "Debit/ct $": FMT_INT, "Contracts": "0.00",
    "Slip entry/ct $": FMT_INT, "Slip exit/ct $": FMT_INT, "Slippage/ct $": FMT_INT,
    "Slippage % debit": FMT_PCT,
    "Exit/ct ideal $": FMT_INT, "Exit/ct cost $": FMT_INT,
    "P&L/ct ideal $": FMT_INT, "P&L/ct cost $": FMT_INT,
    "Return ideal %": FMT_PCT, "Return cost %": FMT_PCT,
    # dollar totals (× contracts)
    "Cost basis $": FMT_INT, "Slip entry $": FMT_INT, "Slip exit $": FMT_INT,
    "Slippage $ total": FMT_INT, "P&L ideal $ total": FMT_INT, "P&L cost $ total": FMT_INT,
}


def _key(p):
    return (p.spec.underlying, p.spec.expiry, str(p.entry_date))


def trade_rows(name, cls, inp):
    eng_i = Engine(inp, cost=IDEAL)
    ideal = eng_i.run(cls(pool_n=50, select_n=SELECT))
    pess  = Engine(inp, cost=PESSIMISTIC).run(cls(pool_n=50, select_n=SELECT))
    pm = {_key(p): p for p in pess.positions}
    slip = PESSIMISTIC.slip   # one-way per-share slip = max($0.02, 3% × price)

    rows = []
    for p in ideal.positions:
        q = pm.get(_key(p))
        u = p.spec.underlying
        legs = p.spec.legs
        long_leg = legs[0]
        short_leg = legs[1] if len(legs) > 1 else None
        closed = p.exit_value is not None

        # per-contract net values (use current mark for still-open trades)
        ix = p.exit_value if closed else p.mark
        qx = (q.exit_value if (q and q.exit_value is not None) else (q.mark if q else ix))
        qc = q.contracts if q else p.contracts

        # recompute each leg's EXIT price (mid) so we can show legs + split slippage
        lx = sx = None
        if closed:
            spot = eng_i.spot(u, p.exit_date)
            vol  = eng_i.vol_63d(u, p.exit_date)
            lx = eng_i._leg_value(u, long_leg.expiry, long_leg.strike, p.exit_date, spot, vol)
            if short_leg:
                sx = eng_i._leg_value(u, short_leg.expiry, short_leg.strike, p.exit_date, spot, vol)

        # slippage per share, split entry vs exit (3% of each leg, both sides)
        entry_slip = slip(long_leg.ref_price) + (slip(short_leg.ref_price) if short_leg else 0.0)
        exit_slip  = (slip(lx) + (slip(sx) if sx is not None else 0.0)) if lx is not None else 0.0

        rows.append({
            "Ticker":          u,
            "Status":          "closed" if closed else "open",
            "Entry date":      str(p.entry_date),
            "Exit date":       str(p.exit_date) if p.exit_date else "",
            "Hold days":       (p.exit_date - p.entry_date).days if p.exit_date else "",
            "Exit reason":     p.exit_reason or "",
            "Expiry":          str(p.spec.expiry),
            "DTE@entry":       p.spec.dte,
            "Long strike":     long_leg.strike,
            "Long price":      round(long_leg.ref_price, 2),
            "Short strike":    short_leg.strike if short_leg else None,
            "Short price":     round(short_leg.ref_price, 2) if short_leg else None,
            "Exit long price": round(lx, 2) if lx is not None else None,
            "Exit short price": round(sx, 2) if sx is not None else None,
            "Width":           p.spec.width,
            "Net debit/sh":    round(p.entry_debit / 100, 2),
            "Debit/ct $":      round(p.entry_debit, 0),
            "Contracts":       round(p.contracts, 2),
            # slippage breakdown (per contract = per-share × 100)
            "Slip entry/ct $": round(entry_slip * 100, 0),
            "Slip exit/ct $":  round(exit_slip * 100, 0),
            "Slippage/ct $":   round((entry_slip + exit_slip) * 100, 0),
            "Slippage % debit": round((entry_slip + exit_slip) * 100 / p.entry_debit * 100, 1) if p.entry_debit else None,
            "Exit/ct ideal $": round(ix, 0),
            "Exit/ct cost $":  round(qx, 0),
            "P&L/ct ideal $":  round(ix - p.entry_debit, 0),
            "P&L/ct cost $":   round(qx - (q.entry_debit if q else p.entry_debit), 0),
            "Return ideal %":  round((ix / p.entry_debit - 1) * 100, 1) if p.entry_debit else None,
            "Return cost %":   round((qx / q.entry_debit - 1) * 100, 1) if (q and q.entry_debit) else None,
            # ── dollar totals (× contracts) ──
            "Cost basis $":      round(p.entry_debit * p.contracts, 0),
            "Slip entry $":      round(entry_slip * 100 * qc, 0),
            "Slip exit $":       round(exit_slip * 100 * qc, 0),
            "Slippage $ total":  round((entry_slip + exit_slip) * 100 * qc, 0),
            "P&L ideal $ total": round((ix - p.entry_debit) * p.contracts, 0),
            "P&L cost $ total":  round((qx - q.entry_debit) * q.contracts, 0) if q else None,
        })

    # summary for this structure
    closed_i = [r for r in rows if r["Status"] == "closed"]
    ri = [r["Return ideal %"] for r in closed_i]
    rc = [r["Return cost %"] for r in closed_i]
    summ = {
        "Structure":            name,
        "Trades (closed)":      len(closed_i),
        "Win rate ideal %":     round(sum(1 for x in ri if x > 0) / len(ri) * 100, 0) if ri else None,
        "Avg return/trade ideal %": round(st.mean(ri), 1) if ri else None,
        "Avg return/trade cost %":  round(st.mean(rc), 1) if rc else None,
        "Avg debit/ct $":       round(st.mean([r["Debit/ct $"] for r in closed_i]), 0) if closed_i else None,
        "Avg slippage/ct $":    round(st.mean([r["Slippage/ct $"] for r in closed_i]), 0) if closed_i else None,
        "Avg slippage % debit": round(st.mean([r["Slippage % debit"] for r in closed_i]), 1) if closed_i else None,
        "Avg hold days":        round(st.mean([r["Hold days"] for r in closed_i if r["Hold days"] != ""]), 0) if closed_i else None,
        "Total return ideal %": round(perf_metrics(ideal.equity)["total"] * 100, 1),
        "Total return cost %":  round(perf_metrics(pess.equity)["total"] * 100, 1),
        "Sharpe (cost)":        round(perf_metrics(pess.equity)["sharpe"], 2),
        "Max DD cost %":        round(perf_metrics(pess.equity)["maxdd"] * 100, 1),
    }
    return rows, summ


def _write_sheet(writer, sheet, df):
    df.to_excel(writer, sheet_name=sheet, index=False, startrow=1, header=False)
    wb, ws = writer.book, writer.sheets[sheet]
    head = wb.add_format({"bold": True, "bg_color": "#312e81", "font_color": "white",
                          "border": 1, "align": "center", "valign": "vcenter"})
    for c, col in enumerate(df.columns):
        ws.write(0, c, col, head)
        width = max(len(str(col)) + 2, 11)
        fmt = wb.add_format({"num_format": COL_FMT[col]}) if col in COL_FMT else None
        ws.set_column(c, c, width, fmt)
    ws.freeze_panes(1, 1)
    ws.autofilter(0, 0, len(df), len(df.columns) - 1)


def main():
    inp = build_inputs()
    all_rows, summaries = {}, []
    for name, cls in STRATS:
        print(f"  running {name}…")
        rows, summ = trade_rows(name, cls, inp)
        all_rows[name] = rows
        summaries.append(summ)

    with pd.ExcelWriter(OUT, engine="xlsxwriter") as writer:
        _write_sheet(writer, "Summary", pd.DataFrame(summaries))
        for name, _ in STRATS:
            _write_sheet(writer, name[:31], pd.DataFrame(all_rows[name]))
        # assumptions tab
        notes = pd.DataFrame({"Assumptions / caveats": [
            "Window: 2025-06-11 to 2026-06-11 (~1 year, one trending regime).",
            f"Selection: monthly, top {SELECT} by composite score (R/R 50% / IV-rank 15% / RSI 35%).",
            "Sizing: equal risk per cohort ($50k/month split across that month's trades).",
            "Slippage (cost run): one-way max($0.02/sh, 3% x option price), charged on EVERY leg, both entry and exit.",
            "Ideal run = 0 slippage (mid fills). Truth lies between the two columns.",
            "Exits: 50% of max profit / -50% stop / 7 DTE, whichever first (shared with live system).",
            "Marks for still-open trades use last-trade close; options data has no bid/ask (delayed feed).",
            "All $ figures are PER CONTRACT (x1 = 100 shares). Returns are on the debit paid.",
        ]})
        _write_sheet(writer, "Assumptions", notes)

    print(f"\nWrote {OUT}")
    print(f"{'Structure':<16}{'Trades':>8}{'Ideal%':>9}{'Cost%':>8}{'Slip%dbt':>10}")
    for s in summaries:
        print(f"{s['Structure']:<16}{s['Trades (closed)']:>8}{s['Total return ideal %']:>9}"
              f"{s['Total return cost %']:>8}{s['Avg slippage % debit']:>10}")


if __name__ == "__main__":
    main()
