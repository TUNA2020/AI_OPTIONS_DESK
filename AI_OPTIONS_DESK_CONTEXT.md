# AI Options Desk Context

Last updated: 2026-04-17

This file is a compact reference for the `AI_OPTIONS_DESK` app so future work does not require re-reading the whole codebase.

## What The App Is

`AI_OPTIONS_DESK` is an automated options research and execution system for Indian index options, mainly `NIFTY` and `BANKNIFTY`.

It combines:

- Live market data ingestion from Kite Connect
- AI-assisted strategy selection and parameter tuning
- Risk checks before trade placement
- Paper and live trading modes
- Scheduled trading cycles and forced exits
- Local persistence in SQLite
- FastAPI backend with a React dashboard

## Core Trading Flow

1. Fetch market data, option chain data, and recent candles.
2. Build market context and regime signals.
3. Rank candidate strategies with AI and quant filters.
4. Convert the selected strategy into concrete option legs.
5. Run risk validation before execution.
6. Place orders in live mode or simulate fills in paper mode.
7. Persist trades, decisions, logs, and runtime state in SQLite.
8. Stream updates to the frontend over REST and WebSocket.

## Trading Modes

- `paper` mode:
  - Uses real market quotes for pricing.
  - Does not place broker orders.
  - Stores simulated trades and PnL locally.
- `live` mode:
  - Places and closes broker orders through Kite Connect.
  - Uses actual signed net positions for PnL.

## Strategy System

The app currently supports these strategies:

- `short_strangle`
- `iron_condor`
- `bull_put_spread`
- `bear_call_spread`
- `broken_wing_butterfly`
- `calendar_spread`
- `ratio_spread`
- `gamma_scalping`
- `vix_reversion`
- `oi_wall_strategy`
- `trend_credit_spread`
- `delta_neutral_condor`
- `skew_arbitrage`
- `expiry_range_trade`
- `momentum_volatility`
- `option_buying_vwap_put`

### Strategy behavior

- Strategy names are normalized through aliases, so user input can vary.
- AI can choose between candidate strategies using regime, recent performance, and portfolio context.
- The strategy generator supports parameter-based overrides such as:
  - `ce_delta`
  - `pe_delta`
  - `width`
  - `atm_offset_ce`
  - `atm_offset_pe`
  - `delta_target_ce`
  - `delta_target_pe`
  - `strike_offset`
- Explicit strike overrides still exist as a fallback.
- Recent strategy performance is weighted with exponential decay so newer results matter more than old ones.

## AI Layer

The AI layer is used for:

- Market regime classification
- Candidate strategy ranking
- Decision reasoning and trade construction
- Quant validation support
- Strategy optimization

Notable behavior:

- The regime prompt classifies conditions like `TRENDING`, `VOLATILE`, `RANGE`, and `MIXED`.
- Strategy selection considers:
  - regime alignment
  - recent win rate and Sharpe-like performance
  - risk/reward
  - volatility suitability
  - strike selection
  - capital efficiency
  - portfolio concentration
  - diversification between primary and secondary picks

## Risk Management

The app has a dedicated risk layer that:

- Evaluates trades with Monte Carlo simulation
- Uses a percentile-based loss estimate instead of only worst-case tails
- Rejects trades that exceed the configured loss limit
- Supports capital-based risk calibration

Configuration supports:

- `risk.max_capital_per_trade`
- `risk.max_loss_pct_per_trade`
- `risk.max_loss_per_trade`

If `max_loss_pct_per_trade` is set above zero, it overrides the absolute loss limit and derives a per-trade max loss from capital allocation.

## Exit And Protection Logic

- Profit target and stoploss are enforced by the PnL monitor.
- A forced exit time closes positions automatically near the end of the session.
- A kill switch can block new trades and force-close open positions.
- Emergency exit is available through the API.
- Market-closed sessions skip strategy execution.
- Weekend and holiday handling reuses the last valid session data for analytics.

## Market Data And Analytics

The backend gathers and derives:

