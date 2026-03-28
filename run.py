from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import threading
from importlib.util import find_spec
from pathlib import Path
from shutil import which
from typing import Any

from ai.llm_reasoner import LLMReasoner
from ai.quant_validator import QuantValidator
from ai.strategy_generator import build_trade_from_decision
from ai.strategy_optimizer import StrategyOptimizer
from analytics.pnl_monitor import PnLMonitor
from core.alerts import TelegramNotifier
from core.config import load_settings
from core.logging_setup import configure_logging
from core.scheduler import TradingScheduler
from core.system_controller import SystemController
from data.kite_market_data import KiteMarketData
from data.option_chain_fetcher import OptionChainFetcher
from database.sqlite_manager import SQLiteManager
from execution.kite_client import KiteClient
from execution.order_manager import OrderManager
from execution.websocket_stream import WebSocketStream
from risk.risk_manager import RiskManager


LOGGER = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent


def _module_available(module_name: str) -> bool:
    return find_spec(module_name) is not None


def _websocket_runtime_available() -> bool:
    return _module_available("websockets") or _module_available("wsproto")


def _apply_risk_calibration(settings: dict[str, Any]) -> None:
    risk = settings.setdefault("risk", {})
    pct_raw = risk.get("max_loss_pct_per_trade")
    try:
        pct = float(pct_raw) if pct_raw is not None else 0.0
    except (TypeError, ValueError):
        pct = 0.0
    if pct <= 0.0:
        return
    capital = float(risk.get("max_capital_per_trade") or 0.0)
    if capital <= 0.0:
        raise RuntimeError(
            "risk.max_capital_per_trade must be set when using risk.max_loss_pct_per_trade"
        )
    max_loss = capital * pct / 100.0
    risk["max_loss_per_trade"] = max_loss
    LOGGER.info(
        "Risk calibrated: %.2f%% of %.2f = %.2f max loss per trade",
        pct,
        capital,
        max_loss,
    )


def _npm_executable() -> str | None:
    if os.name == "nt":
        return which("npm.cmd") or which("npm")
    return which("npm")


def _forward_process_output(process: subprocess.Popen[str], source: str) -> None:
    def _pump() -> None:
        stream = process.stdout
        if stream is None:
            return
        for raw_line in iter(stream.readline, ""):
            line = raw_line.rstrip()
            if not line:
                continue
            LOGGER.info("[%s] %s", source, line)
        try:
            stream.close()
        except Exception:
            pass

    threading.Thread(target=_pump, daemon=True).start()


def start_react_process(settings: dict) -> subprocess.Popen | None:
    frontend_dir = BASE_DIR / "frontend-react"
    if not frontend_dir.exists():
        LOGGER.warning("React frontend folder not found at %s", frontend_dir)
        return None
    npm_exec = _npm_executable()
    if not npm_exec:
        LOGGER.error("React frontend not started: npm is not installed or not in PATH.")
        return None
    node_modules_dir = frontend_dir / "node_modules"
    if not node_modules_dir.exists():
        LOGGER.info("Installing frontend dependencies (npm install)...")
        install = subprocess.run([npm_exec, "install"], cwd=frontend_dir)
        if install.returncode != 0:
            LOGGER.error("React frontend not started: npm install failed with code %s", install.returncode)
            return None
    host = str(settings["dashboard"].get("react_host", "127.0.0.1"))
    port = int(settings["dashboard"].get("react_port", 5173))
    cmd = [npm_exec, "run", "dev", "--", "--host", host, "--port", str(port)]
    LOGGER.info("Starting React frontend on http://%s:%s", host, port)
    process = subprocess.Popen(
        cmd,
        cwd=frontend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    _forward_process_output(process, "frontend")
    time.sleep(1.5)
    if process.poll() is not None:
        LOGGER.error("React frontend process exited during startup. Check frontend terminal output.")
        return None
    return process


def start_api_process(settings: dict) -> subprocess.Popen | None:
    api_settings = settings.get("api", {})
    if not bool(api_settings.get("enabled", True)):
        return None
    if not _module_available("uvicorn"):
        LOGGER.error(
            "API server not started because 'uvicorn' is missing in this environment. "
            "Run: %s -m pip install -r requirements.txt",
            sys.executable,
        )
        return None
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "api.main:app",
        "--host",
        str(api_settings.get("host", "127.0.0.1")),
        "--port",
        str(api_settings.get("port", 8000)),
    ]
    env = os.environ.copy()
    env["AI_OPTIONS_DESK_BASE_DIR"] = str(BASE_DIR)
    LOGGER.info(
        "Starting API server on http://%s:%s",
        api_settings.get("host", "127.0.0.1"),
        api_settings.get("port", 8000),
    )
    process = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    _forward_process_output(process, "api")
    time.sleep(1.0)
    if process.poll() is not None:
        LOGGER.error(
            "API process exited during startup. Check errors above. "
            "React frontend requires API server at http://%s:%s",
            api_settings.get("host", "127.0.0.1"),
            api_settings.get("port", 8000),
        )
        return None
    return process


