# Session Memory - AI Options Desk Enhancements

## Date: 2025-03-27

---

## 1. Backend: Enhanced LLM Prompts for Strategy Selection

### File: `ai/llm_reasoner.py`

#### Market Regime Classification Prompt Enhancement

**Before:**
```python
prompt = (
    "You are an options market regime classifier for Indian index options. "
    "Return only JSON with keys: regime, confidence, summary."
)
```

**After:**
- Added detailed role definition: "options market regime classifier for Indian index options (NIFTY/BANKNIFTY)"
- Provided specific analysis guidelines:
  - VIX thresholds (<14 low, 14-18 normal, 18-25 elevated, >25 high)
  - IV skew pattern analysis
  - Technical indicator interpretation
- Defined clear regime types: TRENDING, VOLATILE, RANGE, MIXED
- Added explicit interpretation rules:
  - "Trend + low VIX = trending regime"
  - "High VIX + sharp moves = volatile regime"
  - "Sideways price + low VIX = range regime"
  - "Conflicting indicators = mixed regime"
- Output format: `{regime: string, confidence: float (0.0-1.0), summary: string}`

#### Strategy Selection Prompt Enhancement

**Before:**
```python
prompt = (
    "Select exactly two options strategies from the candidate list. "
    "Return strict JSON with keys: primary, secondary, candidates. "
    "primary and secondary must be objects with keys strategy, confidence, reason, capital_to_use, ce_strike, pe_strike. "
    "The candidates array must contain those two objects in order of preference and each strategy must be one of the candidate keys."
)
```

**After:**
- Expanded system role: "expert options trading strategist"
- Enumerated 5 input sources (market context, regime, candidates, performance, portfolio)
- Defined 8 selection criteria:
  1. Regime alignment
  2. Recent performance (win rates, Sharpe)
  3. Risk-reward profile
  4. Volatility suitability
  5. Strike selection logic
  6. Capital efficiency
  7. Portfolio fit (avoid over-concentration)
  8. Diversification between primary/secondary
- Detailed output format with parameter-based overrides:
  ```json
  {
    "primary": {
      "strategy": "name",
      "confidence": float,
      "reason": "detailed explanation",
      "capital_to_use": float,
      "ce_delta" | "pe_delta" | "width" | "atm_offset_ce" | "atm_offset_pe",  // Preferred method
      "ce_strike" | "pe_strike"  // Legacy fallback
    },
    "secondary": { ... },
    "candidates": [...],
    "rejection_reason": "optional"
  }
  ```
- Added explicit list of available strategies in prompt
- Guidance to prefer param-based controls over explicit strikes

---

## 2. Strategy Parameterization System

### File: `ai/strategy_generator.py`

#### Added `STRATEGY_DEFAULTS` Dictionary

Defines default parameters for each strategy's strike selection:

```python
STRATEGY_DEFAULTS: dict[str, dict[str, Any]] = {
    "short_strangle": {"ce_delta": 0.20, "pe_delta": 0.20},
    "iron_condor": {"width": 200},
    "delta_neutral_condor": {"width": 150},
    "bull_put_spread": {"width": 200},
    "bear_call_spread": {"width": 200},
    "vix_reversion": {"ce_delta": 0.20, "pe_delta": 0.20},
    "oi_wall_strategy": {},
    "gamma_scalping": {},
    "ratio_spread": {},
    "calendar_spread": {},
    "broken_wing_butterfly": {},
    "skew_arbitrage": {},
    "trend_credit_spread": {},
    "expiry_range_trade": {},
    "momentum_volatility": {},
}
```

#### Added `get_strategy_default_params()` Function

Returns a copy of default parameters for a given strategy, allowing AI to override with precision.

#### Modified `build_trade_from_decision()`

**Before:**
```python
def build_trade_from_decision(decision, context):
    strategy = get_strategy(strategy_name)
    legs = strategy.build_trade(context)
    legs = _apply_strike_overrides(legs, decision, context)
    return strategy.name, legs
```

