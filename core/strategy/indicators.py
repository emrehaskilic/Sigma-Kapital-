"""Technical indicators ported from Pine Script strategy.

Implements only the actively-used indicators:
ALMA, TEMA, HullMA, RSI, Keltner Channel, ATR, EMA, SMA, Swing High/Low.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


# =====================================================================
# Moving Averages
# =====================================================================

def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def tema(series: pd.Series, period: int) -> pd.Series:
    """Triple Exponential Moving Average."""
    e1 = ema(series, period)
    e2 = ema(e1, period)
    e3 = ema(e2, period)
    return 3 * (e1 - e2) + e3


def hull_ma(series: pd.Series, period: int) -> pd.Series:
    """Hull Moving Average."""
    half_len = max(1, period // 2)
    sqrt_len = max(1, round(math.sqrt(period)))
    wma_half = series.rolling(window=half_len, min_periods=half_len).apply(
        lambda x: np.average(x, weights=np.arange(1, len(x) + 1)), raw=True
    )
    wma_full = series.rolling(window=period, min_periods=period).apply(
        lambda x: np.average(x, weights=np.arange(1, len(x) + 1)), raw=True
    )
    diff = 2 * wma_half - wma_full
    return diff.rolling(window=sqrt_len, min_periods=sqrt_len).apply(
        lambda x: np.average(x, weights=np.arange(1, len(x) + 1)), raw=True
    )


def alma(series: pd.Series, period: int, offset: float = 0.85, sigma: int = 5) -> pd.Series:
    """Arnaud Legoux Moving Average."""
    m = offset * (period - 1)
    s = period / sigma
    weights = np.array([math.exp(-((i - m) ** 2) / (2 * s * s)) for i in range(period)])
    weights /= weights.sum()

    def _alma_calc(window: np.ndarray) -> float:
        return np.dot(window, weights)

    return series.rolling(window=period, min_periods=period).apply(_alma_calc, raw=True)


def wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average."""
    return series.rolling(window=period, min_periods=period).apply(
        lambda x: np.average(x, weights=np.arange(1, len(x) + 1)), raw=True
    )


def tma(series: pd.Series, period: int) -> pd.Series:
    """Triangular Moving Average."""
    first = sma(series, math.ceil(period / 2))
    return sma(first, math.floor(period / 2) + 1)


def var_ma(series: pd.Series, period: int) -> pd.Series:
    """Variable Moving Average (Chande's Variable Index Dynamic Average)."""
    valpha = 2.0 / (period + 1)
    result = np.empty(len(series))
    result[:] = np.nan
    src = series.values

    for i in range(9, len(src)):
        if np.isnan(src[i]):
            continue
        # CMO calculation over 9 bars
        vud = 0.0
        vdd = 0.0
        for j in range(9):
            idx = i - j
            if idx < 1 or np.isnan(src[idx]) or np.isnan(src[idx - 1]):
                continue
            if src[idx] > src[idx - 1]:
                vud += src[idx] - src[idx - 1]
            else:
                vdd += src[idx - 1] - src[idx]
        vcmo = (vud - vdd) / (vud + vdd) if (vud + vdd) != 0 else 0.0

        prev = result[i - 1] if not np.isnan(result[i - 1]) else src[i]
        result[i] = valpha * abs(vcmo) * src[i] + (1 - valpha * abs(vcmo)) * prev

    return pd.Series(result, index=series.index)


def wwma(series: pd.Series, period: int) -> pd.Series:
    """Welles Wilder Moving Average."""
    alpha = 1.0 / period
    result = np.empty(len(series))
    result[:] = np.nan
    src = series.values

    for i in range(len(src)):
        if np.isnan(src[i]):
            continue
        if np.isnan(result[i - 1]) if i > 0 else True:
            result[i] = src[i]
        else:
            result[i] = alpha * src[i] + (1 - alpha) * result[i - 1]

    return pd.Series(result, index=series.index)


def zlema(series: pd.Series, period: int) -> pd.Series:
    """Zero Lag EMA."""
    lag = period // 2 if period % 2 == 0 else (period - 1) // 2
    adjusted = series + (series - series.shift(lag))
    return ema(adjusted, period)


