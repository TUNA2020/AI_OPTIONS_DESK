from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from math import erf, exp, log, pi, sqrt
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI, Query, WebSocket
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.alerts import TelegramNotifier
from core.config import load_settings
from core.market_hours import market_session_status
from analytics.payoff_engine import generate_payoff_curve
from ai.strategy_generator import build_trade_from_decision, canonical_strategy_name, rank_strategy_candidates
from ai.quant_validator import QuantValidator
from database.sqlite_manager import SQLiteManager
from execution.kite_client import KiteClient
from execution.order_manager import OrderManager
from risk.risk_manager import RiskManager


BASE_DIR = Path(
    os.getenv("AI_OPTIONS_DESK_BASE_DIR", Path(__file__).resolve().parents[1])
)
SETTINGS_PATH = BASE_DIR / "config/settings.yaml"
DB_PATH = BASE_DIR / "ai_options_desk.db"

app = FastAPI(title="Jugal's AI Options Desk API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_kite_client: KiteClient | None = None
_kite_client_mode: str | None = None


class KillSwitchUpdate(BaseModel):
    enabled: bool
    close_positions: bool = True
    reason: str = ""


class ToggleUpdate(BaseModel):
    enabled: bool
    reason: str = ""


class ModeUpdate(BaseModel):
    mode: str
    reason: str = ""


def _runtime_control_enabled(db: SQLiteManager, key: str, default: bool = True) -> bool:
    return bool(db.get_runtime_control(key, default))


def _quant_gate_enabled(db: SQLiteManager) -> bool:
    return _runtime_control_enabled(db, "quant_gate_enabled", False)


def _risk_engine_enabled(db: SQLiteManager) -> bool:
    return _runtime_control_enabled(db, "risk_engine_enabled", False)


def _safe_json(text: Any) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _to_timezone_series(values: pd.Series, timezone_name: str) -> pd.Series:
    def _parse_one(value: Any) -> pd.Timestamp:
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            return pd.NaT
        if getattr(ts, "tzinfo", None) is None:
            return ts.tz_localize(timezone_name)
        return ts.tz_convert(timezone_name)

    return values.apply(_parse_one)


def _floor_to_five_minutes(ts: pd.Series) -> pd.Series:
    return ts.dt.floor("5min")


def _build_heatmap_snapshot(
    chain_df: pd.DataFrame,
    block_size: int,
    timezone_name: str,
    session: dict[str, str | bool],
    spot: float,
    strikes_each_side: int,
) -> dict[str, Any]:
    if chain_df.empty or "timestamp" not in chain_df or "strike" not in chain_df:
        return {
            "block_size": block_size,
            "rows": [],
            "latest_timestamp": "",
            "previous_timestamp": "",
        }

    df = chain_df.copy()
    df["timestamp_ist"] = _to_timezone_series(df["timestamp"], timezone_name)
    df = df[df["timestamp_ist"].notna()].copy()
    if df.empty:
        return {
            "block_size": block_size,
            "rows": [],
            "latest_timestamp": "",
            "previous_timestamp": "",
        }

    now_local = datetime.now(timezone.utc).astimezone(ZoneInfo(timezone_name))
    df["trade_date"] = df["timestamp_ist"].dt.date
    if bool(session.get("is_open", False)):
        target_date = now_local.date()
        day_df = df[df["trade_date"] == target_date].copy()
        day_df = day_df[day_df["timestamp_ist"] <= now_local].copy()
        if day_df.empty:
            target_date = df["trade_date"].max()
            day_df = df[df["trade_date"] == target_date].copy()
    else:
        target_date = df["trade_date"].max()
        day_df = df[df["trade_date"] == target_date].copy()

    if day_df.empty:
        return {
            "block_size": block_size,
            "rows": [],
            "latest_timestamp": "",
            "previous_timestamp": "",
        }

    day_df["bucket_ts"] = _floor_to_five_minutes(day_df["timestamp_ist"])
    latest_bucket = day_df["bucket_ts"].max()
    previous_bucket = day_df.loc[day_df["bucket_ts"] < latest_bucket, "bucket_ts"].max()

    latest_df = day_df[day_df["bucket_ts"] == latest_bucket].copy()
    previous_df = day_df[day_df["bucket_ts"] == previous_bucket].copy() if pd.notna(previous_bucket) else pd.DataFrame()

    if "payload" in latest_df.columns:
        latest_payload = latest_df["payload"].apply(_safe_json)
        latest_df["ce_ltp"] = latest_payload.apply(lambda p: float((p or {}).get("ce_ltp", 0.0) or 0.0))
        latest_df["pe_ltp"] = latest_payload.apply(lambda p: float((p or {}).get("pe_ltp", 0.0) or 0.0))
    else:
        latest_df["ce_ltp"] = 0.0
        latest_df["pe_ltp"] = 0.0

    latest_df["strike_block"] = (
        latest_df["strike"].astype(float) / float(block_size)
    ).round().astype(int) * block_size
    previous_df["strike_block"] = (
        previous_df["strike"].astype(float) / float(block_size)
    ).round().astype(int) * block_size if not previous_df.empty else pd.Series(dtype=int)

    latest_group = (
        latest_df.groupby("strike_block")[["ce_oi", "pe_oi", "ce_ltp", "pe_ltp"]]
        .agg({"ce_oi": "sum", "pe_oi": "sum", "ce_ltp": "mean", "pe_ltp": "mean"})
        if not latest_df.empty
        else pd.DataFrame()
    )
    previous_group = (
        previous_df.groupby("strike_block")[["ce_oi", "pe_oi"]].sum()
        if not previous_df.empty
        else pd.DataFrame()
    )
    if not latest_group.empty:
        latest_group.index.name = "strike_block"
    if not previous_group.empty:
        previous_group.index.name = "strike_block"

    strikes = sorted(latest_df["strike"].astype(float).unique().tolist())
    if not strikes:
        return {
            "block_size": block_size,
            "rows": [],
            "latest_timestamp": latest_bucket.isoformat(),
            "previous_timestamp": previous_bucket.isoformat() if pd.notna(previous_bucket) else "",
        }

    if spot <= 0:
        spot = strikes[len(strikes) // 2]
    center_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    start = max(0, center_idx - strikes_each_side)
    end = min(len(strikes), center_idx + strikes_each_side + 1)
    selected_strikes = set(strikes[start:end])
    latest_df = latest_df[latest_df["strike"].isin(selected_strikes)].copy()
    latest_df["strike_block"] = (
        latest_df["strike"].astype(float) / float(block_size)
    ).round().astype(int) * block_size
    out = (
        latest_df.groupby("strike_block", as_index=False)[["ce_oi", "pe_oi"]]
        .sum()
        .sort_values("strike_block")
    )

    rows_out: list[dict[str, Any]] = []
    for _, row in out.iterrows():
        block = int(row["strike_block"])
        latest_ce = int(row["ce_oi"] or 0)
        latest_pe = int(row["pe_oi"] or 0)
        latest_ce_ltp = float(row["ce_ltp"] or 0.0)
        latest_pe_ltp = float(row["pe_ltp"] or 0.0)
        prev_ce = int(previous_group.loc[block]["ce_oi"]) if not previous_group.empty and block in previous_group.index else 0
        prev_pe = int(previous_group.loc[block]["pe_oi"]) if not previous_group.empty and block in previous_group.index else 0
        ce_change = latest_ce - prev_ce
        pe_change = latest_pe - prev_pe
        rows_out.append(
            {
                "strike_block": block,
                "ce_oi": latest_ce,
                "pe_oi": latest_pe,
                "ce_ltp": round(latest_ce_ltp, 2),
                "pe_ltp": round(latest_pe_ltp, 2),
                "ce_oi_change": ce_change,
                "pe_oi_change": pe_change,
                "ce_trend": "up" if ce_change > 0 else "down" if ce_change < 0 else "flat",
                "pe_trend": "up" if pe_change > 0 else "down" if pe_change < 0 else "flat",
                "resistance": latest_ce >= latest_pe,
                "support": latest_pe >= latest_ce,
            }
        )

    return {
        "block_size": block_size,
        "rows": rows_out,
        "latest_timestamp": latest_bucket.isoformat(),
        "previous_timestamp": previous_bucket.isoformat() if pd.notna(previous_bucket) else "",
    }


def _normalize_audit_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    out["payload"] = _safe_json(out.get("payload", ""))
    return out


def _proposal_to_decision(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {}

    direct_strategy = str(payload.get("strategy", "")).strip()
    if direct_strategy:
        return dict(payload)

    proposal = payload.get("proposal")
    if isinstance(proposal, dict) and proposal:
        for key in ("primary", "selected", "secondary"):
            candidate = proposal.get(key)
            if isinstance(candidate, dict) and str(candidate.get("strategy", "")).strip():
                return dict(candidate)
        candidates = proposal.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict) and str(candidate.get("strategy", "")).strip():
                    return dict(candidate)
    return {}


def _settings() -> dict[str, Any]:
    settings = load_settings(SETTINGS_PATH)
    settings["__base_dir__"] = BASE_DIR
    return settings


def _db() -> SQLiteManager:
    return SQLiteManager(DB_PATH)


def _get_kite_client() -> KiteClient:
    global _kite_client, _kite_client_mode
    settings = _settings()
    mode = str(settings["app"].get("mode", "paper")).lower()
    if _kite_client is None or _kite_client_mode != mode:
        _kite_client = KiteClient(settings=settings)
        _kite_client_mode = mode
    return _kite_client


def _current_trading_mode(settings: dict[str, Any] | None = None, db: SQLiteManager | None = None) -> str:
    settings = settings or _settings()
    fallback = str(settings["app"].get("mode", "paper")).lower().strip() or "paper"
    if db is None:
        return fallback
    value = db.get_runtime_control("trading_mode", fallback)
    return str(value).lower().strip() or fallback


def _build_order_manager(settings: dict[str, Any], db: SQLiteManager, notifier: TelegramNotifier | None = None) -> OrderManager:
    mode = _current_trading_mode(settings, db)
    return OrderManager(
        kite_client=_get_kite_client(),
        db=db,
        mode=mode,
        product=str(settings.get("kite", {}).get("product", "NRML")).upper(),
        notifier=notifier,
    )


def _build_risk_manager(settings: dict[str, Any]) -> RiskManager:
    return RiskManager(
        max_loss_per_trade=float(settings["risk"]["max_loss_per_trade"]),
        monte_carlo_paths=int(settings["risk"]["monte_carlo_paths"]),
    )


def _build_quant_validator(settings: dict[str, Any]) -> QuantValidator:
    _ = settings
    return QuantValidator()


def _manual_trade_ready(db: SQLiteManager) -> bool:
    open_trades = db.fetch_open_trades()
    return not bool(open_trades)


def _trades_df(timezone_name: str) -> pd.DataFrame:
    db = _db()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, strategy, symbol, side, qty, price, status, metadata FROM trades ORDER BY id ASC"
        ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["metadata_obj"] = df["metadata"].apply(_safe_json)
    df["timestamp_ist"] = _to_timezone_series(df["timestamp"], "Asia/Kolkata")
    df = df[df["timestamp_ist"].notna()].copy()
    df["timestamp_local"] = df["timestamp_ist"].dt.tz_convert(timezone_name)
    df["local_day"] = df["timestamp_local"].dt.strftime("%Y-%m-%d")
    df["has_order_result"] = df["metadata_obj"].apply(lambda x: "order_result" in x)
    return df


def _realized_events(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(columns=["timestamp_local", "local_day", "strategy", "pnl"])
    rows_by_id = {int(row["id"]): row for row in trades_df.to_dict("records")}
    events: list[dict[str, Any]] = []
    for row in trades_df.to_dict("records"):
        meta = row.get("metadata_obj", {})
        if "closed_trade_id" not in meta:
            continue
        try:
            open_id = int(meta.get("closed_trade_id"))
        except Exception:
            continue
        open_row = rows_by_id.get(open_id)
        if not open_row:
            continue
        qty = int(row.get("qty") or open_row.get("qty") or 0)
        if qty <= 0:
            continue
        entry_price = float(open_row.get("price", 0.0) or 0.0)
        exit_price = float(row.get("price", 0.0) or 0.0)
        if exit_price <= 0:
            exit_price = entry_price
        entry_side = str(open_row.get("side", "BUY")).upper()
        pnl = (
            (entry_price - exit_price) * qty
            if entry_side == "SELL"
            else (exit_price - entry_price) * qty
        )
        events.append(
            {
                "timestamp_local": row["timestamp_local"],
                "local_day": row["local_day"],
                "strategy": str(open_row.get("strategy", "")),
                "pnl": float(pnl),
            }
        )
    return pd.DataFrame(events)


def _unrealized_open_pnl(open_trades: list[dict[str, Any]]) -> float:
    if not open_trades:
        return 0.0
    try:
        kite = _get_kite_client()
        symbols = [f"NFO:{t['symbol']}" for t in open_trades if t.get("symbol")]
        quotes = kite.quote(symbols) if symbols else {}
    except Exception:
        return 0.0
    pnl = 0.0
    for trade in open_trades:
        symbol = str(trade.get("symbol", ""))
        side = str(trade.get("side", "BUY")).upper()
        qty = int(trade.get("qty", 0))
        entry = float(trade.get("price", 0.0))
        ltp = float(quotes.get(f"NFO:{symbol}", {}).get("last_price", entry))
        pnl += (entry - ltp) * qty if side == "SELL" else (ltp - entry) * qty
    return float(pnl)


def _enrich_open_trades_with_ltp(
    open_trades: list[dict[str, Any]],
    option_ltps: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not open_trades:
        return []
    ws_ltps: dict[str, float] = {}
    if isinstance(option_ltps, dict):
        for symbol, value in option_ltps.items():
            key = str(symbol).strip()
            if not key:
                continue
            try:
                price = float(value or 0.0)
            except Exception:
                price = 0.0
            if price > 0:
                ws_ltps[key] = price
    symbols = [f"NFO:{str(trade.get('symbol', '')).strip()}" for trade in open_trades if str(trade.get("symbol", "")).strip()]
    quotes: dict[str, Any] = {}
    if symbols:
        try:
            quotes = _get_kite_client().quote(symbols)
        except Exception:
            quotes = {}
    rows: list[dict[str, Any]] = []
    for trade in open_trades:
        symbol = str(trade.get("symbol", "")).strip()
        entry = float(trade.get("price", 0.0) or 0.0)
        ltp = float(ws_ltps.get(symbol, 0.0) or 0.0)
        if ltp <= 0:
            ltp = float(quotes.get(f"NFO:{symbol}", {}).get("last_price", 0.0) or 0.0)
        if ltp <= 0:
            ltp = entry
        row = dict(trade)
        row["ltp"] = ltp
        rows.append(row)
    return rows


def _strike_block_size(symbol: str) -> int:
    return 100 if "BANK" in str(symbol).upper() else 50


def _websocket_supported() -> bool:
    return (find_spec("websockets") is not None) or (find_spec("wsproto") is not None)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _normal_pdf(x: float) -> float:
    return (1.0 / sqrt(2.0 * pi)) * exp(-0.5 * x * x)


def _bs_greeks(
    spot: float,
    strike: float,
    time_years: float,
    iv: float,
    is_call: bool,
    risk_free_rate: float = 0.07,
) -> tuple[float, float, float]:
    if spot <= 0 or strike <= 0 or time_years <= 0 or iv <= 0:
        return 0.0, 0.0, 0.0
    d1 = (log(spot / strike) + (risk_free_rate + 0.5 * iv * iv) * time_years) / (
        iv * sqrt(time_years)
    )
    d2 = d1 - iv * sqrt(time_years)
    delta = _normal_cdf(d1) if is_call else (_normal_cdf(d1) - 1.0)
    front = -(spot * _normal_pdf(d1) * iv) / (2.0 * sqrt(time_years))
    discount = risk_free_rate * strike * exp(-risk_free_rate * time_years)
    theta_annual = (
        front - discount * _normal_cdf(d2)
        if is_call
        else front + discount * _normal_cdf(-d2)
    )
    theta_per_day = theta_annual / 365.0
    vega_per_1pct = (spot * _normal_pdf(d1) * sqrt(time_years)) * 0.01
    return float(delta), float(theta_per_day), float(vega_per_1pct)


def _symbol_to_strike_option(symbol: str) -> tuple[float, str] | None:
    match = re.search(r"(\d+)(CE|PE)$", str(symbol).upper())
    if not match:
        return None
    return float(match.group(1)), str(match.group(2))


def _option_meta_lookup(chain_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row in chain_rows:
        payload = _safe_json(row.get("payload", ""))
        strike = float(payload.get("strike", row.get("strike", 0.0)) or 0.0)
        days_to_expiry = int(payload.get("days_to_expiry", 0) or 0)
        ce_symbol = str(payload.get("ce_symbol", "")).strip()
        pe_symbol = str(payload.get("pe_symbol", "")).strip()
        if ce_symbol:
            lookup[ce_symbol] = {
                "strike": strike,
                "option_type": "CE",
                "iv": float(payload.get("ce_iv", 0.0) or 0.0),
                "delta": float(payload.get("ce_delta", 0.0) or 0.0),
                "days_to_expiry": days_to_expiry,
            }
        if pe_symbol:
            lookup[pe_symbol] = {
                "strike": strike,
                "option_type": "PE",
                "iv": float(payload.get("pe_iv", 0.0) or 0.0),
                "delta": float(payload.get("pe_delta", 0.0) or 0.0),
                "days_to_expiry": days_to_expiry,
            }
    return lookup


def _ist_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))


def _market_session(settings: dict[str, Any]) -> dict[str, str | bool]:
    return market_session_status(
        timezone_name=str(settings["app"].get("timezone", "Asia/Kolkata")),
        open_time=str(settings["app"].get("market_open_time", "09:15")),
        close_time=str(settings["app"].get("market_close_time", "15:30")),
    )


@app.on_event("startup")
def _seed_runtime_controls() -> None:
    db = _db()
    db.ensure_runtime_control("quant_gate_enabled", False)
    db.ensure_runtime_control("risk_engine_enabled", False)


@app.get("/", response_class=HTMLResponse)
def root_page() -> str:
    return """
    <html>
      <head>
        <title>Jugal's AI Options Desk API</title>
        <style>
          body { font-family: Segoe UI, Arial, sans-serif; margin: 28px; color: #102a43; }
          .card { max-width: 760px; border: 1px solid #d9e2ec; border-radius: 10px; padding: 18px; }
          h1 { margin: 0 0 8px 0; }
          p { margin: 8px 0; }
          a { color: #0f766e; text-decoration: none; }
          code { background: #f0f4f8; padding: 2px 6px; border-radius: 6px; }
        </style>
      </head>
      <body>
        <div class="card">
          <h1>Jugal's AI Options Desk API is running</h1>
          <p>This is the backend API service.</p>
          <p>Frontend app: <a href="http://127.0.0.1:5173" target="_blank">http://127.0.0.1:5173</a></p>
          <p>API docs: <a href="/docs" target="_blank">/docs</a></p>
          <p>Health check: <a href="/health" target="_blank">/health</a></p>
          <p>If frontend is not opening, run <code>run_fullstack.bat</code> or <code>run_frontend_dev.bat</code>.</p>
        </div>
      </body>
    </html>
    """


@app.get("/health")
def health() -> dict[str, Any]:
    ws_ok = _websocket_supported()
    return {
        "status": "ok",
        "websocket_supported": ws_ok,
        "websocket_note": ""
        if ws_ok
        else 'Install websocket runtime via: pip install "uvicorn[standard]"',
    }


@app.get("/config")
def get_config() -> dict[str, Any]:
    settings = _settings()
    return {
        "app": settings.get("app", {}),
        "risk": settings.get("risk", {}),
        "dashboard": settings.get("dashboard", {}),
    }


@app.get("/controls")
def get_controls() -> dict[str, Any]:
    settings = _settings()
    db = _db()
    return {
        "kill_switch": bool(db.get_runtime_control("kill_switch", False)),
        "auto_trading_paused": bool(db.get_runtime_control("auto_trading_paused", False)),
        "trading_mode": _current_trading_mode(settings, db),
        "quant_gate_enabled": _quant_gate_enabled(db),
        "risk_engine_enabled": _risk_engine_enabled(db),
        "all": db.fetch_runtime_controls(),
    }


@app.put("/controls/kill-switch")
def update_kill_switch(payload: KillSwitchUpdate) -> dict[str, Any]:
    settings = _settings()
    db = _db()
    enabled = bool(payload.enabled)
    db.set_runtime_control("kill_switch", enabled)
    db.insert_audit_event(
        level="WARNING" if enabled else "INFO",
        event_type="kill_switch_toggled",
        message=f"Kill switch {'enabled' if enabled else 'disabled'} via API",
        payload={"enabled": enabled, "reason": payload.reason},
    )
    notifier = TelegramNotifier(settings)
    notifier.send(
        "KILL_SWITCH",
        f"Kill switch {'ENABLED' if enabled else 'DISABLED'} via UI/API",
        payload={"reason": payload.reason},
    )

    closed_now = 0
    if enabled and bool(payload.close_positions):
        try:
            order_manager = OrderManager(
                kite_client=_get_kite_client(),
                db=db,
                mode=str(settings["app"].get("mode", "paper")),
                product=str(settings.get("kite", {}).get("product", "NRML")).upper(),
                notifier=notifier,
            )
            before = len(db.fetch_open_trades())
            order_manager.close_all_positions()
            after = len(db.fetch_open_trades())
            closed_now = max(0, before - after)
        except Exception as exc:
            db.insert_audit_event(
                level="ERROR",
                event_type="kill_switch_close_failed",
                message=f"Kill switch close failed: {exc}",
            )
            raise

    return {
        "kill_switch": enabled,
        "close_positions_requested": bool(payload.close_positions),
        "closed_now": closed_now,
        "open_positions_remaining": len(db.fetch_open_trades()),
    }


@app.put("/controls/auto-trading")
def update_auto_trading(payload: ToggleUpdate) -> dict[str, Any]:
    db = _db()
    enabled = bool(payload.enabled)
    paused = not enabled
    db.set_runtime_control("auto_trading_paused", paused)
    db.insert_audit_event(
        level="INFO" if enabled else "WARNING",
        event_type="auto_trading_toggled",
        message=f"Auto trading {'resumed' if enabled else 'paused'} via API",
        payload={"enabled": enabled, "reason": payload.reason},
    )
    return {
        "auto_trading_paused": paused,
        "enabled": enabled,
    }


@app.put("/controls/quant-gate")
def update_quant_gate(payload: ToggleUpdate) -> dict[str, Any]:
    db = _db()
    enabled = bool(payload.enabled)
    db.set_runtime_control("quant_gate_enabled", enabled)
    db.insert_audit_event(
        level="INFO" if enabled else "WARNING",
        event_type="quant_gate_toggled",
        message=f"Quant gate {'enabled' if enabled else 'disabled'} via API",
        payload={"enabled": enabled, "reason": payload.reason},
    )
    return {
        "quant_gate_enabled": enabled,
        "enabled": enabled,
    }


@app.put("/controls/risk-engine")
def update_risk_engine(payload: ToggleUpdate) -> dict[str, Any]:
    db = _db()
    enabled = bool(payload.enabled)
    db.set_runtime_control("risk_engine_enabled", enabled)
    db.insert_audit_event(
        level="INFO" if enabled else "WARNING",
        event_type="risk_engine_toggled",
        message=f"Risk engine {'enabled' if enabled else 'disabled'} via API",
        payload={"enabled": enabled, "reason": payload.reason},
    )
    return {
        "risk_engine_enabled": enabled,
        "enabled": enabled,
    }


@app.put("/controls/mode")
def update_trading_mode(payload: ModeUpdate) -> dict[str, Any]:
    db = _db()
    mode = str(payload.mode).lower().strip()
    if mode not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="mode must be paper or live")
    db.set_runtime_control("trading_mode", mode)
    db.insert_audit_event(
        level="WARNING" if mode == "live" else "INFO",
        event_type="trading_mode_toggled",
        message=f"Trading mode switched to {mode.upper()} via API",
        payload={"mode": mode, "reason": payload.reason},
    )
    return {
        "trading_mode": mode,
    }


@app.post("/controls/emergency-exit")
def emergency_exit(payload: ToggleUpdate | None = None) -> dict[str, Any]:
    settings = _settings()
    db = _db()
    notifier = TelegramNotifier(settings)
    order_manager = _build_order_manager(settings, db, notifier)
    order_manager.refresh_mode(_current_trading_mode(settings, db))
    before = len(db.fetch_open_trades())
    order_manager.close_all_positions()
    after = len(db.fetch_open_trades())
    closed_now = max(0, before - after)
    db.insert_audit_event(
        level="WARNING",
        event_type="emergency_exit",
        message="Emergency exit executed via API",
        payload={"reason": getattr(payload, "reason", ""), "closed_now": closed_now},
    )
    return {
        "closed_now": closed_now,
        "open_positions_remaining": after,
    }


@app.post("/strategy/deploy")
def deploy_strategy() -> dict[str, Any]:
    settings = _settings()
    db = _db()
    if bool(db.get_runtime_control("kill_switch", False)):
        raise HTTPException(status_code=409, detail="Kill switch is ON. Deployment blocked.")
    context_row = db.fetch_recent_context()
    if context_row is None:
        raise HTTPException(status_code=409, detail="No market context available for deployment.")
    context = _safe_json(context_row["payload"])
    session_open = bool(context.get("market_open", False))
    decision_row = db.fetch_recent_ai_decision()
    if not decision_row:
        raise HTTPException(status_code=409, detail="No AI decision available for deployment.")
    decision = _safe_json(decision_row.get("payload", "")) or dict(decision_row)
    if not _manual_trade_ready(db):
        raise HTTPException(status_code=409, detail="Existing open positions found. Close them before deploying a new trade.")
    if not session_open:
        raise HTTPException(status_code=409, detail="Market is closed. Manual deployment is disabled.")

    proposal_candidates = decision.get("candidates") if isinstance(decision.get("candidates"), list) else [decision]
    quant_enabled = _quant_gate_enabled(db)
    quant_result: dict[str, Any]
    if quant_enabled:
        quant_validator = _build_quant_validator(settings)
        quant_result = quant_validator.validate_candidates(
            context,
            [row for row in proposal_candidates if isinstance(row, dict)],
            regime=decision.get("regime") if isinstance(decision.get("regime"), dict) else None,
        )
        if not quant_result.get("allowed"):
            db.insert_audit_event(
                level="INFO",
                event_type="manual_trade_quant_rejected",
                message=f"Manual trade deployment rejected by quant gate ({quant_result.get('selected_strategy', 'NO TRADE')})",
                payload={"quant": quant_result},
            )
            raise HTTPException(status_code=409, detail="Quant scoring engine returned NO TRADE for the current AI proposals.")

        selected_decision = quant_result.get("selected")
        if not isinstance(selected_decision, dict):
            raise HTTPException(status_code=409, detail="Quant scoring engine returned NO TRADE for the current AI proposals.")
    else:
        selected_decision = dict(decision)
        quant_result = {
            "allowed": True,
            "selected": selected_decision,
            "selected_strategy": str(selected_decision.get("strategy", "NO TRADE")),
            "candidates": [],
            "bypassed": True,
            "enabled": False,
            "reason": "Quant gate disabled via UI",
        }
    try:
        strategy_name, legs = build_trade_from_decision(selected_decision, context)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    risk_enabled = _risk_engine_enabled(db)
    if risk_enabled:
        risk_manager = _build_risk_manager(settings)
        risk_result = risk_manager.evaluate_trade(context, legs)
        if not risk_result["allowed"]:
            db.insert_audit_event(
                level="WARNING",
                event_type="manual_trade_rejected",
                message="Manual trade deployment rejected by risk manager",
                payload={"reason": risk_result.get("reason"), "stats": risk_result.get("stats", {})},
            )
            raise HTTPException(status_code=409, detail=str(risk_result.get("reason", "Risk rejection")))
    else:
        risk_result = {
            "allowed": True,
            "bypassed": True,
            "enabled": False,
            "reason": "Risk engine disabled via UI",
            "stats": {},
        }

    notifier = TelegramNotifier(settings)
    order_manager = _build_order_manager(settings, db, notifier)
    order_manager.refresh_mode(_current_trading_mode(settings, db))
    results = order_manager.execute_legs(strategy_name, legs)
    db.insert_audit_event(
        level="INFO",
        event_type="manual_trade_executed",
        message=f"Manual deploy executed strategy={strategy_name}",
        payload={"strategy": strategy_name, "results": results, "risk": risk_result, "quant": quant_result},
    )
    return {
        "strategy": strategy_name,
        "results": results,
        "risk": risk_result,
        "quant": quant_result,
    }


@app.get("/market/latest")
def market_latest() -> dict[str, Any]:
    settings = _settings()
    db = _db()
    session = _market_session(settings)
    context_row = db.fetch_recent_context()
    tick_row = db.fetch_recent_realtime_tick()
    context = _safe_json(context_row["payload"]) if context_row else {}
    tick = _safe_json(tick_row["payload"]) if tick_row else {}
    if tick.get("nifty_price"):
        context["nifty_price"] = float(tick["nifty_price"])
    if tick.get("vix"):
        context["vix"] = float(tick["vix"])
    if tick.get("volume"):
        context["volume"] = int(tick["volume"])
    if tick.get("market_local_time"):
        context["market_local_time"] = tick["market_local_time"]
    if not context.get("market_status"):
        context["market_open"] = bool(session["is_open"])
        context["market_status"] = str(session["status"])
        context["market_status_reason"] = str(session["reason"])
        context["market_local_time"] = str(session["local_time"])
    return {"context": context, "tick": tick, "market_session": session}


@app.get("/strategy/status")
def strategy_status() -> dict[str, Any]:
    settings = _settings()
    db = _db()
    timezone_name = str(settings["app"].get("timezone", "Asia/Kolkata"))
    trades_df = _trades_df(timezone_name)
    today_local = pd.Timestamp.now(tz=timezone_name).strftime("%Y-%m-%d")
    if trades_df.empty:
        today_strategies: list[str] = []
    else:
        today_exec = trades_df[
            (trades_df["local_day"] == today_local) & (trades_df["has_order_result"])
        ]
        today_strategies = sorted(set(today_exec["strategy"].astype(str).tolist()))

    audit = db.fetch_audit_events(limit=1)
    latest_audit = _normalize_audit_row(audit[0]) if audit else None
    open_rows = db.fetch_open_trades()
    open_notional = float(
        sum(abs(float(r.get("price", 0.0)) * int(r.get("qty", 0))) for r in open_rows)
    )
    max_capital = float(
        settings.get("risk", {}).get("max_capital_per_trade", 0.0) or 0.0
    )
    risk_usage_pct = (open_notional / max_capital * 100.0) if max_capital > 0 else 0.0
    session = _market_session(settings)

    active_strategy = "None"
    if latest_audit:
        active_strategy = (
            str(latest_audit.get("payload", {}).get("strategy", "")).strip() or "None"
        )
    if active_strategy == "None" and today_strategies:
        active_strategy = str(today_strategies[-1])

    stage = "idle"
    if latest_audit:
        et = str(latest_audit.get("event_type", ""))
        if et == "strategy_cycle_started":
            stage = "analyzing"
        elif et == "ai_insight_ready":
            stage = "ai_insight_ready"
        elif et == "trade_executed":
            stage = "executed"
        elif et == "trade_rejected":
            stage = "risk_rejected"
        elif et == "trade_blocked_kill_switch":
            stage = "blocked_kill_switch"
        elif et == "force_exit":
            stage = "closed"
        elif et == "kill_switch_toggled":
            stage = "kill_switch"
    state = stage
    recent_decision = db.fetch_recent_ai_decision()
    decision_reason = str(recent_decision.get("reason", "")).strip() if recent_decision else ""
    decision_payload = _safe_json(recent_decision.get("payload", "")) if recent_decision else {}
    context_row = db.fetch_recent_context()
    market_context = _safe_json(context_row.get("payload", "")) if context_row else {}
    market_trend = str(context_row.get("trend", "")).strip() if context_row else ""
    tick_row = db.fetch_recent_realtime_tick()
    ai_model = str(settings.get("openrouter", {}).get("model", "unknown"))
    audit_payload = latest_audit.get("payload", {}) if isinstance(latest_audit, dict) else {}
    if (not decision_payload or not str(decision_payload.get("strategy", "")).strip()) and isinstance(audit_payload, dict):
        fallback_decision = _proposal_to_decision(audit_payload)
        if fallback_decision:
            decision_payload = fallback_decision
            if not decision_reason:
                fallback_reason = str(fallback_decision.get("reason", "")).strip()
                audit_reason = str(audit_payload.get("reason", "")).strip()
                audit_message = str(latest_audit.get("message", "")).strip() if latest_audit else ""
                decision_reason = fallback_reason or audit_reason or audit_message
    decision_regime = decision_payload.get("regime") if isinstance(decision_payload.get("regime"), dict) else {}
    if not decision_regime and isinstance(audit_payload, dict):
        audit_regime = audit_payload.get("regime")
        if isinstance(audit_regime, dict):
            decision_regime = audit_regime
    market_regime = str(
        (decision_regime or {}).get("regime")
        or market_context.get("model_regime")
        or "range"
    ).strip()
    confidence = float(
        decision_payload.get("confidence")
        or (decision_regime or {}).get("confidence")
        or 0.0
    )
    atr = float(market_context.get("atr", 0.0) or 0.0)
    spot = float(market_context.get("nifty_price", 0.0) or 0.0)
    pe_wall = float((market_context.get("oi_analysis") or {}).get("pe_wall", 0.0) or 0.0)
    ce_wall = float((market_context.get("oi_analysis") or {}).get("ce_wall", 0.0) or 0.0)
    lower_range = float(min(pe_wall or spot - atr * 1.5, spot - atr * 1.25) if spot > 0 else 0.0)
    upper_range = float(max(ce_wall or spot + atr * 1.5, spot + atr * 1.25) if spot > 0 else 0.0)
    if lower_range <= 0 and spot > 0:
        lower_range = max(0.0, spot - atr * 1.25)
    if upper_range <= 0 and spot > 0:
        upper_range = spot + atr * 1.25
    risk_level = "LOW"
    if risk_usage_pct >= 2.0 or abs(float(decision_payload.get("confidence", 0.0) or 0.0)) < 0.6:
        risk_level = "WARNING"
    if risk_usage_pct >= 4.0 or bool(db.get_runtime_control("kill_switch", False)):
        risk_level = "DANGER"
    with db.connection() as conn:
        perf_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT strategy, date, trades_count, win_rate, avg_pnl, drawdown, sharpe, rank_score
                FROM strategy_performance
                ORDER BY id DESC
                LIMIT 40
                """
            ).fetchall()
        ]
    candidate_rankings = rank_strategy_candidates(
        market_context,
        regime=decision_regime if isinstance(decision_regime, dict) else None,
        recent_performance=perf_rows,
    )
    auto_trading_paused = bool(db.get_runtime_control("auto_trading_paused", False))
    quant_gate_enabled = _quant_gate_enabled(db)
    risk_engine_enabled = _risk_engine_enabled(db)
    latest_risk: dict[str, Any] = {}
    latest_quant: dict[str, Any] = {}
    risk_allowed = False
    quant_allowed = False
    if decision_payload and context_row and market_context:
        try:
            strategy_name, legs = build_trade_from_decision(decision_payload, market_context)
            audit_quant = audit_payload.get("quant") if isinstance(audit_payload, dict) else None
            if quant_gate_enabled:
                if isinstance(audit_quant, dict) and audit_quant:
                    latest_quant = dict(audit_quant)
                else:
                    quant_validator = _build_quant_validator(settings)
                    latest_quant = quant_validator.validate_candidates(
                        market_context,
                        [decision_payload],
                        regime=decision_regime if isinstance(decision_regime, dict) else None,
                    )
                quant_allowed = bool(latest_quant.get("allowed", False))
            else:
                latest_quant = {
                    "allowed": True,
                    "selected": decision_payload,
                    "selected_strategy": str(decision_payload.get("strategy", strategy_name) or strategy_name or "NO TRADE"),
                    "candidates": [],
                    "bypassed": True,
                    "enabled": False,
                    "reason": "Quant gate disabled via UI",
                }
                quant_allowed = True

            if risk_engine_enabled:
                risk_manager = _build_risk_manager(settings)
                latest_risk = risk_manager.evaluate_trade(market_context, legs)
                risk_allowed = bool(latest_risk.get("allowed", False))
            else:
                latest_risk = {
                    "allowed": True,
                    "bypassed": True,
                    "enabled": False,
                    "reason": "Risk engine disabled via UI",
                    "stats": {},
                }
                risk_allowed = True
            decision_payload.setdefault("strategy", strategy_name)
        except Exception as exc:
            latest_risk = {"allowed": False, "reason": str(exc)}
    restored_state = {
        "has_context": bool(context_row),
        "has_tick": bool(tick_row),
        "has_decision": bool(decision_payload),
        "has_open_trades": bool(open_rows),
        "active_strategy": active_strategy,
        "market_status": str(session["status"]),
        "market_reason": str(session["reason"]),
        "market_local_time": str(session["local_time"]),
    }
    return {
        "stage": stage,
        "state": state,
        "today": today_local,
        "today_strategies": today_strategies,
        "latest_audit": latest_audit,
        "active_strategy": active_strategy,
        "ai_model": ai_model,
        "decision_reason": decision_reason,
        "market_trend": market_trend,
        "open_positions": len(open_rows),
        "open_notional": open_notional,
        "max_capital_per_trade": max_capital,
        "risk_usage_pct": round(risk_usage_pct, 2),
        "market_status": str(session["status"]),
        "market_reason": str(session["reason"]),
        "market_local_time": str(session["local_time"]),
        "kill_switch": bool(db.get_runtime_control("kill_switch", False)),
        "auto_trading_paused": auto_trading_paused,
        "quant_gate_enabled": quant_gate_enabled,
        "risk_engine_enabled": risk_engine_enabled,
        "trading_mode": _current_trading_mode(settings, db),
        "market_regime": market_regime,
        "decision_confidence": round(confidence, 2),
        "risk_level": risk_level,
        "expected_range_low": round(lower_range, 2),
        "expected_range_high": round(upper_range, 2),
        "candidate_strategies": candidate_rankings[:5],
        "risk_allowed": risk_allowed,
        "quant_allowed": quant_allowed,
        "latest_quant": latest_quant,
        "latest_risk": latest_risk,
        "restored_state": restored_state,
        "market_session": session,
        "can_deploy": bool(
            risk_allowed
            and quant_allowed
            and bool(session["is_open"])
            and not bool(db.get_runtime_control("kill_switch", False))
            and bool(decision_payload)
            and bool(context_row)
            and len(open_rows) == 0
        ),
        "latest_decision": decision_payload,
    }


@app.get("/strategy/payoff")
def strategy_payoff() -> dict[str, Any]:
    db = _db()
    open_rows = db.fetch_open_trades()
    context_row = db.fetch_recent_context()
    context = _safe_json(context_row["payload"]) if context_row else {}
    if not open_rows:
        return {
            "strategy": "",
            "spot": float(context.get("nifty_price", 0.0) or 0.0),
            "legs": [],
            "curve": [],
        }
    strategy_name = str(open_rows[0].get("strategy", "")).strip()
    legs = [
        {
            "symbol": str(row.get("symbol", "")),
            "side": str(row.get("side", "BUY")),
            "qty": int(row.get("qty", 0) or 0),
        }
        for row in open_rows
    ]
    curve = generate_payoff_curve(context=context, legs=legs)
    return {
        "strategy": strategy_name,
        "spot": float(context.get("nifty_price", 0.0) or 0.0),
        "legs": legs,
        "curve": curve,
        "market_status": str(context.get("market_status", "")),
        "market_local_time": str(context.get("market_local_time", "")),
    }


@app.get("/trades/open")
def open_trades() -> dict[str, Any]:
    db = _db()
    trades = db.fetch_open_trades()
    tick_row = db.fetch_recent_realtime_tick()
    tick_payload = _safe_json(tick_row["payload"]) if tick_row else {}
    rows = _enrich_open_trades_with_ltp(
        trades,
        option_ltps=tick_payload.get("option_ltps", {}),
    )
    return {"count": len(rows), "rows": rows}


@app.get("/positions/greeks")
def position_greeks() -> dict[str, Any]:
    db = _db()
    context_row = db.fetch_recent_context()
    context = _safe_json(context_row["payload"]) if context_row else {}
    spot = float(context.get("nifty_price", 0.0) or 0.0)
    chain_rows = db.fetch_option_chain_latest()
    meta_lookup = _option_meta_lookup(chain_rows)
    open_rows = db.fetch_open_trades()
    net_delta = 0.0
    net_theta = 0.0
    net_vega = 0.0
    rows: list[dict[str, Any]] = []

    for trade in open_rows:
        symbol = str(trade.get("symbol", ""))
        side = str(trade.get("side", "BUY")).upper()
        qty = int(trade.get("qty", 0) or 0)
        if qty <= 0:
            continue
        sign = 1.0 if side == "BUY" else -1.0
        meta = meta_lookup.get(symbol, {})
        strike = float(meta.get("strike", 0.0) or 0.0)
        option_type = str(meta.get("option_type", "")).upper()
        iv = float(meta.get("iv", 0.0) or 0.0)
        days_to_expiry = int(meta.get("days_to_expiry", 0) or 0)
        delta_hint = float(meta.get("delta", 0.0) or 0.0)

        if (strike <= 0 or option_type not in {"CE", "PE"}) and symbol:
            parsed = _symbol_to_strike_option(symbol)
            if parsed is not None:
                strike, option_type = parsed

        time_years = max(days_to_expiry, 1) / 365.0
        calc_delta, calc_theta, calc_vega = _bs_greeks(
            spot=spot,
            strike=strike,
            time_years=time_years,
            iv=iv,
            is_call=(option_type == "CE"),
        )
        delta = delta_hint if abs(delta_hint) > 0 else calc_delta
        delta_contrib = sign * qty * delta
        theta_contrib = sign * qty * calc_theta
        vega_contrib = sign * qty * calc_vega
        net_delta += delta_contrib
        net_theta += theta_contrib
        net_vega += vega_contrib
        rows.append(
            {
                "id": trade.get("id"),
                "strategy": trade.get("strategy"),
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "strike": strike,
                "option_type": option_type,
                "iv": iv,
                "delta": delta,
                "theta_per_day": calc_theta,
                "vega_per_1pct": calc_vega,
                "delta_contrib": delta_contrib,
                "theta_contrib": theta_contrib,
                "vega_contrib": vega_contrib,
            }
        )
    return {
        "spot": spot,
        "rows": rows,
        "net": {
            "delta": float(net_delta),
            "theta_per_day": float(net_theta),
            "vega_per_1pct": float(net_vega),
        },
    }


@app.get("/trades/recent")
def recent_trades(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    db = _db()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return {"rows": [dict(r) for r in rows]}


@app.get("/orders/blotter")
def order_blotter(limit: int = Query(default=200, ge=1, le=2000)) -> dict[str, Any]:
    db = _db()
    rows = db.fetch_order_blotter(limit=limit)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = _safe_json(item.get("payload", ""))
        normalized.append(item)
    return {"rows": normalized}


@app.get("/pnl/summary")
def pnl_summary() -> dict[str, Any]:
    settings = _settings()
    timezone_name = str(settings["app"].get("timezone", "Asia/Kolkata"))
    trades_df = _trades_df(timezone_name)
    events = _realized_events(trades_df)
    today_local = pd.Timestamp.now(tz=timezone_name).strftime("%Y-%m-%d")
    if events.empty:
        realized_today = 0.0
        realized_total = 0.0
        day_wise: list[dict[str, Any]] = [{"day": today_local, "pnl": 0.0}]
        cumulative: list[dict[str, Any]] = []
    else:
        realized_today = float(events[events["local_day"] == today_local]["pnl"].sum())
        realized_total = float(events["pnl"].sum())
        day_df = (
            events.groupby("local_day", as_index=False)["pnl"]
            .sum()
            .rename(columns={"local_day": "day"})
            .sort_values("day", ascending=False)
        )
        if today_local not in set(day_df["day"].tolist()):
            day_df = pd.concat(
                [pd.DataFrame([{"day": today_local, "pnl": 0.0}]), day_df],
                ignore_index=True,
            )
            day_df = day_df.drop_duplicates(subset=["day"], keep="first")
        day_wise = day_df.to_dict(orient="records")

        cdf = events.sort_values("timestamp_local").copy()
        cdf["cum_pnl"] = cdf["pnl"].cumsum()
        cumulative = [
            {
                "timestamp": row["timestamp_local"].isoformat(),
                "cum_pnl": float(row["cum_pnl"]),
            }
            for _, row in cdf.iterrows()
        ]

    unrealized_open = _unrealized_open_pnl(_db().fetch_open_trades())
    return {
        "today": today_local,
        "realized_today": realized_today,
        "realized_total": realized_total,
        "unrealized_open": unrealized_open,
        "day_wise": day_wise,
        "cumulative": cumulative,
    }


@app.get("/oi/heatmap")
def oi_heatmap(
    strikes_each_side: int = Query(default=6, ge=2, le=30),
) -> dict[str, Any]:
    db = _db()
    settings = _settings()
    symbol = str(settings["app"].get("symbol", "NIFTY 50"))
    block_size = _strike_block_size(symbol)
    timezone_name = str(settings["app"].get("timezone", "Asia/Kolkata"))
    session = _market_session(settings)

    context_row = db.fetch_recent_context()
    context = _safe_json(context_row["payload"]) if context_row else {}
    spot = float(context.get("nifty_price", 0.0))
    with db.connection() as conn:
        rows = [dict(row) for row in conn.execute(
            """
            SELECT *
            FROM option_chain
            ORDER BY timestamp ASC, id ASC
            """
        ).fetchall()]
    chain_df = pd.DataFrame(rows)
    if chain_df.empty or "strike" not in chain_df:
        return {"block_size": block_size, "rows": [], "latest_timestamp": "", "previous_timestamp": ""}

    if "strike" in chain_df:
        chain_df["strike"] = chain_df["strike"].astype(float)
    heatmap = _build_heatmap_snapshot(
        chain_df=chain_df,
        block_size=block_size,
        timezone_name=timezone_name,
        session=session,
        spot=spot,
        strikes_each_side=strikes_each_side,
    )
    return heatmap


@app.get("/ticks/recent")
def ticks_recent(
    limit: int = Query(default=600, ge=10, le=5000),
    bucket_seconds: int = Query(default=5, ge=1, le=300),
) -> dict[str, Any]:
    db = _db()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, nifty_price, vix, volume, payload FROM realtime_ticks ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return {"rows": []}
    df["ts"] = _to_timezone_series(df["timestamp"], "Asia/Kolkata")
    df = df[df["ts"].notna()].copy()
    df = df.sort_values("ts")
    df["bucket"] = (df["ts"].astype("int64") // (bucket_seconds * 1_000_000_000)) * (
        bucket_seconds * 1_000_000_000
    )
    agg = (
        df.groupby("bucket", as_index=False)
        .agg({"nifty_price": "last", "vix": "last", "volume": "last", "ts": "last"})
        .sort_values("ts")
    )
    out = [
        {
            "timestamp": row["ts"].isoformat(),
            "nifty_price": float(row["nifty_price"]),
            "vix": float(row["vix"]) if pd.notna(row["vix"]) else 0.0,
            "volume": int(row["volume"]) if pd.notna(row["volume"]) else 0,
        }
        for _, row in agg.iterrows()
    ]
    return {"rows": out}


@app.get("/audit/events")
def audit_events(limit: int = Query(default=500, ge=1, le=500)) -> dict[str, Any]:
    db = _db()
    since = _ist_now() - timedelta(days=2)
    rows = db.fetch_audit_events_since(since.isoformat(), limit=limit)
    return {"rows": [_normalize_audit_row(r) for r in rows]}


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket) -> None:
    await websocket.accept()
    db = _db()
    last_id = 0
    try:
        while True:
            session = _market_session(_settings())
            if not bool(session["is_open"]):
                await websocket.close(code=1000)
                return
            tick = db.fetch_recent_realtime_tick()
            if tick and int(tick.get("id", 0)) > last_id:
                payload = _safe_json(tick.get("payload", ""))
                try:
                    await websocket.send_json({"type": "tick", "data": payload})
                except Exception:
                    return
                last_id = int(tick.get("id", 0))
            await asyncio.sleep(1.0)
    except Exception:
        return


@app.websocket("/ws/strategy")
async def ws_strategy(websocket: WebSocket) -> None:
    await websocket.accept()
    db = _db()
    last_id = 0
    try:
        while True:
            events = db.fetch_audit_events(limit=1)
            if events:
                top = events[0]
                if int(top.get("id", 0)) > last_id:
                    try:
                        await websocket.send_json({"type": "audit", "data": _normalize_audit_row(top)})
                    except Exception:
                        return
                    last_id = int(top.get("id", 0))
            await asyncio.sleep(1.0)
    except Exception:
        return