**After:**
```python
def build_trade_from_decision(decision, context):
    strategy = get_strategy(strategy_name)
    params = get_strategy_default_params(strategy_name)
    
    # Apply AI param-based overrides (preferred method)
    override_keys = ["ce_delta", "pe_delta", "width", "atm_offset_ce", "atm_offset_pe",
                     "delta_target_ce", "delta_target_pe", "strike_offset"]
    for key in override_keys:
        if key in decision:
            params[key] = decision[key]
    
    # Build trade with parameters
    legs = strategy.build_trade(context, **params)
    
    # Legacy: Apply explicit strike overrides if provided
    if "ce_strike" in decision or "pe_strike" in decision:
        legs = _apply_strike_overrides(legs, decision, context)
    
    return strategy.name, legs
```

**Result:** AI can now fine-tune strikes using strategy-specific parameters (delta targets, spread widths) instead of crude strike overrides.

---

## 3. Exponential Performance Weighting

### File: `ai/strategy_generator.py`

#### Rewrote `_recent_performance_lookup()`

**Before:** Simple median of last 5 scores
```python
lookup[strategy] = float(median(scores[-5:]))
```

**After:** Exponential weighted average over last 20 days with date awareness:

```python
def _recent_performance_lookup(recent_performance):
    # Group by strategy with timestamps
    grouped: dict[str, list[tuple[float, datetime]]] = {}
    for row in recent_performance:
        strategy = canonical_strategy_name(str(row.get("strategy", "")))
        rank_score = float(row.get("rank_score", 0.0))
        date_val = row.get("date")
        # Parse date to datetime
        row_date = datetime.fromisoformat(date_str) if isinstance(date_val, str) else date_val
        grouped.setdefault(strategy, []).append((rank_score, row_date))
    
    # Exponential weighting: weight = exp(-0.2 * days_ago)
    # Half-life ~3.5 days, more recent performance has higher weight
    for strategy, records in grouped.items():
        records.sort(key=lambda x: x[1], reverse=True)  # Most recent first
        recent = records[:20]
        weighted_sum = 0.0
        weight_sum = 0.0
        for score, dt in recent:
            days_ago = max(0, (today - dt).days)
            weight = math.exp(-decay_lambda * days_ago)
            weighted_sum += score * weight
            weight_sum += weight
        lookup[strategy] = round(weighted_sum / weight_sum, 3) if weight_sum > 0 else 0
    
    return lookup
```

**Result:** Recent performance (yesterday) weighs more than older performance (2 weeks ago), providing more accurate strategy rankings based on current market conditions.

---

## 4. Strategy Class Updates

### All Strategy Files Updated to Accept `**kwargs`

Modified 14 strategy classes to accept optional parameters:

1. **short_strangle.py**: `build_trade(self, context, ce_delta=0.20, pe_delta=0.20, **kwargs)`
2. **iron_condor.py**: `build_trade(self, context, width=200, **kwargs)`
3. **delta_neutral_condor.py**: `build_trade(self, context, width=150, **kwargs)`
4. **bull_put_spread.py**: Uses `width` parameter for spread adjustment
5. **bear_call_spread.py**: Uses `width` parameter for spread adjustment
6. **vix_reversion.py**: Accepts `ce_delta`, `pe_delta` to override VIX-based defaults
7. **gamma_scalping.py**: `build_trade(self, context, **kwargs)`
8. **ratio_spread.py**: `build_trade(self, context, **kwargs)`
9. **calendar_spread.py**: `build_trade(self, context, **kwargs)`
10. **broken_wing_butterfly.py**: `build_trade(self, context, **kwargs)`
11. **skew_arbitrage.py**: `build_trade(self, context, **kwargs)`
12. **oi_wall_strategy.py**: `build_trade(self, context, **kwargs)`
13. **trend_credit_spread.py**: Forwards `**kwargs` to children
14. **expiry_range_trade.py**: Forwards `**kwargs` to ShortStrangle
15. **momentum_volatility.py**: Forwards `**kwargs` to children

**Result:** All strategies now support parameter-based customizations from AI while maintaining backward compatibility with direct `ce_strike`/`pe_strike` overrides.

---

## 5. Frontend: Day-wise P&L Page Separation

### Created: `frontend-react/src/pages/DayWisePnlPage.jsx`

- New standalone page for historical P&L analysis
- Shows two tables:
  1. **Day-wise Performance**: Date, Realized PnL, Unrealized, Net PnL
  2. **Cumulative Curve**: Date, Cumulative PnL, Daily Δ