def tsf(series: pd.Series, period: int) -> pd.Series:
    """Time Series Forecast (linear regression forecast)."""
    lrc = series.rolling(window=period, min_periods=period).apply(
        lambda x: np.polyval(np.polyfit(np.arange(len(x)), x, 1), len(x) - 1), raw=True
    )
    lrc1 = series.rolling(window=period, min_periods=period).apply(
        lambda x: np.polyval(np.polyfit(np.arange(len(x)), x, 1), len(x)), raw=True
    )
    return lrc + (lrc - lrc1)


def variant(
    ma_type: str,
    series: pd.Series,
    period: int,
    sigma: int = 5,
    offset_alma: float = 0.85,
) -> pd.Series:
    """Select MA type — mirrors Pine Script variant() function."""
    ma_type = ma_type.upper()
    if ma_type == "ALMA":
        return alma(series, period, offset_alma, sigma)
    elif ma_type == "TEMA":
        return tema(series, period)
    elif ma_type in ("HULLMA", "HULL"):
        return hull_ma(series, period)
    elif ma_type == "EMA":
        return ema(series, period)
    elif ma_type == "WMA":
        return wma(series, period)
    elif ma_type == "TMA":
        return tma(series, period)
    elif ma_type == "VAR":
        return var_ma(series, period)
    elif ma_type == "WWMA":
        return wwma(series, period)
    elif ma_type == "ZLEMA":
        return zlema(series, period)
    elif ma_type == "TSF":
        return tsf(series, period)
    else:
        return sma(series, period)


# =====================================================================
# PMax (Profit Maximizer)
# =====================================================================

