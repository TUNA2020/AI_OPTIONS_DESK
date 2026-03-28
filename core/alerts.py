from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import requests


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TelegramNotifier:
    settings: dict[str, Any]
    timeout_seconds: float = 5.0
    _enabled: bool = field(init=False, default=False)
    _bot_token: str = field(init=False, default="")
    _chat_id: str = field(init=False, default="")
    _disable_notification: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        cfg = self.settings.get("telegram", {})
        self._enabled = bool(cfg.get("enabled", False))
        self._bot_token = str(cfg.get("bot_token", "")).strip()
        self._chat_id = str(cfg.get("chat_id", "")).strip()
        self._disable_notification = bool(cfg.get("disable_notification", False))

    def send(self, title: str, message: str, payload: dict[str, Any] | None = None) -> None:
        if not self._enabled:
            return
        if not self._bot_token or not self._chat_id:
            LOGGER.warning("Telegram alerts enabled but bot_token/chat_id is missing.")
            return

        text = f"[{title}] {message}"
        if payload:
            text += "\n" + json.dumps(payload, default=str)[:1200]

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        data = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "disable_notification": self._disable_notification,
        }
        try:
            response = requests.post(url, data=data, timeout=self.timeout_seconds)
            response.raise_for_status()
        except Exception:
            LOGGER.exception("Failed to send Telegram alert.")
