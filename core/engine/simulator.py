"""Dry-run simulation engine — virtual wallet + order execution."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core.strategy.risk_manager import ExitEvent, ExitReason, PositionState, RiskManager
from core.strategy.signals import Signal


@dataclass
class Trade:
    """Completed trade record."""
    id: int
    symbol: str
    side: str
    entry_price: float
    entry_time: int
    exit_price: float
    exit_time: int
    exit_reason: str
    qty_usdt: float
    leverage: int
    pnl_usdt: float
    pnl_percent: float
    fee_usdt: float


@dataclass
class Wallet:
    """Virtual wallet state."""
    initial_balance: float
    balance: float
    leverage: int
    margin_per_trade: float
    maker_fee: float          # limit orders (TP/SL exits)
    taker_fee: float          # market orders (entry/reversal)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    maker_fees: float = 0.0
    taker_fees: float = 0.0


class Simulator:
    """Paper trading simulator — processes signals and manages positions."""

    def __init__(self, config: dict) -> None:
        trading = config["trading"]
        self._risk_mgr = RiskManager(config)

        # Support both old single fee_rate and new maker/taker split
        maker = trading.get("maker_fee", trading.get("fee_rate", 0.0002))
        taker = trading.get("taker_fee", trading.get("fee_rate", 0.0005))

        self.wallet = Wallet(
            initial_balance=trading["initial_balance"],
            balance=trading["initial_balance"],
            leverage=trading["leverage"],
            margin_per_trade=trading["margin_per_trade"],
            maker_fee=maker,
            taker_fee=taker,
        )
        # Active positions: symbol → PositionState
        self._positions: dict[str, PositionState] = {}
        # Trade history
        self.trades: list[Trade] = []
        self._trade_counter = 0

    @property
    def positions(self) -> dict[str, PositionState]:
        return self._positions

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions and self._positions[symbol].condition != 0.0

    def process_signal(self, signal: Signal, entry_time: int = 0) -> list[Trade]:
        """Handle a new entry signal.

        Pine Script reversal logic:
          leTrigger and condition[1] <= 0.0 → open LONG  (closes SHORT if any)
          seTrigger and condition[1] >= 0.0 → open SHORT (closes LONG if any)
        Same-direction signal while in position → skip (no pyramiding).
        """
        closed_trades: list[Trade] = []

        if self.has_position(signal.symbol):
            existing = self._positions[signal.symbol]
            if existing.side == signal.side:
                # Same direction — no pyramiding
                return closed_trades
            # Opposite direction — close existing position at current price (reversal)
            closed_trades.extend(self._close_position(signal.symbol, signal.price))

        # Check if we have enough balance
        margin = self.wallet.margin_per_trade
        if self.wallet.balance < margin:
            return closed_trades

        # Open position
        pos = self._risk_mgr.open_position(signal.symbol, signal.side, signal.price)
        pos.entry_time = entry_time or signal.timestamp
        self._positions[signal.symbol] = pos

        # Entry fee — market order = taker fee
        notional = margin * self.wallet.leverage
        entry_fee = notional * self.wallet.taker_fee
        self.wallet.balance -= entry_fee
        self.wallet.total_fees += entry_fee
        self.wallet.taker_fees += entry_fee

        return closed_trades

    def _close_position(self, symbol: str, exit_price: float) -> list[Trade]:
        """Force-close a position at the given price (used for reversals)."""
        if not self.has_position(symbol):
            return []

        pos = self._positions[symbol]
        self._trade_counter += 1

        margin = self.wallet.margin_per_trade
        notional = margin * self.wallet.leverage
        trade_notional = notional * pos.remaining_qty

        if pos.side == "LONG":
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        pnl_usdt = trade_notional * pnl_pct / 100
        # Reversal close = market order = taker fee
        exit_fee = trade_notional * self.wallet.taker_fee

        self.wallet.balance += pnl_usdt - exit_fee
        self.wallet.total_pnl += pnl_usdt
        self.wallet.total_fees += exit_fee
        self.wallet.taker_fees += exit_fee
        self.wallet.total_trades += 1
        if pnl_usdt > 0:
            self.wallet.winning_trades += 1
        else:
            self.wallet.losing_trades += 1

        trade = Trade(
            id=self._trade_counter,
            symbol=symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=exit_price,
            exit_time=int(time.time() * 1000),
            exit_reason="REVERSAL",
            qty_usdt=trade_notional,
            leverage=self.wallet.leverage,
            pnl_usdt=round(pnl_usdt, 4),
            pnl_percent=round(pnl_pct, 4),
            fee_usdt=round(exit_fee, 4),
        )
        self.trades.append(trade)

        # Mark closed
        pos.condition = 0.0
        pos.remaining_qty = 0.0

        return [trade]

    def process_candle(self, symbol: str, high: float, low: float, close_time: int) -> list[Trade]:
        """Check TP/SL for an active position. Returns completed trades."""
        if not self.has_position(symbol):
            return []

        pos = self._positions[symbol]
        exits = self._risk_mgr.check_exits(pos, high, low)
        completed: list[Trade] = []

        for exit_ev in exits:
            trade = self._record_trade(pos, exit_ev, close_time)
            completed.append(trade)

        # Mark fully closed positions (don't delete — let caller handle cleanup)
        if pos.remaining_qty <= 0 or abs(pos.condition) >= 1.3:
            pos.condition = 0.0
            pos.remaining_qty = 0.0

        return completed

    def _record_trade(self, pos: PositionState, exit_ev: ExitEvent, close_time: int) -> Trade:
        """Record a completed (partial) trade."""
        self._trade_counter += 1

        margin = self.wallet.margin_per_trade
        notional = margin * self.wallet.leverage
        trade_notional = notional * (exit_ev.qty_percent / 100)

        # PnL calculation
        if pos.side == "LONG":
            pnl_pct = (exit_ev.price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl_pct = (pos.entry_price - exit_ev.price) / pos.entry_price * 100

        pnl_usdt = trade_notional * pnl_pct / 100
        # TP/SL exits = limit order = maker fee
        exit_fee = trade_notional * self.wallet.maker_fee

        # Update wallet
        self.wallet.balance += pnl_usdt - exit_fee
        self.wallet.total_pnl += pnl_usdt
        self.wallet.total_fees += exit_fee
        self.wallet.maker_fees += exit_fee
        self.wallet.total_trades += 1
        if pnl_usdt > 0:
            self.wallet.winning_trades += 1
        else:
            self.wallet.losing_trades += 1

        trade = Trade(
            id=self._trade_counter,
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=exit_ev.price,
            exit_time=close_time,
            exit_reason=exit_ev.reason.value,
            qty_usdt=trade_notional,
            leverage=self.wallet.leverage,
            pnl_usdt=round(pnl_usdt, 4),
            pnl_percent=round(pnl_pct, 4),
            fee_usdt=round(exit_fee, 4),
        )
        self.trades.append(trade)
        return trade

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics."""
        win_rate = (
            self.wallet.winning_trades / self.wallet.total_trades * 100
            if self.wallet.total_trades > 0
            else 0
        )
        return {
            "initial_balance": self.wallet.initial_balance,
            "current_balance": round(self.wallet.balance, 2),
            "total_pnl": round(self.wallet.total_pnl, 2),
            "total_pnl_pct": round(
                (self.wallet.balance - self.wallet.initial_balance)
                / self.wallet.initial_balance
                * 100,
                2,
            ),
            "total_trades": self.wallet.total_trades,
            "winning_trades": self.wallet.winning_trades,
            "losing_trades": self.wallet.losing_trades,
            "win_rate": round(win_rate, 2),
            "total_fees": round(self.wallet.total_fees, 4),
            "maker_fees": round(self.wallet.maker_fees, 4),
            "taker_fees": round(self.wallet.taker_fees, 4),
            "leverage": self.wallet.leverage,
        }