- Spot and options market quotes
- Historical candles
- Option chain snapshots
- Open interest analysis
- IV surface and IV skew estimates
- Technical indicators such as RSI, MACD, Bollinger Bands, ATR, support/resistance, volume percentile, and PCR
- Real-time tick storage

Analytics features include:

- Payoff curve generation
- Net greeks aggregation
- Day-wise and cumulative PnL tracking
- OI heatmap snapshots
- Recent tick views

## Backend API

The FastAPI server exposes:

- `GET /health`
- `GET /config`
- `GET /controls`
- `PUT /controls/kill-switch`
- `PUT /controls/auto-trading`
- `PUT /controls/quant-gate`
- `PUT /controls/risk-engine`
- `PUT /controls/option-buying`
- `PUT /controls/mode`
- `POST /controls/emergency-exit`
- `POST /strategy/deploy`
- `GET /market/latest`
- `GET /strategy/status`
- `GET /strategy/payoff`
- `GET /trades/open`
- `GET /positions/greeks`
- `GET /trades/recent`
- `GET /orders/blotter`
- `GET /pnl/summary`
- `GET /oi/heatmap`
- `GET /ticks/recent`
- `GET /audit/events`
- `WS /ws/market`
- `WS /ws/strategy`

### API purpose

- The control endpoints let the UI toggle runtime behavior without editing config files.
- The data endpoints feed the dashboard.
- WebSockets provide live market and strategy updates.

## Frontend Dashboard

The React frontend provides:

- A trading dashboard for live state
- A separate day-wise PnL page
- Payoff chart visualization
- Positions and greeks display
- Order blotter
- Live logs and strategy status
- Market session status
- Risk usage indicators
- AI insights and control toggles

Day-wise PnL page features:

- Daily realized PnL
- Unrealized PnL
- Net PnL
- Cumulative equity curve
- Copy-to-clipboard on numeric values

## Runtime Controls

The app persists runtime state in SQLite and exposes it through controls such as:

- `kill_switch`
- `auto_trading_paused`
- `quant_gate_enabled`
- `risk_engine_enabled`
- `option_buying_enabled`
- `trading_mode`

These controls are used by the scheduler, controller, and API layer to decide whether strategies should run.

## Persistence

SQLite is used for:

- Trades
- Market context
- Option chain snapshots
- AI decisions
- Strategy performance
- Real-time ticks
- Audit events
- Runtime controls
- Order blotter entries

This means the app keeps both current state and historical evidence locally.

## Scheduler And Execution

The runtime is coordinated by a scheduler and system controller that handle:

- Strategy decision cycles
- PnL monitoring
- Forced exits
- Day-end optimization
- Token refresh

Execution is integrated with:

- Kite Connect client
- Order manager
- WebSocket tick stream
- Telegram notifications

## Startup And Launching

Main entrypoints:

- `python run.py`
- `run_morning.bat`
- `run_fullstack.bat`
- `run_frontend_dev.bat`

`run.py` can start:

- the FastAPI backend
- the React frontend
- both together, depending on config and environment

## Configuration Highlights

The main config file is `config/settings.yaml`, usually created from `config/config.sample.yaml`.

Important settings include:

- Kite API credentials and product type
- LLM model selection
- Optional OpenRouter fallback
- Risk limits
- Telegram alerts
- Dashboard frontend mode
- API host and port
- Scheduler times
- Trading mode

## Dependencies

Key runtime dependencies include:

- `fastapi`
- `uvicorn[standard]`
- `kiteconnect`
- `pandas`
- `numpy`
- `scikit-learn`
- `scipy`
- `joblib`
- `PyYAML`
- `requests`

## Practical Notes

- Paper mode is the safest way to test strategy changes.
- The frontend is intentionally tied to the backend API, so the dashboard is mostly a live control and inspection surface.
- The app is centered around Indian index options workflows, not generic equities trading.
- The codebase already has enough modular separation that strategy logic, risk, data, and UI can be changed independently.

