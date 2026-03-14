"""Live execution engine — real Binance Futures orders.

Same signal processing pipeline as dry-run Simulator, but:
- Entry/Reversal → market orders via API
- SL → stop-market order on exchange (protection)
- TP monitoring → candle-based (same as dry-run), partial close via market order
- Per-pair config: each pair has its own margin, leverage
- Order fill verification — polls Binance to confirm actual avgPrice
- Account protection — max drawdown circuit breaker, max total margin check
- Position sync — periodically reconciles with Binance exchange positions

State machine:
  OBSERVING → first start, shows existing positions, waits for first signal
  ACTIVE    → after first signal, places real orders
  STOPPED   → circuit breaker triggered, no new trades
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
    STOPPED = "STOPPED"      # circuit breaker triggered


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
        self._initial_balance: float = 0.0  # captured on first refresh

        # Fee rates (for PnL estimation before fills arrive)
        trading = config.get("trading", {})
        self._maker_fee = trading.get("maker_fee", 0.0002)
        self._taker_fee = trading.get("taker_fee", 0.0005)

        # ── Account Protection ──
        protection = config.get("protection", {})
        # Max drawdown as % of initial balance — triggers circuit breaker
        self._max_drawdown_pct: float = protection.get("max_drawdown_pct", 10.0)
        # Max total margin across all open positions as % of balance
        self._max_total_margin_pct: float = protection.get("max_total_margin_pct", 50.0)
        # Max concurrent open positions
        self._max_open_positions: int = protection.get("max_open_positions", 5)
        # Circuit breaker state
        self.circuit_breaker_triggered: bool = False
        self.circuit_breaker_reason: str = ""

        # ── Position Sync ──
        self._last_sync_ts: float = 0.0
        self._sync_interval: float = 60.0  # seconds between syncs
        self.sync_warnings: list[str] = []  # drift log for frontend

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
        # Capture initial balance on first call (for drawdown calc)
        if self._initial_balance == 0.0 and self.balance > 0:
            self._initial_balance = self.balance
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
    # Order Fill Verification
    # ------------------------------------------------------------------

    def _verify_fill(self, symbol: str, result: dict, fallback_price: float) -> float:
        """Verify order fill price from Binance. Returns actual avgPrice.

        1. Check avgPrice in order response
        2. If missing/zero, poll order by orderId
        3. Last resort: use fallback_price and log warning
        """
        # Step 1: Check immediate response
        avg_price = float(result.get("avgPrice", 0))
        if avg_price > 0:
            return avg_price

        # Step 2: Poll order for fill confirmation
        order_id = result.get("orderId")
        if order_id:
            verified_price = self._client.get_order_fill_price(symbol, int(order_id))
            if verified_price > 0:
                return verified_price

        # Step 3: Fallback — log warning
        logger.warning(
            "[FILL] Could not verify fill for %s orderId=%s — using fallback %.4f",
            symbol, result.get("orderId"), fallback_price,
        )
        return fallback_price

    # ------------------------------------------------------------------
    # Account Protection
    # ------------------------------------------------------------------

    def _check_account_protection(self, symbol: str) -> str | None:
        """Check account protection rules before opening a new position.

        Returns None if OK, or a reason string if blocked.
        """
        # Circuit breaker already triggered
        if self.circuit_breaker_triggered:
            return f"Circuit breaker active: {self.circuit_breaker_reason}"

        # 1. Max drawdown check
        if self._initial_balance > 0:
            drawdown_pct = (self._initial_balance - self.balance) / self._initial_balance * 100
            if drawdown_pct >= self._max_drawdown_pct:
                self.circuit_breaker_triggered = True
                self.circuit_breaker_reason = (
                    f"Max drawdown {self._max_drawdown_pct}% reached "
                    f"(current: {drawdown_pct:.2f}%)"
                )
                logger.critical("[PROTECTION] %s", self.circuit_breaker_reason)
                return self.circuit_breaker_reason

        # 2. Max concurrent positions check
        open_count = sum(
            1 for pos in self._positions.values() if pos.condition != 0.0
        )
        if open_count >= self._max_open_positions:
            reason = (
                f"Max open positions ({self._max_open_positions}) reached — "
                f"currently {open_count} open"
            )
            logger.warning("[PROTECTION] %s — skipping %s", reason, symbol)
            return reason

        # 3. Max total margin check
        pc = self._pair_configs.get(symbol)
        if pc and self._initial_balance > 0:
            # Sum margin of all open positions + this new one
            total_margin = pc.margin  # the new trade
            for sym, pos in self._positions.items():
                if pos.condition != 0.0:
                    sym_pc = self._pair_configs.get(sym)
                    if sym_pc:
                        total_margin += sym_pc.margin * pos.remaining_qty

            margin_pct = total_margin / self._initial_balance * 100
            if margin_pct > self._max_total_margin_pct:
                reason = (
                    f"Total margin {margin_pct:.1f}% exceeds limit "
                    f"{self._max_total_margin_pct}% — skipping {symbol}"
                )
                logger.warning("[PROTECTION] %s", reason)
                return reason

        return None

    def reset_circuit_breaker(self) -> None:
        """Manually reset the circuit breaker (after user review)."""
        self.circuit_breaker_triggered = False
        self.circuit_breaker_reason = ""
        logger.info("[PROTECTION] Circuit breaker manually reset")

    # ------------------------------------------------------------------
    # Position Sync — reconcile with Binance exchange
    # ------------------------------------------------------------------

    def sync_positions(self) -> list[str]:
        """Compare bot's position tracking with actual Binance positions.

        Returns list of warning messages about any drift detected.
        """
        now = time.time()
        if now - self._last_sync_ts < self._sync_interval:
            return []
        self._last_sync_ts = now

        warnings: list[str] = []

        try:
            exchange_positions = self._client.get_positions()
        except Exception as e:
            msg = f"[SYNC] Failed to fetch exchange positions: {e}"
            logger.error(msg)
            warnings.append(msg)
            return warnings

        # Build lookup: symbol → exchange position
        exchange_map: dict[str, dict] = {}
        for ep in exchange_positions:
            exchange_map[ep["symbol"]] = ep

        # Check 1: Bot thinks position is open, but exchange says closed
        for sym in list(self._positions.keys()):
            pos = self._positions[sym]
            if pos.condition == 0.0:
                continue  # bot already knows it's closed

            if sym not in exchange_map:
                # Position closed externally (manually, liquidation, etc.)
                msg = (
                    f"[SYNC] {sym} {pos.side}: bot has open position but "
                    f"exchange shows no position — marking as closed"
                )
                logger.warning(msg)
                warnings.append(msg)

                # Clean up SL orders
                old_sl = self._sl_order_ids.get(sym)
                if old_sl:
                    try:
                        self._client.cancel_order(sym, old_sl)
                    except Exception:
                        pass

                # Mark closed in bot state
                pos.condition = 0.0
                pos.remaining_qty = 0.0
                self._position_qty.pop(sym, None)
                self._sl_order_ids.pop(sym, None)
                continue

            # Check 2: Position exists on both, but quantity drifted
            ep = exchange_map[sym]
            exchange_qty = ep["amount"]
            bot_qty = self._position_qty.get(sym, 0)

            if bot_qty > 0 and abs(exchange_qty - bot_qty) / bot_qty > 0.01:
                msg = (
                    f"[SYNC] {sym}: qty drift detected — "
                    f"bot={bot_qty:.6f} exchange={exchange_qty:.6f}"
                )
                logger.warning(msg)
                warnings.append(msg)
                # Update bot qty to match exchange
                self._position_qty[sym] = exchange_qty

            # Check 3: Side mismatch
            if ep["side"] != pos.side:
                msg = (
                    f"[SYNC] {sym}: side mismatch — "
                    f"bot={pos.side} exchange={ep['side']}"
                )
                logger.warning(msg)
                warnings.append(msg)
                # This is a critical mismatch — close the bot's position tracking
                pos.condition = 0.0
                pos.remaining_qty = 0.0
                self._position_qty.pop(sym, None)
                self._sl_order_ids.pop(sym, None)

        # Check 4: Exchange has position that bot doesn't know about
        for sym, ep in exchange_map.items():
            if sym not in self._pair_configs:
                continue  # not a pair we're trading
            bot_pos = self._positions.get(sym)
            if bot_pos is None or bot_pos.condition == 0.0:
                msg = (
                    f"[SYNC] {sym} {ep['side']}: exchange has position "
                    f"(qty={ep['amount']:.6f}) but bot has none — "
                    f"external position, not managed"
                )
                logger.warning(msg)
                warnings.append(msg)

        # Check 5: Verify SL orders still exist on exchange
        for sym, sl_id in list(self._sl_order_ids.items()):
            if not self.has_position(sym):
                continue
            try:
                order = self._client.get_order(sym, sl_id)
                status = order.get("status", "")
                if status in ("CANCELED", "EXPIRED", "FILLED"):
                    if status == "FILLED":
                        msg = f"[SYNC] {sym}: SL order {sl_id} already FILLED on exchange"
                        logger.info(msg)
                        warnings.append(msg)
                    else:
                        msg = (
                            f"[SYNC] {sym}: SL order {sl_id} is {status} — "
                            f"re-placing SL for protection"
                        )
                        logger.warning(msg)
                        warnings.append(msg)
                        # Re-place SL
                        pos = self._positions.get(sym)
                        qty = self._position_qty.get(sym, 0)
                        if pos and qty > 0:
                            self._place_sl_order(sym, pos, qty)
            except Exception as e:
                logger.error("[SYNC] Failed to check SL order %s for %s: %s", sl_id, sym, e)

        if warnings:
            self.sync_warnings.extend(warnings)
            # Keep last 50 warnings
            self.sync_warnings = self.sync_warnings[-50:]

        # Also refresh balance during sync
        try:
            self.refresh_balance()
        except Exception:
            pass

        return warnings

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

        # ── Account protection checks ──
        self.refresh_balance()
        block_reason = self._check_account_protection(symbol)
        if block_reason:
            logger.warning("[SIGNAL] Blocked for %s: %s", symbol, block_reason)
            return closed_trades

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

        # Place market order + verify fill
        order_side = "BUY" if signal.side == "LONG" else "SELL"
        try:
            result = self._client.market_order(symbol, order_side, qty)
            fill_price = self._verify_fill(symbol, result, signal.price)
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
        entry_fee = notional * self._maker_fee
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

        # Place market close order + verify fill
        close_side = "SELL" if pos.side == "LONG" else "BUY"
        actual_qty = qty * pos.remaining_qty
        try:
            result = self._client.market_order(symbol, close_side, actual_qty, reduce_only=True)
            fill_price = self._verify_fill(symbol, result, exit_price)
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
        exit_fee = actual_qty * fill_price * self._maker_fee

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
            # TP exit — send market close order for partial qty + verify fill
            close_side = "SELL" if pos.side == "LONG" else "BUY"
            try:
                result = self._client.market_order(symbol, close_side, exit_qty, reduce_only=True)
                actual_price = self._verify_fill(symbol, result, exit_ev.price)
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
        drawdown_pct = 0.0
        if self._initial_balance > 0:
            drawdown_pct = (self._initial_balance - self.balance) / self._initial_balance * 100

        open_count = sum(1 for pos in self._positions.values() if pos.condition != 0.0)

        return {
            "initial_balance": round(self._initial_balance, 2),
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
            # Protection stats
            "drawdown_pct": round(drawdown_pct, 2),
            "max_drawdown_limit": self._max_drawdown_pct,
            "open_positions": open_count,
            "max_open_positions": self._max_open_positions,
            "circuit_breaker": self.circuit_breaker_triggered,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "sync_warnings": self.sync_warnings[-10:],
        }

    def get_pair_state(self, symbol: str) -> str:
        """Return pair's current state (OBSERVING/ACTIVE)."""
        return self._pair_states.get(symbol, PairState.OBSERVING).value

    def emergency_close_all(self) -> list[LiveTrade]:
        """Emergency: close all positions immediately.

        Fetches real-time prices from Binance book ticker for accurate PnL.
        Falls back to entry price only if price fetch fails.
        """
        all_trades: list[LiveTrade] = []

        # Try to get current prices for all symbols at once
        current_prices: dict[str, float] = {}
        for symbol in list(self._positions.keys()):
            if not self.has_position(symbol):
                continue
            try:
                # Use bookTicker for fastest price
                ticker = self._client._request(
                    "GET", "/fapi/v1/ticker/price",
                    {"symbol": symbol}, signed=False,
                )
                current_prices[symbol] = float(ticker.get("price", 0))
            except Exception:
                pass

        for symbol in list(self._positions.keys()):
            if self.has_position(symbol):
                pos = self._positions[symbol]
                price = current_prices.get(symbol) or pos.entry_price
                trades = self._close_position(symbol, price)
                all_trades.extend(trades)
        return all_trades
