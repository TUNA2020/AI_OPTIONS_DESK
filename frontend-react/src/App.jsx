import React, { lazy, Suspense, useState, useEffect } from "react";
import { api } from "./api";
import AnimatedNumber from "./components/AnimatedNumber";
import { usePanelEntrance, useMagneticButtons, useHeroReveal } from "./hooks/useGsapEffects";
import "./styles.css";

const DayWisePnlPage = lazy(() => import("./pages/DayWisePnlPage"));
// three.js is heavy — stream the 3D backdrop in after the dashboard paints
const ThreeBackground = lazy(() => import("./components/ThreeBackground"));

// Helper functions (shared from DayWisePnlPage)
function formatNum(value, digits = 2) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toFixed(digits) : "0.00";
}

function formatNumWithSign(value, digits = 2) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0.00";
  const formatted = n.toFixed(digits);
  return n > 0 ? `+${formatted}` : n < 0 ? `${formatted}` : formatted;
}

function pnlColorClass(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "";
  return n > 0 ? "pnl-positive" : n < 0 ? "pnl-negative" : "";
}

function firstNonEmptyText(...values) {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) return text;
  }
  return "";
}

// PayoffChart component (moved from the broken file)
function PayoffChart({ payoff }) {
  const curve = Array.isArray(payoff?.curve) ? payoff.curve : [];
  const [hoveredPoint, setHoveredPoint] = React.useState(null);

  if (curve.length < 2) {
    return <div className="muted">No payoff curve available for the active positions.</div>;
  }

  const width = 760;
  const height = 300;
  const padX = 30;
  const padY = 22;
  const prices = curve.map((point) => Number(point.price || 0));
  const pnls = curve.map((point) => Number(point.pnl || 0));
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const minPnl = Math.min(...pnls);
  const maxPnl = Math.max(...pnls);
  const pnlRange = Math.max(1, maxPnl - minPnl);
  const priceRange = Math.max(1, maxPrice - minPrice);

  const xFor = (price) => padX + ((price - minPrice) / priceRange) * (width - padX * 2);
  const yFor = (pnl) => height - padY - ((pnl - minPnl) / pnlRange) * (height - padY * 2);
  const points = curve.map((point) => `${xFor(Number(point.price || 0))},${yFor(Number(point.pnl || 0))}`);
  const zeroY = yFor(0);
  const spot = Number(payoff?.spot || 0);
  const spotX = Number.isFinite(spot) && spot > 0 ? xFor(spot) : null;
  const breakEvens = [];
  for (let i = 1; i < curve.length; i += 1) {
    const prev = curve[i - 1];
    const current = curve[i];
    const prevPnl = Number(prev.pnl || 0);
    const currPnl = Number(current.pnl || 0);
    if (prevPnl === 0) {
      breakEvens.push(Number(prev.price || 0));
      continue;
    }
    if (prevPnl === 0 || currPnl === 0 || prevPnl * currPnl < 0) {
      const prevPrice = Number(prev.price || 0);
      const currPrice = Number(current.price || 0);
      const slope = currPnl - prevPnl;
      const ratio = slope !== 0 ? (0 - prevPnl) / slope : 0;
      breakEvens.push(prevPrice + (currPrice - prevPrice) * Math.max(0, Math.min(1, ratio)));
    }
  }
  const beLabels = breakEvens.slice(0, 2).map((value) => Number(value).toFixed(0));
  const profitZone = maxPnl > 0 ? "Profit zone" : "Neutral";
  const riskZone = minPnl < 0 ? "Risk zone" : "Floor protected";
  const fillPath = `M ${padX} ${zeroY} L ${points.join(" L ")} L ${width - padX} ${zeroY} Z`;
  const spotPnl = interpolatePnl(curve, spot);

  return (
    <div className="payoffCard tilt-card gsap-panel">
      <div className="payoffMeta">
        <div>
          <div className="payoffLabel">Strategy</div>
          <div className="payoffValue">{payoff?.strategy || "Open positions"}</div>
        </div>
        <div>
          <div className="payoffLabel">Spot</div>
          <div className="payoffValue">{Number.isFinite(spot) && spot > 0 ? spot.toFixed(2) : "-"}</div>
        </div>
        <div>
          <div className="payoffLabel">Max Profit</div>
          <div className="payoffValue">{formatNum(maxPnl)}</div>
        </div>
        <div>
          <div className="payoffLabel">Max Loss</div>
          <div className="payoffValue">{formatNum(minPnl)}</div>
        </div>
      </div>
      <svg
        className="payoffSvg"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Strategy payoff chart"
      >
        <defs>
          <linearGradient id="payoffLine" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#0f766e" />
            <stop offset="100%" stopColor="#2563eb" />
          </linearGradient>
          <linearGradient id="payoffFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(20,184,166,0.18)" />
            <stop offset="100%" stopColor="rgba(251,113,133,0.14)" />
          </linearGradient>
        </defs>
        <path d={fillPath} className="payoffFill" />
        <line x1={padX} y1={zeroY} x2={width - padX} y2={zeroY} className="payoffZeroLine" />
        {breakEvens.map((be) => {
          const x = xFor(be);
          return <line key={`be-${be}`} x1={x} y1={padY} x2={x} y2={height - padY} className="payoffBreakEvenLine" />;
        })}
        {spotX !== null ? (
          <g>
            <line x1={spotX} y1={padY} x2={spotX} y2={height - padY} className="payoffSpotLine" />
            <circle
              cx={spotX}
              cy={yFor(spotPnl)}
              r="5"
              className="payoffSpotDot"
              onMouseEnter={() => setHoveredPoint({ price: spot, pnl: spotPnl, x: spotX, y: yFor(spotPnl) })}
              onMouseLeave={() => setHoveredPoint(null)}
            />
            <text x={spotX + 6} y={padY + 14} className="payoffSpotLabel">Spot</text>
          </g>
        ) : null}
        <polyline points={points.join(" ")} className="payoffCurve" />
        {curve.map((point, idx) => {
          const x = xFor(Number(point.price || 0));
          const y = yFor(Number(point.pnl || 0));
          return (
            <circle
              key={`${point.price}-${idx}`}
              cx={x}
              cy={y}
              r="3"
              className="payoffPoint"
              onMouseEnter={() => setHoveredPoint({ price: point.price, pnl: point.pnl, x, y })}
              onMouseLeave={() => setHoveredPoint(null)}
            />
          );
        })}
      </svg>
      {hoveredPoint && (
        <div
          className="payoffTooltip"
          style={{
            left: hoveredPoint.x + 12,
            top: hoveredPoint.y - 40
          }}
        >
          <span className="tooltip-price">Price: {Number(hoveredPoint.price).toLocaleString()}</span>
          <span className={`tooltip-pnl ${pnlColorClass(hoveredPoint.pnl)}`}>
            P&L: {formatNumWithSign(hoveredPoint.pnl)}
          </span>
        </div>
      )}
      <div className="payoffFooter">
        <span>{profitZone}</span>
        <span>{riskZone}</span>
        <span>{beLabels.length ? `Break-evens: ${beLabels.join(", ")}` : "Break-even not found"}</span>
      </div>
    </div>
  );
}

