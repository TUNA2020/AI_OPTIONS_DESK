from __future__ import annotations

from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo


def _get_ist_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=5, minutes=30), "IST")
    )


def _parse_hhmm(value: str) -> time:
    hour_str, minute_str = value.split(":")
    return time(hour=int(hour_str), minute=int(minute_str))


def market_session_status(
    timezone_name: str,
    open_time: str = "09:15",
    close_time: str = "15:30",
    now_utc: datetime | None = None,
) -> dict[str, str | bool]:
    tz = ZoneInfo(timezone_name)
    current_utc = now_utc or datetime.now(timezone.utc)
    if current_utc.tzinfo is None:
        current_utc = current_utc.replace(tzinfo=timezone.utc)
    local_dt = current_utc.astimezone(tz)
    open_at = _parse_hhmm(open_time)
    close_at = _parse_hhmm(close_time)

    if local_dt.weekday() >= 5:
        return {
            "is_open": False,
            "status": "CLOSED",
            "reason": "Weekend",
            "local_time": local_dt.isoformat(),
            "open_time": open_time,
            "close_time": close_time,
        }

    now_local_time = local_dt.time()
    is_open = open_at <= now_local_time <= close_at
    reason = "Within session hours" if is_open else "Outside session hours"
    return {
        "is_open": is_open,
        "status": "OPEN" if is_open else "CLOSED",
        "reason": reason,
        "local_time": local_dt.isoformat(),
        "open_time": open_time,
        "close_time": close_time,
    }
