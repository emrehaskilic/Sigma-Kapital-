"""Generate PMax Strategy Documentation PDF."""
from fpdf import FPDF


class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "Scalper Bot - PMax Strategy Documentation", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 8)
        self.cell(0, 5, "3m Profit Maximizer (PMax) Trading Strategy", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title):
        self.set_font("Helvetica", "B", 12)
        self.set_fill_color(30, 40, 55)
        self.set_text_color(255, 255, 255)
        self.cell(0, 8, f"  {title}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(3)

    def sub_title(self, title):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(40, 80, 140)
        self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def body_text(self, text):
        self.set_font("Helvetica", "", 9)
        self.set_x(self.l_margin)
        self.multi_cell(0, 5, text)
        self.ln(2)

    def bullet(self, text):
        self.set_font("Helvetica", "", 9)
        indent = self.l_margin + 5
        self.set_x(indent)
        self.cell(5, 5, "-")
        self.multi_cell(0, 5, text)

    def code_block(self, text):
        self.set_font("Courier", "", 8)
        self.set_fill_color(240, 240, 240)
        for line in text.split("\n"):
            self.cell(5)
            self.cell(0, 4.5, line, fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.ln(3)


pdf = PDF()
pdf.alias_nb_pages()
pdf.set_auto_page_break(auto=True, margin=20)
pdf.add_page()

# 1. OVERVIEW
pdf.section_title("1. Genel Bakis (Overview)")
pdf.body_text(
    "Bu bot, KivancOzbilgic tarafindan gelistirilen Profit Maximizer (PMax) "
    "indikatorunu kullanarak Binance Futures uzerinde otomatik islem yapan bir scalper bottir."
)
pdf.body_text(
    "Bot, 3 dakikalik zaman diliminde PMax sinyalleri uretir. "
    "Pozisyonlar sadece crossover reversal ile kapatilir, TP/SL kullanilmaz. "
    "Bu yaklasim PMax indikatorunun orijinal mantigina sadik kalir."
)

pdf.sub_title("Temel Ozellikler:")
pdf.bullet("Timeframe: 3 dakika (3m)")
pdf.bullet("Cikis: Sadece crossover reversal ile (TP/SL yok)")
pdf.bullet("Giris: MAvg, PMax cizgisini kestigi anda")
pdf.bullet("Islem tipi: BOTH (hem LONG hem SHORT)")
pdf.bullet("Borsa: Binance USDT-M Futures")
pdf.ln(3)

# 2. PMAX INDICATOR
pdf.section_title("2. PMax Indikatoru")
pdf.body_text(
    "PMax (Profit Maximizer), ATR tabanli bir trailing stop mekanizmasidir. "
    "Supertrend indikatorune benzer ancak fiyat yerine bir hareketli ortalama "
    "(Moving Average) uzerine uygulanir."
)

pdf.sub_title("2.1 Hesaplama Adimlari:")
pdf.body_text("Adim 1 - ATR Hesaplama:")
pdf.code_block(
    "ATR = RMA(True Range, ATR_Period)  // Wilder method\n"
    "True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)"
)

pdf.body_text("Adim 2 - Hareketli Ortalama (MAvg):")
pdf.code_block(
    "MAvg = EMA(Source, MA_Length)  // varsayilan\n"
    "Source = (High + Low) / 2     // hl2"
)
pdf.body_text("Desteklenen MA turleri: SMA, EMA, WMA, TMA, VAR, WWMA, ZLEMA, TSF")

pdf.body_text("Adim 3 - Long Stop ve Short Stop:")
pdf.code_block(
    "LongStop  = MAvg - (ATR_Multiplier * ATR)\n"
    "ShortStop = MAvg + (ATR_Multiplier * ATR)\n"
    "\n"
    "// Trailing mantigi:\n"
    "LongStop  = max(LongStop, PrevLongStop)   eger MAvg > PrevLongStop\n"
    "ShortStop = min(ShortStop, PrevShortStop) eger MAvg < PrevShortStop"
)

pdf.body_text("Adim 4 - Yon Belirleme (Direction):")
pdf.code_block(
    "Eger PrevDirection = -1 VE MAvg > PrevShortStop => Direction = 1 (YUKARI)\n"
    "Eger PrevDirection =  1 VE MAvg < PrevLongStop  => Direction = -1 (ASAGI)"
)

pdf.body_text("Adim 5 - PMax Cizgisi:")
pdf.code_block("PMax = Direction == 1 ? LongStop : ShortStop")
pdf.ln(2)

# 3. SIGNAL LOGIC
pdf.section_title("3. Sinyal Mantigi")
pdf.body_text("Sinyaller, MAvg ile PMax cizgisinin kesisiminden uretilir:")

pdf.sub_title("LONG (Alis) Sinyali:")
pdf.code_block(
    "Onceki bar: MAvg <= PMax\n"
    "Mevcut bar: MAvg >  PMax\n"
    "=> MAvg, PMax cizgisini YUKARI kesti"
)

pdf.sub_title("SHORT (Satis) Sinyali:")
pdf.code_block(
    "Onceki bar: MAvg >= PMax\n"
    "Mevcut bar: MAvg <  PMax\n"
    "=> MAvg, PMax cizgisini ASAGI kesti"
)

pdf.body_text(
    "Onemli: Pozisyon sadece TERS YONDE bir crossover oldugunda kapanir. "
    "TP (Take Profit) ve SL (Stop Loss) YOKTUR. "
    "Bu, PMax indikatorunun orijinal mantigina sadik kalir."
)
pdf.ln(2)

# 4. POSITION LIFECYCLE
pdf.section_title("4. Pozisyon Yasam Dongusu")

pdf.sub_title("Giris (Entry):")
pdf.bullet("MAvg, PMax cizgisini yukari keserse => LONG pozisyon acilir")
pdf.bullet("MAvg, PMax cizgisini asagi keserse => SHORT pozisyon acilir")
pdf.bullet("Giris fiyati: Crossover mumunun kapanis fiyati (3m mum)")

pdf.sub_title("Cikis (Exit):")
pdf.bullet("Sadece TERS YONDE crossover ile kapatilir (Reversal)")
pdf.bullet("LONG pozisyon: MAvg asagi kestiginde kapatilir ve SHORT acilir")
pdf.bullet("SHORT pozisyon: MAvg yukari kestiginde kapatilir ve LONG acilir")
pdf.bullet("TP/SL YOKTUR - tamamen crossover bazli")

pdf.sub_title("Ornek Senaryo:")
pdf.code_block(
    "t=0  : MAvg PMax i yukari kesti  => LONG @ 70,000\n"
    "t=45m: MAvg PMax i asagi kesti   => LONG kapatildi @ 70,500 (+0.71%)\n"
    "                                    SHORT acildi @ 70,500\n"
    "t=2h : MAvg PMax i yukari kesti  => SHORT kapatildi @ 69,800 (+1.00%)\n"
    "                                    LONG acildi @ 69,800"
)
pdf.ln(2)

# 5. FILTERS
pdf.section_title("5. Sinyal Filtreleri")
pdf.body_text(
    "Canli taramada (scanner loop) sinyaller su filtrelerden gecer. "
    "Not: Backfill (baslangic simuelasyonu) sirasinda filtreler UYGULANMAZ."
)

pdf.sub_title("5.1 EMA Trend Filtresi:")
pdf.bullet("Period: 144")
pdf.bullet("LONG engellenir eger: Close < EMA(144)")
pdf.bullet("SHORT engellenir eger: Close > EMA(144)")

pdf.sub_title("5.2 RSI Filtresi:")
pdf.bullet("Period: 28")
pdf.bullet("LONG engellenir eger: RSI > 65 VE RSI > EMA(RSI, 10)")
pdf.bullet("SHORT engellenir eger: RSI < 35 VE RSI < EMA(RSI, 10)")

pdf.sub_title("5.3 ATR Volatilite Filtresi:")
pdf.bullet("Period: 50")
pdf.bullet("Sinyal engellenir eger: ATR < son 200 barin %20 percentile i")
pdf.bullet("Dusuk volatiliteli piyasalarda islem yapmaz")
pdf.ln(2)

# 6. CONFIGURATION
pdf.section_title("6. Yapilandirma (Configuration)")

pdf.sub_title("PMax Parametreleri:")
pdf.code_block(
    "timeframe: 3m        # 3 dakikalik mumlar\n"
    "source: hl2          # Fiyat kaynagi (hl2, close, hlc3, ohlc4)\n"
    "atr_period: 10       # ATR hesaplama periyodu\n"
    "atr_multiplier: 3.0  # ATR carpani (band genisligi)\n"
    "ma_type: EMA         # Hareketli ortalama turu\n"
    "ma_length: 10        # Hareketli ortalama uzunlugu\n"
    "change_atr: true     # Wilder ATR metodu (true) veya SMA (false)\n"
    "normalize_atr: false # ATR yi fiyata gore normalize et"
)

pdf.sub_title("Trading Parametreleri:")
pdf.code_block(
    "initial_balance: 1000  # Baslangic bakiyesi (USDT)\n"
    "leverage: 10           # Kaldirac\n"
    "margin_per_trade: 100  # Islem basina margin (USDT)\n"
    "trade_type: BOTH       # LONG, SHORT veya BOTH\n"
    "hedge_mode: false      # Tek yonlu pozisyon"
)

pdf.sub_title("Pozisyon Boyutlandirma:")
pdf.code_block(
    "3m: margin=100 x leverage=10 = 1,000 USDT notional"
)
pdf.ln(2)

# 7. ARCHITECTURE
pdf.section_title("7. Teknik Mimari")

pdf.sub_title("Dosya Yapisi:")
pdf.code_block(
    "core/strategy/indicators.py  -> PMax hesaplama, MA turleri, ATR\n"
    "core/strategy/signals.py     -> SignalEngine: crossover algilama\n"
    "core/strategy/risk_manager.py-> PositionState (TP/SL yok)\n"
    "core/engine/simulator.py     -> Dry-run simulasyon motoru\n"
    "core/engine/pair_manager.py  -> WS + candle yonetimi\n"
    "core/engine/backtester.py    -> Gecmis veri backtest motoru\n"
    "core/engine/live_executor.py -> Canli Binance islem motoru\n"
    "backend/server.py            -> FastAPI REST API\n"
    "frontend/                    -> React + Vite dashboard"
)

pdf.sub_title("Veri Akisi:")
pdf.code_block(
    "Binance WS (3m kline stream)\n"
    "    |\n"
    "    v\n"
    "PairManager (candle buffer)\n"
    "    |\n"
    "    v\n"
    "SignalEngine.process() (PMax crossover algilama)\n"
    "    |\n"
    "    v\n"
    "Simulator.process_signal() (pozisyon ac/kapat)\n"
    "    |\n"
    "    v\n"
    "FastAPI /api/status (frontend e PnL gonder)"
)
pdf.ln(2)

# 8. DRY-RUN FLOW
pdf.section_title("8. Dry-Run Baslangic Akisi")
pdf.body_text("Bot baslatildiginda su adimlar izlenir:")
pdf.bullet("1. Her sembol icin 3m timeframe den son 1500 mum cekilir")
pdf.bullet("2. process_backfill() ile tum crossover gecmisi taranir")
pdf.bullet("3. Son aktif crossover bulunur (orn: 12 saat once LONG)")
pdf.bullet("4. Bu pozisyon ACIK olarak simulatore yuklenir")
pdf.bullet("5. Filtreler backfill sirasinda UYGULANMAZ")
pdf.bullet("6. WS baglantisi kurulur, canli fiyat akisi baslar")
pdf.bullet("7. Scanner loop her 3m mum kapanisinda yeni crossover arar")
pdf.bullet("8. Yeni crossover varsa reversal yapilir (eski kapanir, yenisi acilir)")

# Save
output_path = "PMax_Strategy_Documentation.pdf"
pdf.output(output_path)
print(f"PDF created: {output_path}")