- Copy-to-clipboard functionality on all numeric values
- Styled with existing UI components (cards, tables, gradients)
- Embedded helper functions (`formatNum`, `formatNumWithSign`, `pnlColorClass`, `copyToClipboard`) to avoid cross-file dependencies

### Updated: `frontend-react/src/App.jsx`

#### State Management
Added `currentPage` state: `'dashboard' | 'pnl'`

#### Navigation UI
```jsx
<div className="page-nav" style={{ marginBottom: '16px', display: 'flex', gap: '12px', alignItems: 'center' }}>
  <button className={`magnetic-btn ${currentPage === 'dashboard' ? 'btnPrimary' : 'btnGhost'}`} onClick={() => setCurrentPage('dashboard')}>
    Dashboard
  </button>
  <button className={`magnetic-btn ${currentPage === 'pnl' ? 'btnPrimary' : 'btnGhost'}`} onClick={() => setCurrentPage('pnl')}>
    Day-wise P&L
  </button>
</div>
```

#### Conditional Rendering
```jsx
{currentPage === 'dashboard' ? (
  <>{/* Full dashboard with positions, Greeks, heatmap, AI insights, blotter, logs */}</>
) : (
  <DayWisePnlPage pnl={pnl} />
)}
```

#### Removed from Dashboard
- Entire "Day-wise PnL" table section (previously under Performance History)
- Replaced with page navigation to dedicated P&L page

### Updated: `frontend-react/src/styles.css`

Added styles for page navigation:
```css
.page-nav {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.page-nav button {
  min-width: 120px;
  justify-content: center;
}
```

---

## 6. Data Flow Summary

### AI Decision Pipeline with New Features:

1. **Market Context Build** (`system_controller.build_market_context()`)
   - Fetches 7 days of 5-min candles (unchanged)
   - Computes technicals (RSI, MACD, Bollinger, ADR, support/resistance, volume percentile)
   - Gathers option chain (40 strikes around ATM)
   - Calculates OI analysis (walls, bias)
   - Estimates IV surface (ATM IV, skew slope, term structure)
   - Predicts regime via `VolatilityRegimeModel`

2. **Deep Thinking Mode** (when enabled)
   - Adds last 10 AI decisions
   - Adds last 20 strategy performance records (now with exponential weighting)
   - Includes portfolio state (Greeks, positions)

3. **Strategy Ranking** (`rank_strategy_candidates()`)
   - Uses regime priors + trend/bias adjustments + VIX filtering
   - Scores: `base_score = (position_weight * 10) + (performance_bias)`
   - Performance bias from exponentially weighted recent performance

4. **LLM Proposal** (`propose_strategies()`)
   - Enhanced prompt with 8 criteria
   - Requests param-based strike selection
   - Returns primary/secondary with confidence, reasons, capital, and strike parameters

5. **Execution**
   - `build_trade_from_decision()` extracts strategy defaults + AI overrides
   - Each strategy builds legs with custom parameters
   - Risk manager evaluates
   - Orders executed

---

## 7. Configuration Notes

### Market Data Settings
- **Candle lookback**: 7 days (`days_back=7`)
- **5-minute candles**: Used for technical indicators and market context
- **Option chain**: 20 strikes each side of ATM (~40 total)

### Risk Parameters
- `max_capital_per_trade`: Used for AI capital allocation suggestions
- `max_loss_pct_per_trade`: Overrides absolute max_loss if >0
- `max_loss_per_trade`: Monte Carlo 5th percentile check

### Dashboard Settings
- `deep_thinking_mode`: Enables portfolio state + historical decisions in AI context
- `auto_optimize`: Day-end strategy ranking optimization
- `quant_gate_enabled`: Toggle quant validation
- `risk_engine_enabled`: Toggle risk manager

---

## 8. Testing Checklist

- [ ] Backend starts: `python run.py`
- [ ] Frontend builds: `cd frontend-react && npm run build`
- [ ] Frontend dev server: `npm run dev`
- [ ] Dashboard visible with navigation
- [ ] "Day-wise P&L" page accessible via button
- [ ] AI strategy selection produces param-based strikes (check logs)
- [ ] Performance rankings update with recent trades
- [ ] Quant scoring respects new parameters
- [ ] Strategy execution uses correct strikes/deltas

