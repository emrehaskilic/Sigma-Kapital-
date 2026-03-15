"""Generate PMax + Keltner Channel Strategy Documentation PDF."""
from fpdf import FPDF


class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "Sigma Kapital - Scalper Bot Strategy", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 8)
        self.cell(0, 5, "PMax + Keltner Channel DCA/TP Trading System", align="C", new_x="LMARGIN", new_y="NEXT")
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
pdf.section_title("1. Genel Bakis")
pdf.body_text(
    "Bu bot, Binance USDT-M Futures uzerinde 3 dakikalik (3m) zaman diliminde calisan "
    "bir scalper bottir. Iki ana indikator kullanir:"
)
pdf.bullet("PMax (Profit Maximizer): Makro trend yonunu belirler (LONG/SHORT)")
pdf.bullet("Keltner Channel (KC): Mikro DCA/TP seviyelerini belirler")
pdf.body_text(
    "Sistem, PMax ile trendi yakalar ve Keltner bantlarina limit emirler koyarak "
    "trendin icindeki mikro dalgalardan kar toplar. Tum DCA ve TP emirleri "
    "MAKER (Post-Only/GTX) olarak gonderilir - komisyon optimizasyonu saglanir."
)
pdf.ln(2)

# 2. PMAX
pdf.section_title("2. PMax Indikatoru - Trend Belirleme")
pdf.body_text(
    "PMax, ATR tabanli bir trailing stop mekanizmasidir. Fiyat yerine "
    "hareketli ortalama (MAvg) uzerine uygulanir."
)
pdf.sub_title("Hesaplama:")
pdf.code_block(
    "MAvg = EMA(Source, MA_Length)      // Source = (High+Low)/2\n"
    "ATR  = RMA(TrueRange, ATR_Period)\n"
    "LongStop  = MAvg - (ATR_Mult * ATR)   // destek\n"
    "ShortStop = MAvg + (ATR_Mult * ATR)   // direnç\n"
    "PMax = Direction==1 ? LongStop : ShortStop"
)
pdf.sub_title("Sinyal:")
pdf.bullet("MAvg, PMax i yukari keserse => LONG sinyal")
pdf.bullet("MAvg, PMax i asagi keserse => SHORT sinyal")
pdf.sub_title("Mevcut Parametreler:")
pdf.code_block(
    "atr_period: 10\n"
    "atr_multiplier: 3.0\n"
    "ma_type: EMA\n"
    "ma_length: 10"
)
pdf.ln(2)

# 3. KELTNER CHANNEL
pdf.section_title("3. Keltner Channel - DCA/TP Seviyeleri")
pdf.body_text(
    "Keltner Kanallari, EMA tabanli bir volatilite bandidir. "
    "Ust ve alt bantlar ATR ile hesaplanir."
)
pdf.sub_title("Hesaplama:")
pdf.code_block(
    "Middle = EMA(Close, KC_Length)\n"
    "Upper  = Middle + (KC_Multiplier * ATR)\n"
    "Lower  = Middle - (KC_Multiplier * ATR)"
)
pdf.sub_title("Kullanim:")
pdf.bullet("LONG modda: KC Lower Band = DCA alim seviyesi (limit buy)")
pdf.bullet("LONG modda: KC Upper Band = TP satis seviyesi (limit sell)")
pdf.bullet("SHORT modda: KC Upper Band = DCA short ekleme seviyesi")
pdf.bullet("SHORT modda: KC Lower Band = TP kapatma seviyesi")
pdf.sub_title("Mevcut Parametreler:")
pdf.code_block(
    "kc_length: 20        # EMA periyodu\n"
    "kc_multiplier: 1.5   # Bant genisligi\n"
    "kc_atr_period: 10    # ATR periyodu"
)
pdf.ln(2)

# 4. TRADING LOGIC
pdf.section_title("4. Islem Mantigi")

pdf.sub_title("4.1 Ilk Giris (PMax Crossover):")
pdf.bullet("PMax LONG sinyali => Market BUY emri (taker fee: 0.05%)")
pdf.bullet("PMax SHORT sinyali => Market SELL emri (taker fee: 0.05%)")

pdf.sub_title("4.2 DCA (Maliyet Dusurme):")
pdf.bullet("LONG: KC Lower Band a limit BUY emri konur (maker fee: 0.02%)")
pdf.bullet("SHORT: KC Upper Band a limit SELL emri konur (maker fee: 0.02%)")
pdf.bullet("Her dalga basina maksimum 2 DCA")
pdf.bullet("Lot buyuklugu sabit (1x) - Martingale YOK")
pdf.bullet("Her mumda emir iptal edilip yeni KC degerine tasinir (trailing)")

pdf.sub_title("4.3 TP (Kar Alma):")
pdf.bullet("LONG: KC Upper Band a limit SELL emri konur (maker fee: 0.02%)")
pdf.bullet("SHORT: KC Lower Band a limit BUY emri konur (maker fee: 0.02%)")
pdf.bullet("Her TP mevcut pozisyonun %20 sini kapatir")
pdf.bullet("TP sadece DCA fill olduktan sonra aktif olur")
pdf.bullet("Her mumda emir iptal edilip yeni KC degerine tasinir")

pdf.sub_title("4.4 Kill Switch (PMax Reversal):")
pdf.bullet("PMax ters sinyal verdiginde:")
pdf.bullet("  1. Tum acik limit emirler iptal edilir (DCA + TP)")
pdf.bullet("  2. Pozisyonun tamami market emriyle kapatilir (taker fee)")
pdf.bullet("  3. Yeni yonde pozisyon acilir")
pdf.ln(2)

