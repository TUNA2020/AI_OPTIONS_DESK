const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`HTTP ${response.status} ${body}`);
  }
  return response.json();
}

export const api = {
  base: API_BASE,
  health: () => request("/health"),
  getConfig: () => request("/config"),
  getControls: () => request("/controls"),
  updateKillSwitch: (payload) =>
    request("/controls/kill-switch", {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  updateAutoTrading: (payload) =>
    request("/controls/auto-trading", {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  updateQuantGate: (payload) =>
    request("/controls/quant-gate", {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  updateRiskEngine: (payload) =>
    request("/controls/risk-engine", {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  updateTradingMode: (payload) =>
    request("/controls/mode", {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  emergencyExit: (payload) =>
    request("/controls/emergency-exit", {
      method: "POST",
      body: JSON.stringify(payload || {})
    }),
  deployStrategy: () =>
    request("/strategy/deploy", {
      method: "POST"
    }),
  getMarketLatest: () => request("/market/latest"),
  getStrategyStatus: () => request("/strategy/status"),
  getOpenTrades: () => request("/trades/open"),
  getPositionGreeks: () => request("/positions/greeks"),
  getOrderBlotter: (limit = 200) => request(`/orders/blotter?limit=${limit}`),
  getPnlSummary: () => request("/pnl/summary"),
  getOiHeatmap: async () => {
    const data = await request("/oi/heatmap?strikes_each_side=10");
    const rows = Array.isArray(data?.rows) ? data.rows : [];
    return rows.map((row) => ({
      ...row,
      strike: row?.strike ?? row?.strike_block
    }));
  },
  getAuditEvents: (limit = 80) => request(`/audit/events?limit=${limit}`),
  getStrategyPayoff: () => request("/strategy/payoff")
};

export function wsUrl(path) {
  const base = API_BASE.replace("http://", "ws://").replace("https://", "wss://");
  return `${base}${path}`;
}