---

## 9. Potential Improvements (Future)

1. **Strike Selection**: Add `delta_target` support for all income strategies
2. **Capital Allocation**: Kelly criterion integration based on win rate / payoff
3. **Regime Detection**: Add GARCH volatility forecasts
4. **Performance Filter**: Only consider trades in same regime for ranking
5. **Liquidity Filter**: Exclude low-volume strikes from option chain
6. **Portfolio Greeks**: Actually compute and pass to deep context (currently placeholder)
7. **Prompt Engineering**: Add few-shot examples in system messages
8. **Monitoring**: Add metrics for AI strike parameter distribution

---

## 10. Frontend: Header Trading Controls

### Moved Controls to Header

Trading controls are now displayed in a compact horizontal bar at the top-right of the page, next to the "Jugal's AI Options Desk" header. This provides immediate access to all critical trading switches without scrolling.

### HeaderControls Component

Created a new compact component (`HeaderControls`) with 5 controls in a single horizontal row:

1. **Trading Mode** - Two-button toggle (Paper | Live)
   - Paper: green button (safe mode)
   - Live: red button (production mode)
   - API: `POST /controls/mode`

2. **Quant Gate** - Toggle button
   - "Quant ON" (green) when enabled / "Bypass Quant" (gray) when disabled
   - API: `PUT /controls/quant-gate`

3. **Risk Engine** - Toggle button
   - "Risk ON" (green) when enabled / "Bypass Risk" (gray) when disabled
   - API: `PUT /controls/risk-engine`

4. **Auto Trading** - Toggle button
   - "Auto ON" (green) when running / "Resume" (gray) when paused
   - API: `PUT /controls/auto-trading`

5. **Kill Switch** - Emergency toggle
   - "KILL ON" (red when active) / "Kill Switch" (gray when inactive)
   - Requires confirmation dialog
   - API: `PUT /controls/kill-switch`

### Layout Features

- Horizontal flexbox layout (wraps on small screens)
- Compact buttons with minimal padding (6px 12px)
- Skeleton loader shows 5 placeholder bars during initial load
- Controls are NOT included in background refresh (prevents state flickering)
- Created `executeControlUpdate` helper: executes API update, then calls `refreshControls()`
- Buttons call: `executeControlUpdate(() => api.updateXxx(payload))`
- After successful API call, controls are re-fetched to get authoritative state
- All buttons provide instant visual feedback with proper state updates

### Removed Full Controls Panel

The large grid-based controls panel at the bottom of the dashboard was removed entirely. All functionality is now accessible from the header in a more compact, always-visible format.

---

## 11. Performance Improvements

### Progressive Loading
- Full-page blocking loader replaced with per-section skeleton UI
- Page renders immediately (<500ms)
- Critical data (P&L, market, positions, strategy, Greeks) loads first
- Secondary data (payoff, blotter, audit, heatmap, config, controls) loads in background
- Each section shows shimmer animation while loading

### Background Refresh
- Data refreshes every 10 seconds without visual interruption
- No spinner or flickering during updates
- Individual sections update independently

### Timeouts
- 5-second timeout per API call
- Failed calls show empty state without blocking other sections

---

## 12. Files Modified

| File | Changes | Lines |
|------|---------|-------|
| `ai/llm_reasoner.py` | Enhanced prompts | ~80 |
| `ai/strategy_generator.py` | Defaults, exponential lookup, build_trade override | ~150 |
| `strategies/*.py` | All 15 strategies accept `**kwargs` | ~50 |
| `frontend-react/src/pages/DayWisePnlPage.jsx` | New file | ~120 |
| `frontend-react/src/App.jsx` | Page navigation, skeleton loading, HeaderControls component, removed bottom controls panel | ~250 |
| `frontend-react/src/styles.css` | Page nav, skeleton loader, button variants, spin animation | ~50 |

---

## 13. Build Status

✅ Backend: Python compiles (no syntax errors)
✅ Frontend: `npm run build` succeeds
   - `index.html`: 0.55 kB
   - `index-*.css`: ~31 kB (gzip: ~7 kB)
   - `index-*.js`: ~525 kB (gzip: ~158 kB)

---

**End of Session Memory**
Generated: 2025-03-27