# 5. COMMISSION
pdf.section_title("5. Komisyon Yapisi")
pdf.code_block(
    "Emir Tipi          | Yontem  | Fee\n"
    "--------------------+---------+-------\n"
    "Ilk giris (PMax)   | Market  | Taker 0.05%\n"
    "DCA alim           | Limit   | Maker 0.02%\n"
    "TP satis           | Limit   | Maker 0.02%\n"
    "Kill switch kapanis| Market  | Taker 0.05%"
)
pdf.body_text(
    "Limit emirler Binance API sine timeInForce='GTX' (Post-Only) ile gonderilir. "
    "Bu sayede DCA ve TP emirlerinde sadece maker komisyonu odenir."
)
pdf.ln(2)

# 6. WAVE CYCLE
pdf.section_title("6. Dalga Dongusu")
pdf.body_text("Bir trend icinde su dongu tekrarlanir:")
pdf.code_block(
    "1. PMax LONG sinyal => Market giris @ 70000\n"
    "2. Fiyat KC Lower a duser => DCA limit buy @ 69800 (1. DCA)\n"
    "3. Fiyat KC Lower a duser => DCA limit buy @ 69750 (2. DCA - max)\n"
    "4. Fiyat KC Upper a cikar => TP limit sell @ 70100 (%20 kapatir)\n"
    "5. Fiyat KC Upper a cikar => TP limit sell @ 70150 (%20 kapatir)\n"
    "6. Dalga biter, DCA sayaci sifirlanir\n"
    "7. Yeni dalga baslar: tekrar 2 DCA + TP dongusu\n"
    "...\n"
    "N. PMax SHORT sinyal => KILL SWITCH: hepsini kapat, SHORT ac"
)
pdf.ln(2)

# 7. FILTERS
pdf.section_title("7. Sinyal Filtreleri")
pdf.sub_title("EMA Trend Filtresi (period=144):")
pdf.bullet("LONG engellenir: Close < EMA(144)")
pdf.bullet("SHORT engellenir: Close > EMA(144)")
pdf.sub_title("RSI Filtresi (period=28):")
pdf.bullet("LONG engellenir: RSI > 65 VE RSI > EMA(RSI, 10)")
pdf.bullet("SHORT engellenir: RSI < 35 VE RSI < EMA(RSI, 10)")
pdf.sub_title("Not:")
pdf.body_text("Filtreler sadece canli taramada uygulanir. Backfill sirasinda uygulanmaz.")
pdf.ln(2)

# 8. CONFIG
pdf.section_title("8. Yapilandirma")
pdf.code_block(
    "Borsa: Binance USDT-M Futures\n"
    "Timeframe: 3m\n"
    "Kaldirac: 10x (ayarlanabilir)\n"
    "Margin/trade: 100 USDT (ayarlanabilir)\n"
    "Max DCA: 2 (dalga basina)\n"
    "TP boyutu: %20 (pozisyonun)\n"
    "Hedge mode: Kapali\n"
    "Max drawdown: %40\n"
    "Max open positions: 10"
)
pdf.ln(2)

# 9. ARCHITECTURE
pdf.section_title("9. Teknik Mimari")
pdf.code_block(
    "core/strategy/indicators.py  -> PMax + Keltner Channel hesaplama\n"
    "core/strategy/signals.py     -> PMax crossover sinyal algilama\n"
    "core/strategy/risk_manager.py-> Keltner DCA/TP state yonetimi\n"
    "core/engine/simulator.py     -> Dry-run simulasyon motoru\n"
    "core/engine/backtester.py    -> Gecmis veri backtest motoru\n"
    "core/engine/live_executor.py -> Canli Binance islem motoru\n"
    "backend/server.py            -> FastAPI REST API\n"
    "frontend/                    -> React + Vite dashboard\n"
    "optimize.py                  -> Optuna parametre optimizasyonu"
)
pdf.sub_title("Veri Akisi:")
pdf.code_block(
    "Binance WS (3m kline)\n"
    "    |\n"
    "    v\n"
    "PMax crossover algilama\n"
    "    |\n"
    "    v\n"
    "Keltner Channel bant hesaplama\n"
    "    |\n"
    "    v\n"
    "DCA limit @ KC Lower | TP limit @ KC Upper\n"
    "    |\n"
    "    v\n"
    "Simulator / LiveExecutor (pozisyon yonetimi)\n"
    "    |\n"
    "    v\n"
    "FastAPI -> React Dashboard (canli PnL + grafik)"
)
pdf.ln(2)

# 10. DRY-RUN
pdf.section_title("10. Dry-Run Akisi")
pdf.bullet("1. PMax backfill ile son crossover bulunur")
pdf.bullet("2. Pozisyon ACIK olarak simulatore yuklenir")
pdf.bullet("3. Entry den bu ana kadar tum mumlar replay edilir")
pdf.bullet("4. Her mumda Keltner DCA/TP kontrol edilir")
pdf.bullet("5. Fill olan DCA/TP ler islem gecmisine kaydedilir")
pdf.bullet("6. WS baglantisi kurulur, canli fiyat akisi baslar")
pdf.bullet("7. Her 3m mum kapanisinda yeni Keltner degerleri hesaplanir")
pdf.bullet("8. Limit emirler yeni bant degerlerine tasinir")

# Save
output_path = "PMax_Keltner_Strategy.pdf"
pdf.output(output_path)
print(f"PDF created: {output_path}")
