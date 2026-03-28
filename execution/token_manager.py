from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from core.retry import retry

try:
    from kiteconnect import KiteConnect
except Exception:  # pragma: no cover
    KiteConnect = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)
KITE_LOGIN_URL = "https://kite.zerodha.com/api/login"
KITE_TWOFA_URL = "https://kite.zerodha.com/api/twofa"


@dataclass(slots=True)
class KiteTokenManager:
    settings: dict[str, Any]
    base_dir: Path
    token_store_path: Path = field(init=False)
    api_key: str = field(init=False)
    api_secret: str = field(init=False)

    def __post_init__(self) -> None:
        if KiteConnect is None:
            raise RuntimeError("kiteconnect package is required for Kite token management.")
        token_store = self.settings["kite"].get("token_store_path", "config/kite_tokens.json")
        path = Path(token_store)
        self.token_store_path = path if path.is_absolute() else (self.base_dir / path)
        self.token_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.api_key = str(self.settings["kite"]["api_key"])
        self.api_secret = str(self.settings["kite"].get("api_secret", "")).strip()

    def _kite(self) -> Any:
        return KiteConnect(api_key=self.api_key)

    def _load_token_file(self) -> dict[str, str]:
        if not self.token_store_path.exists():
            return {}
        try:
            data = json.loads(self.token_store_path.read_text(encoding="utf-8"))
            return {
                "access_token": str(data.get("access_token") or ""),
                "refresh_token": str(data.get("refresh_token") or ""),
            }
        except Exception:
            LOGGER.exception("Failed reading token store %s", self.token_store_path)
            return {}

    def _save_token_file(self, access_token: str, refresh_token: str) -> None:
        stored = self._load_token_file()
        existing_refresh = str(stored.get("refresh_token") or "").strip()
        refresh_value = refresh_token.strip() or existing_refresh
        payload = {"access_token": access_token, "refresh_token": refresh_value}
        self.token_store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _is_invalid_or_expired_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "invalid" in text
            or "expired" in text
            or "tokenexception" in text
            or "access token" in text
        )

    @retry(attempts=2, delay_seconds=0.8)
    def _token_valid(self, access_token: str) -> bool:
        if not access_token:
            return False
        kite = self._kite()
        kite.set_access_token(access_token)
        try:
            _ = kite.profile()
            return True
        except Exception:
            return False

    def _auto_login_settings(self) -> dict[str, Any]:
        raw = self.settings["kite"].get("auto_login", {})
        if not isinstance(raw, dict):
            raw = {}
        return raw

    def _auto_login_enabled(self) -> bool:
        cfg = self._auto_login_settings()
        return bool(cfg.get("enabled", False))

    def _auto_login_timeout(self) -> int:
        cfg = self._auto_login_settings()
        try:
            timeout = int(cfg.get("timeout_seconds", 20))
        except (TypeError, ValueError):
            timeout = 20
        return timeout if timeout > 0 else 20

    def _login_help_message(self) -> str:
        login_url = self._kite().login_url()
        return (
            "No valid Kite access token. Generate a fresh request token from Kite login, then either "
            "set `KITE_REQUEST_TOKEN` in terminal or paste it into `kite.request_token` in settings.yaml, "
            "then run `python run.py`. "
            "For automated login, enable `kite.auto_login.enabled: true` and provide "
            "`user_id/password/totp_secret`. "
            f"Login URL: {login_url}"
        )

    def _totp_code(self, secret: str, digits: int = 6, period_seconds: int = 30) -> str:
        normalized = secret.strip().replace(" ", "")
        if not normalized:
            raise RuntimeError("Kite TOTP secret is empty.")
        padded = normalized + ("=" * ((8 - (len(normalized) % 8)) % 8))
        try:
            key = base64.b32decode(padded, casefold=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError(
                "Invalid Kite TOTP secret. Set a valid base32 value in "
                "`KITE_TOTP_SECRET` or `kite.auto_login.totp_secret`."
            ) from exc
        counter = int(time.time() // period_seconds)
        digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
        return f"{binary % (10**digits):0{digits}d}"

    def _resolve_twofa_value(self, cfg: dict[str, Any]) -> tuple[str, str]:
        totp_secret = str(
            os.getenv("KITE_TOTP_SECRET") or cfg.get("totp_secret") or ""
        ).strip()
        if totp_secret:
            return self._totp_code(totp_secret), "totp"
        pin = str(
            os.getenv("KITE_PIN")
            or os.getenv("KITE_TWOFA_PIN")
            or cfg.get("twofa_pin")
            or ""
        ).strip()
        if pin:
            return pin, "pin"
        raise RuntimeError(
            "Kite auto-login needs either TOTP secret or PIN. Set `KITE_TOTP_SECRET` "
            "or `KITE_PIN` (or corresponding values in `kite.auto_login`)."
        )

    def _extract_request_token(self, candidate_url: str) -> str:
        if not candidate_url:
            return ""
        parsed = urlparse(candidate_url)
        parsed_qs = parse_qs(parsed.query)
        return str((parsed_qs.get("request_token") or [""])[0]).strip()

    def _extract_request_token_from_text(self, text: str) -> str:
        if not text:
            return ""
        candidates = [text, unquote(text)]
        patterns = [
            r"[?&]request_token=([A-Za-z0-9]+)",
            r"request_token['\"\\s:=]+([A-Za-z0-9]+)",
            r"request_token%3D([A-Za-z0-9]+)",
        ]
        for candidate in candidates:
            for pattern in patterns:
                match = re.search(pattern, candidate, flags=re.IGNORECASE)
                if match:
                    return str(match.group(1)).strip()
        return ""

    def _auto_login_request_token_credentials(self) -> str:
        cfg = self._auto_login_settings()
        user_id = str(os.getenv("KITE_USER_ID") or cfg.get("user_id") or "").strip()
        password = str(os.getenv("KITE_PASSWORD") or cfg.get("password") or "").strip()
        if not user_id or not password:
            raise RuntimeError(
                "Kite auto-login (credentials mode) requires `user_id` and `password`. "
                "Set env vars (`KITE_USER_ID`, `KITE_PASSWORD`) or values in `kite.auto_login`."
            )
        twofa_value, inferred_twofa_type = self._resolve_twofa_value(cfg)
        twofa_type = str(cfg.get("twofa_type", inferred_twofa_type)).strip().lower()
        if twofa_type not in {"totp", "pin"}:
            twofa_type = inferred_twofa_type
        skip_session = bool(cfg.get("skip_session", True))
        timeout_seconds = self._auto_login_timeout()
        kite = self._kite()

        headers = {"X-Kite-Version": "3", "User-Agent": "AI_OPTIONS_DESK/1.0"}
        session = requests.Session()

        login_page = session.get(kite.login_url(), headers=headers, timeout=timeout_seconds)
        login_page.raise_for_status()

        login_resp = session.post(
            KITE_LOGIN_URL,
            data={"user_id": user_id, "password": password},
            headers=headers,
            timeout=timeout_seconds,
        )
        login_resp.raise_for_status()
        try:
            login_json = login_resp.json()
        except ValueError as exc:
            raise RuntimeError("Kite auto-login failed: invalid JSON from login endpoint.") from exc
        if login_json.get("status") != "success":
            raise RuntimeError(f"Kite auto-login login step failed: {login_json!r}")
        req_id = str((login_json.get("data") or {}).get("request_id") or "").strip()
        if not req_id:
            raise RuntimeError(f"Kite auto-login login step missing request_id: {login_json!r}")

        twofa_resp = session.post(
            KITE_TWOFA_URL,
            data={
                "user_id": user_id,
                "request_id": req_id,
                "twofa_value": str(twofa_value),
                "twofa_type": twofa_type,
                "skip_session": str(skip_session).lower(),
            },
            headers=headers,
            timeout=timeout_seconds,
        )
        twofa_resp.raise_for_status()
        try:
            twofa_json = twofa_resp.json()
        except ValueError as exc:
            raise RuntimeError("Kite auto-login failed: invalid JSON from twofa endpoint.") from exc
        if twofa_json.get("status") != "success":
            raise RuntimeError(f"Kite auto-login 2FA step failed: {twofa_json!r}")

        max_redirect_hops = int(cfg.get("max_redirect_hops", 8) or 8)
        next_url = kite.login_url()
        last_url = next_url
        last_status = 0
        redirect_chain: list[str] = []
        for _ in range(max_redirect_hops):
            redirect_resp = session.get(
                next_url,
                allow_redirects=False,
                headers=headers,
                timeout=timeout_seconds,
            )
            last_url = str(redirect_resp.url or next_url)
            last_status = int(getattr(redirect_resp, "status_code", 0) or 0)
            redirect_chain.append(last_url)

            location = str(redirect_resp.headers.get("Location") or "").strip()
            if location:
                redirect_chain.append(location)
            for candidate in (location, last_url):
                request_token = self._extract_request_token(candidate)
                if request_token:
                    return request_token

            request_token = self._extract_request_token_from_text(
                str(getattr(redirect_resp, "text", "") or "")
            )
            if request_token:
                return request_token

            if not location:
                break
            next_url = urljoin(last_url, location)

        raise RuntimeError(
            "Kite auto-login credentials flow could not find request_token after redirect chain. "
            f"Last URL: {last_url} (HTTP {last_status}). "
            f"Chain: {redirect_chain!r}"
        )

    def _auto_login_request_token(self) -> str:
        if not self._auto_login_enabled():
            raise RuntimeError(
                "Kite auto-login is disabled. Set `kite.auto_login.enabled: true` to use it."
            )
        return self._auto_login_request_token_credentials()

    def ensure_access_token(self, force_refresh: bool = False) -> str:
        stored = self._load_token_file()
        access_token = str(
            self.settings["kite"].get("access_token") or stored.get("access_token") or ""
        )
        refresh_token = str(
            self.settings["kite"].get("refresh_token") or stored.get("refresh_token") or ""
        )
        self.settings["kite"]["refresh_token"] = refresh_token

        if not force_refresh and access_token and self._token_valid(access_token):
            self.settings["kite"]["access_token"] = access_token
            return access_token

        if refresh_token:
            try:
                return self.refresh_access_token(refresh_token)
            except Exception:
                LOGGER.exception("Kite refresh_token renewal failed. Falling back to login flow.")

        request_token_env = str(os.getenv("KITE_REQUEST_TOKEN", "")).strip()
        allow_cfg_request_token = bool(
            self.settings["kite"].get("allow_request_token_from_settings", False)
        )
        request_token_cfg = (
            str(self.settings["kite"].get("request_token", "")).strip()
            if allow_cfg_request_token
            else ""
        )
        request_token = request_token_env or request_token_cfg

        if request_token and self.api_secret:
            try:
                return self.generate_from_request_token(request_token)
            except Exception as exc:
                if self._is_invalid_or_expired_error(exc):
                    if self._auto_login_enabled():
                        LOGGER.warning(
                            "Configured request token is invalid/expired. Attempting auto-login."
                        )
                        request_token = self._auto_login_request_token()
                        return self.generate_from_request_token(request_token)
                    token_source = (
                        "environment variable KITE_REQUEST_TOKEN"
                        if request_token_env
                        else "kite.request_token in settings.yaml"
                    )
                    raise RuntimeError(
                        f"Provided request token ({token_source}) is invalid or expired. "
                        f"{self._login_help_message()}"
                    ) from exc
                raise

        if self._auto_login_enabled():
            request_token = self._auto_login_request_token()
            return self.generate_from_request_token(request_token)

        raise RuntimeError(self._login_help_message())

    @retry(attempts=3, delay_seconds=1.0)
    def refresh_access_token(self, refresh_token: str | None = None) -> str:
        if not self.api_secret:
            raise RuntimeError("kite.api_secret is required to auto-refresh access token.")
        token = refresh_token or str(self.settings["kite"].get("refresh_token", ""))
        if not token:
            raise RuntimeError("No refresh token available for Kite token renewal.")
        kite = self._kite()
        response = kite.renew_access_token(token, self.api_secret)
        access_token = str(response.get("access_token", ""))
        next_refresh = str(response.get("refresh_token", token))
        if not access_token:
            raise RuntimeError("Kite renew_access_token did not return access_token.")
        self.settings["kite"]["access_token"] = access_token
        self.settings["kite"]["refresh_token"] = next_refresh
        self._save_token_file(access_token, next_refresh)
        LOGGER.info("Kite access token refreshed and stored.")
        return access_token

    def generate_from_request_token(self, request_token: str) -> str:
        if not self.api_secret:
            raise RuntimeError("kite.api_secret is required to generate session from request token.")
        kite = self._kite()
        response = kite.generate_session(request_token, api_secret=self.api_secret)
        access_token = str(response.get("access_token", "")).strip()
        refresh_token = str(response.get("refresh_token", "")).strip()
        if not access_token:
            raise RuntimeError("Kite generate_session did not return access_token.")
        self.settings["kite"]["access_token"] = access_token
        stored = self._load_token_file()
        fallback_refresh = stored.get("refresh_token", "")
        refresh_value = refresh_token or fallback_refresh
        if refresh_value:
            self.settings["kite"]["refresh_token"] = refresh_value
        self._save_token_file(access_token, refresh_token)
        LOGGER.info("Kite session generated from request token and stored.")
        return access_token
