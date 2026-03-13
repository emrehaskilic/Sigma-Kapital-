"""Risk manager — TP1/TP2/TP3 + SL state machine.

Mirrors the Pine Script condition-based exit logic:
  condition  1.0 → long entry, waiting TP1
  condition  1.1 → TP1 hit, waiting TP2
  condition  1.2 → TP2 hit, waiting TP3
  condition  1.3 → TP3 hit, fully closed
  condition -1.0 → short entry, waiting TP1
  (mirrors for negative)
  condition  0.0 → flat
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExitReason(str, Enum):
    TP1 = "TP1"
    TP2 = "TP2"
    TP3 = "TP3"
    SL = "SL"


@dataclass
class ExitEvent:
    reason: ExitReason
    price: float
    qty_percent: float
    pnl: float


@dataclass
class PositionState:
    symbol: str = ""
    side: str = ""                # "LONG" or "SHORT"
    entry_price: float = 0.0
    condition: float = 0.0        # state machine value
    tp1_line: float = 0.0
    tp2_line: float = 0.0
    tp3_line: float = 0.0
    sl_line: float = 0.0
    remaining_qty: float = 1.0    # fraction of original position
    entry_time: int = 0           # entry timestamp (ms)


class RiskManager:
    """Manages TP/SL state machine for one position."""

    def __init__(self, config: dict) -> None:
        risk = config["risk"]
        self.tp1_level = risk["tp1_level"]    # %
        self.tp1_qty = risk["tp1_qty"]        # % of position
        self.tp2_level = risk["tp2_level"]
        self.tp2_qty = risk["tp2_qty"]
        self.tp3_level = risk["tp3_level"]
        self.tp3_qty = risk["tp3_qty"]
        self.sl_level = risk["stop_loss"]

    def open_position(self, symbol: str, side: str, entry_price: float) -> PositionState:
        """Create a new position with calculated TP/SL levels."""
        pos = PositionState(symbol=symbol, side=side, entry_price=entry_price)

        if side == "LONG":
            pos.condition = 1.0
            pos.tp1_line = entry_price * (1 + self.tp1_level / 100)
            pos.tp2_line = entry_price * (1 + self.tp2_level / 100)
            pos.tp3_line = entry_price * (1 + self.tp3_level / 100)
            pos.sl_line = entry_price * (1 - self.sl_level / 100)
        else:  # SHORT
            pos.condition = -1.0
            pos.tp1_line = entry_price * (1 - self.tp1_level / 100)
            pos.tp2_line = entry_price * (1 - self.tp2_level / 100)
            pos.tp3_line = entry_price * (1 - self.tp3_level / 100)
            pos.sl_line = entry_price * (1 + self.sl_level / 100)

        return pos

    def check_exits(self, pos: PositionState, high: float, low: float) -> list[ExitEvent]:
        """Check current candle H/L against TP/SL lines. Returns exit events."""
        exits: list[ExitEvent] = []

        if pos.condition == 0.0:
            return exits

        is_long = pos.condition > 0
        price = pos.entry_price

        if is_long:
            # Pine Script switch priority: TP3 → TP2 → TP1 → SL
            # TP takes priority over SL when both trigger on the same candle.

            # TP1
            if pos.condition == 1.0 and high >= pos.tp1_line:
                qty_frac = self.tp1_qty / 100
                pnl = (pos.tp1_line - price) / price * 100 * qty_frac
                exits.append(ExitEvent(ExitReason.TP1, pos.tp1_line, self.tp1_qty, pnl))
                pos.condition = 1.1
                pos.remaining_qty -= qty_frac

            # TP2
            if pos.condition == 1.1 and high >= pos.tp2_line:
                qty_frac = self.tp2_qty / 100
                pnl = (pos.tp2_line - price) / price * 100 * qty_frac
                exits.append(ExitEvent(ExitReason.TP2, pos.tp2_line, self.tp2_qty, pnl))
                pos.condition = 1.2
                pos.remaining_qty -= qty_frac

            # TP3
            if pos.condition == 1.2 and high >= pos.tp3_line:
                qty_frac = self.tp3_qty / 100
                pnl = (pos.tp3_line - price) / price * 100 * qty_frac
                exits.append(ExitEvent(ExitReason.TP3, pos.tp3_line, self.tp3_qty, pnl))
                pos.condition = 1.3
                pos.remaining_qty = 0.0

            # Stop Loss — only if no TP was triggered this bar
            if not exits and pos.condition >= 1.0 and low <= pos.sl_line:
                pnl = (pos.sl_line - price) / price * 100 * pos.remaining_qty
                exits.append(ExitEvent(ExitReason.SL, pos.sl_line, pos.remaining_qty * 100, pnl))
                pos.condition = 0.0
                pos.remaining_qty = 0.0

        else:  # SHORT
            # Pine Script switch priority: TP3 → TP2 → TP1 → SL

            # TP1
            if pos.condition == -1.0 and low <= pos.tp1_line:
                qty_frac = self.tp1_qty / 100
                pnl = (price - pos.tp1_line) / price * 100 * qty_frac
                exits.append(ExitEvent(ExitReason.TP1, pos.tp1_line, self.tp1_qty, pnl))
                pos.condition = -1.1
                pos.remaining_qty -= qty_frac

            # TP2
            if pos.condition == -1.1 and low <= pos.tp2_line:
                qty_frac = self.tp2_qty / 100
                pnl = (price - pos.tp2_line) / price * 100 * qty_frac
                exits.append(ExitEvent(ExitReason.TP2, pos.tp2_line, self.tp2_qty, pnl))
                pos.condition = -1.2
                pos.remaining_qty -= qty_frac

            # TP3
            if pos.condition == -1.2 and low <= pos.tp3_line:
                qty_frac = self.tp3_qty / 100
                pnl = (price - pos.tp3_line) / price * 100 * qty_frac
                exits.append(ExitEvent(ExitReason.TP3, pos.tp3_line, self.tp3_qty, pnl))
                pos.condition = -1.3
                pos.remaining_qty = 0.0

            # Stop Loss — only if no TP was triggered this bar
            if not exits and pos.condition <= -1.0 and high >= pos.sl_line:
                pnl = (price - pos.sl_line) / price * 100 * pos.remaining_qty
                exits.append(ExitEvent(ExitReason.SL, pos.sl_line, pos.remaining_qty * 100, pnl))
                pos.condition = 0.0
                pos.remaining_qty = 0.0

        return exits