// Skeleton loader component
function Skeleton({ height = "20px", width = "100%", style = {} }) {
  return (
    <div
      className="skeleton-loader"
      style={{
        height,
        width,
        ...style
      }}
    />
  );
}

function interpolatePnl(curve, price) {
  if (!curve || curve.length < 2) return 0;
  const sorted = [...curve].sort((a, b) => a.price - b.price);
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i].price >= price) {
      const prev = sorted[i - 1];
      const curr = sorted[i];
      const ratio = (price - prev.price) / (curr.price - prev.price);
      return prev.pnl + ratio * (curr.pnl - prev.pnl);
    }
  }
  return sorted[sorted.length - 1]?.pnl || 0;
}

// Empty state component
function EmptyState({ icon, message, submessage }) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon}</div>
      <p>{message}</p>
      {submessage && <small>{submessage}</small>}
    </div>
  );
}

// Status indicator component
function StatusIndicator({ status, label }) {
  const statusClass = status ? `status-${status}` : "status-unknown";
  return (
    <div className={`status-indicator ${statusClass}`}>
      <div className="status-dot"></div>
      <span>{label || status || "Unknown"}</span>
    </div>
  );
}

// Header Controls Component (compact horizontal layout)
function HeaderControls({ controls, loadingStates, onUpdate }) {
  if (loadingStates.controls) {
    return (
      <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
        {[1, 2, 3, 4, 5].map(i => (
          <Skeleton key={i} height="32px" width="80px" />
        ))}
      </div>
    );
  }

  if (!controls) {
    return (
      <div style={{ color: "var(--danger)", fontSize: "0.9rem" }}>
        Controls unavailable
      </div>
    );
  }

  const ModeButton = ({ active, onClick, children, title }) => (
    <button
      className={`magnetic-btn ${active ? 'btnPrimary' : 'btnGhost'}`}
      style={{ padding: "6px 12px", fontSize: "0.8rem", minWidth: "60px" }}
      onClick={onClick}
      title={title}
    >
      {children}
    </button>
  );

  const ToggleButton = ({ active, onClick, children, danger = false }) => (
    <button
      className={`magnetic-btn ${active ? (danger ? 'btnDanger' : 'btnPrimary') : 'btnGhost'}`}
      style={{ padding: "6px 12px", fontSize: "0.8rem", minWidth: "80px" }}
      onClick={onClick}
    >
      {children}
    </button>
  );

  return (
    <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
      {/* Trading Mode */}
      <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
        <ModeButton
          active={controls.trading_mode === 'paper'}
          onClick={async () => { await onUpdate(() => api.updateTradingMode({ mode: 'paper' })); }}
          title="Set Paper trading mode"
        >
          Paper
        </ModeButton>
        <ModeButton
          active={controls.trading_mode === 'live'}
          onClick={async () => { await onUpdate(() => api.updateTradingMode({ mode: 'live' })); }}
          title="Set Live trading mode"
        >
          Live
        </ModeButton>
      </div>

      {/* Auto Trading */}
      <ToggleButton
        active={!controls.auto_trading_paused}
        onClick={async () => { await onUpdate(() => api.updateAutoTrading({ enabled: controls.auto_trading_paused })); }}
      >
      {controls.auto_trading_paused ? 'Resume' : 'Auto ON'}
      </ToggleButton>

      <ToggleButton
        active={false}
        onClick={async () => {
          if (window.confirm("Close existing open positions and re-enter a fresh AI-selected trade?")) {
            await onUpdate(() => api.reenterTrade());
          }
        }}
      >
        Re-enter AI
      </ToggleButton>

      {/* Option Buying Kill Switch */}
      <ToggleButton
        active={controls.option_buying_enabled !== false}
        onClick={async () => {
          const currentlyEnabled = controls.option_buying_enabled !== false;
          const nextEnabled = !currentlyEnabled;
          if (!nextEnabled) {
            const closePositions = window.confirm("Disable option buying and close active option-buying positions?");
            await onUpdate(() => api.updateOptionBuying({
              enabled: false,
              close_positions: closePositions
            }));
            return;
          }
          await onUpdate(() => api.updateOptionBuying({
            enabled: true,
            close_positions: false
          }));
        }}
      >
        {(controls.option_buying_enabled !== false) ? 'OptBuy ON' : 'OptBuy OFF'}
      </ToggleButton>

      {/* Kill Switch */}
      <ToggleButton
        active={controls.kill_switch}
        danger
        onClick={async () => {
          if (window.confirm(controls.kill_switch ? 'Disable kill switch?' : 'Enable kill switch? This will STOP ALL trading immediately.')) {
            await onUpdate(() => api.updateKillSwitch({ enabled: !controls.kill_switch }));
          }
        }}
      >
        {controls.kill_switch ? 'KILL ON' : 'Kill Switch'}
      </ToggleButton>
    </div>
  );
}

