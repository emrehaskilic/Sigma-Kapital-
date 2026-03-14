const BASE = "http://localhost:8000";

export async function fetchSymbols(): Promise<{ symbols: string[]; count: number }> {
  const res = await fetch(`${BASE}/api/symbols`);
  return res.json();
}

export async function fetchConfig() {
  const res = await fetch(`${BASE}/api/config`);
  return res.json();
}

export async function updateConfig(body: object) {
  const res = await fetch(`${BASE}/api/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export async function startBot(symbols: string[]) {
  const res = await fetch(`${BASE}/api/bot/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols }),
  });
  return res.json();
}

export async function stopBot() {
  const res = await fetch(`${BASE}/api/bot/stop`, { method: "POST" });
  return res.json();
}

export async function fetchStatus() {
  const res = await fetch(`${BASE}/api/status`);
  return res.json();
}

// ── Backtest API ──

export async function runBacktest(symbols: string[], lookbackDays: number, config: object) {
  const res = await fetch(`${BASE}/api/backtest/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols, lookback_days: lookbackDays, config }),
  });
  return res.json();
}

export async function fetchBacktestStatus() {
  const res = await fetch(`${BASE}/api/backtest/status`);
  return res.json();
}

export async function fetchBacktestResults() {
  const res = await fetch(`${BASE}/api/backtest/results`);
  return res.json();
}

export async function resetBacktest() {
  const res = await fetch(`${BASE}/api/backtest/reset`, { method: "POST" });
  return res.json();
}

// ── Live Trading API ──

export async function liveSetKeys(apiKey: string, apiSecret: string, testnet: boolean) {
  const res = await fetch(`${BASE}/api/live/keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret, testnet }),
  });
  return res.json();
}

export async function liveGetBalance() {
  const res = await fetch(`${BASE}/api/live/balance`);
  return res.json();
}

export async function liveGetExchangePositions() {
  const res = await fetch(`${BASE}/api/live/positions`);
  return res.json();
}

export async function liveStart(
  pairConfigs: Record<string, { margin: number; leverage: number }>,
  protection?: { max_drawdown_pct: number; max_total_margin_pct: number; max_open_positions: number },
  strategy?: { use_alternate_signals: boolean; alternate_multiplier: number },
) {
  const res = await fetch(`${BASE}/api/live/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pair_configs: pairConfigs, protection, strategy }),
  });
  return res.json();
}

export async function liveStop() {
  const res = await fetch(`${BASE}/api/live/stop`, { method: "POST" });
  return res.json();
}

export async function liveEmergencyClose() {
  const res = await fetch(`${BASE}/api/live/emergency-close`, { method: "POST" });
  return res.json();
}

export async function liveStatus() {
  const res = await fetch(`${BASE}/api/live/status`);
  return res.json();
}

export async function liveUpdateProtection(settings: {
  max_drawdown_pct?: number;
  max_total_margin_pct?: number;
  max_open_positions?: number;
}) {
  const res = await fetch(`${BASE}/api/live/protection`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  return res.json();
}

export async function liveGetProtection() {
  const res = await fetch(`${BASE}/api/live/protection`);
  return res.json();
}

export async function liveResetCircuitBreaker() {
  const res = await fetch(`${BASE}/api/live/reset-circuit-breaker`, { method: "POST" });
  return res.json();
}
