from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.market_hours import market_session_status


LOGGER = logging.getLogger(__name__)


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":")
    return int(hour_str), int(minute_str)


@dataclass(slots=True)
class TradingScheduler:
    settings: dict[str, Any]
    on_decision: Callable[[], None]
    on_pnl_monitor: Callable[[], None]
    on_forced_exit: Callable[[], None]
    on_day_end_optimize: Callable[[], None]
    on_token_refresh: Callable[[], None] | None = None
    _scheduler: BackgroundScheduler = field(init=False, repr=False)

    def __post_init__(self) -> None:
        tz = self.settings["app"].get("timezone", "Asia/Kolkata")
        self._scheduler = BackgroundScheduler(timezone=tz)

    def start(self) -> None:
        decision_hour, _decision_min = _parse_hhmm(self.settings["scheduler"]["decision_time"])
        force_exit_hour, force_exit_min = _parse_hhmm(self.settings["scheduler"]["forced_exit_time"])
        optimize_hour, optimize_min = _parse_hhmm(self.settings["scheduler"]["optimization_time"])
        token_hour, token_min = _parse_hhmm(
            self.settings["scheduler"].get("token_refresh_time", "08:45")
        )
        interval_seconds = int(self.settings["scheduler"]["pnl_interval_seconds"])
        timezone_name = str(self.settings["app"].get("timezone", "Asia/Kolkata"))
        open_time = str(self.settings["app"].get("market_open_time", "09:15"))
        close_time = str(self.settings["app"].get("market_close_time", "15:30"))

        def _session_guard(job_name: str, func: Callable[[], None]) -> Callable[[], None]:
            def _wrapped() -> None:
                session = market_session_status(
                    timezone_name=timezone_name,
                    open_time=open_time,
                    close_time=close_time,
                )
                if not bool(session["is_open"]):
                    LOGGER.info(
                        "%s skipped because market is %s (%s)",
                        job_name,
                        session["status"],
                        session["reason"],
                    )
                    return
                func()

            return _wrapped

        self._scheduler.add_job(
            _session_guard("Strategy decision cycle", self.on_decision),
            trigger=CronTrigger(hour=f"{decision_hour}-15", minute="*/5"),
            id="strategy_decision",
            replace_existing=True,
        )
        self._scheduler.add_job(
            _session_guard("PnL monitor", self.on_pnl_monitor),
            trigger=IntervalTrigger(seconds=interval_seconds),
            id="pnl_monitor",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self.on_forced_exit,
            trigger=CronTrigger(hour=force_exit_hour, minute=force_exit_min),
            id="forced_exit",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self.on_day_end_optimize,
            trigger=CronTrigger(hour=optimize_hour, minute=optimize_min),
            id="day_end_optimize",
            replace_existing=True,
        )
        if self.on_token_refresh is not None:
            self._scheduler.add_job(
                self.on_token_refresh,
                trigger=CronTrigger(hour=token_hour, minute=token_min),
                id="kite_token_refresh",
                replace_existing=True,
            )
        self._scheduler.start()
        LOGGER.info("Trading scheduler started")

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        LOGGER.info("Trading scheduler stopped")
