# Jugal's AI Options Desk

## Contributor

- TUNA2020

Automated options research/execution engine for NIFTY/BANKNIFTY with:

- Kite Connect market data (quotes, historical candles, options chain via instruments+quotes)
- Paper and live modes (paper never places broker orders)
- LLM strategy reasoning + risk filters
- APScheduler strategy cycles
- SQLite trade/context persistence
- FastAPI backend + React frontend

## Python

Python `3.11+`

## Setup

```bash
cd AI_OPTIONS_DESK
python -m venv .venv313
.venv313\Scripts\activate
pip install -r requirements.txt
```

For WebSocket updates (`/ws/market`, `/ws/strategy`), keep `uvicorn[standard]` installed
(already included in `requirements.txt`).

If React frontend is enabled:

```bash
cd frontend-react
npm install
```

Create config from sample:

```bash
copy config\config.sample.yaml config\settings.yaml
```

Fill credentials in `config/settings.yaml`:

- `kite.api_key`
- `kite.api_secret`
- `kite.product` (`NRML` or `MIS`)
- `openrouter.api_key`
- `risk.max_capital_per_trade` (capital allocated per idea)
- `risk.max_loss_pct_per_trade` (percent of the above capital you are willing to lose in a single trade)
- Optional Telegram alerts:
  - `telegram.enabled`
  - `telegram.bot_token`
  - `telegram.chat_id`

Setting `risk.max_loss_pct_per_trade` > 0 overrides `risk.max_loss_per_trade` so you always risk at most that percentage of `risk.max_capital_per_trade` per trade.

## Kite Token Setup

Use `kite.auto_login.enabled: true` with `user_id/password/totp_secret`
for programmatic login.

Set this in `config/settings.yaml`:

```yaml
kite:
  allow_request_token_from_settings: false
  auto_login:
    user_id: "your_kite_user_id"
    password: "your_kite_password"
    totp_secret: "your_base32_totp_secret"
    enabled: true
    timeout_seconds: 20
    twofa_type: "totp"
    skip_session: true
    max_redirect_hops: 8
```

Then run `python run.py` (or `run_morning.bat`).
`request_token` and `access_token` are generated automatically.

Optional manual fallback (short-lived request token):

```bash
set KITE_REQUEST_TOKEN=your_fresh_request_token
python run.py
```

Tokens are persisted at `config/kite_tokens.json` and refreshed daily at `scheduler.token_refresh_time`.

## Frontend

Set in `config/settings.yaml`:

```yaml
dashboard:
  frontend: react
```

`run.py` starts FastAPI backend and also attempts to auto-start Vite frontend (`http://127.0.0.1:5173`).

## Run

Backend engine + scheduler + websocket:

```bash
python run.py
```

Quick Windows launchers (desktop-friendly):

- `run_morning.bat`: starts backend, and if `frontend: react`, auto-starts Vite in new window
- `run_fullstack.bat`: always starts backend + React dev server in separate windows
- `run_frontend_dev.bat`: frontend only

## Runtime Behavior

- Strategy execution is skipped when market is closed.
- Weekend/holiday handling uses last available trading-session candles for analytics.
- Paper mode uses real Kite quotes for fills and stores simulated trades/PnL locally.
- Live mode places/ closes broker orders and uses signed net positions for live PnL.
- Kill switch support:
  - One-click UI action toggles `kill_switch` runtime control.
  - When enabled, new strategy orders are blocked and open positions are force-closed.
- UI includes:
  - Live order blotter (`SUBMITTED`, `FILLED`, `REJECTED`, `CLOSED`)
  - Position Greeks panel (net delta/theta/vega)
  - Session header (OPEN/CLOSED, active strategy, risk usage %)
## Database

`ai_options_desk.db` main tables:

- `trades`
- `market_context`
- `option_chain`
- `ai_decisions`
- `strategy_performance`
- `realtime_ticks`
- `audit_events`