def bootstrap_controller(settings: dict) -> tuple[SystemController, TradingScheduler]:
    settings.setdefault("__base_dir__", BASE_DIR)
    db = SQLiteManager(BASE_DIR / "ai_options_desk.db")
    db.ensure_runtime_control(
        "kill_switch",
        bool(settings.get("dashboard", {}).get("kill_switch_default", False)),
    )
    db.ensure_runtime_control("quant_gate_enabled", False)
    db.ensure_runtime_control("risk_engine_enabled", False)
    if bool(settings["app"].get("clear_runtime_data_on_start", False)):
        db.clear_market_runtime_data()
        LOGGER.info("Cleared stale runtime market data from database")
    notifier = TelegramNotifier(settings)
    kite_client = KiteClient(settings)
    market_data = KiteMarketData(
        kite_client=kite_client,
        symbol=settings["app"]["symbol"],
        timezone=settings["app"].get("timezone", "Asia/Kolkata"),
        market_open_time=settings["app"].get("market_open_time", "09:15"),
        market_close_time=settings["app"].get("market_close_time", "15:30"),
    )
    chain_fetcher = OptionChainFetcher(kite_client)
    llm = LLMReasoner(settings)
    risk_manager = RiskManager(
        max_loss_per_trade=float(settings["risk"]["max_loss_per_trade"]),
        monte_carlo_paths=int(settings["risk"]["monte_carlo_paths"]),
    )
    order_manager = OrderManager(
        kite_client,
        db,
        settings["app"]["mode"],
        product=str(settings.get("kite", {}).get("product", "NRML")).upper(),
        notifier=notifier,
    )
    pnl_monitor = PnLMonitor(
        kite_client,
        profit_target=float(settings["risk"]["profit_target"]),
        stoploss=float(settings["risk"]["stoploss"]),
        force_exit_time=str(settings["scheduler"]["forced_exit_time"]),
        mode=str(settings["app"]["mode"]),
        open_trades_provider=db.fetch_open_trades,
    )
    websocket = WebSocketStream(settings=settings, kite_client=kite_client)
    optimizer = StrategyOptimizer(db)
    quant_validator = QuantValidator()

    controller = SystemController(
        settings=settings,
        db=db,
        market_data=market_data,
        chain_fetcher=chain_fetcher,
        llm=llm,
        risk_manager=risk_manager,
        order_manager=order_manager,
        pnl_monitor=pnl_monitor,
        websocket_stream=websocket,
        strategy_optimizer=optimizer,
        quant_validator=quant_validator,
        notifier=notifier,
    )
    websocket.on_tick = controller.handle_market_tick
    scheduler = TradingScheduler(
        settings=settings,
        on_decision=controller.run_strategy_decision_cycle,
        on_pnl_monitor=controller.monitor_and_exit_if_needed,
        on_forced_exit=controller.force_exit_all_positions,
        on_day_end_optimize=controller.run_day_end_optimization,
        on_token_refresh=controller.refresh_kite_session,
    )
    return controller, scheduler


