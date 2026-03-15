"""Scalper Bot Dashboard — Streamlit UI.

Run with:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config
from core.data.binance_rest import BinanceRest

# ─────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Scalper Bot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────
# Dark Mode CSS
# ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* ── Global dark background ── */
    .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"],
    [data-testid="stSidebar"], [data-testid="stSidebarContent"] {
        background-color: #0e1117 !important;
        color: #fafafa !important;
    }

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background-color: #161b22 !important;
        border-right: 1px solid #30363d !important;
    }

    /* ── Metric cards ── */
    [data-testid="stMetric"] {
        background-color: #161b22 !important;
        border: 1px solid #30363d !important;
        border-radius: 8px !important;
        padding: 12px 16px !important;
    }
    [data-testid="stMetricLabel"] { color: #8b949e !important; }
    [data-testid="stMetricValue"] { color: #58a6ff !important; }

    /* ── Inputs ── */
    .stSelectbox > div > div, .stNumberInput > div > div > input,
    .stTextInput > div > div > input {
        background-color: #0d1117 !important;
        color: #c9d1d9 !important;
        border-color: #30363d !important;
    }

    /* ── Buttons ── */
    .stButton > button {
        background-color: #21262d !important;
        color: #c9d1d9 !important;
        border: 1px solid #30363d !important;
        border-radius: 6px !important;
        transition: all 0.2s !important;
    }
    .stButton > button:hover {
        background-color: #30363d !important;
        border-color: #58a6ff !important;
    }
    .stButton > button[kind="primary"] {
        background-color: #238636 !important;
        border-color: #2ea043 !important;
        color: #ffffff !important;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #2ea043 !important;
    }

    /* ── Dataframe / table ── */
    .stDataFrame, [data-testid="stDataFrame"] {
        border: 1px solid #30363d !important;
        border-radius: 8px !important;
    }

    /* ── Info/Warning/Success/Error boxes ── */
    .stAlert { border-radius: 8px !important; }

    /* ── Dividers ── */
    hr { border-color: #21262d !important; }

    /* ── Headers ── */
    h1, h2, h3, h4, h5, h6 { color: #e6edf3 !important; }

    /* ── Pair status cards ── */
    .pair-card {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 10px;
        padding: 16px;
        margin: 4px 0;
    }
    .pair-card-long {
        border-left: 4px solid #3fb950;
    }
    .pair-card-short {
        border-left: 4px solid #f85149;
    }
    .pair-card-neutral {
        border-left: 4px solid #8b949e;
    }
    .pair-symbol { font-size: 1.1em; font-weight: 700; color: #e6edf3; }
    .pair-price { font-size: 1.3em; font-weight: 600; color: #58a6ff; }
    .pair-signal { font-size: 0.85em; padding: 2px 8px; border-radius: 12px; }
    .signal-long { background-color: #238636; color: #fff; }
    .signal-short { background-color: #da3633; color: #fff; }
    .signal-wait { background-color: #30363d; color: #8b949e; }
    .pnl-positive { color: #3fb950; font-weight: 600; }
    .pnl-negative { color: #f85149; font-weight: 600; }

    /* ── Slider ── */
    .stSlider > div > div > div { color: #c9d1d9 !important; }

    /* ── Footer ── */
    .footer-text { color: #484f58; text-align: center; font-size: 0.8em; margin-top: 20px; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────
# Session state initialization
# ─────────────────────────────────────────────────────────────────
if "config" not in st.session_state:
    st.session_state.config = load_config()

if "rest_client" not in st.session_state:
    st.session_state.rest_client = BinanceRest()

if "all_symbols" not in st.session_state:
    try:
        symbols = st.session_state.rest_client.fetch_futures_symbols_sync()
        st.session_state.all_symbols = symbols
    except Exception as e:
        st.error(f"Binance API connection error: {e}")
        st.session_state.all_symbols = []

if "bot_running" not in st.session_state:
    st.session_state.bot_running = False

if "active_symbols" not in st.session_state:
    st.session_state.active_symbols = []

if "trade_log" not in st.session_state:
    st.session_state.trade_log = []

if "signal_log" not in st.session_state:
    st.session_state.signal_log = []

if "initial_scan_results" not in st.session_state:
    st.session_state.initial_scan_results = {}


# ─────────────────────────────────────────────────────────────────
# Initial scan — run strategy on historical data immediately
# ─────────────────────────────────────────────────────────────────
def run_initial_scan(symbols: list[str], config: dict) -> dict:
    """Fetch last 500 candles for each pair, run strategy, return immediate signals."""
    from core.strategy.signals import SignalEngine
    import pandas as pd

    rest = st.session_state.rest_client
    results = {}

    for sym in symbols:
        try:
            klines = rest.fetch_klines_sync(sym, config["strategy"]["timeframe"], limit=500)
            if len(klines) < 200:
                results[sym] = {"status": "insufficient_data", "candles": len(klines)}
                continue

            df = pd.DataFrame(klines)
            df["symbol"] = sym

            engine = SignalEngine(config)
            signal = engine.process(df)

            if signal:
                results[sym] = {
                    "status": "signal",
                    "side": signal.side,
                    "price": signal.price,
                    "rsi": round(signal.rsi_value, 2),
                    "atr": round(signal.atr_value, 4),
                }
            else:
                # Check current MA state (is close_ma above or below open_ma?)
                from core.strategy.indicators import pmax as calc_pmax, rsi as calc_rsi
                pmax_cfg = config["strategy"].get("pmax", {})
                src_type = pmax_cfg.get("source", "hl2").lower()
                if src_type == "hl2":
                    src = (df["high"] + df["low"]) / 2
                elif src_type == "hlc3":
                    src = (df["high"] + df["low"] + df["close"]) / 3
                elif src_type == "ohlc4":
                    src = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
                else:
                    src = df["close"]
                _, mavg, direction = calc_pmax(
                    src, df["high"], df["low"], df["close"],
                    atr_period=pmax_cfg.get("atr_period", 10),
                    atr_multiplier=pmax_cfg.get("atr_multiplier", 3.0),
                    ma_type=pmax_cfg.get("ma_type", "EMA"),
                    ma_length=pmax_cfg.get("ma_length", 10),
                    change_atr=pmax_cfg.get("change_atr", True),
                    normalize_atr=pmax_cfg.get("normalize_atr", False),
                )
                trend = "BULLISH" if direction.iloc[-1] == 1 else "BEARISH"
                rsi_val = calc_rsi(df["close"], 28).iloc[-1]
                results[sym] = {
                    "status": "monitoring",
                    "trend": trend,
                    "last_price": float(df["close"].iloc[-1]),
                    "rsi": round(rsi_val, 2),
                }
        except Exception as e:
            results[sym] = {"status": "error", "message": str(e)}

    return results


# ─────────────────────────────────────────────────────────────────
# Sidebar — Settings
# ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🤖 Scalper Bot")
    st.caption("Dry-Run Simulation Engine")
    st.divider()

    st.subheader("💰 Hesap Ayarlari")
    initial_balance = st.number_input(
        "Ana Kasa (USDT)",
        min_value=10.0,
        max_value=1_000_000.0,
        value=float(st.session_state.config["trading"]["initial_balance"]),
        step=100.0,
    )
    margin_per_trade = st.number_input(
        "Islem Basina Margin (USDT)",
        min_value=1.0,
        max_value=100_000.0,
        value=float(st.session_state.config["trading"]["margin_per_trade"]),
        step=10.0,
    )
    leverage = st.slider(
        "Kaldirac",
        min_value=1,
        max_value=125,
        value=int(st.session_state.config["trading"]["leverage"]),
    )
    trade_type = st.selectbox(
        "Islem Tipi",
        options=["BOTH", "LONG", "SHORT"],
        index=0,
    )

    st.divider()
    st.subheader("📊 PMax Strateji Ayarlari")
    pmax_cfg = st.session_state.config["strategy"].get("pmax", {})
    pmax_ma_type = st.selectbox(
        "MA Tipi",
        ["SMA", "EMA", "WMA", "TMA", "VAR", "WWMA", "ZLEMA", "TSF"],
        index=["SMA", "EMA", "WMA", "TMA", "VAR", "WWMA", "ZLEMA", "TSF"].index(
            pmax_cfg.get("ma_type", "EMA")
        ),
    )
    pmax_ma_length = st.number_input("MA Uzunlugu", min_value=1, max_value=200, value=pmax_cfg.get("ma_length", 10))
    pmax_atr_period = st.number_input("ATR Periyodu", min_value=1, max_value=200, value=pmax_cfg.get("atr_period", 10))
    pmax_atr_mult = st.number_input("ATR Carpani", min_value=0.1, max_value=20.0, value=float(pmax_cfg.get("atr_multiplier", 3.0)), step=0.1)
    timeframe = st.selectbox(
        "Zaman Dilimi",
        ["1m", "3m", "5m", "15m", "30m", "1h", "4h"],
        index=0,
    )

    st.divider()
    st.subheader("🎯 Risk Yonetimi")
    col1, col2 = st.columns(2)
    with col1:
        tp1_level = st.number_input("TP1 (%)", min_value=0.1, max_value=50.0, value=1.0, step=0.1)
        tp2_level = st.number_input("TP2 (%)", min_value=0.1, max_value=50.0, value=1.5, step=0.1)
        tp3_level = st.number_input("TP3 (%)", min_value=0.1, max_value=50.0, value=2.0, step=0.1)
    with col2:
        tp1_qty = st.number_input("TP1 Miktar (%)", min_value=1, max_value=100, value=50)
        tp2_qty = st.number_input("TP2 Miktar (%)", min_value=1, max_value=100, value=30)
        tp3_qty = st.number_input("TP3 Miktar (%)", min_value=1, max_value=100, value=20)
    sl_level = st.number_input("Stop Loss (%)", min_value=0.1, max_value=50.0, value=0.5, step=0.1)

    # Update config from sidebar
    st.session_state.config["trading"]["initial_balance"] = initial_balance
    st.session_state.config["trading"]["margin_per_trade"] = margin_per_trade
    st.session_state.config["trading"]["leverage"] = leverage
    st.session_state.config["trading"]["trade_type"] = trade_type
    if "pmax" not in st.session_state.config["strategy"]:
        st.session_state.config["strategy"]["pmax"] = {}
    st.session_state.config["strategy"]["pmax"]["ma_type"] = pmax_ma_type
    st.session_state.config["strategy"]["pmax"]["ma_length"] = pmax_ma_length
    st.session_state.config["strategy"]["pmax"]["atr_period"] = pmax_atr_period
    st.session_state.config["strategy"]["pmax"]["atr_multiplier"] = pmax_atr_mult
    st.session_state.config["strategy"]["timeframe"] = timeframe
    st.session_state.config["risk"]["tp1_level"] = tp1_level
    st.session_state.config["risk"]["tp1_qty"] = tp1_qty
    st.session_state.config["risk"]["tp2_level"] = tp2_level
    st.session_state.config["risk"]["tp2_qty"] = tp2_qty
    st.session_state.config["risk"]["tp3_level"] = tp3_level
    st.session_state.config["risk"]["tp3_qty"] = tp3_qty
    st.session_state.config["risk"]["stop_loss"] = sl_level


# ─────────────────────────────────────────────────────────────────
# Main content
# ─────────────────────────────────────────────────────────────────

# ── Pair Selection ──
st.header("📈 Pair Secimi")

all_symbol_names = [s["symbol"] for s in st.session_state.all_symbols]

col_select, col_active = st.columns([1, 2])

with col_select:
    selected_pair = st.selectbox(
        "Binance Futures Pair Ekle",
        options=[s for s in all_symbol_names if s not in st.session_state.active_symbols],
        index=None,
        placeholder="Pair ara ve sec...",
    )
    if st.button("➕ Pair Ekle", disabled=selected_pair is None, use_container_width=True):
        if selected_pair and selected_pair not in st.session_state.active_symbols:
            if len(st.session_state.active_symbols) >= 50:
                st.warning("Maksimum 50 pair ekleyebilirsiniz!")
            else:
                st.session_state.active_symbols.append(selected_pair)
                st.rerun()

with col_active:
    st.markdown("**Aktif Pairler:**")
    if not st.session_state.active_symbols:
        st.info("Henuz pair eklenmedi. Soldan bir pair secin.")
    else:
        # Display active pairs as removable chips
        num_cols = min(len(st.session_state.active_symbols), 6)
        cols = st.columns(num_cols)
        for idx, sym in enumerate(st.session_state.active_symbols):
            col_idx = idx % num_cols
            with cols[col_idx]:
                if st.button(f"❌ {sym}", key=f"remove_{sym}", use_container_width=True):
                    st.session_state.active_symbols.remove(sym)
                    # Clean up scan results
                    st.session_state.initial_scan_results.pop(sym, None)
                    st.rerun()

st.divider()

# ── Bot Control ──
st.header("🤖 Bot Kontrol")

col_start, col_stop, col_status = st.columns(3)
with col_start:
    if st.button(
        "▶️ Botu Baslat",
        disabled=st.session_state.bot_running or not st.session_state.active_symbols,
        use_container_width=True,
        type="primary",
    ):
        # Run initial scan on historical data
        with st.spinner(f"Son 500 mum verisi aliniyor ve strateji taramasi yapiliyor ({len(st.session_state.active_symbols)} pair)..."):
            scan_results = run_initial_scan(
                st.session_state.active_symbols,
                st.session_state.config,
            )
            st.session_state.initial_scan_results = scan_results

            # Process immediate signals through simulator
            from core.engine.simulator import Simulator
            from core.strategy.signals import Signal

            if "simulator" not in st.session_state:
                st.session_state.simulator = Simulator(st.session_state.config)

            sim = st.session_state.simulator
            immediate_entries = 0
            for sym, result in scan_results.items():
                if result.get("status") == "signal":
                    signal = Signal(
                        timestamp=int(time.time() * 1000),
                        symbol=sym,
                        side=result["side"],
                        price=result["price"],
                        rsi_value=result["rsi"],
                        atr_value=result["atr"],
                    )
                    sim.process_signal(signal)
                    immediate_entries += 1
                    st.session_state.signal_log.append({
                        "zaman": time.strftime("%H:%M:%S"),
                        "pair": sym,
                        "sinyal": result["side"],
                        "fiyat": result["price"],
                        "rsi": result["rsi"],
                        "kaynak": "BASLANGIC TARAMASI",
                    })

        st.session_state.bot_running = True
        if immediate_entries > 0:
            st.success(f"Bot baslatildi! {immediate_entries} pair'de aninda pozisyon acildi, {len(st.session_state.active_symbols)} pair izleniyor.")
        else:
            st.success(f"Bot baslatildi! Sinyal bulunamadi, {len(st.session_state.active_symbols)} pair izleniyor. Yeni sinyal bekleniyor...")

with col_stop:
    if st.button(
        "⏹️ Botu Durdur",
        disabled=not st.session_state.bot_running,
        use_container_width=True,
    ):
        st.session_state.bot_running = False
        st.session_state.initial_scan_results = {}
        if "simulator" in st.session_state:
            del st.session_state.simulator
        st.info("Bot durduruldu.")

with col_status:
    status = "🟢 Calisiyor" if st.session_state.bot_running else "🔴 Durduruldu"
    st.metric("Durum", status)

st.divider()

# ── Dashboard Metrics ──
st.header("📊 Dashboard")

# Get simulator stats if running
sim_stats = None
if "simulator" in st.session_state:
    sim_stats = st.session_state.simulator.get_stats()

balance_display = sim_stats["current_balance"] if sim_stats else initial_balance
total_pnl = sim_stats["total_pnl"] if sim_stats else 0.0
total_trades = sim_stats["total_trades"] if sim_stats else 0
win_rate = sim_stats["win_rate"] if sim_stats else 0.0

col_m1, col_m2, col_m3, col_m4, col_m5, col_m6 = st.columns(6)
col_m1.metric("💰 Bakiye", f"{balance_display:.2f} USDT")
col_m2.metric("📊 Kaldirac", f"{leverage}x")
col_m3.metric("📈 Aktif Pair", len(st.session_state.active_symbols))
col_m4.metric("🎯 Toplam Islem", total_trades)
pnl_prefix = "+" if total_pnl > 0 else ""
col_m5.metric("💵 Toplam PnL", f"{pnl_prefix}{total_pnl:.2f} USDT")
col_m6.metric("🏆 Win Rate", f"{win_rate:.1f}%")

st.divider()

# ── Active Pairs Grid ──
if st.session_state.active_symbols:
    st.subheader("📋 Aktif Pair Durumlari")

    scan = st.session_state.initial_scan_results
    num_pair_cols = min(len(st.session_state.active_symbols), 4)
    pair_cols = st.columns(num_pair_cols)

    for idx, sym in enumerate(st.session_state.active_symbols):
        col_idx = idx % num_pair_cols
        result = scan.get(sym, {})
        status = result.get("status", "waiting")

        if status == "signal":
            side = result["side"]
            card_class = "pair-card-long" if side == "LONG" else "pair-card-short"
            signal_class = "signal-long" if side == "LONG" else "signal-short"
            with pair_cols[col_idx]:
                st.markdown(f"""
                <div class="pair-card {card_class}">
                    <div class="pair-symbol">{sym}</div>
                    <div class="pair-price">{result['price']:.4f}</div>
                    <span class="pair-signal {signal_class}">{side} AKTIF</span>
                    <div style="margin-top:6px; color:#8b949e; font-size:0.85em;">
                        RSI: {result['rsi']} &nbsp;|&nbsp; ATR: {result['atr']}
                    </div>
                </div>
                """, unsafe_allow_html=True)

        elif status == "monitoring":
            trend = result.get("trend", "?")
            card_class = "pair-card-long" if trend == "BULLISH" else "pair-card-short"
            with pair_cols[col_idx]:
                st.markdown(f"""
                <div class="pair-card {card_class}">
                    <div class="pair-symbol">{sym}</div>
                    <div class="pair-price">{result.get('last_price', 0):.4f}</div>
                    <span class="pair-signal signal-wait">BEKLIYOR</span>
                    <div style="margin-top:6px; color:#8b949e; font-size:0.85em;">
                        Trend: {trend} &nbsp;|&nbsp; RSI: {result.get('rsi', '-')}
                    </div>
                </div>
                """, unsafe_allow_html=True)

        elif status == "error":
            with pair_cols[col_idx]:
                st.markdown(f"""
                <div class="pair-card pair-card-neutral">
                    <div class="pair-symbol">{sym}</div>
                    <span class="pair-signal signal-wait">HATA</span>
                    <div style="color:#f85149; font-size:0.85em;">{result.get('message', '')[:50]}</div>
                </div>
                """, unsafe_allow_html=True)

        else:
            with pair_cols[col_idx]:
                st.markdown(f"""
                <div class="pair-card pair-card-neutral">
                    <div class="pair-symbol">{sym}</div>
                    <span class="pair-signal signal-wait">BEKLIYOR</span>
                    <div style="color:#8b949e; font-size:0.85em;">Bot baslatilmadi</div>
                </div>
                """, unsafe_allow_html=True)

st.divider()

# ── Open Positions ──
if "simulator" in st.session_state and st.session_state.simulator.positions:
    st.subheader("📌 Acik Pozisyonlar")
    pos_data = []
    for sym, pos in st.session_state.simulator.positions.items():
        if pos.condition != 0.0:
            scan_price = st.session_state.initial_scan_results.get(sym, {}).get("price", pos.entry_price)
            if pos.side == "LONG":
                upnl = (scan_price - pos.entry_price) / pos.entry_price * 100
            else:
                upnl = (pos.entry_price - scan_price) / pos.entry_price * 100
            pos_data.append({
                "Pair": sym,
                "Yon": pos.side,
                "Giris": f"{pos.entry_price:.4f}",
                "TP1": f"{pos.tp1_line:.4f}",
                "TP2": f"{pos.tp2_line:.4f}",
                "TP3": f"{pos.tp3_line:.4f}",
                "SL": f"{pos.sl_line:.4f}",
                "Durum": f"{pos.condition}",
                "uPnL %": f"{upnl:+.2f}%",
            })
    if pos_data:
        import pandas as pd
        st.dataframe(pd.DataFrame(pos_data), use_container_width=True, hide_index=True)
    st.divider()

# ── Trade History ──
st.subheader("📜 Islem Gecmisi")
if st.session_state.trade_log:
    import pandas as pd
    df = pd.DataFrame(st.session_state.trade_log)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("Henuz islem yapilmadi. Botu baslatin.")

# ── Signal Log ──
st.subheader("📡 Sinyal Gecmisi")
if st.session_state.signal_log:
    import pandas as pd
    df = pd.DataFrame(st.session_state.signal_log)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("Henuz sinyal uretilmedi.")

# ── Footer ──
st.markdown('<div class="footer-text">Scalper Bot v0.1.0 — Dry Run Mode | Pine Script Strategy Port</div>', unsafe_allow_html=True)