function App() {
  const [currentPage, setCurrentPage] = useState("dashboard");
  const [pnlData, setPnlData] = useState(null);
  const [marketData, setMarketData] = useState(null);
  const [positions, setPositions] = useState(null);
  const [greeks, setGreeks] = useState(null);
  const [strategyStatus, setStrategyStatus] = useState(null);
  const [payoff, setPayoff] = useState(null);
  const [ blotter, setBlotter] = useState(null);
  const [auditEvents, setAuditEvents] = useState(null);
  const [config, setConfig] = useState(null);
  const [controls, setControls] = useState(null);
  const [llmHealth, setLlmHealth] = useState(null);
  useHeroReveal();
  useMagneticButtons();
  usePanelEntrance([currentPage]);

  const [loadingStates, setLoadingStates] = useState({
    pnl: true,
    market: true,
    positions: true,
    greeks: true,
    strategy: true,
    payoff: true,
    blotter: true,
    audit: true,
    config: true,
    controls: true,
    llmHealth: true
  });

  // Fetch data progressively
  useEffect(() => {
    let mounted = true;

    const setLoadingState = (key, value) => {
      if (mounted) {
        setLoadingStates(prev => ({ ...prev, [key]: value }));
      }
    };

    async function fetchDataWithTimeout(fn, key) {
      let timeoutId = null;
      try {
        const timeoutPromise = new Promise((_, reject) => {
          timeoutId = setTimeout(() => reject(new Error("Timeout")), 5000);
        });
        const data = await Promise.race([
          fn(),
          timeoutPromise
        ]);
        if (timeoutId) clearTimeout(timeoutId);
        if (mounted) {
          // Update data based on key
          switch (key) {
            case 'pnl':
              const totalPnl = (data.realized_total || 0) + (data.unrealized_open || 0);
              setPnlData({ ...data, total_pnl: totalPnl });
              break;
            case 'market': setMarketData(data); break;
            case 'positions': setPositions(data); break;
            case 'greeks': setGreeks(data); break;
            case 'strategy': setStrategyStatus(data); break;
            case 'payoff': setPayoff(data); break;
            case 'blotter': setBlotter(data); break;
            case 'audit': setAuditEvents(data); break;
            case 'config': setConfig(data); break;
            case 'controls': setControls(data); break;
            case 'llmHealth': setLlmHealth(data); break;
          }
          setLoadingState(key, false);
        }
      } catch (err) {
        if (timeoutId) clearTimeout(timeoutId);
        console.warn(`Failed to fetch ${key}:`, err.message);
        setLoadingState(key, false); // Still mark as not loading to show empty state
      }
    }

    async function initialFetch() {
      setLoadingStates(s => ({ ...s, initial: true }));

      // Critical data: fetch in parallel for fastest initial render
      await Promise.allSettled([
        fetchDataWithTimeout(() => api.getPnlSummary(), 'pnl'),
        fetchDataWithTimeout(() => api.getMarketLatest(), 'market'),
        fetchDataWithTimeout(() => api.getOpenTrades(), 'positions'),
        fetchDataWithTimeout(() => api.getStrategyStatus(), 'strategy'),
        fetchDataWithTimeout(() => api.getPositionGreeks(), 'greeks')
      ]);

      // Secondary data: fetch in background (less critical, can arrive later)
      fetchDataWithTimeout(() => api.getStrategyPayoff(), 'payoff');
      fetchDataWithTimeout(() => api.getOrderBlotter(50), 'blotter');
      fetchDataWithTimeout(() => api.getAuditEvents(30), 'audit');
      fetchDataWithTimeout(() => api.getControls(), 'controls');
      fetchDataWithTimeout(() => api.getConfig(), 'config');
      fetchDataWithTimeout(() => api.getLlmHealth(), 'llmHealth');

      if (mounted) {
        setLoadingStates(s => ({ ...s, initial: false }));
      }
    }

    initialFetch();

    // Background refresh every 10 seconds
    const interval = setInterval(() => {
      // In background, we don't update loading states at all
      Promise.allSettled([
        api.getPnlSummary().then(data => {
          const totalPnl = (data.realized_total || 0) + (data.unrealized_open || 0);
          setPnlData({ ...data, total_pnl: totalPnl });
        }),
        api.getMarketLatest().then(setMarketData),
        api.getOpenTrades().then(setPositions),
        api.getStrategyStatus().then(setStrategyStatus),
        api.getPositionGreeks().then(setGreeks),
        api.getStrategyPayoff().then(setPayoff),
        api.getOrderBlotter(50).then(setBlotter),
        api.getAuditEvents(30).then(setAuditEvents),
        api.getConfig().then(setConfig),
        api.getLlmHealth().then(setLlmHealth)
      ]);
    }, 10000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  // Refresh controls after manual updates
  const refreshControls = async () => {
    try {
      const data = await api.getControls();
      setControls(data);
    } catch (err) {
      console.warn('Failed to refresh controls:', err);
    }
  };

  // Execute a control update and refresh
  const executeControlUpdate = async (updateFn) => {
    try {
      console.log('Executing control update...');
      await updateFn();
      console.log('Update complete, refreshing controls...');
      await refreshControls();
      console.log('Controls refreshed');
    } catch (err) {
      console.error('Control update failed:', err);
    }
  };

  // Navigation
  const pageNav = (
    <div className="page-nav">
      <button
        className={`magnetic-btn ${currentPage === "dashboard" ? "btnPrimary" : "btnGhost"}`}
        onClick={() => setCurrentPage("dashboard")}
      >
        Dashboard
      </button>
      <button
        className={`magnetic-btn ${currentPage === "pnl" ? "btnPrimary" : "btnGhost"}`}
        onClick={() => setCurrentPage("pnl")}
      >
        Day-wise P&L
      </button>
    </div>
  );

  // Dashboard view
  const renderDashboard = () => (
    <>
      {/* Top Stats Row */}
      <section className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", marginBottom: "24px" }}>
        <article className="panel tilt-card gsap-panel">
          <div className="section-badge">Portfolio</div>
          <h2>Total P&L</h2>
          {loadingStates.pnl ? (
            <Skeleton height="60px" style={{ marginTop: "8px" }} />
          ) : (
            <div className="stat-value stat-big">
              <AnimatedNumber
                value={pnlData?.total_pnl || 0}
                signed
                className={pnlColorClass(pnlData?.total_pnl || 0)}
              />
            </div>
          )}
          {pnlData?.as_of && !loadingStates.pnl && (
            <small style={{ color: "var(--muted)" }}>As of {new Date(pnlData.as_of).toLocaleTimeString()}</small>
          )}
          {!loadingStates.pnl && pnlData && (
            <div style={{ marginTop: "8px", display: "grid", gap: "4px" }}>
              <small style={{ color: "var(--muted)" }}>
                Realized: <span className={pnlColorClass(pnlData?.realized_total || 0)}>{formatNumWithSign(pnlData?.realized_total || 0)}</span>
              </small>
              <small style={{ color: "var(--muted)" }}>
                Unrealized: <span className={pnlColorClass(pnlData?.unrealized_open || 0)}>{formatNumWithSign(pnlData?.unrealized_open || 0)}</span>
              </small>
            </div>
          )}
        </article>

        <article className="panel tilt-card gsap-panel">
          <div className="section-badge">Market</div>
          <h2>NIFTY 50</h2>
          {loadingStates.market ? (
            <Skeleton height="60px" style={{ marginTop: "8px" }} />
          ) : (
            <>
              <div className="stat-value stat-big">
                {Number.isFinite(Number(marketData?.context?.nifty_price ?? marketData?.tick?.nifty_price ?? marketData?.spot))
                  ? (
                    <AnimatedNumber
                      value={Number(marketData?.context?.nifty_price ?? marketData?.tick?.nifty_price ?? marketData?.spot)}
                    />
                  )
                  : "-"}
              </div>
              <div style={{ marginTop: "8px", display: "grid", gap: "4px" }}>
                {Number.isFinite(Number(marketData?.context?.vwap ?? marketData?.vwap)) && (
                  <small style={{ color: "var(--muted)" }}>
                    VWAP: {Number(marketData?.context?.vwap ?? marketData?.vwap).toFixed(2)}
                  </small>
                )}
                {Number.isFinite(Number(marketData?.context?.vix ?? marketData?.vix)) && Number(marketData?.context?.vix ?? marketData?.vix) > 0 && (
                  <small style={{ color: "var(--muted)" }}>
                    VIX: <span style={{ color: Number(marketData?.context?.vix ?? marketData?.vix) > 20 ? "var(--danger)" : "var(--success)" }}>
                      {Number(marketData?.context?.vix ?? marketData?.vix).toFixed(2)}
                    </span>
                  </small>
                )}
              </div>
            </>
          )}
        </article>

        <article className="panel tilt-card gsap-panel">
          <div className="section-badge">Strategy</div>
          <h2>Status</h2>
          {loadingStates.strategy ? (
            <Skeleton height="40px" width="60%" style={{ marginTop: "8px" }} />
          ) : (
            <div className="stat-value" style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "1.5rem" }}>
              <StatusIndicator
                status={
                  ["executed", "ai_insight_ready"].includes(String(strategyStatus?.stage || strategyStatus?.state || "").toLowerCase())
                    ? "good"
                    : ["risk_rejected", "blocked_kill_switch", "kill_switch", "closed"].includes(String(strategyStatus?.stage || strategyStatus?.state || "").toLowerCase())
                      ? "danger"
                      : ["analyzing"].includes(String(strategyStatus?.stage || strategyStatus?.state || "").toLowerCase())
                        ? "warn"
                        : null
                }
                label={String(strategyStatus?.stage || strategyStatus?.state || "idle")
                  .replace(/_/g, " ")
                  .replace(/\b\w/g, (m) => m.toUpperCase())}
              />
            </div>
          )}
          {!loadingStates.strategy && (
            <>
              <small style={{ color: "var(--muted)", display: "block" }}>
                Strategy: {strategyStatus?.active_strategy || strategyStatus?.latest_decision?.strategy || "None"}
              </small>
              <small style={{ color: "var(--muted)", display: "block", marginTop: "6px" }}>
                {(() => {
                  const reason = firstNonEmptyText(
                    strategyStatus?.decision_reason,
                    strategyStatus?.latest_decision?.reason,
                    strategyStatus?.latest_audit?.payload?.reason,
                    strategyStatus?.latest_audit?.message,
                    ""
                  );
                  if (!reason) return null;
                  // Strip any "LLM unavailable; using deterministic fallback..." prefix
                  const clean = reason.replace(/^LLM unavailable[^.]*\.\s*/i, "").replace(/^using deterministic fallback[^.]*\.\s*/i, "").trim();
                  if (!clean) return null;
                  return clean.length > 120 ? clean.slice(0, 120) + "…" : clean;
                })()}
              </small>
              <small style={{ color: "var(--muted)", display: "block", marginTop: "6px" }}>
                LLM: {firstNonEmptyText(
                  strategyStatus?.llm_health?.last?.last_status,
                  llmHealth?.cached?.last?.last_status,
                  llmHealth?.live?.last?.last_status,
                  "unknown"
                )}
                {" · "}
                {firstNonEmptyText(
                  llmHealth?.active_provider,
                  strategyStatus?.llm_health?.last?.last_provider,
                  llmHealth?.cached?.last?.last_provider,
                  llmHealth?.live?.last?.last_provider,
                  "n/a"
                )}
                {" · "}
                {firstNonEmptyText(
                  llmHealth?.active_model,
                  strategyStatus?.llm_health?.last?.last_model,
                  llmHealth?.cached?.last?.last_model,
                  llmHealth?.live?.last?.last_model,
                  "n/a"
                )}
              </small>
            </>
          )}
        </article>

      </section>

      {/* Main Dashboard Grid */}
      <section className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(600px, 1fr))", gap: "20px" }}>
        {/* Positions */}
        <article className="panel full tilt-card gsap-panel">
          <div className="section-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
            </svg>
            Open Positions
          </div>
          <h2>Active Trades</h2>
          {loadingStates.positions ? (
            <div className="tableWrap">
              <table>
                <thead>
                  <tr><th>Symbol</th><th>LTP</th><th>Side</th><th>Qty</th><th>Entry</th><th>PnL</th><th>Delta</th></tr>
                </thead>
                <tbody>
                  {[1,2,3].map(i => (
                    <tr key={i}>
                      <td><Skeleton height="16px" width="60%" /></td>
                      <td><Skeleton height="16px" width="40%" /></td>
                      <td><Skeleton height="16px" width="45%" /></td>
                      <td><Skeleton height="16px" width="30%" /></td>
                      <td><Skeleton height="16px" width="50%" /></td>
                      <td><Skeleton height="16px" width="60%" /></td>
                      <td><Skeleton height="16px" width="60%" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : positions && positions.length > 0 ? (
            <div className="tableWrap">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>LTP</th>
                    <th>Side</th>
                    <th>Qty</th>
                    <th>Entry</th>
                    <th>PnL</th>
                    <th>Delta</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((pos, idx) => {
                    const greekRow = Array.isArray(greeks?.rows)
                      ? (
                        greeks.rows.find((row) => Number(row?.id) === Number(pos?.id)) ||
                        greeks.rows.find((row) =>
                          String(row?.symbol || "") === String(pos?.symbol || "") &&
                          String(row?.side || "").toUpperCase() === String(pos?.side || "").toUpperCase() &&
                          Number(row?.qty || 0) === Number(pos?.qty || pos?.quantity || 0)
                        )
                      )
                      : null;
                    const qty = Number(pos.quantity ?? pos.qty ?? greekRow?.qty ?? 0);
                    const side = String(pos.side || greekRow?.side || "BUY").toUpperCase();
                    const displayQty = side === "SELL" ? -Math.abs(qty) : Math.abs(qty);
                    const entry = Number(pos.entry_price ?? pos.price ?? 0);
                    // Ensure LTP is valid; if not > 0.5, likely stale/incorrect, use entry as fallback
                    let ltp = Number(pos.ltp ?? entry);
                    if (!Number.isFinite(ltp) || ltp < 0.5) {
                      ltp = entry;
                    }
                    const pnl = Number.isFinite(Number(pos.pnl))
                      ? Number(pos.pnl)
                      : (side === "SELL" ? (entry - ltp) : (ltp - entry)) * (Number.isFinite(qty) ? qty : 0);
                    const deltaVal = Number(pos.delta ?? greekRow?.delta ?? 0);

                    return (
                      <tr key={idx}>
                        <td>{pos.symbol || pos.option_type?.toUpperCase()}</td>
                        <td>{Number.isFinite(ltp) && ltp > 0 ? formatNum(ltp, 2) : "-"}</td>
                        <td className={side === "BUY" ? "pnl-positive" : "pnl-negative"}>{side}</td>
                        <td>{displayQty}</td>
                        <td>{formatNum(entry, 2)}</td>
                        <td className={pnlColorClass(pnl)}>{formatNumWithSign(pnl, 2)}</td>
                        <td>{formatNum(deltaVal, 4)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState message="No open positions" />
          )}
        </article>

        {/* Strategy Payoff */}
        <article className="panel full tilt-card gsap-panel">
          <div className="section-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 3v18h18" />
              <path d="M18.7 8l-5.1 5.2-2.8-2.7L7 14.3" />
            </svg>
            Strategy Payoff
          </div>
          <h2>Current Position Payoff</h2>
          {loadingStates.payoff ? (
            <Skeleton height="300px" style={{ width: "100%" }} />
          ) : payoff ? (
            <div style={{ overflowX: "auto" }}>
              <PayoffChart payoff={payoff} />
            </div>
          ) : (
            <EmptyState message="No strategy payoff data" submessage="Deploy a strategy to see payoff chart" />
          )}
        </article>

        {/* Order Blotter */}
        <article className="panel full tilt-card gsap-panel">
          <div className="section-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
              <polyline points="10 9 9 9 8 9" />
            </svg>
            Order Blotter
          </div>
          <h2>Recent Orders</h2>
          {loadingStates.blotter ? (
            <div className="tableWrap">
              <table>
                <thead>
                  <tr><th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {[1,2,3,4,5].map(i => (
                    <tr key={i}>
                      <td><Skeleton height="14px" /></td>
                      <td><Skeleton height="14px" /></td>
                      <td><Skeleton height="14px" width="50%" /></td>
                      <td><Skeleton height="14px" /></td>
                      <td><Skeleton height="14px" /></td>
                      <td><Skeleton height="14px" width="60%" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : blotter && blotter.length > 0 ? (
            <div className="tableWrap">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Qty</th>
                    <th>Price</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {blotter.slice(0, 10).map((order, idx) => (
                    <tr key={idx}>
                      <td style={{ fontSize: "0.85rem" }}>{new Date(order.timestamp).toLocaleTimeString()}</td>
                      <td>{order.symbol}</td>
                      <td className={order.side === "BUY" ? "pnl-positive" : "pnl-negative"}>{order.side}</td>
                      <td>{order.quantity}</td>
                      <td>{formatNum(order.price, 2)}</td>
                      <td>{order.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState message="No recent orders" />
          )}
        </article>

        {/* Audit Log */}
        <article className="panel full tilt-card gsap-panel">
          <div className="section-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Recent Events
          </div>
          <h2>Audit Log</h2>
          {loadingStates.audit ? (
            <ul className="event-list" style={{ listStyle: "none", padding: 0, margin: 0, fontSize: "0.9rem" }}>
              {[1,2,3,4,5].map(i => (
                <li key={i} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between" }}>
                  <Skeleton height="14px" width="60%" />
                  <Skeleton height="14px" width="80px" />
                </li>
              ))}
            </ul>
          ) : auditEvents && auditEvents.length > 0 ? (
            <ul className="event-list" style={{ listStyle: "none", padding: 0, margin: 0, fontSize: "0.88rem" }}>
              {auditEvents.slice(0, 15).map((evt, idx) => {
                const detail = String(
                  evt.message || evt.reason || evt.payload?.reason || evt.payload?.message || ""
                ).replace(/^LLM unavailable[^.]*\.\s*/i, "").trim();
                return (
                  <li key={idx} style={{ padding: "10px 0", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
                    <div>
                      <div style={{ fontWeight: 600, color: "var(--ink)" }}>{evt.event_type || evt.type || "Event"}</div>
                      {detail && (
                        <div style={{ fontSize: "0.78rem", color: "var(--muted)", marginTop: "3px", lineHeight: 1.4 }}>
                          {detail.length > 100 ? detail.slice(0, 100) + "…" : detail}
                        </div>
                      )}
                    </div>
                    <span style={{ color: "var(--muted)", fontSize: "0.78rem", flexShrink: 0, paddingTop: "2px" }}>
                      {new Date(evt.timestamp).toLocaleTimeString()}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState message="No events" />
          )}
        </article>

      </section>
    </>
  );

  return (
    <div className="page">
      <Suspense fallback={null}>
        <ThreeBackground />
      </Suspense>
      <header style={{ marginBottom: "28px", display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "24px", flexWrap: "wrap" }}>
        <div className="masthead" style={{ flex: 1, minWidth: "280px" }}>
          <span className="eyebrow-luxe hero-subtitle">
            <span className="live-dot" aria-hidden="true" />
            NSE Derivatives · Algorithmic Desk
          </span>
          <h1 className="gradient-text hero-title">AI Options Desk</h1>
          <p className="hero-subtitle masthead-sub">Advanced options trading analytics powered by AI</p>
        </div>
        <div className="hero-controls" style={{ flexShrink: 0 }}>
          <HeaderControls
            controls={controls}
            loadingStates={loadingStates}
            onUpdate={executeControlUpdate}
          />
        </div>
      </header>

      {pageNav}

      <div className="gradient-divider"></div>

      {currentPage === "dashboard" ? (
        renderDashboard()
      ) : (
        <Suspense fallback={<div className="panel"><Skeleton height="120px" /></div>}>
          <DayWisePnlPage pnl={pnlData} />
        </Suspense>
      )}
    </div>
  );
}

export default App;
