"""
Exit rules — shared by the backtest engine and the live trading system.

Keeping the rule here (not in the backtest) guarantees the live "time to close"
alerts use the EXACT logic the backtest validated. All amounts are per-contract
dollars; `dte` is calendar days to expiry.

Defaults (tunable):
  profit_take   = 0.5  → capped (spread): close at 50% of MAX profit
                         uncapped (call): close at +50% on the debit paid
  stop_loss     = 0.5  → close if down 50% of the debit paid
  time_exit_dte = 7    → close once 7 days to expiry remain
"""

from dataclasses import dataclass


@dataclass
class ExitRules:
    profit_take:   float | None = 0.5
    stop_loss:     float | None = 0.5
    time_exit_dte: int | None   = 7


@dataclass
class ExitDecision:
    reason: str   # 'profit_target' | 'stop' | 'time'


def evaluate_exit(
    mark_pc: float | None,
    entry_debit_pc: float,
    max_value_pc: float | None,
    dte: int,
    rules: ExitRules,
) -> ExitDecision | None:
    """Return an ExitDecision if any rule fires, else None.

    mark_pc       — current per-contract value to close the position now
    entry_debit_pc— per-contract debit paid at entry
    max_value_pc  — per-contract max value (= width×100) for capped structures, else None
    """
    if mark_pc is None:
        return None
    debit, mark = entry_debit_pc, mark_pc

    if rules.profit_take is not None and debit > 0:
        if max_value_pc is not None:                       # capped: fraction of max profit
            denom = max_value_pc - debit
            frac  = (mark - debit) / denom if denom > 0 else 0.0
            if frac >= rules.profit_take:
                return ExitDecision("profit_target")
        elif (mark / debit - 1) >= rules.profit_take:      # uncapped: gain multiple
            return ExitDecision("profit_target")

    if rules.stop_loss is not None and debit > 0 and (mark / debit - 1) <= -rules.stop_loss:
        return ExitDecision("stop")

    if rules.time_exit_dte is not None and dte <= rules.time_exit_dte:
        return ExitDecision("time")

    return None
