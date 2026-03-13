"""Backtesting engine — historical simulation with full metrics.

Reuses the same SignalEngine + RiskManager + Simulator as the dry-run,
but walks through historical candles bar-by-bar to produce:
  - Equity curve
  - Drawdown / run-up curves
  - Comprehensive trade statistics
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from core.engine.simulator import Simulator, Trade
from core.strategy.indicators import atr, rsi, variant
from core.strategy.signals import Signal, SignalEngine

logger = logging.getLogger(__name__)

_BASE = "https://fapi.binance.com"


@dataclass
class BacktestResult:
    """Complete backtest output."""
    trades: list[dict]
    equity_curve: list[dict]
    drawdown_curve: list[dict]
    metrics: dict
    per_symbol: list[dict]


class Backtester:
    """Runs historical backtest using the same signal + risk logic as dry-run."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self._tf = config["strategy"]["timeframe"]
        self._progress = 0.0
        self._status = "idle"

    # ── Public API ──────────────────────────────────────────────────────

    def run(self, symbols: list[str], lookback_days: int = 30) -> BacktestResult:
        self._status = "fetching"
        self._progress = 0.0

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - lookback_days * 86400 * 1000

        # 1. Fetch historical data
        all_dfs: dict[str, pd.DataFrame] = {}
        for i, sym in enumerate(symbols):
            self._progress = (i / len(symbols)) * 25
            klines = self._fetch_historical(sym, self._tf, start_ms, end_ms)
            if len(klines) >= 50:
                df = pd.DataFrame(klines)
                df["symbol"] = sym
                all_dfs[sym] = df
            logger.info("Backtest: fetched %s — %d candles", sym, len(klines))

        if not all_dfs:
            return BacktestResult([], [], [], self._empty_metrics(), [])

        # 2. Compute signal timelines
        self._status = "computing"
        self._progress = 25.0
        signal_timelines: dict[str, dict[int, Signal]] = {}
        for i, (sym, df) in enumerate(all_dfs.items()):
            self._progress = 25 + (i / len(all_dfs)) * 15
            signal_timelines[sym] = self._compute_signal_timeline(df)
            logger.info("Backtest: %s — %d signals", sym, len(signal_timelines[sym]))

        # 3. Walk candles chronologically
        self._status = "simulating"
        self._progress = 40.0
        sim = Simulator(self.config)

        # Pre-index for fast O(1) lookups
        indexed: dict[str, dict[str, Any]] = {}
        for sym, df in all_dfs.items():
            times = df["open_time"].values
            indexed[sym] = {
                "highs": df["high"].values,
                "lows": df["low"].values,
                "close_times": df["close_time"].values,
                "time_to_idx": {int(t): i for i, t in enumerate(times)},
            }

        all_times = sorted(set(
            int(t) for df in all_dfs.values() for t in df["open_time"].values
        ))

        raw_equity: list[dict] = []
        total = len(all_times)

        for step, t in enumerate(all_times):
            if step % 200 == 0:
                self._progress = 40 + (step / total) * 50

            for sym in all_dfs:
                idx_map = indexed[sym]["time_to_idx"]
                if t not in idx_map:
                    continue
                i = idx_map[t]

                # TP/SL first, then signal — matches dry-run order
                sim.process_candle(
                    sym,
                    float(indexed[sym]["highs"][i]),
                    float(indexed[sym]["lows"][i]),
                    int(indexed[sym]["close_times"][i]),
                )

                if t in signal_timelines[sym]:
                    sim.process_signal(signal_timelines[sym][t], entry_time=int(t))

            raw_equity.append({"time": t, "equity": round(sim.wallet.balance, 2)})

        # 4. Compute metrics & downsample curves
        self._status = "finalizing"
        self._progress = 92.0

        trades = [self._trade_to_dict(t) for t in sim.trades]
        eq_values = [e["equity"] for e in raw_equity]
        dd_curve = self._compute_drawdown(raw_equity)

        equity_curve = self._downsample(raw_equity, 600)
        dd_curve = self._downsample(dd_curve, 600)

        metrics = self._compute_metrics(sim, eq_values)
        per_symbol = self._compute_per_symbol(sim)

        self._progress = 100.0
        self._status = "done"
        logger.info("Backtest complete: %d trades, PnL=%.2f", len(trades), metrics["total_pnl"])

        return BacktestResult(trades, equity_curve, dd_curve, metrics, per_symbol)

    # ── Data Fetching ───────────────────────────────────────────────────

    @staticmethod
    def _fetch_historical(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
        """Fetch klines in paginated 1500-candle chunks."""
        all_candles: list[dict] = []
        current_start = start_ms

        while current_start < end_ms:
            params = {
                "symbol": symbol.upper(),
                "interval": interval,
                "startTime": current_start,
                "limit": 1500,
            }
            try:
                url = f"{_BASE}/fapi/v1/klines"
                qs = urllib.parse.urlencode(params)
                req = urllib.request.Request(
                    f"{url}?{qs}", headers={"User-Agent": "ScalperBot/0.1"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = json.loads(resp.read().decode())
            except Exception as e:
                logger.warning("Backtest fetch error %s: %s", symbol, str(e)[:80])
                break

            if not raw:
                break

            for k in raw:
                all_candles.append({
                    "open_time": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": int(k[6]),
                })

            current_start = int(raw[-1][6]) + 1
            if len(raw) < 1500:
                break

        return all_candles

    # ── Signal Timeline ─────────────────────────────────────────────────

    def _compute_signal_timeline(self, df: pd.DataFrame) -> dict[int, Signal]:
        """Walk alt-TF bars and record ALL crossover signals chronologically.

        Uses the exact same crossover + condition logic as the dry-run
        SignalEngine.process_backfill().
        """
        signals: dict[int, Signal] = {}
        engine = SignalEngine(self.config)

        cfg = self.config["strategy"]
        use_alt = cfg.get("use_alternate_signals", True)
        alt_mult = cfg.get("alternate_multiplier", 8)
        ma_type = cfg["ma_type"]
        ma_period = cfg["ma_period"]
        alma_sigma = cfg["alma_sigma"]
        alma_offset = cfg["alma_offset"]
        trade_type = self.config["trading"]["trade_type"]

        close = df["close"]
        open_ = df["open"]
        high = df["high"]
        low = df["low"]
        symbol = str(df["symbol"].iloc[0])

        base_times = df["open_time"].values
        base_closes = df["close"].values

        rsi_series = rsi(close, 28)
        atr_series = atr(high, low, close, 50)

        if use_alt and alt_mult > 1:
            alt_df = engine._resample_ohlc(df, alt_mult)
            if len(alt_df) < max(ma_period + 2, 10):
                return signals
            close_ma = variant(ma_type, alt_df["close"], ma_period, alma_sigma, alma_offset)
            open_ma = variant(ma_type, alt_df["open"], ma_period, alma_sigma, alma_offset)
            bar_times = alt_df["open_time"].values
        else:
            close_ma = variant(ma_type, close, ma_period, alma_sigma, alma_offset)
            open_ma = variant(ma_type, open_, ma_period, alma_sigma, alma_offset)
            bar_times = df["open_time"].values

        close_ma_vals = close_ma.values
        open_ma_vals = open_ma.values

        def _base_close_at(alt_open_time: int) -> float:
            idx = np.searchsorted(base_times, alt_open_time)
            if idx < len(base_closes):
                return float(base_closes[idx])
            return float(base_closes[-1])

        def _indicator_at(series: pd.Series, alt_open_time: int) -> float:
            idx = np.searchsorted(base_times, alt_open_time)
            idx = min(idx, len(series) - 1)
            return float(series.iloc[idx])

        # Walk all bars — Pine Script condition state machine.
        # condition tracks MA crossover state independently of trade_type filter.
        # This matches dry-run process() behavior exactly.
        condition = 0.0

        for i in range(1, len(close_ma_vals)):
            prev_c = close_ma_vals[i - 1]
            prev_o = open_ma_vals[i - 1]
            curr_c = close_ma_vals[i]
            curr_o = open_ma_vals[i]

            if np.isnan(prev_c) or np.isnan(prev_o) or np.isnan(curr_c) or np.isnan(curr_o):
                continue

            le_trigger = prev_c <= prev_o and curr_c > curr_o
            se_trigger = prev_c >= prev_o and curr_c < curr_o

            t = int(bar_times[i])
            entry_price = _base_close_at(t)

            # Condition updates FIRST (regardless of trade_type), then signal
            # is emitted only if trade_type matches — same as Pine Script
            if le_trigger and condition <= 0.0:
                condition = 1.0
                if trade_type in ("LONG", "BOTH"):
                    signals[t] = Signal(
                        timestamp=t, symbol=symbol, side="LONG",
                        price=entry_price,
                        rsi_value=_indicator_at(rsi_series, t),
                        atr_value=_indicator_at(atr_series, t),
                    )
            elif se_trigger and condition >= 0.0:
                condition = -1.0
                if trade_type in ("SHORT", "BOTH"):
                    signals[t] = Signal(
                        timestamp=t, symbol=symbol, side="SHORT",
                        price=entry_price,
                        rsi_value=_indicator_at(rsi_series, t),
                        atr_value=_indicator_at(atr_series, t),
                    )

        return signals

    # ── Metrics ─────────────────────────────────────────────────────────

    @staticmethod
    def _compute_drawdown(equity_curve: list[dict]) -> list[dict]:
        if not equity_curve:
            return []

        init_eq = equity_curve[0]["equity"]
        peak = init_eq
        result = []

        for point in equity_curve:
            eq = point["equity"]
            if eq > peak:
                peak = eq
            dd_pct = ((eq - peak) / peak * 100) if peak > 0 else 0
            ru_pct = ((eq - init_eq) / init_eq * 100) if init_eq > 0 else 0
            result.append({
                "time": point["time"],
                "drawdown_pct": round(dd_pct, 4),
                "runup_pct": round(ru_pct, 4),
            })

        return result

    def _compute_metrics(self, sim: Simulator, eq_values: list[float]) -> dict:
        stats = sim.get_stats()
        trades = sim.trades

        gross_profit = sum(t.pnl_usdt for t in trades if t.pnl_usdt > 0)
        gross_loss = abs(sum(t.pnl_usdt for t in trades if t.pnl_usdt < 0))
        profit_factor = (
            round(gross_profit / gross_loss, 2)
            if gross_loss > 0
            else (999.99 if gross_profit > 0 else 0)
        )

        # Max drawdown
        peak = eq_values[0] if eq_values else 0
        max_dd = 0.0
        max_dd_pct = 0.0
        for eq in eq_values:
            if eq > peak:
                peak = eq
            dd = peak - eq
            dd_pct = (dd / peak * 100) if peak > 0 else 0
            max_dd = max(max_dd, dd)
            max_dd_pct = max(max_dd_pct, dd_pct)

        # Max run-up
        trough = eq_values[0] if eq_values else 0
        max_ru = 0.0
        max_ru_pct = 0.0
        for eq in eq_values:
            if eq < trough:
                trough = eq
            ru = eq - trough
            ru_pct = (ru / trough * 100) if trough > 0 else 0
            max_ru = max(max_ru, ru)
            max_ru_pct = max(max_ru_pct, ru_pct)

        # Average win / loss
        winning = [t.pnl_usdt for t in trades if t.pnl_usdt > 0]
        losing = [t.pnl_usdt for t in trades if t.pnl_usdt < 0]
        avg_win = sum(winning) / len(winning) if winning else 0
        avg_loss = sum(losing) / len(losing) if losing else 0

        # Sharpe-like ratio
        if len(trades) > 1:
            pnls = [t.pnl_usdt for t in trades]
            avg_pnl = sum(pnls) / len(pnls)
            std_pnl = (sum((p - avg_pnl) ** 2 for p in pnls) / len(pnls)) ** 0.5
            sharpe = round(avg_pnl / std_pnl, 4) if std_pnl > 0 else 0
        else:
            sharpe = 0

        return {
            **stats,
            "profit_factor": profit_factor,
            "max_drawdown_usdt": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_runup_usdt": round(max_ru, 2),
            "max_runup_pct": round(max_ru_pct, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "sharpe_ratio": sharpe,
            "total_symbols": len(set(t.symbol for t in trades)),
        }

    @staticmethod
    def _compute_per_symbol(sim: Simulator) -> list[dict]:
        """Compute metrics broken down by symbol."""
        from collections import defaultdict
        by_sym: dict[str, list[Trade]] = defaultdict(list)
        for t in sim.trades:
            by_sym[t.symbol].append(t)

        result = []
        for sym, trades in sorted(by_sym.items()):
            wins = [t for t in trades if t.pnl_usdt > 0]
            losses = [t for t in trades if t.pnl_usdt < 0]
            gross_p = sum(t.pnl_usdt for t in wins)
            gross_l = abs(sum(t.pnl_usdt for t in losses))
            pf = round(gross_p / gross_l, 2) if gross_l > 0 else (999.99 if gross_p > 0 else 0)
            result.append({
                "symbol": sym,
                "total_trades": len(trades),
                "winning_trades": len(wins),
                "losing_trades": len(losses),
                "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0,
                "total_pnl": round(sum(t.pnl_usdt for t in trades), 4),
                "total_fees": round(sum(t.fee_usdt for t in trades), 4),
                "profit_factor": pf,
                "avg_win": round(sum(t.pnl_usdt for t in wins) / len(wins), 4) if wins else 0,
                "avg_loss": round(sum(t.pnl_usdt for t in losses) / len(losses), 4) if losses else 0,
            })
        return result

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _trade_to_dict(t: Trade) -> dict:
        return {
            "id": t.id,
            "symbol": t.symbol,
            "side": t.side,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "exit_reason": t.exit_reason,
            "pnl_usdt": t.pnl_usdt,
            "pnl_pct": t.pnl_percent,
            "fee_usdt": t.fee_usdt,
            "leverage": t.leverage,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
        }

    @staticmethod
    def _downsample(data: list[dict], max_points: int = 600) -> list[dict]:
        if len(data) <= max_points:
            return data
        step = len(data) / max_points
        # Always include first and last points
        result = [data[int(i * step)] for i in range(max_points - 1)]
        result.append(data[-1])
        return result

    def _empty_metrics(self) -> dict:
        return {
            "initial_balance": self.config["trading"]["initial_balance"],
            "current_balance": self.config["trading"]["initial_balance"],
            "total_pnl": 0, "total_pnl_pct": 0,
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0, "total_fees": 0,
            "leverage": self.config["trading"]["leverage"],
            "profit_factor": 0, "max_drawdown_usdt": 0, "max_drawdown_pct": 0,
            "max_runup_usdt": 0, "max_runup_pct": 0,
            "gross_profit": 0, "gross_loss": 0,
            "avg_win": 0, "avg_loss": 0, "sharpe_ratio": 0, "total_symbols": 0,
        }

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def status(self) -> str:
        return self._status
