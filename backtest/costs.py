"""
Execution-cost model.

The flat file has no bid/ask, so we cannot know the true spread paid. We model
one-way slippage per share as `max(floor, pct × option_price)` and apply it on
every leg, both entering and exiting. Running the backtest at pct=0 (ideal) and
a pessimistic pct brackets the truth — trust the *range*, not a point estimate.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    pct:   float = 0.0    # one-way slippage as a fraction of the option price
    floor: float = 0.0    # one-way slippage floor, $ per share

    def slip(self, price: float) -> float:
        return max(self.floor, self.pct * abs(price))


IDEAL       = CostModel(pct=0.0,  floor=0.0)
# ~half a typical bid/ask on a liquid equity option, with a 2¢ floor.
PESSIMISTIC = CostModel(pct=0.03, floor=0.02)
