"""Live execution engine — real Binance Futures orders.

Same signal processing pipeline as dry-run Simulator, but:
- Entry/Reversal → market orders via API
- SL → stop-market order on exchange (protection)
- TP monitoring → candle-based (same as dry-run), partial close via market order
- Per-pair config: each pair has its own margin, leverage

State machine:
  OBSERVING → first start, shows existing positions, waits for first signal
  ACTIVE    → after first signal, places real orders
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.data.binance_futures import BinanceFutures
from core.strategy.risk_manager import ExitEvent, ExitReason, PositionState, RiskManager
from core.strategy.signals import Signal

logger = logging.getLogger("live_executor")


class PairState(str, Enum):
    """Per-pair operational state."""
    OBSERVING = "OBSERVING"  # watching, not trading yet
    ACTIVE = "ACTIVE"        # live trading enabled


@dataclass
class PairConfig:
    """Per-pair trading configuration."""
    symbol: str
    margin: float        # USDT margin per trade
    leverage: int        # leverage multiplier
    enabled: bool = True


@dataclass
class LiveTrade:
    """Completed live trade record."""
    id: int
    symbol: str
    side: str
    entry_price: float
    entry_time: int
    exit_price: float
    exit_time: int
    exit_reason: str
    qty: float            # base asset quantity
    qty_usdt: float       # notional value
    leverage: int
    pnl_usdt: float
    pnl_percent: float
    fee_usdt: float
    order_id: int = 0


class LiveExecutor:
    """Live trading engine with per-pair config and observe-first logic."""

    def __init__(self, client: BinanceFutures, config: dict) -> None:
        self._client = client
        self._config = config
        self._risk_mgr = RiskManager(config)

        # Per-pair state
        self._pair_configs: dict[str, PairConfig] = {}
        self._pair_states: dict[str, PairState] = {}

        # Active positions: symbol → PositionState (same as Simulator)
        self._positions: dict[str, PositionState] = {}
        # Track real quantities on exchange
        self._position_qty: dict[str, float] = {}  # symbol → base qty
        # SL order IDs on exchange
        self._sl_order_ids: dict[str, int] = {}

        # Last processed signal timestamp per symbol
        self._last_signal_ts: dict[str, int] = {}

        # Trade history
        self.trades: list[LiveTrade] = []
        self._trade_counter = 0

        # Wallet tracking (from Binance)
        self.balance: float = 0.0
        self.available_balance: float = 0.0

        # Fee rates (for PnL estimation before fills arrive)
        trading = config.get("trading", {})
        self._maker_fee = trading.get("maker_fee", 0.0002)
        self._taker_fee = trading.get("taker_fee", 0.0005)

        # Stats
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.losing_trades: int = 0
        self.total_pnl: float = 0.0
        self.total_fees: float = 0.0

    @property
    def positions(self) -> dict[str, PositionState]:
        return self._positions

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def configure_pair(self, symbol: str, margin: float, leverage: int) -> None:
        """Configure a pair for live trading."""
        self._pair_configs[symbol] = PairConfig(
            symbol=symbol, margin=margin, leverage=leverage,
        )
        self._pair_states[symbol] = PairState.OBSERVING

        # Set leverage on exchange
        try:
            self._client.set_leverage(symbol, leverage)
            logger.info("[SETUP] %s leverage set to %dx", symbol, leverage)
        except Exception as e:
            logger.error("[SETUP] Failed to set leverage for %s: %s", symbol, e)

        # Set margin type to ISOLATED
        try:
            self._client.set_margin_type(symbol, "ISOLATED")
            logger.info("[SETUP] %s margin type set to ISOLATED", symbol)
        except Exception as e:
            logger.warning("[SETUP] Margin type for %s: %s", symbol, e)

    def refresh_balance(self) -> dict[str, float]:
        """Fetch latest USDT balance from Binance."""
        bal = self._client.get_balance()
        self.balance = bal["balance"]
        self.available_balance = bal["available"]
        return bal

    def load_exchange_positions(self) -> list[dict[str, Any]]:
        """Load existing open positions from Binance.

        Called on start to show what's already open.
        Does NOT create PositionState — just returns raw data for display.
        """
        return self._client.get_positions()

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions and self._positions[symbol].condition != 0.0

    def is_observing(self, symbol: str) -> bool:
        return self._pair_states.get(symbol) == PairState.OBSERVING

    def is_active(self, symbol: str) -> bool:
        return self._pair_states.get(symbol) == PairState.ACTIVE

    def activate_pair(self, symbol: str) -> None:
        """Transition pair from OBSERVING to ACTIVE."""
        self._pair_states[symbol] = PairState.ACTIVE
        logger.info("[STATE] %s → ACTIVE (live trading enabled)", symbol)

    # ------------------------------------------------------------------
    # Signal Processing (same logic as Simulator)
    # ------------------------------------------------------------------

    def process_signal(self, signal: Signal, entry_time: int = 0) -> list[LiveTrade]:
        """Handle a new entry signal — places real orders on Binance.

        If pair is OBSERVING, this is the first signal → activate pair.
        """
        closed_trades: list[LiveTrade] = []
        symbol = signal.symbol
        pc = self._pair_configs.get(symbol)
        if not pc:
            logger.warning("[SIGNAL] No config for %s — skipping", symbol)
            return closed_trades

        # Prevent reopening from same signal after TP/SL exit
        last_ts = self._last_signal_ts.get(symbol, 0)
        if signal.timestamp == last_ts and not self.has_position(symbol):
            return closed_trades

        # If OBSERVING → activate on first signal
        if self.is_observing(symbol):
            self.activate_pair(symbol)

        if self.has_position(symbol):
            existing = self._positions[symbol]
            if existing.side == signal.side:
                return closed_trades  # same direction — no pyramiding
            # Opposite direction — close existing (reversal)
            closed_trades.extend(self._close_position(symbol, signal.price))

        # Check balance
        self.refresh_balance()
        if self.available_balance < pc.margin:
            logger.warning(
                "[SIGNAL] Insufficient balance for %s: need %.2f, have %.2f",
                symbol, pc.margin, self.available_balance,
            )
            return closed_trades

        # Calculate quantity
        qty = self._client.calc_quantity(symbol, pc.margin, pc.leverage, signal.price)
        if qty <= 0:
            logger.warning("[SIGNAL] Calculated qty=0 for %s — skipping", symbol)
            return closed_trades

        # Place market order
        order_side = "BUY" if signal.side == "LONG" else "SELL"
        try:
            result = self._client.market_order(symbol, order_side, qty)
            fill_price = float(result.get("avgPrice", signal.price))
            if fill_price == 0:
                fill_price = signal.price
        except Exception as e:
            logger.error("[ORDER] Market order failed for %s: %s", symbol, e)
            return closed_trades

        # Create position state (same as Simulator)
        pos = self._risk_mgr.open_position(symbol, signal.side, fill_price)
        pos.entry_time = entry_time or signal.timestamp
        self._positions[symbol] = pos
        self._position_qty[symbol] = qty
        self._last_signal_ts[symbol] = signal.timestamp

        # Place SL on exchange (protection)
        self._place_sl_order(symbol, pos, qty)

        # Entry fee estimation
        notional = qty * fill_price
        entry_fee = notional * self._taker_fee
        self.total_fees += entry_fee

        logger.info(
            "[ENTRY] %s %s @ %.4f qty=%.6f notional=%.2f | TP1=%.4f TP2=%.4f TP3=%.4f SL=%.4f",
            symbol, signal.side, fill_price, qty, notional,
            pos.tp1_line, pos.tp2_line, pos.tp3_line, pos.sl_line,
        )

        return closed_trades

    def _place_sl_order(self, symbol: str, pos: PositionState, qty: float) -> None:
        """Place stop-loss order on exchange for protection."""
        sl_side = "SELL" if pos.side == "LONG" else "BUY"
        sl_price = self._client.calc_price(symbol, pos.sl_line)
        try:
            result = self._client.stop_market_order(symbol, sl_side, qty, sl_price)
            self._sl_order_ids[symbol] = result.get("orderId", 0)
            logger.info("[SL] Placed for %s @ %.4f orderId=%s", symbol, sl_price, result.get("orderId"))
        except Exception as e:
            logger.error("[SL] Failed to place SL for %s: %s", symbol, e)

    def _update_sl_order(self, symbol: str, pos: PositionState, remaining_qty: float) -> None:
        """Cancel old SL and place new one with updated quantity."""
        # Cancel existing SL
        old_id = self._sl_order_ids.get(symbol)
        if old_id:
            try:
                self._client.cancel_order(symbol, old_id)
            except Exception:
                pass

        if remaining_qty <= 0 or pos.condition == 0.0:
            return

        # Place new SL with remaining qty
        self._place_sl_order(symbol, pos, remaining_qty)

    def _close_position(self, symbol: str, exit_price: float) -> list[LiveTrade]:
        """Force-close a position (reversal). Sends market close order."""
        if not self.has_position(symbol):
            return []

        pos = self._positions[symbol]
        qty = self._position_qty.get(symbol, 0)
        if qty <= 0:
            return []

        # Cancel all open orders (SL/TP) for this symbol
        try:
            self._client.cancel_all_orders(symbol)
        except Exception as e:
            logger.error("[CLOSE] Failed to cancel orders for %s: %s", symbol, e)

        # Place market close order
        close_side = "SELL" if pos.side == "LONG" else "BUY"
        actual_qty = qty * pos.remaining_qty
        try:
            result = self._client.market_order(symbol, close_side, actual_qty, reduce_only=True)
            fill_price = float(result.get("avgPrice", exit_price))
            if fill_price == 0:
                fill_price = exit_price
        except Exception as e:
            logger.error("[CLOSE] Market close failed for %s: %s", symbol, e)
            fill_price = exit_price

        # Record trade
        self._trade_counter += 1
        pc = self._pair_configs.get(symbol)
        notional = (pc.margin * pc.leverage * pos.remaining_qty) if pc else actual_qty * fill_price

        if pos.side == "LONG":
            pnl_pct = (fill_price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl_pct = (pos.entry_price - fill_price) / pos.entry_price * 100

        pnl_usdt = notional * pnl_pct / 100
        exit_fee = actual_qty * fill_price * self._taker_fee

        self._update_stats(pnl_usdt, exit_fee)

        trade = LiveTrade(
            id=self._trade_counter,
            symbol=symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=fill_price,
            exit_time=int(time.time() * 1000),
            exit_reason="REVERSAL",
            qty=actual_qty,
            qty_usdt=round(notional, 2),
            leverage=pc.leverage if pc else 1,
            pnl_usdt=round(pnl_usdt, 4),
            pnl_percent=round(pnl_pct, 4),
            fee_usdt=round(exit_fee, 4),
        )
        self.trades.append(trade)

        # Mark closed
        pos.condition = 0.0
        pos.remaining_qty = 0.0
        self._position_qty.pop(symbol, None)
        self._sl_order_ids.pop(symbol, None)

        logger.info(
            "[CLOSE] %s %s @ %.4f | PnL=%.4f USDT (%.2f%%)",
            symbol, pos.side, fill_price, pnl_usdt, pnl_pct,
        )

        return [trade]

    def process_candle(self, symbol: str, high: float, low: float, close_time: int) -> list[LiveTrade]:
        """Check TP/SL for an active position — same as Simulator but sends real orders.

        SL is already on exchange as stop-market. TP exits are detected here and
        executed via market order (partial close).
        """
        if not self.has_position(symbol):
            return []

        pos = self._positions[symbol]
        exits = self._risk_mgr.check_exits(pos, high, low)
        completed: list[LiveTrade] = []

        for exit_ev in exits:
            trade = self._execute_exit(symbol, pos, exit_ev, close_time)
            if trade:
                completed.append(trade)

        # Mark fully closed
        if pos.remaining_qty <= 0 or abs(pos.condition) >= 1.3:
            pos.condition = 0.0
            pos.remaining_qty = 0.0
            self._position_qty.pop(symbol, None)
            self._sl_order_ids.pop(symbol, None)

        return completed

    def _execute_exit(
        self, symbol: str, pos: PositionState, exit_ev: ExitEvent, close_time: int
    ) -> LiveTrade | None:
        """Execute a TP/SL exit on Binance."""
        total_qty = self._position_qty.get(symbol, 0)
        exit_qty = total_qty * (exit_ev.qty_percent / 100)
        if exit_qty <= 0:
            return None

        pc = self._pair_configs.get(symbol)

        # For SL: exchange stop-market should have fired, but verify/cancel remaining
        if exit_ev.reason == ExitReason.SL:
            # SL was on exchange — it should have filled
            # Cancel any remaining orders
            try:
                self._client.cancel_all_orders(symbol)
            except Exception:
                pass
        else:
            # TP exit — send market close order for partial qty
            close_side = "SELL" if pos.side == "LONG" else "BUY"
            try:
                result = self._client.market_order(symbol, close_side, exit_qty, reduce_only=True)
                actual_price = float(result.get("avgPrice", exit_ev.price))
                if actual_price > 0:
                    exit_ev = ExitEvent(exit_ev.reason, actual_price, exit_ev.qty_percent, exit_ev.pnl)
            except Exception as e:
                logger.error("[EXIT] TP market order failed for %s: %s", symbol, e)

            # Update SL order with remaining qty
            remaining_qty = total_qty - exit_qty
            if remaining_qty > 0:
                self._position_qty[symbol] = remaining_qty  # temp update for SL recalc
                self._update_sl_order(symbol, pos, remaining_qty)

        # Record trade
        self._trade_counter += 1
        notional = (pc.margin * pc.leverage * (exit_ev.qty_percent / 100)) if pc else exit_qty * exit_ev.price

        if pos.side == "LONG":
            pnl_pct = (exit_ev.price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl_pct = (pos.entry_price - exit_ev.price) / pos.entry_price * 100

        pnl_usdt = notional * pnl_pct / 100
        exit_fee = exit_qty * exit_ev.price * self._maker_fee

        self._update_stats(pnl_usdt, exit_fee)

        trade = LiveTrade(
            id=self._trade_counter,
            symbol=symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=exit_ev.price,
            exit_time=close_time,
            exit_reason=exit_ev.reason.value,
            qty=exit_qty,
            qty_usdt=round(notional, 2),
            leverage=pc.leverage if pc else 1,
            pnl_usdt=round(pnl_usdt, 4),
            pnl_percent=round(pnl_pct, 4),
            fee_usdt=round(exit_fee, 4),
        )
        self.trades.append(trade)

        logger.info(
            "[EXIT] %s %s %s @ %.4f qty=%.6f | PnL=%.4f USDT (%.2f%%)",
            exit_ev.reason.value, symbol, pos.side, exit_ev.price, exit_qty, pnl_usdt, pnl_pct,
        )

        return trade

    def _update_stats(self, pnl_usdt: float, fee: float) -> None:
        self.total_pnl += pnl_usdt
        self.total_fees += fee
        self.total_trades += 1
        if pnl_usdt > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

    # ------------------------------------------------------------------
    # Status / Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics (same shape as Simulator.get_stats)."""
        win_rate = (
            self.winning_trades / self.total_trades * 100
            if self.total_trades > 0
            else 0
        )
        return {
            "initial_balance": self.balance,
            "current_balance": round(self.balance, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_pnl_pct": 0,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(win_rate, 2),
            "total_fees": round(self.total_fees, 4),
            "maker_fees": 0,
            "taker_fees": 0,
            "leverage": 0,
        }

    def get_pair_state(self, symbol: str) -> str:
        """Return pair's current state (OBSERVING/ACTIVE)."""
        return self._pair_states.get(symbol, PairState.OBSERVING).value

    def emergency_close_all(self) -> list[LiveTrade]:
        """Emergency: close all positions immediately."""
        all_trades: list[LiveTrade] = []
        for symbol in list(self._positions.keys()):
            if self.has_position(symbol):
                pos = self._positions[symbol]
                # Use entry price as estimate — actual fill will differ
                trades = self._close_position(symbol, pos.entry_price)
                all_trades.extend(trades)
        return all_trades
