"""Keltner Channel DCA + TP risk manager.

PMax determines macro trend. Keltner Channels determine DCA/TP levels:
  LONG:  Limit BUY  at KC Lower Band (DCA)  |  Limit SELL at KC Upper Band (TP)
  SHORT: Limit SELL at KC Upper Band (DCA)  |  Limit BUY  at KC Lower Band (TP)

Every new candle: cancel unfilled orders, re-place at updated Keltner levels.
All DCA/TP orders are Post-Only (GTX) = Maker fee guaranteed.
Kill switch on PMax reversal: cancel all limits, market close.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_DCA_STEPS = 2
TP_CLOSE_PERCENT = 0.20  # each TP closes 20% of current position


@dataclass
class PositionState:
    symbol: str = ""
    side: str = ""                     # "LONG" or "SHORT"
    condition: float = 0.0             # 1.0=LONG, -1.0=SHORT, 0.0=flat
    entry_time: int = 0

    # Position tracking
    initial_entry_price: float = 0.0
    average_entry_price: float = 0.0
    entry_atr: float = 0.0
    margin_per_step: float = 0.0
    leverage: int = 1
    total_position_notional: float = 0.0
    total_fills: int = 0
    dca_fills_count: int = 0           # current wave DCA count (resets after all TPs sold)
    dca_wave_sold: int = 0             # how many TPs sold in current wave

    # Dynamic order tracking (for live executor)
    pending_dca_order_id: int = 0      # current open DCA limit order
    pending_tp_order_id: int = 0       # current open TP limit order
    pending_dca_price: float = 0.0     # current DCA limit price
    pending_tp_price: float = 0.0      # current TP limit price

    # Backward compat
    remaining_qty: float = 1.0
    entry_price: float = 0.0

    # Legacy fields (for frontend compat)
    dca_levels: list = field(default_factory=list)
    tp_price: float = 0.0
    tp_order_id: int = 0
    tp_armed: bool = True


class RiskManager:
    """Keltner Channel DCA + TP manager."""

    def __init__(self, config: dict, risk_config: dict | None = None) -> None:
        trading = config.get("trading", {})
        self._margin_per_trade = trading.get("margin_per_trade", 100.0)
        self._leverage = trading.get("leverage", 10)

    def open_position(
        self, symbol: str, side: str, entry_price: float, atr_value: float,
        margin_per_trade: float | None = None, leverage: int | None = None,
    ) -> PositionState:
        """Create new position on PMax crossover (market order)."""
        margin = margin_per_trade or self._margin_per_trade
        lev = leverage or self._leverage
        notional = margin * lev

        pos = PositionState(
            symbol=symbol, side=side,
            condition=1.0 if side == "LONG" else -1.0,
            initial_entry_price=entry_price,
            average_entry_price=entry_price,
            entry_price=entry_price,
            entry_atr=atr_value,
            margin_per_step=margin,
            leverage=lev,
            total_position_notional=notional,
            total_fills=1,
            dca_fills_count=0,
            remaining_qty=1.0,
        )

        logger.info("[ENTRY] %s %s @ %.4f | notional=%.2f", symbol, side, entry_price, notional)
        return pos

    def check_keltner_signals(
        self, pos: PositionState,
        candle_high: float, candle_low: float, candle_close: float,
        kc_upper: float, kc_lower: float,
    ) -> tuple[str, float]:
        """Check if Keltner band was touched during this candle.

        Simulates limit orders sitting at KC bands:
          LONG DCA:  limit buy  @ KC Lower → candle low touches lower band
          LONG TP:   limit sell @ KC Upper → candle high touches upper band
          SHORT DCA: limit sell @ KC Upper → candle high touches upper band
          SHORT TP:  limit buy  @ KC Lower → candle low touches lower band

        Returns ("DCA"|"TP"|"", fill_price).
        Priority: DCA first (buy the dip), then TP (sell the rip).
        """
        if pos.condition == 0.0:
            return "", 0.0

        if pos.side == "LONG":
            # DCA: price dips to KC Lower Band (max per wave)
            if pos.dca_fills_count < MAX_DCA_STEPS and candle_low <= kc_lower:
                return "DCA", kc_lower

            # TP: price rises to KC Upper Band (only if we have DCA to unload)
            if pos.dca_fills_count > 0 and candle_high >= kc_upper:
                return "TP", kc_upper

        else:  # SHORT
            # DCA: price rises to KC Upper Band (max per wave)
            if pos.dca_fills_count < MAX_DCA_STEPS and candle_high >= kc_upper:
                return "DCA", kc_upper

            # TP: price dips to KC Lower Band
            if pos.dca_fills_count > 0 and candle_low <= kc_lower:
                return "TP", kc_lower

        return "", 0.0

    def process_dca_fill(self, pos: PositionState, fill_price: float) -> None:
        """DCA limit order filled — recalculate average entry."""
        step_notional = pos.margin_per_step * pos.leverage
        old_total = pos.total_position_notional
        new_total = old_total + step_notional

        pos.average_entry_price = (
            (pos.average_entry_price * old_total + fill_price * step_notional) / new_total
        )
        pos.entry_price = pos.average_entry_price
        pos.total_position_notional = new_total
        pos.total_fills += 1
        pos.dca_fills_count += 1
        pos.dca_wave_sold = 0  # reset sell counter — new buys in this wave

        logger.info(
            "[DCA] %s fill @ %.4f | avg=%.4f | notional=%.2f | dca=%d/%d",
            pos.symbol, fill_price, pos.average_entry_price,
            pos.total_position_notional, pos.dca_fills_count, MAX_DCA_STEPS,
        )

    def process_tp_fill(self, pos: PositionState, fill_price: float) -> float:
        """TP limit order filled — close 20% of position. Returns closed notional."""
        closed_notional = pos.total_position_notional * TP_CLOSE_PERCENT
        pos.total_position_notional -= closed_notional
        pos.dca_fills_count = max(0, pos.dca_fills_count - 1)
        pos.dca_wave_sold += 1
        pos.total_fills = max(1, pos.total_fills - 1)

        # All DCAs in this wave sold → reset for next wave
        if pos.dca_fills_count == 0:
            pos.dca_wave_sold = 0

        pos.remaining_qty = (
            pos.total_position_notional / (pos.margin_per_step * pos.leverage)
            if pos.total_position_notional > 0 else 0.0
        )

        if pos.total_position_notional < 1.0:
            pos.condition = 0.0
            pos.remaining_qty = 0.0
            pos.total_position_notional = 0.0

        logger.info(
            "[TP] %s closed %.2f @ %.4f | remaining=%.2f | dca=%d/%d",
            pos.symbol, closed_notional, fill_price,
            pos.total_position_notional, pos.dca_fills_count, MAX_DCA_STEPS,
        )
        return closed_notional

    def get_grid_info(self, pos: PositionState) -> dict[str, Any]:
        """Return state for API/frontend."""
        return {
            "initial_entry": pos.initial_entry_price,
            "average_entry": pos.average_entry_price,
            "entry_atr": pos.entry_atr,
            "total_notional": pos.total_position_notional,
            "total_fills": pos.total_fills,
            "dca_fills_count": pos.dca_fills_count,
            "max_dca_steps": MAX_DCA_STEPS,
            "pending_dca_price": pos.pending_dca_price,
            "pending_tp_price": pos.pending_tp_price,
            "tp_price": pos.pending_tp_price,
            "dca_levels": [],
        }
