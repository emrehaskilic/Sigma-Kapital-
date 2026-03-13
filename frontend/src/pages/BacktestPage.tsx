import { useState, useEffect, useRef } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, BarChart, Bar, ReferenceLine, Cell,
} from "recharts";
import {
  fetchSymbols, fetchConfig, runBacktest,
  fetchBacktestStatus, fetchBacktestResults, resetBacktest,
} from "../api";
import type { Config, BacktestResult, BacktestTrade } from "../types";
import { MetricTile } from "../components/MetricTile";
import { formatNum, pnlColor } from "../utils";

/* ── date formatters ── */
const fmtDate = (ts: number) => {
  const d = new Date(ts);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}/${dd} ${hh}:${mi}`;
};
const fmtDateTime = (ts: number) => {
  const d = new Date(ts);
  const yy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${yy}-${mm}-${dd} ${hh}:${mi}`;
};

export default function BacktestPage() {
  /* ── state ── */
  const [allSymbols, setAllSymbols] = useState<string[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [config, setConfig] = useState<Config | null>(null);
  const [lookbackDays, setLookbackDays] = useState(30);

  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [btStatus, setBtStatus] = useState("idle");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tradeFilter, setTradeFilter] = useState<string>("ALL");

  const pollRef = useRef<number | null>(null);

  /* ── init ── */
  useEffect(() => {
    fetchSymbols().then((d) => setAllSymbols(d.symbols));
    fetchConfig().then((c) => setConfig(c));
  }, []);

  /* ── poll backtest progress ── */
  useEffect(() => {
    if (!running) return;
    const poll = async () => {
      try {
        const s = await fetchBacktestStatus();
        setProgress(s.progress);
        setBtStatus(s.status);
        if (!s.running) {
          setRunning(false);
          if (s.error) {
            setError(s.error);
          } else {
            const r = await fetchBacktestResults();
            if (r.metrics) setResult(r);
          }
        }
      } catch { /* ignore */ }
    };
    poll();
    pollRef.current = window.setInterval(poll, 1000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [running]);

  /* ── handlers ── */
  const handleAddPair = (sym: string) => {
    if (selectedSymbols.length >= 50) return;
    setSelectedSymbols((prev) => [...prev, sym]);
    setSearchQuery("");
  };
  const handleRemovePair = (sym: string) => {
    setSelectedSymbols((prev) => prev.filter((s) => s !== sym));
  };
  const handleConfigChange = (section: string, key: string, value: number | string | boolean) => {
    if (!config) return;
    setConfig({ ...config, [section]: { ...(config as any)[section], [key]: value } });
  };
  const handleRun = async () => {
    if (selectedSymbols.length === 0 || !config) return;
    setRunning(true);
    setProgress(0);
    setResult(null);
    setError(null);
    setBtStatus("starting");
    setTradeFilter("ALL");
    await runBacktest(selectedSymbols, lookbackDays, config);
  };

  const handleReset = async () => {
    await resetBacktest();
    setResult(null);
    setError(null);
    setProgress(0);
    setBtStatus("idle");
    setTradeFilter("ALL");
  };

  const filteredSymbols = allSymbols
    .filter((s) => !selectedSymbols.includes(s))
    .filter((s) => s.toLowerCase().includes(searchQuery.toLowerCase()));

  const m = result?.metrics;

  /* ── custom tooltip ── */
  const ChartTooltip = ({ active, payload, label }: any) => {
    if (!active || !payload?.length) return null;
    return (
      <div className="bg-[#0b1217] border border-slate-700/30 rounded-lg px-3 py-2 text-xs">
        <p className="text-slate-400 mb-1">{fmtDate(label)}</p>
        {payload.map((p: any) => (
          <p key={p.dataKey} style={{ color: p.color }}>
            {p.name}: {formatNum(p.value, 2)}
            {p.dataKey.includes("pct") ? "%" : p.dataKey === "equity" ? " USDT" : ""}
          </p>
        ))}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-[#0b1217] text-slate-200 p-4 md:p-6">
      <div className="max-w-7xl mx-auto space-y-5">

        {/* ── Header ── */}
        <div>
          <h1 className="text-xl font-bold text-slate-100">Backtest</h1>
          <span className="text-[11px] text-slate-500">Historical simulation using the same signal + risk engine</span>
        </div>

        {/* ── Config Panel ── */}
        {config && (
          <div className="bg-[#131d2a]/80 rounded-xl border border-slate-700/20 p-4 space-y-4">
            {/* Trading */}
            <div>
              <h2 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Trading</h2>
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 text-xs">
                {[
                  { section: "trading", key: "initial_balance", label: "Ana Kasa (USDT)", step: "10" },
                  { section: "trading", key: "margin_per_trade", label: "Margin/Trade (USDT)", step: "10" },
                  { section: "trading", key: "leverage", label: "Kaldirac", step: "1" },
                ].map(({ section, key, label, step }) => (
                  <label key={key} className="space-y-1">
                    <span className="text-slate-500 text-[10px] uppercase">{label}</span>
                    <input type="number" step={step}
                      value={(config as any)[section][key]}
                      onChange={(e) => handleConfigChange(section, key, +e.target.value)}
                      className="w-full bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-sm font-mono text-slate-200" />
                  </label>
                ))}
                <label className="space-y-1">
                  <span className="text-slate-500 text-[10px] uppercase">Islem Tipi</span>
                  <select value={config.trading.trade_type}
                    onChange={(e) => handleConfigChange("trading", "trade_type", e.target.value)}
                    className="w-full bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-sm text-slate-200">
                    {["BOTH", "LONG", "SHORT"].map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </label>
              </div>
            </div>

            {/* Strategy */}
            <div>
              <h2 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Strateji</h2>
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 text-xs">
                <label className="space-y-1">
                  <span className="text-slate-500 text-[10px] uppercase">Timeframe</span>
                  <select value={config.strategy.timeframe}
                    onChange={(e) => handleConfigChange("strategy", "timeframe", e.target.value)}
                    className="w-full bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-sm text-slate-200">
                    {["1m", "3m", "5m", "15m", "30m", "1h", "4h"].map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-slate-500 text-[10px] uppercase">MA Type</span>
                  <select value={config.strategy.ma_type}
                    onChange={(e) => handleConfigChange("strategy", "ma_type", e.target.value)}
                    className="w-full bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-sm text-slate-200">
                    {["ALMA", "TEMA", "HullMA"].map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-slate-500 text-[10px] uppercase">MA Period</span>
                  <input type="number" step="1" min="1"
                    value={config.strategy.ma_period}
                    onChange={(e) => handleConfigChange("strategy", "ma_period", +e.target.value)}
                    className="w-full bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-sm font-mono text-slate-200" />
                </label>
                <label className="space-y-1">
                  <span className="text-slate-500 text-[10px] uppercase">Multiplier</span>
                  <input type="number" step="1" min="1"
                    value={config.strategy.alternate_multiplier}
                    onChange={(e) => handleConfigChange("strategy", "alternate_multiplier", +e.target.value)}
                    className="w-full bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-sm font-mono text-slate-200" />
                </label>
                <label className="space-y-1">
                  <span className="text-slate-500 text-[10px] uppercase">ALMA Sigma</span>
                  <input type="number" step="1" min="0"
                    value={config.strategy.alma_sigma}
                    onChange={(e) => handleConfigChange("strategy", "alma_sigma", +e.target.value)}
                    className="w-full bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-sm font-mono text-slate-200" />
                </label>
                <label className="space-y-1">
                  <span className="text-slate-500 text-[10px] uppercase">ALMA Offset</span>
                  <input type="number" step="0.01" min="0"
                    value={config.strategy.alma_offset}
                    onChange={(e) => handleConfigChange("strategy", "alma_offset", +e.target.value)}
                    className="w-full bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-sm font-mono text-slate-200" />
                </label>
              </div>
            </div>

            {/* Risk */}
            <div>
              <h2 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2">Risk Management</h2>
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 text-xs">
                {[
                  { section: "risk", key: "tp1_level", label: "Level TP1 %", step: "0.1" },
                  { section: "risk", key: "tp1_qty", label: "Qty TP1 %", step: "1" },
                  { section: "risk", key: "tp2_level", label: "Level TP2 %", step: "0.1" },
                  { section: "risk", key: "tp2_qty", label: "Qty TP2 %", step: "1" },
                  { section: "risk", key: "tp3_level", label: "Level TP3 %", step: "0.1" },
                  { section: "risk", key: "tp3_qty", label: "Qty TP3 %", step: "1" },
                  { section: "risk", key: "stop_loss", label: "Stop Loss %", step: "0.1" },
                ].map(({ section, key, label, step }) => (
                  <label key={key} className="space-y-1">
                    <span className="text-slate-500 text-[10px] uppercase">{label}</span>
                    <input type="number" step={step}
                      value={(config as any)[section][key]}
                      onChange={(e) => handleConfigChange(section, key, +e.target.value)}
                      className="w-full bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-sm font-mono text-slate-200" />
                  </label>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ── Pair Selection + Controls ── */}
        <div className="bg-[#131d2a]/80 rounded-xl border border-slate-700/20 p-4">
          <div className="flex items-center gap-3 mb-3 flex-wrap">
            <h2 className="text-sm font-semibold text-slate-300">Pair Secimi</h2>
            <span className="text-[10px] text-slate-500">{selectedSymbols.length}/50</span>
            <div className="flex-1" />
            <label className="flex items-center gap-2 text-xs">
              <span className="text-slate-400">Lookback</span>
              <input type="number" min={1} max={365} step={1}
                value={lookbackDays}
                onChange={(e) => setLookbackDays(+e.target.value)}
                className="w-16 bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-sm font-mono text-slate-200" />
              <span className="text-slate-500">gun</span>
            </label>
            {result && !running && (
              <button onClick={handleReset}
                className="px-4 py-1.5 rounded-lg text-xs font-semibold bg-slate-600/15 text-slate-300 border border-slate-600/25 hover:bg-red-500/15 hover:text-red-400 hover:border-red-500/25 transition-colors">
                Sifirla
              </button>
            )}
            <button onClick={handleRun}
              disabled={selectedSymbols.length === 0 || running || !!result}
              className="px-4 py-1.5 rounded-lg text-xs font-semibold bg-sky-500/15 text-sky-400 border border-sky-500/25 hover:bg-sky-500/25 transition-colors disabled:opacity-30 disabled:cursor-not-allowed">
              {running ? "Calisiyor..." : "Backtest Baslat"}
            </button>
          </div>

          <div className="flex gap-3 mb-3">
            <div className="relative flex-1 max-w-xs">
              <input type="text" value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Pair ara... (BTCUSDT)"
                className="w-full bg-[#0b1217] border border-slate-700/30 rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-sky-500/50" />
              {searchQuery && filteredSymbols.length > 0 && (
                <div className="absolute z-10 top-full left-0 right-0 mt-1 bg-[#0b1217] border border-slate-700/30 rounded-lg max-h-48 overflow-y-auto">
                  {filteredSymbols.slice(0, 20).map((s) => (
                    <button key={s} onClick={() => handleAddPair(s)}
                      className="w-full text-left px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700/20 transition-colors">
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {selectedSymbols.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {selectedSymbols.map((s) => (
                <button key={s} onClick={() => handleRemovePair(s)}
                  className="flex items-center gap-1 px-2 py-1 rounded bg-slate-700/20 text-xs text-slate-300 hover:bg-red-500/20 hover:text-red-400 transition-colors">
                  {s} <span className="text-slate-500 hover:text-red-400">&times;</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* ── Progress Bar ── */}
        {running && (
          <div className="bg-[#131d2a]/80 rounded-xl border border-slate-700/20 p-4">
            <div className="flex items-center justify-between text-xs mb-2">
              <span className="text-slate-400">{btStatus === "fetching" ? "Veri indiriliyor..." : btStatus === "computing" ? "Sinyaller hesaplaniyor..." : btStatus === "simulating" ? "Simulasyon calisiyor..." : "Metrikler hesaplaniyor..."}</span>
              <span className="text-slate-500 font-mono">{progress.toFixed(0)}%</span>
            </div>
            <div className="w-full h-2 bg-slate-700/40 rounded-full overflow-hidden">
              <div className="h-full bg-sky-500 transition-all duration-300 rounded"
                style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}

        {/* ── Error ── */}
        {error && (
          <div className="bg-red-500/10 border border-red-500/25 rounded-xl p-3 text-xs text-red-400">
            Backtest hatasi: {error}
          </div>
        )}

        {/* ══ RESULTS ══ */}
        {m && (
          <>
            {/* ── Summary Metrics ── */}
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
              <MetricTile label="Total PnL" value={`${formatNum(m.total_pnl, 2, true)} USDT`} color={pnlColor(m.total_pnl)} />
              <MetricTile label="Total PnL %" value={`${formatNum(m.total_pnl_pct, 2, true)}%`} color={pnlColor(m.total_pnl_pct)} />
              <MetricTile label="Net (- Fees)" value={`${formatNum(m.total_pnl - m.total_fees, 2, true)} USDT`} color={pnlColor(m.total_pnl - m.total_fees)} />
              <MetricTile label="Bakiye" value={`${formatNum(m.current_balance, 2)} USDT`} color="text-sky-400" />
              <MetricTile label="Max Drawdown" value={`${formatNum(m.max_drawdown_pct, 2)}%`} color="text-red-400" />
              <MetricTile label="Profit Factor" value={formatNum(m.profit_factor, 2)} color={m.profit_factor >= 1 ? "text-emerald-400" : "text-red-400"} />
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
              <MetricTile label="Toplam Islem" value={m.total_trades} />
              <MetricTile label="Kazanan" value={m.winning_trades} color="text-emerald-400" />
              <MetricTile label="Kaybeden" value={m.losing_trades} color="text-red-400" />
              <MetricTile label="Win Rate" value={`${formatNum(m.win_rate, 1)}%`} color={m.win_rate >= 50 ? "text-emerald-400" : "text-red-400"} />
              <MetricTile label="Avg Win" value={`${formatNum(m.avg_win, 2)} USDT`} color="text-emerald-400" />
              <MetricTile label="Avg Loss" value={`${formatNum(m.avg_loss, 2)} USDT`} color="text-red-400" />
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
              <MetricTile label="Gross Profit" value={`${formatNum(m.gross_profit, 2)} USDT`} color="text-emerald-400" />
              <MetricTile label="Gross Loss" value={`${formatNum(m.gross_loss, 2)} USDT`} color="text-red-400" />
              <MetricTile label="Max Run-up" value={`${formatNum(m.max_runup_pct, 2)}%`} color="text-emerald-400" />
              <MetricTile label="Max DD (USDT)" value={`${formatNum(m.max_drawdown_usdt, 2)} USDT`} color="text-red-400" />
              <MetricTile label="Sharpe Ratio" value={formatNum(m.sharpe_ratio, 3)} color={m.sharpe_ratio >= 0 ? "text-emerald-400" : "text-red-400"} />
              <MetricTile label="Toplam Fee" value={`${formatNum(m.total_fees, 2)} USDT`} color="text-slate-400" />
            </div>

            {/* ── Per-Symbol Metrics ── */}
            {result!.per_symbol && result!.per_symbol.length > 0 && (
              <div className="bg-[#131d2a]/80 rounded-xl border border-slate-700/20 p-4">
                <h2 className="text-sm font-semibold text-slate-300 mb-3">Parite Bazinda Sonuclar</h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-[10px] uppercase tracking-wider text-slate-500 border-b border-slate-700/20">
                        <th className="text-left py-2 px-2">Symbol</th>
                        <th className="text-right py-2 px-2">Islem</th>
                        <th className="text-right py-2 px-2">Kazanan</th>
                        <th className="text-right py-2 px-2">Kaybeden</th>
                        <th className="text-right py-2 px-2">Win Rate</th>
                        <th className="text-right py-2 px-2">PnL (USDT)</th>
                        <th className="text-right py-2 px-2">Fees</th>
                        <th className="text-right py-2 px-2">Net PnL</th>
                        <th className="text-right py-2 px-2">PF</th>
                        <th className="text-right py-2 px-2">Avg Win</th>
                        <th className="text-right py-2 px-2">Avg Loss</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result!.per_symbol.map((ps) => (
                        <tr key={ps.symbol} className="border-b border-slate-700/10 hover:bg-slate-700/20/30">
                          <td className="py-1.5 px-2 font-semibold">{ps.symbol}</td>
                          <td className="py-1.5 px-2 text-right">{ps.total_trades}</td>
                          <td className="py-1.5 px-2 text-right text-emerald-400">{ps.winning_trades}</td>
                          <td className="py-1.5 px-2 text-right text-red-400">{ps.losing_trades}</td>
                          <td className={`py-1.5 px-2 text-right ${ps.win_rate >= 50 ? "text-emerald-400" : "text-red-400"}`}>
                            {formatNum(ps.win_rate, 1)}%
                          </td>
                          <td className={`py-1.5 px-2 text-right font-mono ${pnlColor(ps.total_pnl)}`}>
                            {formatNum(ps.total_pnl, 2, true)}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-slate-500">
                            {formatNum(ps.total_fees, 2)}
                          </td>
                          <td className={`py-1.5 px-2 text-right font-mono ${pnlColor(ps.total_pnl - ps.total_fees)}`}>
                            {formatNum(ps.total_pnl - ps.total_fees, 2, true)}
                          </td>
                          <td className={`py-1.5 px-2 text-right ${ps.profit_factor >= 1 ? "text-emerald-400" : "text-red-400"}`}>
                            {formatNum(ps.profit_factor, 2)}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-emerald-400">
                            {formatNum(ps.avg_win, 2)}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-red-400">
                            {formatNum(ps.avg_loss, 2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* ── Equity Curve Chart ── */}
            {result!.equity_curve.length > 0 && (
              <div className="bg-[#131d2a]/80 rounded-xl border border-slate-700/20 p-4">
                <h2 className="text-sm font-semibold text-slate-300 mb-3">Equity Curve</h2>
                <ResponsiveContainer width="100%" height={300}>
                  <AreaChart data={result!.equity_curve}>
                    <defs>
                      <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="time" tickFormatter={fmtDate} tick={{ fontSize: 10, fill: "#64748b" }}
                      interval="preserveStartEnd" minTickGap={60} />
                    <YAxis tick={{ fontSize: 10, fill: "#64748b" }} domain={["auto", "auto"]}
                      tickFormatter={(v: number) => `${v.toFixed(0)}`} />
                    <Tooltip content={<ChartTooltip />} />
                    <Area type="monotone" dataKey="equity" name="Equity"
                      stroke="#10b981" fill="url(#eqGrad)" strokeWidth={1.5} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* ── Drawdown & Run-up Chart ── */}
            {result!.drawdown_curve.length > 0 && (
              <div className="bg-[#131d2a]/80 rounded-xl border border-slate-700/20 p-4">
                <h2 className="text-sm font-semibold text-slate-300 mb-3">Run-up & Drawdown (%)</h2>
                <ResponsiveContainer width="100%" height={250}>
                  <AreaChart data={result!.drawdown_curve}>
                    <defs>
                      <linearGradient id="ruGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#10b981" stopOpacity={0.25} />
                        <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                      </linearGradient>
                      <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#ef4444" stopOpacity={0} />
                        <stop offset="95%" stopColor="#ef4444" stopOpacity={0.25} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="time" tickFormatter={fmtDate} tick={{ fontSize: 10, fill: "#64748b" }}
                      interval="preserveStartEnd" minTickGap={60} />
                    <YAxis tick={{ fontSize: 10, fill: "#64748b" }}
                      tickFormatter={(v: number) => `${v.toFixed(1)}%`} />
                    <Tooltip content={<ChartTooltip />} />
                    <ReferenceLine y={0} stroke="#334155" strokeWidth={1} />
                    <Area type="monotone" dataKey="runup_pct" name="Run-up %"
                      stroke="#10b981" fill="url(#ruGrad)" strokeWidth={1} dot={false} />
                    <Area type="monotone" dataKey="drawdown_pct" name="Drawdown %"
                      stroke="#ef4444" fill="url(#ddGrad)" strokeWidth={1} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* ── Per-Trade PnL Chart ── */}
            {result!.trades.length > 0 && (
              <div className="bg-[#131d2a]/80 rounded-xl border border-slate-700/20 p-4">
                <h2 className="text-sm font-semibold text-slate-300 mb-3">Islem Bazinda PnL (USDT)</h2>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={result!.trades}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="id" tick={{ fontSize: 9, fill: "#64748b" }} />
                    <YAxis tick={{ fontSize: 10, fill: "#64748b" }}
                      tickFormatter={(v: number) => `${v.toFixed(1)}`} />
                    <Tooltip content={({ active, payload }: any) => {
                      if (!active || !payload?.length) return null;
                      const t = payload[0].payload as BacktestTrade;
                      return (
                        <div className="bg-[#0b1217] border border-slate-700/30 rounded-lg px-3 py-2 text-xs">
                          <p className="font-semibold mb-1">{t.symbol} #{t.id}</p>
                          <p className="text-slate-400">{t.side} — {t.exit_reason}</p>
                          <p>Entry: {formatNum(t.entry_price, 4)}</p>
                          <p>Exit: {formatNum(t.exit_price, 4)}</p>
                          <p className={t.pnl_usdt >= 0 ? "text-emerald-400" : "text-red-400"}>
                            PnL: {formatNum(t.pnl_usdt, 4, true)} USDT ({formatNum(t.pnl_pct, 2, true)}%)
                          </p>
                        </div>
                      );
                    }} />
                    <ReferenceLine y={0} stroke="#334155" strokeWidth={1} />
                    <Bar dataKey="pnl_usdt" name="PnL">
                      {result!.trades.map((t, i) => (
                        <Cell key={i} fill={t.pnl_usdt >= 0 ? "#10b981" : "#ef4444"} fillOpacity={0.7} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* ── Trade Table ── */}
            {result!.trades.length > 0 && (() => {
              const uniqueSyms = [...new Set(result!.trades.map(t => t.symbol))].sort();
              const filtered = tradeFilter === "ALL" ? result!.trades : result!.trades.filter(t => t.symbol === tradeFilter);
              return (
              <div className="bg-[#131d2a]/80 rounded-xl border border-slate-700/20 p-4">
                <div className="flex items-center gap-3 mb-3">
                  <h2 className="text-sm font-semibold text-slate-300">Islem Listesi ({filtered.length})</h2>
                  <select value={tradeFilter} onChange={(e) => setTradeFilter(e.target.value)}
                    className="bg-[#0b1217] border border-slate-700/30 rounded-lg px-2 py-1 text-xs text-slate-200">
                    <option value="ALL">Tum Pairler</option>
                    {uniqueSyms.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div className="overflow-x-auto max-h-96 overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-[#131d2a]">
                      <tr className="text-[10px] uppercase tracking-wider text-slate-500 border-b border-slate-700/20">
                        <th className="text-left py-2 px-2">#</th>
                        <th className="text-left py-2 px-2">Symbol</th>
                        <th className="text-center py-2">Side</th>
                        <th className="text-left py-2 px-2">Giris Zamani</th>
                        <th className="text-right py-2 px-2">Entry</th>
                        <th className="text-left py-2 px-2">Cikis Zamani</th>
                        <th className="text-right py-2 px-2">Exit</th>
                        <th className="text-center py-2 px-2">Reason</th>
                        <th className="text-right py-2 px-2">PnL (USDT)</th>
                        <th className="text-right py-2 px-2">PnL %</th>
                        <th className="text-right py-2 px-2">Fee</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.map((t) => (
                        <tr key={t.id} className="border-b border-slate-700/10 hover:bg-slate-700/10">
                          <td className="py-1.5 px-2 text-slate-500">{t.id}</td>
                          <td className="py-1.5 px-2 font-semibold">{t.symbol}</td>
                          <td className="py-1.5 text-center">
                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                              t.side === "LONG" ? "bg-emerald-400/15 text-emerald-400" : "bg-red-400/15 text-red-400"
                            }`}>{t.side}</span>
                          </td>
                          <td className="py-1.5 px-2 text-slate-400 font-mono whitespace-nowrap">
                            {t.entry_time ? fmtDateTime(t.entry_time) : "-"}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono">{formatNum(t.entry_price, 4)}</td>
                          <td className="py-1.5 px-2 text-slate-400 font-mono whitespace-nowrap">
                            {t.exit_time ? fmtDateTime(t.exit_time) : "-"}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono">{formatNum(t.exit_price, 4)}</td>
                          <td className="py-1.5 px-2 text-center">
                            <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                              t.exit_reason.startsWith("TP") ? "bg-emerald-400/10 text-emerald-300" :
                              t.exit_reason === "SL" ? "bg-red-400/10 text-red-300" :
                              "bg-yellow-400/10 text-yellow-300"
                            }`}>{t.exit_reason}</span>
                          </td>
                          <td className={`py-1.5 px-2 text-right font-mono ${pnlColor(t.pnl_usdt)}`}>
                            {formatNum(t.pnl_usdt, 4, true)}
                          </td>
                          <td className={`py-1.5 px-2 text-right font-mono ${pnlColor(t.pnl_pct)}`}>
                            {formatNum(t.pnl_pct, 2, true)}%
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-slate-500">
                            {formatNum(t.fee_usdt, 4)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              );
            })()}

            {/* ── Saat Bazli Analiz (Parite Bazinda) ── */}
            {result!.trades.length > 0 && (() => {
              type HourStat = { hour: number; trades: number; pnl: number; wins: number; losses: number };
              type SymbolHourData = { symbol: string; hours: HourStat[]; bestHour: HourStat; worstHour: HourStat };

              const symbolsForAnalysis = [...new Set(result!.trades.map(t => t.symbol))].sort();
              const analysisData: SymbolHourData[] = symbolsForAnalysis.map(sym => {
                const symTrades = result!.trades.filter(t => t.symbol === sym && t.entry_time > 0);
                const hourMap = new Map<number, { pnl: number; trades: number; wins: number; losses: number }>();

                for (let h = 0; h < 24; h++) {
                  hourMap.set(h, { pnl: 0, trades: 0, wins: 0, losses: 0 });
                }

                for (const t of symTrades) {
                  const h = new Date(t.entry_time).getUTCHours();
                  const stat = hourMap.get(h)!;
                  stat.pnl += t.pnl_usdt;
                  stat.trades += 1;
                  if (t.pnl_usdt > 0) stat.wins += 1;
                  else stat.losses += 1;
                }

                const hours: HourStat[] = [];
                hourMap.forEach((v, h) => {
                  if (v.trades > 0) hours.push({ hour: h, ...v });
                });
                hours.sort((a, b) => b.pnl - a.pnl);

                const bestHour = hours[0] || { hour: 0, trades: 0, pnl: 0, wins: 0, losses: 0 };
                const worstHour = hours[hours.length - 1] || bestHour;

                return { symbol: sym, hours, bestHour, worstHour };
              });

              return (
              <div className="bg-[#131d2a]/80 rounded-xl border border-slate-700/20 p-4">
                <h2 className="text-sm font-semibold text-slate-300 mb-3">Saat Bazli Performans Analizi (UTC)</h2>
                <div className="space-y-4">
                  {analysisData.map(({ symbol, hours, bestHour, worstHour }) => (
                    <div key={symbol} className="bg-[#0b1217]/60 rounded-lg p-3">
                      <div className="flex items-center gap-3 mb-2">
                        <span className="text-sm font-semibold text-slate-200">{symbol}</span>
                        <span className="text-[10px] text-emerald-400 bg-emerald-400/10 px-2 py-0.5 rounded">
                          En Iyi: {String(bestHour.hour).padStart(2, "0")}:00 ({formatNum(bestHour.pnl, 2, true)} USDT, {bestHour.trades} islem)
                        </span>
                        <span className="text-[10px] text-red-400 bg-red-400/10 px-2 py-0.5 rounded">
                          En Kotu: {String(worstHour.hour).padStart(2, "0")}:00 ({formatNum(worstHour.pnl, 2, true)} USDT, {worstHour.trades} islem)
                        </span>
                      </div>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="text-[10px] uppercase tracking-wider text-slate-500 border-b border-slate-700/20">
                              <th className="text-left py-1.5 px-2">Saat (UTC)</th>
                              <th className="text-right py-1.5 px-2">Islem</th>
                              <th className="text-right py-1.5 px-2">Kazanan</th>
                              <th className="text-right py-1.5 px-2">Kaybeden</th>
                              <th className="text-right py-1.5 px-2">Win Rate</th>
                              <th className="text-right py-1.5 px-2">Toplam PnL</th>
                              <th className="text-right py-1.5 px-2">Ort PnL</th>
                            </tr>
                          </thead>
                          <tbody>
                            {hours.map((h) => (
                              <tr key={h.hour} className={`border-b border-slate-700/10 ${
                                h.hour === bestHour.hour ? "bg-emerald-400/5" :
                                h.hour === worstHour.hour ? "bg-red-400/5" : ""
                              }`}>
                                <td className="py-1 px-2 font-mono text-slate-300">
                                  {String(h.hour).padStart(2, "0")}:00 - {String(h.hour).padStart(2, "0")}:59
                                </td>
                                <td className="py-1 px-2 text-right">{h.trades}</td>
                                <td className="py-1 px-2 text-right text-emerald-400">{h.wins}</td>
                                <td className="py-1 px-2 text-right text-red-400">{h.losses}</td>
                                <td className={`py-1 px-2 text-right ${h.trades > 0 && (h.wins / h.trades * 100) >= 50 ? "text-emerald-400" : "text-red-400"}`}>
                                  {h.trades > 0 ? formatNum(h.wins / h.trades * 100, 1) : "0.0"}%
                                </td>
                                <td className={`py-1 px-2 text-right font-mono ${pnlColor(h.pnl)}`}>
                                  {formatNum(h.pnl, 2, true)}
                                </td>
                                <td className={`py-1 px-2 text-right font-mono ${pnlColor(h.pnl / h.trades)}`}>
                                  {formatNum(h.pnl / h.trades, 2, true)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              );
            })()}
          </>
        )}

        <div className="text-center text-[10px] text-slate-600 pb-4">
          Sigma Kapital Trading Technologies & Market Making Services
        </div>
      </div>
    </div>
  );
}
