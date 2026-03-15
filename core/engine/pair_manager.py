"""Pair manager — orchestrates multiple symbol × timeframe strategy instances."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd

from core.data.binance_rest import BinanceRest
from core.data.binance_ws import BinanceWS
from core.engine.simulator import Simulator
from core.strategy.signals import Signal, SignalEngine

logger = logging.getLogger(__name__)

# Minimum candles required before generating signals
_MIN_CANDLES = 200


class TFInstance:
    """Holds candle buffer + strategy engine for a single (symbol, timeframe) pair."""

    def __init__(self, symbol: str, config: dict, tf_config: dict) -> None:
        self.symbol = symbol
        self.tf_label = tf_config.get("label", "1m")
        self.timeframe = tf_config.get("timeframe", "1m")
        self.signal_engine = SignalEngine(config, tf_config=tf_config)
        self.candles: list[dict[str, Any]] = []

    def add_candle(self, candle: dict[str, Any]) -> None:
        """Append a closed candle to the buffer."""
        self.candles.append(candle)
        if len(self.candles) > 1000:
            self.candles = self.candles[-800:]

    def get_dataframe(self) -> pd.DataFrame:
        if not self.candles:
            return pd.DataFrame()
        df = pd.DataFrame(self.candles)
        df["symbol"] = self.symbol
        return df

    def generate_signal(self) -> Signal | None:
        df = self.get_dataframe()
        if len(df) < _MIN_CANDLES:
            return None
        return self.signal_engine.process(df)


class PairManager:
    """Manages active pair subscriptions across multiple timeframes.

    For each symbol, creates one TFInstance per configured timeframe.
    All TF instances share the same Simulator (single wallet).
    """

    def __init__(self, config: dict) -> None:
        self._config = config
        self._rest = BinanceRest()
        self._simulator = Simulator(config)

        # Parse timeframe configs
        self._tf_configs: list[dict] = config["strategy"].get("timeframes", [])
        if not self._tf_configs:
            # Legacy fallback: single timeframe
            self._tf_configs = [{
                "label": config["strategy"].get("timeframe", "1m"),
                "timeframe": config["strategy"].get("timeframe", "1m"),
                "size_multiplier": 1,
                "pmax": config["strategy"].get("pmax", {}),
                "filters": config["strategy"].get("filters", {}),
                "risk": config.get("risk", {}),
            }]

        # TF instances: "BTCUSDT:1m" → TFInstance
        self._instances: dict[str, TFInstance] = {}
        # Track which symbols are active
        self._active_symbols: set[str] = set()
        self._ws: BinanceWS | None = None
        self._all_symbols: list[dict[str, Any]] = []

        # Callbacks for dashboard
        self.on_signal: Any = None
        self.on_trade: Any = None

    @property
    def simulator(self) -> Simulator:
        return self._simulator

    @property
    def active_pairs(self) -> list[str]:
        return list(self._active_symbols)

    @property
    def all_symbols(self) -> list[dict[str, Any]]:
        return self._all_symbols

    async def initialize(self) -> None:
        """Fetch symbol list and start WS connection."""
        self._all_symbols = await self._rest.fetch_futures_symbols()
        self._ws = BinanceWS(on_candle=self._on_candle)
        await self._ws.connect()
        logger.info("PairManager initialized — %d symbols, %d timeframes",
                     len(self._all_symbols), len(self._tf_configs))

    async def shutdown(self) -> None:
        if self._ws:
            await self._ws.close()
        await self._rest.close()

    async def add_pair(self, symbol: str) -> bool:
        """Add a pair: for each TF, load historical candles, create instance, subscribe WS."""
        symbol = symbol.upper()
        if symbol in self._active_symbols:
            return False

        for tf_cfg in self._tf_configs:
            tf_label = tf_cfg.get("label", "1m")
            interval = tf_cfg.get("timeframe", "1m")
            key = f"{symbol}:{tf_label}"

            try:
                klines = await self._rest.fetch_klines(symbol, interval, limit=500)
            except Exception:
                logger.exception("Failed to fetch klines for %s %s", symbol, interval)
                continue

            instance = TFInstance(symbol, self._config, tf_cfg)
            for k in klines:
                instance.add_candle(k)
            self._instances[key] = instance

            # Subscribe to WS stream for this interval
            if self._ws:
                await self._ws.subscribe(symbol, interval)

            logger.info("Added %s [%s] (%d historical candles)", symbol, tf_label, len(klines))

        self._active_symbols.add(symbol)
        return True

    async def remove_pair(self, symbol: str) -> bool:
        """Remove a pair: unsubscribe all TFs."""
        symbol = symbol.upper()
        if symbol not in self._active_symbols:
            return False

        for tf_cfg in self._tf_configs:
            tf_label = tf_cfg.get("label", "1m")
            interval = tf_cfg.get("timeframe", "1m")
            key = f"{symbol}:{tf_label}"

            if self._ws:
                await self._ws.unsubscribe(symbol, interval)
            self._instances.pop(key, None)

        self._active_symbols.discard(symbol)
        logger.info("Removed pair %s (all timeframes)", symbol)
        return True

    async def _on_candle(self, candle: dict[str, Any]) -> None:
        """Callback from WS — route candle to the correct TF instance(s)."""
        symbol = candle.get("symbol", "")
        interval = candle.get("interval", "")

        if symbol not in self._active_symbols:
            return

        # Find matching instance by symbol + interval
        matching_key = None
        for tf_cfg in self._tf_configs:
            if tf_cfg.get("timeframe") == interval:
                key = f"{symbol}:{tf_cfg.get('label', interval)}"
                if key in self._instances:
                    matching_key = key
                    break

        if not matching_key:
            return

        instance = self._instances[matching_key]

        # Only process closed candles for signals
        if candle.get("is_closed"):
            instance.add_candle(candle)

            signal = instance.generate_signal()
            if signal:
                self._simulator.process_signal(signal)
                if self.on_signal:
                    self.on_signal(signal)
                logger.info("Signal: %s %s [%s] @ %.4f",
                            signal.side, signal.symbol, signal.tf_label, signal.price)

        # PMax: no TP/SL — positions close only on crossover reversal

    def get_pair_status(self, symbol: str) -> dict[str, Any] | None:
        """Return status info for a pair across all timeframes."""
        if symbol not in self._active_symbols:
            return None

        tf_statuses = []
        last_price = 0.0

        for tf_cfg in self._tf_configs:
            tf_label = tf_cfg.get("label", "1m")
            key = f"{symbol}:{tf_label}"
            instance = self._instances.get(key)
            if not instance:
                continue

            df = instance.get_dataframe()
            if len(df) > 0:
                last_price = df["close"].iloc[-1]

            pos = self._simulator.positions.get(key)
            pos_info = None
            if pos and pos.condition != 0.0:
                pnl_pct = 0.0
                if pos.side == "LONG":
                    pnl_pct = (last_price - pos.entry_price) / pos.entry_price * 100
                else:
                    pnl_pct = (pos.entry_price - last_price) / pos.entry_price * 100
                pos_info = {
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "unrealized_pnl_pct": round(pnl_pct, 4),
                    "condition": pos.condition,
                }

            tf_statuses.append({
                "tf_label": tf_label,
                "candle_count": len(instance.candles),
                "position": pos_info,
            })

        return {
            "symbol": symbol,
            "last_price": last_price,
            "timeframes": tf_statuses,
        }