def _restore_controller_state(controller: SystemController) -> None:
    db = controller.db
    try:
        recent_context = db.fetch_recent_context()
        if recent_context:
            controller.latest_context = dict(json.loads(str(recent_context.get("payload", "{}"))))
            LOGGER.info("Restored latest market context from database")

        recent_tick = db.fetch_recent_realtime_tick()
        if recent_tick:
            tick_payload = json.loads(str(recent_tick.get("payload", "{}")))
            if controller.latest_context:
                controller.latest_context.update(
                    {
                        "nifty_price": float(
                            tick_payload.get("nifty_price", controller.latest_context.get("nifty_price", 0.0)) or 0.0
                        ),
                        "vix": float(tick_payload.get("vix", controller.latest_context.get("vix", 0.0)) or 0.0),
                        "volume": int(tick_payload.get("volume", controller.latest_context.get("volume", 0)) or 0),
                        "market_status": str(
                            tick_payload.get("market_status", controller.latest_context.get("market_status", ""))
                        ),
                        "market_status_reason": str(
                            tick_payload.get("market_status_reason", controller.latest_context.get("market_status_reason", ""))
                        ),
                        "market_local_time": str(
                            tick_payload.get("market_local_time", controller.latest_context.get("market_local_time", ""))
                        ),
                    }
                )
            else:
                controller.latest_context = {
                    "nifty_price": float(tick_payload.get("nifty_price", 0.0) or 0.0),
                    "vix": float(tick_payload.get("vix", 0.0) or 0.0),
                    "volume": int(tick_payload.get("volume", 0) or 0),
                    "market_status": str(tick_payload.get("market_status", "")),
                    "market_status_reason": str(tick_payload.get("market_status_reason", "")),
                    "market_local_time": str(tick_payload.get("market_local_time", "")),
                }
            LOGGER.info("Restored latest realtime tick from database")

        recent_decision = db.fetch_recent_ai_decision()
        if recent_decision:
            controller.latest_decision = json.loads(str(recent_decision.get("payload", "{}")))
            LOGGER.info("Restored latest AI decision from database")

        open_trades = db.fetch_open_trades()
        controller.latest_order_results = [
            {
                "symbol": str(row.get("symbol", "")),
                "side": str(row.get("side", "")),
                "qty": int(row.get("qty", 0) or 0),
                "status": str(row.get("status", "")),
                "mode": str(row.get("mode", "")),
            }
            for row in open_trades
        ]

        if controller.latest_context and controller.latest_decision:
            try:
                context = dict(controller.latest_context)
                decision_payload = dict(controller.latest_decision)
                regime = decision_payload.get("regime", {}) if isinstance(decision_payload.get("regime", {}), dict) else None
                quant_result = controller.quant_validator.validate_candidates(
                    context,
                    [decision_payload],
                    regime=regime,
                )
                controller.latest_quant = quant_result
                if quant_result.get("selected"):
                    decision_for_risk = dict(quant_result["selected"])
                else:
                    decision_for_risk = dict(decision_payload)
                strategy_name, legs = build_trade_from_decision(decision_for_risk, context)
                controller.latest_risk = controller.risk_manager.evaluate_trade(context, legs)
                controller.latest_order_results = controller.latest_order_results or [
                    {"strategy": strategy_name, "legs": len(legs), "status": "RESTORED"}
                ]
                LOGGER.info("Restored quant and risk state from database")
            except Exception:
                LOGGER.exception("Failed to restore quant/risk state from database")
    except Exception:
        LOGGER.exception("Failed to restore controller state from database")


def main() -> None:
    settings = load_settings(BASE_DIR / "config/settings.yaml")
    _apply_risk_calibration(settings)
    settings["__base_dir__"] = BASE_DIR
    configure_logging(settings["app"].get("log_level", "INFO"))
    LOGGER.info("Starting Jugal's AI Options Desk")

    controller, scheduler = bootstrap_controller(settings)
    session = controller.market_data.market_session_status()
    LOGGER.info(
        "Market status at startup: %s (%s) | local=%s",
        session["status"],
        session["reason"],
        session["local_time"],
    )
    _restore_controller_state(controller)
    api_process = start_api_process(settings)
    frontend = str(settings["dashboard"].get("frontend", "react")).lower()
    react_process: subprocess.Popen | None = None
    if frontend != "react":
        LOGGER.warning("Unsupported dashboard.frontend=%s. React is the only supported frontend now.", frontend)
    api_host = settings.get("api", {}).get("host", "127.0.0.1")
    api_port = settings.get("api", {}).get("port", 8000)
    react_host = settings["dashboard"].get("react_host", "127.0.0.1")
    react_port = settings["dashboard"].get("react_port", 5173)
    LOGGER.info("React frontend mode enabled.")
    if not _websocket_runtime_available():
        LOGGER.warning(
            "WebSocket runtime is missing. Install with: %s -m pip install \"uvicorn[standard]\". "
            "Dashboard will continue in polling mode.",
            sys.executable,
        )
    LOGGER.info("API URL: http://%s:%s", api_host, api_port)
    LOGGER.info("Frontend URL: http://%s:%s", react_host, react_port)
    LOGGER.info("Start frontend with run_frontend_dev.bat or run_fullstack.bat")
    if api_process is None:
        LOGGER.error("API is not running. React page will not load until API starts.")
    react_process = start_react_process(settings)
    if react_process is None:
        LOGGER.error("React frontend is not running. Open run_frontend_dev.bat to inspect startup errors.")
    controller.websocket_stream.start()
    scheduler.start()

    # Kickoff one immediate cycle so dashboard populates without waiting.
    controller.run_strategy_decision_cycle()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        LOGGER.info("Shutdown signal received")
    finally:
        scheduler.stop()
        controller.websocket_stream.stop()
        if api_process is not None:
            api_process.terminate()
        if react_process is not None:
            react_process.terminate()
        LOGGER.info("System stopped cleanly")


if __name__ == "__main__":
    main()
