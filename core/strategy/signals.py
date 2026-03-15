"""Signal engine — generates LONG/SHORT entry signals using PMax (Profit Maximizer).

PMax crossover logic: MAvg crosses above PMax → LONG, MAvg crosses below PMax → SHORT.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from core.strategy.indicators import (
    atr,
    ema,
    pmax,
    rsi,
)


@dataclass
class Signal:
    """Represents a trading signal."""
    timestamp: int
    symbol: str
    side: str          # "LONG" or "SHORT"
    price: float
    rsi_value: float
    atr_value: float
    tf_label: str = ""  # timeframe label (e.g. "1m", "3m")
    size_multiplier: float = 1.0  # notional size multiplier for this TF


class SignalEngine:
    """Generates signals for a single symbol using PMax (Profit Maximizer) strategy.

    Buy signal:  MAvg crosses above PMax line
    Sell signal: MAvg crosses below PMax line

    Each instance is bound to a specific timeframe config (1m, 3m, etc.).
    """

    def __init__(self, config: dict, tf_config: dict | None = None) -> None:
        self._cfg = config["strategy"]
        self._trade_type = config["trading"]["trade_type"]

        # Timeframe-specific config (from strategy.timeframes[])
        if tf_config is None:
            # Legacy fallback: single TF mode
            tf_config = self._cfg
        self._tf_config = tf_config
        self._tf_label = tf_config.get("label", "1m")
        self._size_multiplier = tf_config.get("size_multiplier", 1.0)

        # PMax settings (from tf_config or top-level)
        pmax_cfg = tf_config.get("pmax", self._cfg.get("pmax", {}))
        self._pmax_source = pmax_cfg.get("source", "hl2")
        self._atr_period = pmax_cfg.get("atr_period", 10)
        self._atr_multiplier = pmax_cfg.get("atr_multiplier", 3.0)
        self._ma_type = pmax_cfg.get("ma_type", "EMA")
        self._ma_length = pmax_cfg.get("ma_length", 10)
        self._change_atr = pmax_cfg.get("change_atr", True)
        self._normalize_atr = pmax_cfg.get("normalize_atr", False)

        # Signal filters (from tf_config or top-level)
        filters = tf_config.get("filters", self._cfg.get("filters", {}))
        self._ema_filter = filters.get("ema_trend", {})
        self._rsi_filter = filters.get("rsi", {})
        self._atr_filter = filters.get("atr_volatility", {})

    def _get_source(self, df: pd.DataFrame) -> pd.Series:
        """Get source series based on config (hl2, close, hlc3, ohlc4)."""
        src = self._pmax_source.lower()
        if src == "hl2":
            return (df["high"] + df["low"]) / 2
        elif src == "hlc3":
            return (df["high"] + df["low"] + df["close"]) / 3
        elif src == "ohlc4":
            return (df["open"] + df["high"] + df["low"] + df["close"]) / 4
        else:
            return df["close"]

    def process(self, df: pd.DataFrame) -> Signal | None:
        """Process candle data — PMax crossover signal detection.

        Walks the entire PMax/MAvg history to find crossover events.
        Returns a signal only when the most recent bar triggered a crossover:
            MAvg crosses above PMax → LONG
            MAvg crosses below PMax → SHORT
        """
        if len(df) < 50:
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        src = self._get_source(df)

        # --- RSI / ATR for filters ---
        rsi_val = rsi(close, 28).iloc[-1]
        atr_val = atr(high, low, close, 50).iloc[-1]




        last = df.iloc[-1]
        symbol = str(last.get("symbol", ""))
        base_close = float(last["close"])

        # --- Compute PMax ---
        pmax_line, mavg, direction = pmax(
            src, high, low, close,
            atr_period=self._atr_period,
            atr_multiplier=self._atr_multiplier,
            ma_type=self._ma_type,
            ma_length=self._ma_length,
            change_atr=self._change_atr,
            normalize_atr=self._normalize_atr,
        )

        pmax_vals = pmax_line.values
        mavg_vals = mavg.values
        n = len(mavg_vals)

        # --- Walk all bars — crossover state machine ---
        condition = 0.0  # 0=flat, 1=LONG, -1=SHORT
        entry_price = 0.0
        entry_time = 0
        last_transition_idx = -1
        times = df["open_time"].values
        closes = df["close"].values

        for i in range(1, n):
            prev_m = mavg_vals[i - 1]
            prev_p = pmax_vals[i - 1]
            curr_m = mavg_vals[i]
            curr_p = pmax_vals[i]

            if np.isnan(prev_m) or np.isnan(prev_p) or np.isnan(curr_m) or np.isnan(curr_p):
                continue

            # MAvg crosses above PMax → LONG
            buy_cross = prev_m <= prev_p and curr_m > curr_p
            # MAvg crosses below PMax → SHORT
            sell_cross = prev_m >= prev_p and curr_m < curr_p

            if buy_cross and condition <= 0.0:
                condition = 1.0
                entry_price = float(closes[i]) if i == n - 1 else float(closes[i])
                entry_time = int(times[i])
                last_transition_idx = i

            elif sell_cross and condition >= 0.0:
                condition = -1.0
                entry_price = float(closes[i]) if i == n - 1 else float(closes[i])
                entry_time = int(times[i])
                last_transition_idx = i

        # Only emit signal if transition happened on the LAST bar
        if last_transition_idx != n - 1:
            return None

        if condition == 1.0 and self._trade_type in ("LONG", "BOTH"):
            if not self._apply_filters(df, "LONG", rsi_val, atr_val):
                logger.info("[FILTERED] %s LONG signal blocked by filters", symbol)
                return None
            return Signal(
                timestamp=entry_time,
                symbol=symbol,
                side="LONG",
                price=entry_price,
                rsi_value=rsi_val,
                atr_value=atr_val,
                tf_label=self._tf_label,
                size_multiplier=self._size_multiplier,
            )

        if condition == -1.0 and self._trade_type in ("SHORT", "BOTH"):
            if not self._apply_filters(df, "SHORT", rsi_val, atr_val):
                logger.info("[FILTERED] %s SHORT signal blocked by filters", symbol)
                return None
            return Signal(
                timestamp=entry_time,
                symbol=symbol,
                side="SHORT",
                price=entry_price,
                rsi_value=rsi_val,
                atr_value=atr_val,
                tf_label=self._tf_label,
                size_multiplier=self._size_multiplier,
            )

        return None

    def process_backfill(self, df: pd.DataFrame) -> Signal | None:
        """Replay full PMax crossover history to find the currently-active position.

        Uses completed bars to determine the current state (LONG/SHORT/flat).
        """
        if len(df) < 50:
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        src = self._get_source(df)

        rsi_val = rsi(close, 28).iloc[-1]
        atr_val = atr(high, low, close, 50).iloc[-1]

        last = df.iloc[-1]
        symbol = str(last.get("symbol", ""))

        # --- Compute PMax ---
        pmax_line, mavg, direction = pmax(
            src, high, low, close,
            atr_period=self._atr_period,
            atr_multiplier=self._atr_multiplier,
            ma_type=self._ma_type,
            ma_length=self._ma_length,
            change_atr=self._change_atr,
            normalize_atr=self._normalize_atr,
        )

        pmax_vals = pmax_line.values
        mavg_vals = mavg.values
        n = len(mavg_vals)
        times = df["open_time"].values
        closes = df["close"].values

        condition = 0.0
        entry_price = 0.0
        entry_time = 0

        for i in range(1, n):
            prev_m = mavg_vals[i - 1]
            prev_p = pmax_vals[i - 1]
            curr_m = mavg_vals[i]
            curr_p = pmax_vals[i]

            if np.isnan(prev_m) or np.isnan(prev_p) or np.isnan(curr_m) or np.isnan(curr_p):
                continue

            buy_cross = prev_m <= prev_p and curr_m > curr_p
            sell_cross = prev_m >= prev_p and curr_m < curr_p

            if buy_cross and condition <= 0.0:
                condition = 1.0
                entry_price = float(closes[i])
                entry_time = int(times[i])

            elif sell_cross and condition >= 0.0:
                condition = -1.0
                entry_price = float(closes[i])
                entry_time = int(times[i])

        # Backfill always returns the last crossover state — no filters applied.
        # This represents the currently-active position that should be simulated.
        if condition == 1.0 and self._trade_type in ("LONG", "BOTH"):
            sig = Signal(
                timestamp=entry_time,
                symbol=symbol,
                side="LONG",
                price=entry_price,
                rsi_value=rsi_val,
                atr_value=atr_val,
                tf_label=self._tf_label,
                size_multiplier=self._size_multiplier,
            )
            logger.info(
                "[BACKFILL] %s LONG [%s] entry=%.4f rsi=%.2f atr=%.4f bars=%d",
                symbol, self._tf_label, entry_price, rsi_val, atr_val, n,
            )
            return sig

        if condition == -1.0 and self._trade_type in ("SHORT", "BOTH"):
            sig = Signal(
                timestamp=entry_time,
                symbol=symbol,
                side="SHORT",
                price=entry_price,
                rsi_value=rsi_val,
                atr_value=atr_val,
                tf_label=self._tf_label,
                size_multiplier=self._size_multiplier,
            )
            logger.info(
                "[BACKFILL] %s SHORT [%s] entry=%.4f rsi=%.2f atr=%.4f bars=%d",
                symbol, self._tf_label, entry_price, rsi_val, atr_val, n,
            )
            return sig

        return None

    def _apply_filters(self, df: pd.DataFrame, side: str, rsi_val: float, atr_val: float) -> bool:
        """Apply signal filters. Returns True if signal passes all filters."""
        close = df["close"]

        # --- EMA Trend Filter ---
        if self._ema_filter.get("enabled", False):
            period = self._ema_filter.get("period", 144)
            if len(close) >= period:
                ema_val = ema(close, period).iloc[-1]
                current_close = float(close.iloc[-1])
                if side == "LONG" and current_close < ema_val:
                    logger.debug("FILTER: LONG blocked — close %.4f < EMA(%d) %.4f",
                                 current_close, period, ema_val)
                    return False
                if side == "SHORT" and current_close > ema_val:
                    logger.debug("FILTER: SHORT blocked — close %.4f > EMA(%d) %.4f",
                                 current_close, period, ema_val)
                    return False

        # --- RSI Filter ---
        if self._rsi_filter.get("enabled", False):
            ob = self._rsi_filter.get("overbought", 65)
            os_ = self._rsi_filter.get("oversold", 35)
            rsi_ema = ema(rsi(close, self._rsi_filter.get("period", 28)), 10).iloc[-1]
            if side == "LONG" and rsi_val > ob and rsi_val > rsi_ema:
                logger.debug("FILTER: LONG blocked — RSI %.2f > OB %d", rsi_val, ob)
                return False
            if side == "SHORT" and rsi_val < os_ and rsi_val < rsi_ema:
                logger.debug("FILTER: SHORT blocked — RSI %.2f < OS %d", rsi_val, os_)
                return False

        # --- ATR Volatility Filter ---
        if self._atr_filter.get("enabled", False):
            min_pct = self._atr_filter.get("min_atr_percentile", 20)
            high = df["high"]
            low = df["low"]
            atr_period = self._atr_filter.get("atr_period", 50)
            atr_series = atr(high, low, close, atr_period)
            lookback = min(200, len(atr_series))
            atr_recent = atr_series.iloc[-lookback:]
            threshold = float(np.percentile(atr_recent.dropna().values, min_pct))
            if atr_val < threshold:
                logger.debug("FILTER: signal blocked — ATR %.6f < percentile(%d) %.6f",
                             atr_val, min_pct, threshold)
                return False

        return True
