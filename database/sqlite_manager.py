from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Generator


def _get_ist_time() -> datetime:
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=5, minutes=30), "IST")
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default)


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SQLiteManager:
    db_path: Path = Path("ai_options_desk.db")

    def __post_init__(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    price REAL NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    metadata TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS market_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    nifty_price REAL NOT NULL,
                    vix REAL NOT NULL,
                    trend TEXT NOT NULL,
                    atr REAL NOT NULL,
                    expiry_days INTEGER NOT NULL,
                    capital_available REAL NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS option_chain (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    strike REAL NOT NULL,
                    ce_oi INTEGER,
                    pe_oi INTEGER,
                    ce_iv REAL,
                    pe_iv REAL,
                    ce_delta REAL,
                    pe_delta REAL,
                    volume INTEGER,
                    payload TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    capital_to_use REAL NOT NULL,
                    ce_strike REAL,
                    pe_strike REAL,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    date TEXT NOT NULL,
                    trades_count INTEGER NOT NULL,
                    win_rate REAL NOT NULL,
                    avg_pnl REAL NOT NULL,
                    drawdown REAL NOT NULL,
                    sharpe REAL NOT NULL,
                    rank_score REAL NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS realtime_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    nifty_price REAL NOT NULL,
                    vix REAL,
                    volume INTEGER,
                    payload TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_controls (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS order_blotter (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    strategy TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def insert_trade(
        self,
        strategy: str,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        status: str,
        mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO trades
                (timestamp, strategy, symbol, side, qty, price, status, mode, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _get_ist_time().isoformat(),
                    strategy,
                    symbol,
                    side,
                    qty,
                    price,
                    status,
                    mode,
                    _json_dumps(metadata or {}),
                ),
            )

    def insert_market_context(self, context: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO market_context
                (timestamp, nifty_price, vix, trend, atr, expiry_days, capital_available, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _get_ist_time().isoformat(),
                    float(context.get("nifty_price", 0.0)),
                    float(context.get("vix", 0.0)),
                    str(context.get("trend", "unknown")),
                    float(context.get("atr", 0.0)),
                    int(context.get("expiry_days", 0)),
                    float(context.get("capital_available", 0.0)),
                    _json_dumps(context),
                ),
            )

    def insert_option_chain(self, chain_rows: list[dict[str, Any]]) -> None:
        now = _get_ist_time().isoformat()
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO option_chain
                (timestamp, strike, ce_oi, pe_oi, ce_iv, pe_iv, ce_delta, pe_delta, volume, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        now,
                        float(row.get("strike", 0.0)),
                        int(row.get("ce_oi", 0)),
                        int(row.get("pe_oi", 0)),
                        float(row.get("ce_iv", 0.0)),
                        float(row.get("pe_iv", 0.0)),
                        float(row.get("ce_delta", 0.0)),
                        float(row.get("pe_delta", 0.0)),
                        int(row.get("volume", 0)),
                        _json_dumps(row),
                    )
                    for row in chain_rows
                ],
            )

    def insert_ai_decision(self, decision: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO ai_decisions
                (timestamp, strategy, capital_to_use, ce_strike, pe_strike, confidence, reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _get_ist_time().isoformat(),
                    str(decision.get("strategy", "")),
                    float(decision.get("capital_to_use", 0.0)),
                    float(decision.get("ce_strike", 0.0)),
                    float(decision.get("pe_strike", 0.0)),
                    float(decision.get("confidence", 0.0)),
                    str(decision.get("reason", "")),
                    _json_dumps(decision),
                ),
            )

    def upsert_strategy_performance(
        self,
        strategy: str,
        date: str,
        trades_count: int,
        win_rate: float,
        avg_pnl: float,
        drawdown: float,
        sharpe: float,
        rank_score: float,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO strategy_performance
                (strategy, date, trades_count, win_rate, avg_pnl, drawdown, sharpe, rank_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy,
                    date,
                    trades_count,
                    win_rate,
                    avg_pnl,
                    drawdown,
                    sharpe,
                    rank_score,
                ),
            )

    def fetch_open_trades(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_trade_closed(self, trade_id: int) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE trades SET status = 'CLOSED' WHERE id = ?", (trade_id,)
            )

    def fetch_recent_ai_decision(self) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM ai_decisions ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def fetch_recent_context(self) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM market_context ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def fetch_option_chain_latest(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM option_chain
                WHERE timestamp = (SELECT MAX(timestamp) FROM option_chain)
                ORDER BY strike ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def strategy_daily_pnls(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT strategy,
                       substr(timestamp, 1, 10) AS date,
                       COUNT(*) AS trades_count,
                       AVG(CASE WHEN side='SELL' THEN price ELSE -price END) AS avg_pnl
                FROM trades
                GROUP BY strategy, substr(timestamp, 1, 10)
                """
            ).fetchall()
        result = [dict(row) for row in rows]
        LOGGER.debug("Fetched strategy daily pnls: %d rows", len(result))
        return result

    def insert_realtime_tick(self, tick: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO realtime_ticks
                (timestamp, nifty_price, vix, volume, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _get_ist_time().isoformat(),
                    float(tick.get("nifty_price", 0.0)),
                    float(tick.get("vix", 0.0)),
                    int(tick.get("volume", 0)),
                    _json_dumps(tick),
                ),
            )

    def fetch_recent_realtime_tick(self) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM realtime_ticks ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def clear_market_runtime_data(self) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM market_context")
            conn.execute("DELETE FROM option_chain")
            conn.execute("DELETE FROM ai_decisions")
            conn.execute("DELETE FROM realtime_ticks")
            conn.execute("DELETE FROM order_blotter")

    def insert_audit_event(
        self,
        level: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (timestamp, level, event_type, message, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _get_ist_time().isoformat(),
                    str(level).upper(),
                    str(event_type),
                    str(message),
                    _json_dumps(payload or {}),
                ),
            )

    def fetch_audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM audit_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_audit_events_since(self, since_iso: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM audit_events
                WHERE timestamp >= ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(since_iso), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_runtime_control(self, key: str, value: Any) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime_controls (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (
                    str(key),
                    _json_dumps(value),
                    _get_ist_time().isoformat(),
                ),
            )

    def ensure_runtime_control(self, key: str, default_value: Any) -> Any:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_controls WHERE key = ?", (str(key),)
            ).fetchone()
            if row is None:
                value_json = _json_dumps(default_value)
                conn.execute(
                    """
                    INSERT INTO runtime_controls (key, value, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (str(key), value_json, _get_ist_time().isoformat()),
                )
                return default_value
            try:
                return json.loads(str(row["value"]))
            except Exception:
                return default_value

    def get_runtime_control(self, key: str, default_value: Any = None) -> Any:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_controls WHERE key = ?", (str(key),)
            ).fetchone()
        if row is None:
            return default_value
        try:
            return json.loads(str(row["value"]))
        except Exception:
            return default_value

    def fetch_runtime_controls(self) -> dict[str, Any]:
        with self.connection() as conn:
            rows = conn.execute("SELECT key, value FROM runtime_controls").fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            key = str(row["key"])
            try:
                result[key] = json.loads(str(row["value"]))
            except Exception:
                result[key] = row["value"]
        return result

    def insert_order_blotter_event(
        self,
        symbol: str,
        side: str,
        qty: int,
        status: str,
        mode: str,
        message: str,
        strategy: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO order_blotter
                (timestamp, strategy, symbol, side, qty, status, mode, message, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _get_ist_time().isoformat(),
                    str(strategy),
                    str(symbol),
                    str(side).upper(),
                    int(qty),
                    str(status).upper(),
                    str(mode).lower(),
                    str(message),
                    _json_dumps(payload or {}),
                ),
            )

    def fetch_order_blotter(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM order_blotter
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]