def atr_sma(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ATR using SMA method (Pine Script's sma(tr, period))."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def atr_rma(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ATR using RMA/Wilder method (Pine Script's atr(period))."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def pmax(
    src: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr_period: int = 10,
    atr_multiplier: float = 3.0,
    ma_type: str = "EMA",
    ma_length: int = 10,
    change_atr: bool = True,
    normalize_atr: bool = False,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Profit Maximizer (PMax) indicator.

    Returns (pmax_line, mavg, direction) where:
      - pmax_line: the PMax trailing stop line
      - mavg: the moving average line
      - direction: 1 = bullish, -1 = bearish
    """
    # Compute ATR
    if change_atr:
        atr_vals = atr_rma(high, low, close, atr_period)
    else:
        atr_vals = atr_sma(high, low, close, atr_period)

    # Compute Moving Average on source
    mavg = variant(ma_type, src, ma_length)

    mavg_vals = mavg.values.copy()
    atr_v = atr_vals.values.copy()
    close_vals = close.values.copy()
    n = len(mavg_vals)

    long_stop = np.full(n, np.nan)
    short_stop = np.full(n, np.nan)
    direction = np.ones(n)
    pmax_line = np.full(n, np.nan)

    for i in range(1, n):
        if np.isnan(mavg_vals[i]) or np.isnan(atr_v[i]):
            continue

        atr_component = atr_v[i] / close_vals[i] if normalize_atr else atr_v[i]

        # Long stop (support)
        ls = mavg_vals[i] - atr_multiplier * atr_component
        prev_ls = long_stop[i - 1] if not np.isnan(long_stop[i - 1]) else ls
        long_stop[i] = max(ls, prev_ls) if mavg_vals[i] > prev_ls else ls

        # Short stop (resistance)
        ss = mavg_vals[i] + atr_multiplier * atr_component
        prev_ss = short_stop[i - 1] if not np.isnan(short_stop[i - 1]) else ss
        short_stop[i] = min(ss, prev_ss) if mavg_vals[i] < prev_ss else ss

        # Direction
        prev_dir = direction[i - 1]
        if prev_dir == -1 and mavg_vals[i] > short_stop[i - 1]:
            direction[i] = 1
        elif prev_dir == 1 and mavg_vals[i] < long_stop[i - 1]:
            direction[i] = -1
        else:
            direction[i] = prev_dir

        pmax_line[i] = long_stop[i] if direction[i] == 1 else short_stop[i]

    return (
        pd.Series(pmax_line, index=src.index),
        mavg,
        pd.Series(direction, index=src.index),
    )


# =====================================================================
# Adaptive PMax (R3) — Continuous adaptive parameter adjustment
# Ported from claude optimizator/adaptive_pmax.py::adaptive_pmax_continuous
# =====================================================================

def adaptive_pmax(
    src: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    pmax_cfg: dict,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Adaptive PMax — continuously adjusts atr_multiplier, ma_length, atr_period.

    Uses pre-computed MA/ATR caches for all period variants (5-24).
    Three adaptive axes recalculated every `update_interval` bars:
      atr_multiplier = mult_base + vol_ratio * mult_scale
      ma_length      = ma_base + trend_dist * ma_scale
      atr_period     = atr_base + flip_count * atr_scale

    Returns (pmax_line, mavg, direction) — same shape as pmax().
    """
    vol_lookback = pmax_cfg.get("vol_lookback", 834)
    flip_window = pmax_cfg.get("flip_window", 423)
    mult_base = pmax_cfg.get("mult_base", 4.0)
    mult_scale = pmax_cfg.get("mult_scale", 3.0)
    ma_base = pmax_cfg.get("ma_base", 18)
    ma_scale = pmax_cfg.get("ma_scale", 4.5)
    atr_base = pmax_cfg.get("atr_base", 20)
    atr_scale = pmax_cfg.get("atr_scale", 1.5)
    update_interval = pmax_cfg.get("update_interval", 31)
    ma_type = pmax_cfg.get("ma_type", "EMA")
    base_atr_period = min(pmax_cfg.get("atr_period", 10), 20)
    base_ma_length = pmax_cfg.get("ma_length", 10)
    base_atr_multiplier = pmax_cfg.get("atr_multiplier", 3.0)

    n = len(close)
    close_arr = close.values

    # Base ATR for regime detection (vol_ratio)
    base_atr = atr_rma(high, low, close, base_atr_period).values

    # Pre-compute MA variants for different lengths (cache 5-24)
    ma_cache: dict[int, np.ndarray] = {}
    for ml in range(5, 25):
        ma_cache[ml] = variant(ma_type, src, ml).values

    # Pre-compute ATR variants for different periods (cache 5-24)
    atr_cache: dict[int, np.ndarray] = {}
    for ap in range(5, 25):
        atr_cache[ap] = atr_rma(high, low, close, ap).values

    # Output arrays
    pmax_line = np.full(n, np.nan)
    mavg_out = np.full(n, np.nan)
    direction = np.ones(n)
    long_stop = np.full(n, np.nan)
    short_stop = np.full(n, np.nan)

    mult_series = np.full(n, base_atr_multiplier)
    ma_len_series = np.full(n, float(base_ma_length))
    atr_p_series = np.full(n, float(base_atr_period))

    for i in range(1, n):
        # Carry forward previous adaptive params
        if i > 1:
            active_mult = mult_series[i - 1]
            active_ma_len = int(ma_len_series[i - 1])
            active_atr_p = int(atr_p_series[i - 1])
        else:
            active_mult = base_atr_multiplier
            active_ma_len = base_ma_length
            active_atr_p = base_atr_period

        # Recalculate adaptive params at update_interval
        if i >= vol_lookback and (update_interval <= 1 or i % update_interval == 0):
            # 1. Vol ratio -> atr_multiplier
            window = base_atr[max(0, i - vol_lookback):i + 1]
            valid = window[~np.isnan(window)]
            if len(valid) > 10:
                median_atr = np.median(valid)
                current_atr = base_atr[i]
                if not np.isnan(current_atr) and median_atr > 0:
                    vol_ratio = current_atr / median_atr
                    vol_ratio = max(0.5, min(2.0, vol_ratio))
                    active_mult = mult_base + vol_ratio * mult_scale

            # 2. Trend distance -> ma_length
            mavg_base = ma_cache.get(base_ma_length, ma_cache[10])
            mavg_v = mavg_base[i] if not np.isnan(mavg_base[i]) else close_arr[i]
            c_atr = base_atr[i] if not np.isnan(base_atr[i]) else 1.0
            trend_dist = abs(close_arr[i] - mavg_v) / c_atr if c_atr > 0 else 0
            trend_dist = min(4.0, trend_dist)
            active_ma_len = int(round(ma_base + trend_dist * ma_scale))
            active_ma_len = max(5, min(24, active_ma_len))

            # 3. Flip count -> atr_period
            flip_start = max(0, i - flip_window)
            dir_window = direction[flip_start:i]
            flips = int(np.sum(np.diff(dir_window) != 0)) if len(dir_window) > 1 else 0
            active_atr_p = int(round(atr_base + flips * atr_scale))
            active_atr_p = max(5, min(24, active_atr_p))

        mult_series[i] = active_mult
        ma_len_series[i] = float(active_ma_len)
        atr_p_series[i] = float(active_atr_p)

        # Get cached MA and ATR for active params
        mavg_arr = ma_cache.get(active_ma_len, ma_cache[10])
        atr_arr = atr_cache.get(active_atr_p, atr_cache[10])

        if np.isnan(mavg_arr[i]) or np.isnan(atr_arr[i]):
            continue

        mavg_out[i] = mavg_arr[i]

        # Long stop (support)
        ls = mavg_arr[i] - active_mult * atr_arr[i]
        prev_ls = long_stop[i - 1] if not np.isnan(long_stop[i - 1]) else ls
        long_stop[i] = max(ls, prev_ls) if mavg_arr[i] > prev_ls else ls

        # Short stop (resistance)
        ss = mavg_arr[i] + active_mult * atr_arr[i]
        prev_ss = short_stop[i - 1] if not np.isnan(short_stop[i - 1]) else ss
        short_stop[i] = min(ss, prev_ss) if mavg_arr[i] < prev_ss else ss

        # Direction
        prev_dir = direction[i - 1]
        if prev_dir == -1 and mavg_arr[i] > short_stop[i - 1]:
            direction[i] = 1
        elif prev_dir == 1 and mavg_arr[i] < long_stop[i - 1]:
            direction[i] = -1
        else:
            direction[i] = prev_dir

        pmax_line[i] = long_stop[i] if direction[i] == 1 else short_stop[i]

    return (
        pd.Series(pmax_line, index=src.index),
        pd.Series(mavg_out, index=src.index),
        pd.Series(direction, index=src.index),
    )


# =====================================================================
# Oscillators & Bands
# =====================================================================

def rsi(series: pd.Series, period: int = 28) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 50) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def keltner_channel(
    high: pd.Series, low: pd.Series, close: pd.Series,
    kc_length: int = 20, kc_multiplier: float = 1.5, atr_period: int = 10,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Keltner Channel with proper ATR.

    Returns (middle, upper, lower):
      middle = EMA(close, kc_length)
      upper  = middle + kc_multiplier * ATR(atr_period)
      lower  = middle - kc_multiplier * ATR(atr_period)
    """
    middle = ema(close, kc_length)
    atr_val = atr_rma(high, low, close, atr_period)
    upper = middle + kc_multiplier * atr_val
    lower = middle - kc_multiplier * atr_val
    return middle, upper, lower


# =====================================================================
# Swing Detection
# =====================================================================

def pivot_high(high: pd.Series, left: int = 10, right: int = 10) -> pd.Series:
    """Detect swing highs (pivot highs). Returns NaN where no pivot."""
    result = pd.Series(np.nan, index=high.index)
    for i in range(left, len(high) - right):
        pivot_val = high.iloc[i]
        is_pivot = True
        for j in range(1, left + 1):
            if high.iloc[i - j] >= pivot_val:
                is_pivot = False
                break
        if is_pivot:
            for j in range(1, right + 1):
                if high.iloc[i + j] >= pivot_val:
                    is_pivot = False
                    break
        if is_pivot:
            result.iloc[i] = pivot_val
    return result


def pivot_low(low: pd.Series, left: int = 10, right: int = 10) -> pd.Series:
    """Detect swing lows (pivot lows). Returns NaN where no pivot."""
    result = pd.Series(np.nan, index=low.index)
    for i in range(left, len(low) - right):
        pivot_val = low.iloc[i]
        is_pivot = True
        for j in range(1, left + 1):
            if low.iloc[i - j] <= pivot_val:
                is_pivot = False
                break
        if is_pivot:
            for j in range(1, right + 1):
                if low.iloc[i + j] <= pivot_val:
                    is_pivot = False
                    break
        if is_pivot:
            result.iloc[i] = pivot_val
    return result
