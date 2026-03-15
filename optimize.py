"""PMax + Keltner Channel parameter optimizer using Optuna.

Optimizes 12 parameters for each symbol independently:
  - PMax: atr_period, atr_multiplier, ma_length, ma_type
  - Keltner: kc_length, kc_multiplier, kc_atr_period
  - Risk: max_dca_steps, tp_close_percent
  - Filters: ema_filter_period, rsi_overbought

Uses 3m kline data from Binance.
70% in-sample optimization, 30% out-of-sample validation.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import optuna
import pandas as pd
import yaml

# Add parent path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.engine.simulator import Simulator, Trade
from core.strategy.signals import SignalEngine, Signal
from core.strategy.indicators import pmax, atr, rsi, ema, keltner_channel

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("optimizer")
logger.setLevel(logging.INFO)

# Suppress Optuna trial logs
optuna.logging.set_verbosity(optuna.logging.WARNING)

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)

MA_TYPES = ["EMA", "SMA", "WMA", "ZLEMA"]


def fetch_klines(symbol: str, interval: str = "3m", days: int = 30) -> pd.DataFrame:
    """Fetch historical klines from Binance."""
    import urllib.parse
    import urllib.request

    base = "https://fapi.binance.com"
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    all_candles = []
    current = start_ms

    logger.info("Fetching %s %s data (%d days)...", symbol, interval, days)

    while current < end_ms:
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": current,
            "limit": 1500,
        }
        url = f"{base}/fapi/v1/klines?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Optimizer/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = json.loads(resp.read().decode())
        except Exception as e:
            logger.warning("Fetch error: %s", e)
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

        current = int(raw[-1][6]) + 1
        if len(raw) < 1500:
            break

    df = pd.DataFrame(all_candles)
    logger.info("Fetched %d candles for %s", len(df), symbol)
    return df


def run_backtest_with_params(df: pd.DataFrame, symbol: str, params: dict) -> dict:
    """Run a single backtest with given parameters. Returns metrics dict."""

    # Build config from params
    config = {
        "strategy": {
            "timeframes": [{
                "label": "3m",
                "timeframe": "3m",
                "size_multiplier": 1,
                "pmax": {
                    "source": "hl2",
                    "atr_period": params["pmax_atr_period"],
                    "atr_multiplier": params["pmax_atr_multiplier"],
                    "ma_type": params["pmax_ma_type"],
                    "ma_length": params["pmax_ma_length"],
                    "change_atr": True,
                    "normalize_atr": False,
                },
                "keltner": {
                    "length": params["kc_length"],
                    "multiplier": params["kc_multiplier"],
                    "atr_period": params["kc_atr_period"],
                },
                "filters": {
                    "ema_trend": {
                        "enabled": True,
                        "period": params["ema_filter_period"],
                    },
                    "rsi": {
                        "enabled": True,
                        "period": 28,
                        "overbought": params["rsi_overbought"],
                        "oversold": 100 - params["rsi_overbought"],
                    },
                    "atr_volatility": {"enabled": False},
                },
            }],
        },
        "trading": {
            "initial_balance": 1000.0,
            "leverage": 50,
            "margin_per_trade": 50.0,
            "trade_type": "BOTH",
            "maker_fee": 0.0002,
            "taker_fee": 0.0005,
        },
    }

    # Patch MAX_DCA_STEPS and TP_CLOSE_PERCENT
    import core.strategy.risk_manager as rm
    rm.MAX_DCA_STEPS = params["max_dca_steps"]
    rm.TP_CLOSE_PERCENT = params["tp_close_percent"]

    try:
        sim = Simulator(config)
        tf_cfg = config["strategy"]["timeframes"][0]
        engine = SignalEngine(config, tf_config=tf_cfg)

        _MIN_CANDLES = 200
        n = len(df)

        # Pre-compute ALL indicators once on full DataFrame
        df["symbol"] = symbol
        src = (df["high"] + df["low"]) / 2

        pmax_cfg = config["strategy"]["timeframes"][0]["pmax"]
        pmax_line, mavg_line, direction = pmax(
            src, df["high"], df["low"], df["close"],
            atr_period=pmax_cfg["atr_period"],
            atr_multiplier=pmax_cfg["atr_multiplier"],
            ma_type=pmax_cfg["ma_type"],
            ma_length=pmax_cfg["ma_length"],
            change_atr=True, normalize_atr=False,
        )

        kc_cfg = config["strategy"]["timeframes"][0]["keltner"]
        kc_mid, kc_upper, kc_lower = keltner_channel(
            df["high"], df["low"], df["close"],
            kc_length=kc_cfg["length"],
            kc_multiplier=kc_cfg["multiplier"],
            atr_period=kc_cfg["atr_period"],
        )

        atr_vals = atr(df["high"], df["low"], df["close"], 50)

        # Pre-extract arrays
        pmax_v = pmax_line.values
        mavg_v = mavg_line.values
        kcu_v = kc_upper.values
        kcl_v = kc_lower.values
        atr_v = atr_vals.values
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        times = df["open_time"].values
        n = len(df)

        # Walk candles — detect PMax crossovers + Keltner DCA/TP
        condition = 0.0  # 0=flat, 1=LONG, -1=SHORT

        for i in range(1, n):
            prev_m = mavg_v[i - 1]
            prev_p = pmax_v[i - 1]
            curr_m = mavg_v[i]
            curr_p = pmax_v[i]

            if np.isnan(prev_m) or np.isnan(prev_p) or np.isnan(curr_m) or np.isnan(curr_p):
                continue

            # PMax crossover detection
            buy_cross = prev_m <= prev_p and curr_m > curr_p
            sell_cross = prev_m >= prev_p and curr_m < curr_p

            if buy_cross and condition <= 0.0:
                condition = 1.0
                atr_val = float(atr_v[i]) if not np.isnan(atr_v[i]) else 50.0
                signal = Signal(
                    timestamp=int(times[i]), symbol=symbol, side="LONG",
                    price=float(closes[i]), rsi_value=0, atr_value=atr_val,
                    tf_label="3m", size_multiplier=1.0,
                )
                sim.process_signal(signal, entry_time=int(times[i]))

            elif sell_cross and condition >= 0.0:
                condition = -1.0
                atr_val = float(atr_v[i]) if not np.isnan(atr_v[i]) else 50.0
                signal = Signal(
                    timestamp=int(times[i]), symbol=symbol, side="SHORT",
                    price=float(closes[i]), rsi_value=0, atr_value=atr_val,
                    tf_label="3m", size_multiplier=1.0,
                )
                sim.process_signal(signal, entry_time=int(times[i]))

            # Keltner DCA/TP — direct array check (no DataFrame needed)
            if np.isnan(kcu_v[i]) or np.isnan(kcl_v[i]):
                continue

            key = f"{symbol}:3m"
            if key in sim.positions and sim.positions[key].condition != 0.0:
                pos = sim.positions[key]
                action, fill_price = sim._risk_mgr.check_keltner_signals(
                    pos, float(highs[i]), float(lows[i]), float(closes[i]),
                    float(kcu_v[i]), float(kcl_v[i]),
                )

                if action == "DCA":
                    sim._risk_mgr.process_dca_fill(pos, fill_price)
                    step_notional = pos.margin_per_step * pos.leverage
                    dca_fee = step_notional * sim.wallet.maker_fee
                    sim.wallet.balance -= dca_fee
                    sim.wallet.total_fees += dca_fee
                    sim.wallet.maker_fees += dca_fee
                    sim._trade_counter += 1
                    from core.engine.simulator import Trade
                    sim.trades.append(Trade(
                        id=sim._trade_counter, symbol=symbol, side=pos.side,
                        entry_price=fill_price, entry_time=int(times[i]),
                        exit_price=fill_price, exit_time=int(times[i]),
                        exit_reason="DCA", qty_usdt=round(step_notional, 2),
                        leverage=pos.leverage, pnl_usdt=0.0, pnl_percent=0.0,
                        fee_usdt=round(dca_fee, 4), tf_label="3m",
                    ))

                elif action == "TP":
                    avg_before = pos.average_entry_price
                    closed_notional = sim._risk_mgr.process_tp_fill(pos, fill_price)
                    if closed_notional > 0:
                        if pos.side == "LONG":
                            pnl_pct = (fill_price - avg_before) / avg_before * 100
                        else:
                            pnl_pct = (avg_before - fill_price) / avg_before * 100
                        pnl_usdt = closed_notional * pnl_pct / 100
                        tp_fee = closed_notional * sim.wallet.maker_fee
                        sim.wallet.balance += pnl_usdt - tp_fee
                        sim.wallet.total_pnl += pnl_usdt
                        sim.wallet.total_fees += tp_fee
                        sim.wallet.maker_fees += tp_fee
                        sim.wallet.total_trades += 1
                        if pnl_usdt > 0:
                            sim.wallet.winning_trades += 1
                        else:
                            sim.wallet.losing_trades += 1
                        sim._trade_counter += 1
                        from core.engine.simulator import Trade
                        sim.trades.append(Trade(
                            id=sim._trade_counter, symbol=symbol, side=pos.side,
                            entry_price=avg_before, entry_time=pos.entry_time,
                            exit_price=fill_price, exit_time=int(times[i]),
                            exit_reason="TP", qty_usdt=round(closed_notional, 2),
                            leverage=pos.leverage, pnl_usdt=round(pnl_usdt, 4),
                            pnl_percent=round(pnl_pct, 4), fee_usdt=round(tp_fee, 4),
                            tf_label="3m",
                        ))

        # Compute metrics
        trades = sim.trades
        tp_trades = [t for t in trades if t.exit_reason == "TP"]
        dca_trades = [t for t in trades if t.exit_reason == "DCA"]
        rev_trades = [t for t in trades if t.exit_reason == "REVERSAL"]

        total_trades = sim.wallet.total_trades
        if total_trades == 0:
            return {"score": -100, "net_pct": 0, "profit_factor": 0, "max_dd": 0,
                    "total_trades": 0, "win_rate": 0, "tp_count": 0, "dca_count": 0}

        net_pct = (sim.wallet.balance - sim.wallet.initial_balance) / sim.wallet.initial_balance * 100
        total_pnl = sim.wallet.total_pnl
        total_fees = sim.wallet.total_fees

        winning = [t.pnl_usdt for t in trades if t.pnl_usdt > 0]
        losing = [t.pnl_usdt for t in trades if t.pnl_usdt < 0]
        gross_profit = sum(winning) if winning else 0
        gross_loss = abs(sum(losing)) if losing else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (10.0 if gross_profit > 0 else 0)

        # Max drawdown
        equity = sim.wallet.initial_balance
        peak = equity
        max_dd = 0
        for t in trades:
            if t.exit_reason == "DCA":
                equity -= t.fee_usdt
            else:
                equity += t.pnl_usdt - t.fee_usdt
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        win_rate = len(winning) / total_trades * 100 if total_trades > 0 else 0

        # Score: maximize profit, reward consistency, penalize drawdown
        score = net_pct + (profit_factor * 5) - (max_dd * 0.3)

        return {
            "score": round(score, 4),
            "net_pct": round(net_pct, 4),
            "profit_factor": round(profit_factor, 4),
            "max_dd": round(max_dd, 4),
            "total_trades": total_trades,
            "win_rate": round(win_rate, 2),
            "tp_count": len(tp_trades),
            "dca_count": len(dca_trades),
            "rev_count": len(rev_trades),
            "total_pnl": round(total_pnl, 4),
            "total_fees": round(total_fees, 4),
            "balance": round(sim.wallet.balance, 2),
        }

    except Exception as e:
        logger.error("Backtest error: %s", str(e)[:100])
        return {"score": -100, "net_pct": 0, "profit_factor": 0, "max_dd": 100,
                "total_trades": 0, "win_rate": 0, "tp_count": 0, "dca_count": 0}


def create_objective(df_is: pd.DataFrame, symbol: str):
    """Create Optuna objective function for a symbol."""

    def objective(trial: optuna.Trial) -> float:
        params = {
            # PMax
            "pmax_atr_period": trial.suggest_int("pmax_atr_period", 5, 30),
            "pmax_atr_multiplier": trial.suggest_float("pmax_atr_multiplier", 1.0, 5.0, step=0.25),
            "pmax_ma_type": trial.suggest_categorical("pmax_ma_type", MA_TYPES),
            "pmax_ma_length": trial.suggest_int("pmax_ma_length", 5, 50),

            # Keltner
            "kc_length": trial.suggest_int("kc_length", 10, 50),
            "kc_multiplier": trial.suggest_float("kc_multiplier", 0.5, 3.0, step=0.1),
            "kc_atr_period": trial.suggest_int("kc_atr_period", 5, 30),

            # Risk
            "max_dca_steps": trial.suggest_int("max_dca_steps", 1, 5),
            "tp_close_percent": trial.suggest_float("tp_close_percent", 0.10, 0.50, step=0.05),

            # Filters
            "ema_filter_period": trial.suggest_int("ema_filter_period", 50, 300),
            "rsi_overbought": trial.suggest_int("rsi_overbought", 60, 80),
        }

        result = run_backtest_with_params(df_is, symbol, params)
        return result["score"]

    return objective


def optimize_symbol(symbol: str, days: int = 14, n_trials: int = 200):
    """Optimize parameters for a single symbol."""
    logger.info("=" * 60)
    logger.info("OPTIMIZING %s (%d trials, %d days)", symbol, n_trials, days)
    logger.info("=" * 60)

    # Fetch data
    df = fetch_klines(symbol, "3m", days)
    if len(df) < 500:
        logger.error("Not enough data for %s: %d candles", symbol, len(df))
        return None

    # Split: 70% in-sample, 30% out-of-sample
    split_idx = int(len(df) * 0.7)
    df_is = df.iloc[:split_idx].reset_index(drop=True)
    df_oos = df.iloc[split_idx:].reset_index(drop=True)

    logger.info("Data split: IS=%d candles, OOS=%d candles", len(df_is), len(df_oos))

    # Create study
    db_path = RESULTS_DIR / f"{symbol}_keltner_study.db"
    study = optuna.create_study(
        study_name=f"{symbol}_keltner",
        direction="maximize",
        storage=f"sqlite:///{db_path}",
        load_if_exists=True,
    )

    # Optimize
    objective = create_objective(df_is, symbol)
    start_time = time.time()

    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    elapsed = time.time() - start_time
    logger.info("Optimization completed in %.1f minutes", elapsed / 60)

    # Top 5 results
    top_trials = sorted(study.trials, key=lambda t: t.value if t.value else -999, reverse=True)[:5]

    results = []
    for i, trial in enumerate(top_trials):
        params = trial.params
        logger.info("\n--- Top %d (IS score=%.2f) ---", i + 1, trial.value)

        # In-sample result
        is_result = run_backtest_with_params(df_is, symbol, params)

        # Out-of-sample validation
        oos_result = run_backtest_with_params(df_oos, symbol, params)

        logger.info("IS:  score=%.2f net=%.2f%% PF=%.2f DD=%.2f%% trades=%d WR=%.1f%%",
                     is_result["score"], is_result["net_pct"], is_result["profit_factor"],
                     is_result["max_dd"], is_result["total_trades"], is_result["win_rate"])
        logger.info("OOS: score=%.2f net=%.2f%% PF=%.2f DD=%.2f%% trades=%d WR=%.1f%%",
                     oos_result["score"], oos_result["net_pct"], oos_result["profit_factor"],
                     oos_result["max_dd"], oos_result["total_trades"], oos_result["win_rate"])

        results.append({
            "rank": i + 1,
            "params": params,
            "in_sample": is_result,
            "out_of_sample": oos_result,
        })

    # Save results
    output = {
        "symbol": symbol,
        "days": days,
        "n_trials": n_trials,
        "is_candles": len(df_is),
        "oos_candles": len(df_oos),
        "elapsed_minutes": round(elapsed / 60, 1),
        "top_results": results,
    }

    result_path = RESULTS_DIR / f"{symbol}_keltner_results.json"
    with open(result_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info("\nResults saved to %s", result_path)
    return output


def main():
    symbols = ["BTCUSDT", "SOLUSDT", "ETHUSDT", "XRPUSDT"]
    days = 14
    n_trials = 200

    # Allow CLI override
    if len(sys.argv) > 1:
        symbols = [sys.argv[1].upper()]
    if len(sys.argv) > 2:
        n_trials = int(sys.argv[2])
    if len(sys.argv) > 3:
        days = int(sys.argv[3])

    logger.info("Optimizing %s | %d trials | %d days lookback", symbols, n_trials, days)

    all_results = {}
    for symbol in symbols:
        result = optimize_symbol(symbol, days=days, n_trials=n_trials)
        if result:
            all_results[symbol] = result

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("OPTIMIZATION SUMMARY")
    logger.info("=" * 60)
    for symbol, res in all_results.items():
        if res["top_results"]:
            best = res["top_results"][0]
            is_r = best["in_sample"]
            oos_r = best["out_of_sample"]
            logger.info(
                "%s: IS=%.2f%% OOS=%.2f%% PF=%.2f/%.2f DD=%.1f%%/%.1f%%",
                symbol, is_r["net_pct"], oos_r["net_pct"],
                is_r["profit_factor"], oos_r["profit_factor"],
                is_r["max_dd"], oos_r["max_dd"],
            )


if __name__ == "__main__":
    main()
