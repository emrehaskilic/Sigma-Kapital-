"""Signal engine — generates LONG/SHORT entry signals from candle data.

Port of the Pine Script crossover/crossunder logic + supply/demand zones.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from core.strategy.indicators import (
    alma,
    atr,
    ema,
    hull_ma,
    pivot_high,
    pivot_low,
    rsi,
    sma,
    tema,
    variant,
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
    supply_zones: list[tuple[float, float]] = field(default_factory=list)
    demand_zones: list[tuple[float, float]] = field(default_factory=list)


class SignalEngine:
    """Generates signals for a single symbol based on the Pine Script strategy.

    Mirrors the Pine Script reso() function: when use_alternate_signals is True,
    the MA crossover is computed on a higher timeframe (base_tf * alternate_multiplier).
    E.g. 15m base * 8 = 120m (2h) alternate resolution.
    """

    def __init__(self, config: dict) -> None:
        self._cfg = config["strategy"]
        self._risk = config["risk"]
        self._trade_type = config["trading"]["trade_type"]

        # MA settings
        self._ma_type = self._cfg["ma_type"]
        self._ma_period = self._cfg["ma_period"]
        self._alma_offset = self._cfg["alma_offset"]
        self._alma_sigma = self._cfg["alma_sigma"]
        self._swing_length = self._cfg["swing_length"]
        self._history_to_keep = self._cfg["history_to_keep"]
        self._box_width = self._cfg["supply_demand_box_width"]

        # Alternate resolution (Pine Script reso() equivalent)
        self._use_alt = self._cfg.get("use_alternate_signals", True)
        self._alt_mult = self._cfg.get("alternate_multiplier", 8)

        # Supply/Demand zone tracking
        self._supply_zones: list[dict[str, float]] = []
        self._demand_zones: list[dict[str, float]] = []

    @staticmethod
    def _resample_ohlc(
        df: pd.DataFrame, multiplier: int, *, drop_incomplete: bool = True,
    ) -> pd.DataFrame:
        """Resample base-timeframe candles into a higher timeframe.

        Aligns to calendar time boundaries to match TradingView's
        request.security() behavior.  E.g. 15m * 8 = 120min bars
        starting at 00:00, 02:00, 04:00 etc.

        When *drop_incomplete* is True (default) the last bucket is removed
        if it contains fewer than *multiplier* base candles, matching
        TradingView's closed-bar semantics (lookahead_off).
        """
        if len(df) < multiplier:
            return df

        # Compute the higher-TF interval in milliseconds
        # Infer base interval from the first two candle open_times
        base_interval_ms = int(df["open_time"].iloc[1] - df["open_time"].iloc[0])
        htf_interval_ms = base_interval_ms * multiplier

        # Assign each candle to its higher-TF bucket via floor division
        open_times = df["open_time"].values
        bucket = (open_times // htf_interval_ms) * htf_interval_ms

        df_copy = df.copy()
        df_copy["_bucket"] = bucket

        grouped = df_copy.groupby("_bucket", sort=True)
        rows = []
        for bucket_key, chunk in grouped:
            # Skip incomplete last bar if requested
            if drop_incomplete and bucket_key == bucket[-1] and len(chunk) < multiplier:
                continue
            rows.append({
                "open_time": chunk["open_time"].iloc[0],
                "open": chunk["open"].iloc[0],
                "high": chunk["high"].max(),
                "low": chunk["low"].min(),
                "close": chunk["close"].iloc[-1],
                "first_close": chunk["close"].iloc[0],
                "volume": chunk["volume"].sum() if "volume" in chunk.columns else 0,
            })
        return pd.DataFrame(rows)

    def process(self, df: pd.DataFrame) -> Signal | None:
        """Process candle data — full Pine Script condition state machine.

        Walks the entire crossover history to maintain correct condition state
        (0=flat, 1=LONG, -1=SHORT).  Returns a signal only when the most recent
        bar triggered a condition change:
            leTrigger AND condition[1] <= 0.0 → enter LONG
            seTrigger AND condition[1] >= 0.0 → enter SHORT

        Matches TradingView real-time behavior: includes the forming (incomplete)
        alt-TF bar so crossovers are detected on each base candle close, just
        like request.security() with lookahead_on + calc_on_every_tick=false.
        Entry price = base TF bar close (process_orders_on_close=true).
        """
        if len(df) < 50:
            return None

        close = df["close"]
        open_ = df["open"]
        high = df["high"]
        low = df["low"]

        # --- RSI / ATR ---
        rsi_val = rsi(close, 28).iloc[-1]
        atr_val = atr(high, low, close, 50).iloc[-1]

        # --- Supply/Demand zones ---
        self._update_zones(df, atr_val)

        last = df.iloc[-1]
        symbol = str(last.get("symbol", ""))
        # Base TF close — used as entry price (matches TV process_orders_on_close)
        base_close = float(last["close"])

        # --- Determine MA series to walk ---
        if self._use_alt and self._alt_mult > 1:
            # Include incomplete bar: matches TV real-time where
            # request.security(lookahead_on) updates on each base bar close
            alt_df = self._resample_ohlc(df, self._alt_mult, drop_incomplete=False)

            if len(alt_df) < max(self._ma_period + 2, 10):
                return None

            close_ma = variant(self._ma_type, alt_df["close"],
                               self._ma_period, self._alma_sigma, self._alma_offset)
            open_ma = variant(self._ma_type, alt_df["open"],
                              self._ma_period, self._alma_sigma, self._alma_offset)
            bar_times = alt_df["open_time"].values
            bar_first_closes = alt_df["first_close"].values
        else:
            close_ma = variant(self._ma_type, close, self._ma_period,
                               self._alma_sigma, self._alma_offset)
            open_ma = variant(self._ma_type, open_, self._ma_period,
                              self._alma_sigma, self._alma_offset)
            bar_times = df["open_time"].values
            bar_first_closes = df["close"].values

        close_ma_vals = close_ma.values
        open_ma_vals = open_ma.values

        # --- Walk all bars — Pine Script condition state machine ---
        condition = 0.0  # 0=flat, 1=LONG, -1=SHORT
        entry_price = 0.0
        entry_time = 0
        last_transition_idx = -1
        n_bars = len(close_ma_vals)

        for i in range(1, n_bars):
            prev_c = close_ma_vals[i - 1]
            prev_o = open_ma_vals[i - 1]
            curr_c = close_ma_vals[i]
            curr_o = open_ma_vals[i]

            if np.isnan(prev_c) or np.isnan(prev_o) or np.isnan(curr_c) or np.isnan(curr_o):
                continue

            le_trigger = (prev_c <= prev_o and curr_c > curr_o)
            se_trigger = (prev_c >= prev_o and curr_c < curr_o)

            if le_trigger and condition <= 0.0:
                condition = 1.0
                # Last bar: entry at base TF close (TV process_orders_on_close)
                # Historical: entry at first base candle close of alt-TF bar
                # (TV lookahead_on makes value available on first bar of period)
                if i == n_bars - 1:
                    entry_price = base_close
                else:
                    entry_price = float(bar_first_closes[i])
                entry_time = int(bar_times[i])
                last_transition_idx = i

            elif se_trigger and condition >= 0.0:
                condition = -1.0
                if i == n_bars - 1:
                    entry_price = base_close
                else:
                    entry_price = float(bar_first_closes[i])
                entry_time = int(bar_times[i])
                last_transition_idx = i

        # Only emit signal if transition happened on the LAST bar
        if last_transition_idx != n_bars - 1:
            return None

        if condition == 1.0 and self._trade_type in ("LONG", "BOTH"):
            return Signal(
                timestamp=entry_time,
                symbol=symbol,
                side="LONG",
                price=entry_price,
                rsi_value=rsi_val,
                atr_value=atr_val,
                supply_zones=[(z["top"], z["bottom"]) for z in self._supply_zones[:5]],
                demand_zones=[(z["top"], z["bottom"]) for z in self._demand_zones[:5]],
            )

        if condition == -1.0 and self._trade_type in ("SHORT", "BOTH"):
            return Signal(
                timestamp=entry_time,
                symbol=symbol,
                side="SHORT",
                price=entry_price,
                rsi_value=rsi_val,
                atr_value=atr_val,
                supply_zones=[(z["top"], z["bottom"]) for z in self._supply_zones[:5]],
                demand_zones=[(z["top"], z["bottom"]) for z in self._demand_zones[:5]],
            )

        return None

    def process_backfill(self, df: pd.DataFrame) -> Signal | None:
        """Replay full historical crossover chain to find the currently-active position.

        Mirrors Pine Script's continuous condition state machine:
          condition = 0.0 (flat) → 1.0 (LONG) or -1.0 (SHORT)
          leTrigger AND condition[1] <= 0.0 → enter LONG
          seTrigger AND condition[1] >= 0.0 → enter SHORT

        Uses ONLY completed alt-TF bars (drop_incomplete=True) so that
        crossover locations are stable and match TradingView's historical
        bar-by-bar evaluation.  Entry price = first base candle close of
        the alt-TF bar (TV lookahead_on makes the value available from the
        first bar of the period).
        """
        if len(df) < 50:
            return None

        close = df["close"]
        open_ = df["open"]
        high = df["high"]
        low = df["low"]

        # Compute RSI / ATR on full base data (for the final signal)
        rsi_series = rsi(close, 28)
        rsi_val = rsi_series.iloc[-1]
        atr_val = atr(high, low, close, 50).iloc[-1]

        # Supply/Demand zones
        self._update_zones(df, atr_val)

        last = df.iloc[-1]
        symbol = str(last.get("symbol", ""))

        # Only completed alt-TF bars — gives stable crossover locations
        # that match TV historical evaluation.  Forming bar is excluded
        # to prevent phantom crossovers caused by partial data shifting
        # MA values.
        if self._use_alt and self._alt_mult > 1:
            alt_df = self._resample_ohlc(df, self._alt_mult, drop_incomplete=True)

            if len(alt_df) < max(self._ma_period + 2, 10):
                # Not enough alt data
                return None
            close_ma = variant(self._ma_type, alt_df["close"],
                               self._ma_period, self._alma_sigma,
                               self._alma_offset)
            open_ma = variant(self._ma_type, alt_df["open"],
                              self._ma_period, self._alma_sigma,
                              self._alma_offset)
            bar_times = alt_df["open_time"].values
            bar_first_closes = alt_df["first_close"].values
        else:
            close_ma = variant(self._ma_type, close, self._ma_period,
                               self._alma_sigma, self._alma_offset)
            open_ma = variant(self._ma_type, open_, self._ma_period,
                              self._alma_sigma, self._alma_offset)
            bar_times = df["open_time"].values
            bar_first_closes = df["close"].values

        close_ma_vals = close_ma.values
        open_ma_vals = open_ma.values
        n_bars = len(close_ma_vals)

        # Walk through bars chronologically, tracking condition state
        condition = 0.0  # 0=flat, 1=LONG, -1=SHORT
        entry_price = 0.0
        entry_time = 0

        for i in range(1, n_bars):
            prev_c = close_ma_vals[i - 1]
            prev_o = open_ma_vals[i - 1]
            curr_c = close_ma_vals[i]
            curr_o = open_ma_vals[i]

            # Skip NaN values (from MA warmup)
            if np.isnan(prev_c) or np.isnan(prev_o) or np.isnan(curr_c) or np.isnan(curr_o):
                continue

            le_trigger = (prev_c <= prev_o and curr_c > curr_o)
            se_trigger = (prev_c >= prev_o and curr_c < curr_o)

            if le_trigger and condition <= 0.0:
                condition = 1.0
                # TV lookahead_on: alt-TF value available from first bar
                # of the period → entry at first base candle close
                entry_price = float(bar_first_closes[i])
                entry_time = int(bar_times[i])

            elif se_trigger and condition >= 0.0:
                condition = -1.0
                entry_price = float(bar_first_closes[i])
                entry_time = int(bar_times[i])

        # After walking all bars, emit a signal for the active position
        if condition == 1.0 and self._trade_type in ("LONG", "BOTH"):
            sig = Signal(
                timestamp=entry_time,
                symbol=symbol,
                side="LONG",
                price=entry_price,
                rsi_value=rsi_val,
                atr_value=atr_val,
                supply_zones=[(z["top"], z["bottom"]) for z in self._supply_zones[:5]],
                demand_zones=[(z["top"], z["bottom"]) for z in self._demand_zones[:5]],
            )
            logger.info(
                "[BACKFILL] %s LONG entry=%.4f rsi=%.2f atr=%.4f alt_bars=%d",
                symbol, entry_price, rsi_val, atr_val, n_bars,
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
                supply_zones=[(z["top"], z["bottom"]) for z in self._supply_zones[:5]],
                demand_zones=[(z["top"], z["bottom"]) for z in self._demand_zones[:5]],
            )
            logger.info(
                "[BACKFILL] %s SHORT entry=%.4f rsi=%.2f atr=%.4f alt_bars=%d",
                symbol, entry_price, rsi_val, atr_val, n_bars,
            )
            return sig

        return None

    @staticmethod
    def _base_crossover(close_ma: pd.Series, open_ma: pd.Series) -> tuple[bool, bool]:
        """Crossover / crossunder on base-timeframe MA series."""
        prev_close = close_ma.iloc[-2]
        prev_open = open_ma.iloc[-2]
        curr_close = close_ma.iloc[-1]
        curr_open = open_ma.iloc[-1]
        le = prev_close <= prev_open and curr_close > curr_open
        se = prev_close >= prev_open and curr_close < curr_open
        return le, se

    # ------------------------------------------------------------------
    # Supply / Demand zone management
    # ------------------------------------------------------------------

    def _update_zones(self, df: pd.DataFrame, atr_val: float) -> None:
        """Detect swing points and maintain supply/demand zones."""
        high = df["high"]
        low = df["low"]
        close_price = df["close"].iloc[-1]

        swing_h = pivot_high(high, self._swing_length, self._swing_length)
        swing_l = pivot_low(low, self._swing_length, self._swing_length)

        atr_buffer = atr_val * (self._box_width / 10)

        # New swing high → supply zone
        last_sh = swing_h.dropna()
        if len(last_sh) > 0:
            sh_val = last_sh.iloc[-1]
            zone = {"top": sh_val, "bottom": sh_val - atr_buffer, "poi": sh_val - atr_buffer / 2}
            if not self._zone_overlaps(zone["poi"], self._supply_zones, atr_val):
                self._supply_zones.insert(0, zone)
                if len(self._supply_zones) > self._history_to_keep:
                    self._supply_zones.pop()

        # New swing low → demand zone
        last_sl = swing_l.dropna()
        if len(last_sl) > 0:
            sl_val = last_sl.iloc[-1]
            zone = {"top": sl_val + atr_buffer, "bottom": sl_val, "poi": sl_val + atr_buffer / 2}
            if not self._zone_overlaps(zone["poi"], self._demand_zones, atr_val):
                self._demand_zones.insert(0, zone)
                if len(self._demand_zones) > self._history_to_keep:
                    self._demand_zones.pop()

        # BOS — remove broken zones
        self._supply_zones = [z for z in self._supply_zones if close_price < z["top"]]
        self._demand_zones = [z for z in self._demand_zones if close_price > z["bottom"]]

    @staticmethod
    def _zone_overlaps(new_poi: float, zones: list[dict], atr_val: float) -> bool:
        threshold = atr_val * 2
        for z in zones:
            if abs(new_poi - z["poi"]) < threshold:
                return True
        return False
