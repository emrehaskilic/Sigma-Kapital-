"""Dry-run simulation — Keltner Channel DCA + TP.

PMax = macro trend. Keltner Channels = micro DCA/TP levels.
  LONG:  Limit BUY @ KC Lower (DCA)  |  Limit SELL @ KC Upper (TP)
  SHORT: Limit SELL @ KC Upper (DCA) |  Limit BUY @ KC Lower (TP)
All DCA/TP = maker fee. Entry + kill switch = taker fee.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from core.strategy.risk_manager import PositionState, RiskManager, MAX_DCA_STEPS
from core.strategy.signals import Signal
from core.strategy.indicators import keltner_channel

logger = logging.getLogger(__name__)


@dataclass
class Trade:
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
    tf_label: str = ""


@dataclass
class Wallet:
    initial_balance: float
    balance: float
    leverage: int
    margin_per_trade: float
    maker_fee: float
    taker_fee: float
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    maker_fees: float = 0.0
    taker_fees: float = 0.0


class Simulator:
    """Keltner Channel DCA + TP dry-run simulator."""

    def __init__(self, config: dict) -> None:
        trading = config["trading"]
        self._risk_mgr = RiskManager(config)
        self._config = config

        # Keltner settings
        tf_configs = config["strategy"].get("timeframes", [])
        kc_cfg = tf_configs[0].get("keltner", {}) if tf_configs else {}
        self._kc_length = kc_cfg.get("length", 20)
        self._kc_multiplier = kc_cfg.get("multiplier", 1.5)
        self._kc_atr_period = kc_cfg.get("atr_period", 10)

        maker = trading.get("maker_fee", 0.0002)
        taker = trading.get("taker_fee", 0.0005)

        self.wallet = Wallet(
            initial_balance=trading["initial_balance"],
            balance=trading["initial_balance"],
            leverage=trading["leverage"],
            margin_per_trade=trading["margin_per_trade"],
            maker_fee=maker,
            taker_fee=taker,
        )
        self._positions: dict[str, PositionState] = {}
        self._size_multipliers: dict[str, float] = {}
        self._tf_labels: dict[str, str] = {}
        self._last_signal_ts: dict[str, int] = {}
        self.trades: list[Trade] = []
        self._trade_counter = 0

    @staticmethod
    def _pos_key(symbol: str, tf_label: str) -> str:
        return f"{symbol}:{tf_label}" if tf_label else symbol

    def _get_margin(self, size_multiplier: float) -> float:
        return self.wallet.margin_per_trade * size_multiplier

    @property
    def positions(self) -> dict[str, PositionState]:
        return self._positions

    def has_position(self, symbol: str, tf_label: str = "") -> bool:
        key = self._pos_key(symbol, tf_label)
        return key in self._positions and self._positions[key].condition != 0.0

    def has_any_position(self, symbol: str) -> bool:
        for key, pos in self._positions.items():
            if key.startswith(symbol + ":") and pos.condition != 0.0:
                return True
        return False

    def process_signal(self, signal: Signal, entry_time: int = 0) -> list[Trade]:
        """PMax crossover → kill switch + new entry (market order = taker)."""
        closed_trades: list[Trade] = []
        tf_label = signal.tf_label or ""
        key = self._pos_key(signal.symbol, tf_label)
        size_mult = signal.size_multiplier if signal.size_multiplier > 0 else 1.0

        last_ts = self._last_signal_ts.get(key, 0)
        if signal.timestamp == last_ts and not self.has_position(signal.symbol, tf_label):
            return closed_trades

        if self.has_position(signal.symbol, tf_label):
            existing = self._positions[key]
            if existing.side == signal.side:
                return closed_trades
            # KILL SWITCH
            closed_trades.extend(
                self._close_position(key, signal.price, exit_time=entry_time or signal.timestamp)
            )

        margin = self._get_margin(size_mult)
        if self.wallet.balance < margin:
            return closed_trades

        pos = self._risk_mgr.open_position(
            signal.symbol, signal.side, signal.price, signal.atr_value,
            margin_per_trade=margin, leverage=self.wallet.leverage,
        )
        pos.entry_time = entry_time or signal.timestamp
        self._positions[key] = pos
        self._size_multipliers[key] = size_mult
        self._tf_labels[key] = tf_label
        self._last_signal_ts[key] = signal.timestamp

        # Entry fee (market = taker)
        notional = margin * self.wallet.leverage
        entry_fee = notional * self.wallet.taker_fee
        self.wallet.balance -= entry_fee
        self.wallet.total_fees += entry_fee
        self.wallet.taker_fees += entry_fee

        return closed_trades

    def _close_position(self, key: str, exit_price: float, exit_time: int = 0) -> list[Trade]:
        """KILL SWITCH — market close (taker fee)."""
        if key not in self._positions or self._positions[key].condition == 0.0:
            return []

        pos = self._positions[key]
        self._trade_counter += 1
        tf_label = self._tf_labels.get(key, "")
        notional = pos.total_position_notional
        if notional <= 0:
            return []

        if pos.side == "LONG":
            pnl_pct = (exit_price - pos.average_entry_price) / pos.average_entry_price * 100
        else:
            pnl_pct = (pos.average_entry_price - exit_price) / pos.average_entry_price * 100

        pnl_usdt = notional * pnl_pct / 100
        exit_fee = notional * self.wallet.taker_fee

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
            id=self._trade_counter, symbol=pos.symbol, side=pos.side,
            entry_price=pos.average_entry_price, entry_time=pos.entry_time,
            exit_price=exit_price, exit_time=exit_time or int(time.time() * 1000),
            exit_reason="REVERSAL", qty_usdt=round(notional, 2),
            leverage=pos.leverage, pnl_usdt=round(pnl_usdt, 4),
            pnl_percent=round(pnl_pct, 4), fee_usdt=round(exit_fee, 4),
            tf_label=tf_label,
        )
        self.trades.append(trade)
        pos.condition = 0.0
        pos.remaining_qty = 0.0
        pos.total_position_notional = 0.0
        return [trade]

    def process_candle_with_df(self, symbol: str, df: pd.DataFrame,
                                tf_label: str = "") -> list[Trade]:
        """Process one candle using full DataFrame for Keltner calculation.

        Simulates limit orders at KC bands:
        - DCA limit at KC Lower (LONG) / KC Upper (SHORT)
        - TP limit at KC Upper (LONG) / KC Lower (SHORT)
        - Fill = candle H/L touched the band
        - Fee = maker (Post-Only / GTX)
        """
        key = self._pos_key(symbol, tf_label)
        if key not in self._positions or self._positions[key].condition == 0.0:
            return []

        pos = self._positions[key]
        pos_tf = self._tf_labels.get(key, "")

        if len(df) < max(self._kc_length, self._kc_atr_period) + 1:
            return []

        # Calculate Keltner Channel
        kc_mid, kc_upper, kc_lower = keltner_channel(
            df["high"], df["low"], df["close"],
            kc_length=self._kc_length,
            kc_multiplier=self._kc_multiplier,
            atr_period=self._kc_atr_period,
        )

        upper_val = kc_upper.iloc[-1]
        lower_val = kc_lower.iloc[-1]
        if np.isnan(upper_val) or np.isnan(lower_val):
            return []

        candle_high = float(df["high"].iloc[-1])
        candle_low = float(df["low"].iloc[-1])
        candle_close = float(df["close"].iloc[-1])
        close_time = int(df["open_time"].iloc[-1])

        # Update pending order prices (for display)
        if pos.side == "LONG":
            pos.pending_dca_price = lower_val
            pos.pending_tp_price = upper_val
        else:
            pos.pending_dca_price = upper_val
            pos.pending_tp_price = lower_val

        # Check Keltner signals
        action, fill_price = self._risk_mgr.check_keltner_signals(
            pos, candle_high, candle_low, candle_close, upper_val, lower_val,
        )

        completed: list[Trade] = []

        if action == "DCA":
            self._risk_mgr.process_dca_fill(pos, fill_price)
            step_notional = pos.margin_per_step * pos.leverage
            dca_fee = step_notional * self.wallet.maker_fee  # limit = maker
            self.wallet.balance -= dca_fee
            self.wallet.total_fees += dca_fee
            self.wallet.maker_fees += dca_fee

            self._trade_counter += 1
            trade = Trade(
                id=self._trade_counter, symbol=pos.symbol, side=pos.side,
                entry_price=fill_price, entry_time=close_time,
                exit_price=fill_price, exit_time=close_time,
                exit_reason="DCA", qty_usdt=round(step_notional, 2),
                leverage=pos.leverage, pnl_usdt=0.0, pnl_percent=0.0,
                fee_usdt=round(dca_fee, 4), tf_label=pos_tf,
            )
            completed.append(trade)
            self.trades.append(trade)

        elif action == "TP":
            avg_before = pos.average_entry_price
            closed_notional = self._risk_mgr.process_tp_fill(pos, fill_price)
            if closed_notional > 0:
                self._trade_counter += 1
                if pos.side == "LONG":
                    pnl_pct = (fill_price - avg_before) / avg_before * 100
                else:
                    pnl_pct = (avg_before - fill_price) / avg_before * 100
                pnl_usdt = closed_notional * pnl_pct / 100
                tp_fee = closed_notional * self.wallet.maker_fee  # limit = maker
                self.wallet.balance += pnl_usdt - tp_fee
                self.wallet.total_pnl += pnl_usdt
                self.wallet.total_fees += tp_fee
                self.wallet.maker_fees += tp_fee
                self.wallet.total_trades += 1
                if pnl_usdt > 0:
                    self.wallet.winning_trades += 1
                else:
                    self.wallet.losing_trades += 1

                trade = Trade(
                    id=self._trade_counter, symbol=pos.symbol, side=pos.side,
                    entry_price=avg_before, entry_time=pos.entry_time,
                    exit_price=fill_price, exit_time=close_time,
                    exit_reason="TP", qty_usdt=round(closed_notional, 2),
                    leverage=pos.leverage, pnl_usdt=round(pnl_usdt, 4),
                    pnl_percent=round(pnl_pct, 4), fee_usdt=round(tp_fee, 4),
                    tf_label=pos_tf,
                )
                completed.append(trade)
                self.trades.append(trade)

        return completed

    def process_candle(self, symbol: str, high: float, low: float, close_time: int,
                       tf_label: str = "", candle_close: float = 0.0) -> list[Trade]:
        """Stub — use process_candle_with_df for Keltner (needs full DataFrame)."""
        return []

    def get_stats(self) -> dict[str, Any]:
        win_rate = (
            self.wallet.winning_trades / self.wallet.total_trades * 100
            if self.wallet.total_trades > 0 else 0
        )
        return {
            "initial_balance": self.wallet.initial_balance,
            "current_balance": round(self.wallet.balance, 2),
            "total_pnl": round(self.wallet.total_pnl, 2),
            "total_pnl_pct": round(
                (self.wallet.balance - self.wallet.initial_balance)
                / self.wallet.initial_balance * 100, 2,
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
