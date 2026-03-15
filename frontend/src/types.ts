export interface Position {
  symbol: string;
  side: "LONG" | "SHORT";
  entry_price: number;
  mark_price: number;
  bid: number;
  ask: number;
  spread: number;
  break_even: number;
  notional_usdt: number;
  condition: number;
  remaining_qty: number;
  unrealized_pnl_usdt: number;
  unrealized_pnl_pct: number;
  realized_pnl_usdt: number;
  total_pnl_usdt: number;
  fees_usdt: number;
}

export interface PairSummary {
  last_price: number;
  bid: number;
  ask: number;
  spread: number;
  status: string;
  trend: string;
  side: string;
  rsi: number;
  unrealized_pnl: number;
  realized_pnl: number;
  total_pnl: number;
  fees: number;
  trade_count: number;
}

export interface FeeBreakdown {
  maker: number;
  taker: number;
  total: number;
}

export interface Totals {
  unrealized_pnl: number;
  realized_pnl: number;
  total_pnl: number;
  total_fees: number;
  net_pnl: number;
}

export interface Stats {
  initial_balance: number;
  current_balance: number;
  total_pnl: number;
  total_pnl_pct: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_fees: number;
  leverage: number;
}

export interface TradeLog {
  id: number;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number;
  exit_reason: string;
  pnl_usdt: number;
  pnl_pct: number;
  fee_usdt: number;
  leverage: number;
}

export interface SignalLog {
  time: string;
  symbol: string;
  side: string;
  price: number;
  rsi: number;
  source: string;
}

export interface StatusResponse {
  bot_running: boolean;
  ws_connected: boolean;
  ws_last_ping: number;
  price_source: string;
  active_symbols: string[];
  stats: Stats;
  positions: Position[];
  pair_summaries: Record<string, PairSummary>;
  fees: FeeBreakdown;
  signal_log: SignalLog[];
  trade_log: TradeLog[];
  totals: Totals;
}

export interface PMaxConfig {
  source: string;
  atr_period: number;
  atr_multiplier: number;
  ma_type: string;
  ma_length: number;
  change_atr: boolean;
  normalize_atr: boolean;
}

export interface TimeframeConfig {
  label: string;
  timeframe: string;
  size_multiplier: number;
  pmax: PMaxConfig;
  filters: {
    ema_trend: { enabled: boolean; period: number };
    rsi: { enabled: boolean; period: number; overbought: number; oversold: number };
    atr_volatility: { enabled: boolean; atr_period: number; min_atr_percentile: number };
  };
}

export interface Config {
  trading: {
    initial_balance: number;
    margin_per_trade: number;
    leverage: number;
    trade_type: string;
    maker_fee: number;
    taker_fee: number;
    max_pairs: number;
    hedge_mode: boolean;
  };
  strategy: {
    timeframes: TimeframeConfig[];
  };
}

// ── Backtest Types ──

export interface BacktestMetrics {
  initial_balance: number;
  current_balance: number;
  total_pnl: number;
  total_pnl_pct: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_fees: number;
  leverage: number;
  profit_factor: number;
  max_drawdown_usdt: number;
  max_drawdown_pct: number;
  max_runup_usdt: number;
  max_runup_pct: number;
  gross_profit: number;
  gross_loss: number;
  avg_win: number;
  avg_loss: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  recovery_factor: number;
  expectancy: number;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  avg_duration_min: number;
  best_trade_pnl: number;
  worst_trade_pnl: number;
  total_symbols: number;
}

export interface EquityPoint {
  time: number;
  equity: number;
}

export interface DrawdownPoint {
  time: number;
  drawdown_pct: number;
  runup_pct: number;
}

export interface BacktestTrade {
  id: number;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number;
  exit_reason: string;
  pnl_usdt: number;
  pnl_pct: number;
  fee_usdt: number;
  leverage: number;
  entry_time: number;
  exit_time: number;
}

export interface PerSymbolMetrics {
  symbol: string;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl: number;
  total_fees: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
}

export interface BacktestResult {
  trades: BacktestTrade[];
  equity_curve: EquityPoint[];
  drawdown_curve: DrawdownPoint[];
  metrics: BacktestMetrics;
  per_symbol: PerSymbolMetrics[];
}
