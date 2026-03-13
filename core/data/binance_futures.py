"""Binance Futures authenticated client — order placement, balance, positions.

Handles HMAC-SHA256 signing for private endpoints.
Uses only urllib (no extra dependencies) to match existing binance_rest.py style.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from typing import Any

import logging

logger = logging.getLogger("binance_futures")

_BASE = "https://fapi.binance.com"
_TESTNET_BASE = "https://testnet.binancefuture.com"


class BinanceFutures:
    """Authenticated Binance USDT-M Futures client."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base = _TESTNET_BASE if testnet else _BASE

    # ------------------------------------------------------------------
    # Signing & HTTP
    # ------------------------------------------------------------------

    def _sign(self, params: dict) -> str:
        """Create HMAC-SHA256 signature for request params."""
        query = urllib.parse.urlencode(params)
        sig = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return sig

    def _request(
        self, method: str, path: str, params: dict | None = None, signed: bool = True
    ) -> Any:
        """Execute HTTP request with optional signing."""
        params = params or {}
        headers = {"X-MBX-APIKEY": self._api_key}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)

        url = f"{self._base}{path}"
        if method == "GET":
            qs = urllib.parse.urlencode(params)
            url = f"{url}?{qs}" if qs else url
            data = None
        else:
            data = urllib.parse.urlencode(params).encode()

        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            logger.error("Binance API error %d: %s — %s", e.code, path, body)
            raise RuntimeError(f"Binance API {e.code}: {body}") from e

    # ------------------------------------------------------------------
    # Account Info
    # ------------------------------------------------------------------

    def get_balance(self) -> dict[str, float]:
        """Get futures account USDT balance.

        Returns: {balance, available, unrealized_pnl}
        """
        data = self._request("GET", "/fapi/v2/balance")
        for asset in data:
            if asset["asset"] == "USDT":
                return {
                    "balance": float(asset["balance"]),
                    "available": float(asset["availableBalance"]),
                    "unrealized_pnl": float(asset.get("crossUnPnl", 0)),
                }
        return {"balance": 0.0, "available": 0.0, "unrealized_pnl": 0.0}

    def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions (non-zero amount).

        Returns list of: {symbol, side, amount, entry_price, unrealized_pnl, leverage, margin_type}
        """
        data = self._request("GET", "/fapi/v2/positionRisk")
        positions = []
        for p in data:
            amt = float(p["positionAmt"])
            if amt == 0:
                continue
            positions.append({
                "symbol": p["symbol"],
                "side": "LONG" if amt > 0 else "SHORT",
                "amount": abs(amt),
                "entry_price": float(p["entryPrice"]),
                "unrealized_pnl": float(p["unRealizedProfit"]),
                "leverage": int(p["leverage"]),
                "margin_type": p["marginType"],
                "notional": abs(float(p.get("notional", 0))),
            })
        return positions

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Get all open orders for a symbol."""
        data = self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        return data

    # ------------------------------------------------------------------
    # Leverage & Margin
    # ------------------------------------------------------------------

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Set leverage for a symbol."""
        return self._request("POST", "/fapi/v1/leverage", {
            "symbol": symbol,
            "leverage": leverage,
        })

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        """Set margin type (ISOLATED or CROSSED). Ignores if already set."""
        try:
            return self._request("POST", "/fapi/v1/marginType", {
                "symbol": symbol,
                "marginType": margin_type,
            })
        except RuntimeError as e:
            # Binance returns error if margin type is already set — ignore
            if "No need to change margin type" in str(e):
                return {"msg": "already set"}
            raise

    # ------------------------------------------------------------------
    # Order Placement
    # ------------------------------------------------------------------

    def market_order(
        self, symbol: str, side: str, quantity: float, reduce_only: bool = False
    ) -> dict:
        """Place a market order.

        Args:
            symbol: e.g. "BTCUSDT"
            side: "BUY" or "SELL"
            quantity: base asset quantity
            reduce_only: True for closing positions
        """
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        result = self._request("POST", "/fapi/v1/order", params)
        logger.info(
            "[ORDER] MARKET %s %s qty=%.6f → orderId=%s status=%s",
            side, symbol, quantity, result.get("orderId"), result.get("status"),
        )
        return result

    def stop_market_order(
        self, symbol: str, side: str, quantity: float, stop_price: float
    ) -> dict:
        """Place a stop-market order (for SL).

        Args:
            side: "BUY" to close SHORT, "SELL" to close LONG
            stop_price: trigger price
        """
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "quantity": quantity,
            "stopPrice": f"{stop_price}",
            "reduceOnly": "true",
        }
        result = self._request("POST", "/fapi/v1/order", params)
        logger.info(
            "[ORDER] STOP_MARKET %s %s qty=%.6f stop=%.4f → orderId=%s",
            side, symbol, quantity, stop_price, result.get("orderId"),
        )
        return result

    def take_profit_market_order(
        self, symbol: str, side: str, quantity: float, stop_price: float
    ) -> dict:
        """Place a take-profit-market order (for TP).

        Args:
            side: "BUY" to close SHORT, "SELL" to close LONG
            stop_price: trigger price
        """
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "TAKE_PROFIT_MARKET",
            "quantity": quantity,
            "stopPrice": f"{stop_price}",
            "reduceOnly": "true",
        }
        result = self._request("POST", "/fapi/v1/order", params)
        logger.info(
            "[ORDER] TP_MARKET %s %s qty=%.6f stop=%.4f → orderId=%s",
            side, symbol, quantity, stop_price, result.get("orderId"),
        )
        return result

    def cancel_all_orders(self, symbol: str) -> dict:
        """Cancel all open orders for a symbol."""
        result = self._request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
        logger.info("[ORDER] Cancel all orders for %s → %s", symbol, result)
        return result

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """Cancel a specific order."""
        return self._request("DELETE", "/fapi/v1/order", {
            "symbol": symbol,
            "orderId": order_id,
        })

    # ------------------------------------------------------------------
    # Exchange Info (for quantity precision)
    # ------------------------------------------------------------------

    _exchange_info_cache: dict[str, dict] = {}
    _exchange_info_ts: float = 0

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Get symbol trading rules (precision, min qty, etc.)."""
        now = time.time()
        if not self._exchange_info_cache or now - self._exchange_info_ts > 3600:
            data = self._request("GET", "/fapi/v1/exchangeInfo", signed=False)
            for s in data.get("symbols", []):
                self._exchange_info_cache[s["symbol"]] = s
            self._exchange_info_ts = now
        return self._exchange_info_cache.get(symbol, {})

    def calc_quantity(
        self, symbol: str, usdt_amount: float, leverage: int, price: float
    ) -> float:
        """Calculate order quantity from USDT margin, leverage, and price.

        Returns quantity rounded to symbol's precision.
        """
        notional = usdt_amount * leverage
        raw_qty = notional / price
        info = self.get_symbol_info(symbol)
        precision = info.get("quantityPrecision", 3)
        qty = round(raw_qty, precision)
        # Ensure minimum notional (Binance requires >= 5 USDT notional)
        if qty * price < 5:
            return 0.0
        return qty

    def calc_price(self, symbol: str, price: float) -> float:
        """Round price to symbol's price precision."""
        info = self.get_symbol_info(symbol)
        precision = info.get("pricePrecision", 2)
        return round(price, precision)
