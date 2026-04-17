from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from core.retry import retry
from ai.strategy_generator import canonical_strategy_name, rank_strategy_candidates


LOGGER = logging.getLogger(__name__)

ALLOWED_REGIMES = {"trending", "volatile", "range", "mixed"}
ALLOWED_OPTION_TYPES = {"CE", "PE"}
ALLOWED_LEG_SIDES = {"BUY", "SELL"}


DEFAULT_DECISION = {
    "strategy": "",
    "capital_to_use": 50000,
    "ce_strike": 0,
    "pe_strike": 0,
    "confidence": 0.55,
    "reason": "LLM output unavailable.",
}


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
    raise TypeError(
        f"Object of type {value.__class__.__name__} is not JSON serializable"
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default)


def _load_json_payload(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        raise ValueError("LLM response content is empty.")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last <= first:
        raise ValueError(f"LLM response is not valid JSON object: {text[:200]}")
    parsed = json.loads(text[first : last + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object.")
    return parsed


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return float(default)
    if number != number or number in {float("inf"), float("-inf")}:
        return float(default)
    return float(number)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return int(default)


def _normalize_strike_plan(plan: Any) -> dict[str, dict[str, int]]:
    if not isinstance(plan, dict):
        return {}
    normalized: dict[str, dict[str, int]] = {}
    for option_type in ALLOWED_OPTION_TYPES:
        option_plan = plan.get(option_type)
        if not isinstance(option_plan, dict):
            continue
        leg_plan: dict[str, int] = {}
        for side in ALLOWED_LEG_SIDES:
            value = option_plan.get(side)
            if value is None:
                continue
            strike = _coerce_int(value, 0)
            if strike > 0:
                leg_plan[side] = strike
        default_strike = _coerce_int(option_plan.get("strike"), 0)
        if default_strike > 0 and "strike" not in leg_plan:
            leg_plan["strike"] = default_strike
        default_value = _coerce_int(option_plan.get("default"), 0)
        if default_value > 0 and "default" not in leg_plan:
            leg_plan["default"] = default_value
        if leg_plan:
            normalized[option_type] = leg_plan
    return normalized


def _normalize_decision_payload(
    decision: Any,
    context: dict[str, Any],
    *,
    fallback_strategy: str | None = None,
) -> dict[str, Any]:
    if not isinstance(decision, dict):
        raise ValueError("LLM decision payload must be an object.")

    strategy = canonical_strategy_name(str(decision.get("strategy", "")).strip())
    if not strategy:
        strategy = canonical_strategy_name(str(fallback_strategy or "").strip())
    if not strategy:
        raise ValueError("LLM decision is missing a valid strategy.")

    chain = context.get("option_chain", [])
    strikes = [int(row["strike"]) for row in chain if "strike" in row]
    atm = strikes[len(strikes) // 2] if strikes else int(_coerce_float(context.get("nifty_price", 0.0), 0.0))

    confidence = _coerce_float(decision.get("confidence", 0.55), 0.55)
    confidence = max(0.0, min(1.0, confidence))
    capital_to_use = _coerce_float(decision.get("capital_to_use", 50000.0), 50000.0)
    if capital_to_use <= 0:
        capital_to_use = 50000.0

    ce_strike = _coerce_int(decision.get("ce_strike"), 0)
    pe_strike = _coerce_int(decision.get("pe_strike"), 0)
    if ce_strike <= 0:
        ce_strike = atm + 200
    if pe_strike <= 0:
        pe_strike = atm - 200

    reason = str(decision.get("reason", "")).strip() or "No reason provided"
    strike_plan = _normalize_strike_plan(decision.get("strike_plan"))

    normalized = dict(decision)
    normalized["strategy"] = strategy
    normalized["confidence"] = confidence
    normalized["capital_to_use"] = capital_to_use
    normalized["ce_strike"] = ce_strike
    normalized["pe_strike"] = pe_strike
    normalized["reason"] = reason
    normalized["strike_plan"] = strike_plan
    return normalized


def _normalize_regime_payload(regime: Any) -> dict[str, Any]:
    if not isinstance(regime, dict):
        raise ValueError("LLM regime payload must be an object.")
    regime_name = str(regime.get("regime", "")).strip().lower()
    if regime_name not in ALLOWED_REGIMES:
        raise ValueError(f"Invalid regime value: {regime_name!r}")
    confidence = _coerce_float(regime.get("confidence", 0.5), 0.5)
    confidence = max(0.0, min(1.0, confidence))
    summary = str(regime.get("summary", "")).strip() or "No summary provided"
    return {
        "regime": regime_name,
        "confidence": confidence,
        "summary": summary,
    }


@dataclass(slots=True)
class LLMReasoner:
    settings: dict[str, Any]

    def _heuristic_regime(self, context: dict[str, Any]) -> dict[str, Any]:
        trend = context.get("trend", "sideways")
        vix = float(context.get("vix", 0.0))
        regime = "volatile" if vix > 18 else "range"
        if trend in {"uptrend", "downtrend"} and vix < 18:
            regime = "trend"
        return {"regime": regime, "confidence": 0.5, "summary": "Heuristic regime"}

    def _provider_api_key(self, provider: str) -> str:
        key = str(self.settings.get(provider, {}).get("api_key", "")).strip()
        return "" if key.lower().startswith("replace") else key

    def _ollama_endpoint(self) -> str:
        endpoint = str(self.settings.get("ollama", {}).get("endpoint", "")).strip()
        if not endpoint:
            return "http://localhost:11434/v1/chat/completions"
        normalized = endpoint.rstrip("/")
        if normalized.endswith("/chat/completions") or normalized.endswith("/api/chat"):
            return normalized
        if normalized.endswith("/v1"):
            return f"{normalized}/chat/completions"
        if normalized.endswith("/api"):
            return f"{normalized}/chat"
        return normalized

    def _ollama_model(self) -> str:
        model = str(self.settings.get("ollama", {}).get("model", "")).strip()
        return model or "deepseek-v3.1:671b-cloud"

    def _openrouter_endpoint(self) -> str:
        endpoint = str(self.settings.get("openrouter", {}).get("endpoint", "")).strip()
        return endpoint or "https://openrouter.ai/api/v1/chat/completions"

    def _openrouter_model(self) -> str:
        model = str(self.settings.get("openrouter", {}).get("model", "")).strip()
        return model or "openrouter/free"

    def _llm_enabled(self) -> bool:
        primary_enabled = bool(self._ollama_endpoint() and self._ollama_model())
        fallback_enabled = bool(self._openrouter_endpoint() and self._openrouter_model())
        return primary_enabled or fallback_enabled

    def _headers(self, provider: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self._provider_api_key(provider)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    @staticmethod
    def _is_local_ollama_endpoint(endpoint: str) -> bool:
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or "").lower()
        return host in {"localhost", "127.0.0.1"} and int(parsed.port or 11434) == 11434

    @staticmethod
    def _ollama_request_variants(endpoint: str) -> list[str]:
        parsed = urlsplit(endpoint)
        path = (parsed.path or "").rstrip("/")
        variants: list[str] = []
        if path.endswith("/chat/completions") or path.endswith("/api/chat"):
            variants.append(endpoint.rstrip("/"))
            return variants
        if path.endswith("/v1"):
            variants.append(urlunsplit(parsed._replace(path=f"{path}/chat/completions")))
            variants.append(urlunsplit(parsed._replace(path=f"{path}/api/chat")))
            return variants
        if path.endswith("/api"):
            variants.append(urlunsplit(parsed._replace(path=f"{path}/chat")))
            return variants
        variants.append(urlunsplit(parsed._replace(path=f"{path}/v1/chat/completions")))
        variants.append(urlunsplit(parsed._replace(path=f"{path}/api/chat")))
        return variants

    @retry(attempts=3, delay_seconds=1.0)
    def _chat(self, messages: list[dict[str, str]]) -> str:
        candidate_routes: list[tuple[str, str, str]] = []
        primary_endpoint = self._ollama_endpoint()
        primary = ("ollama", primary_endpoint, self._ollama_model())
        fallback = ("openrouter", self._openrouter_endpoint(), self._openrouter_model())
        for provider, endpoint, model in (primary, fallback):
            normalized_endpoint = str(endpoint or "").strip()
            normalized_model = str(model or "").strip()
            if normalized_endpoint and normalized_model:
                if provider == "ollama":
                    for ollama_endpoint in self._ollama_request_variants(normalized_endpoint):
                        candidate_routes.append((provider, ollama_endpoint, normalized_model))
                else:
                    candidate_routes.append((provider, normalized_endpoint, normalized_model))
            if provider == "openrouter" and normalized_endpoint:
                if normalized_model != "openrouter/free":
                    candidate_routes.append((provider, normalized_endpoint, "openrouter/free"))

        seen: set[tuple[str, str, str]] = set()
        last_error: str = ""
        for provider, endpoint, model in candidate_routes:
            route = (provider, endpoint, model)
            if route in seen:
                continue
            seen.add(route)
            payload = {
                "model": model,
                "messages": messages,
                "response_format": {"type": "json_object"},
            }
            try:
                response = requests.post(
                    endpoint,
                    headers=self._headers(provider),
                    json=payload,
                    timeout=30,
                )
            except requests.RequestException as exc:
                last_error = f"Transport error for {provider}:{model}: {exc}"
                LOGGER.warning(
                    "LLM transport failed for %s model %s: %s",
                    provider,
                    model,
                    exc,
                )
                continue
            if response.ok:
                try:
                    data = response.json()
                    content = ""
                    if isinstance(data, dict):
                        choices = data.get("choices")
                        if isinstance(choices, list) and choices:
                            first_choice = choices[0] if isinstance(choices[0], dict) else {}
                            content = str(
                                first_choice.get("message", {}).get("content", "")
                                if isinstance(first_choice.get("message", {}), dict)
                                else first_choice.get("text", "")
                            )
                        if not content:
                            message = data.get("message")
                            if isinstance(message, dict):
                                content = str(message.get("content", "") or "")
                        if not content:
                            content = str(data.get("response", "") or "")
                        if not content and isinstance(data.get("content"), str):
                            content = str(data.get("content", ""))
                    if isinstance(content, list):
                        parts: list[str] = []
                        for chunk in content:
                            if isinstance(chunk, dict) and isinstance(chunk.get("text"), str):
                                parts.append(chunk["text"])
                            elif isinstance(chunk, str):
                                parts.append(chunk)
                        content = "".join(parts)
                    if isinstance(content, str) and content.strip():
                        return content
                    raise ValueError("Missing message content in response.")
                except Exception as exc:
                    last_error = f"Malformed success payload for {provider}:{model}: {exc}"
                    LOGGER.warning(
                        "LLM response decode failed for %s model %s: %s",
                        provider,
                        model,
                        exc,
                    )
                    continue

            body = str(response.text or "").strip()
            last_error = (
                f"HTTP {response.status_code} for {provider}:{model}: {body[:300]}"
            )
            if provider == "ollama":
                LOGGER.warning(
                    "Ollama request failed for model %s; trying OpenRouter fallback.",
                    model,
                )
                continue
            if response.status_code == 404 and model != "openrouter/free":
                LOGGER.warning(
                    "OpenRouter model %s unavailable; trying openrouter/free.",
                    model,
                )
                continue
            response.raise_for_status()

        raise RuntimeError(last_error or "LLM request failed.")

    def _market_context(self, context: dict[str, Any]) -> dict[str, Any]:
        nested = context.get("current_market_context")
        if isinstance(nested, dict) and nested:
            return nested
        nested = context.get("market_context")
        if isinstance(nested, dict) and nested:
            return nested
        return context

    def _build_candidate_payload(
        self,
        market_context: dict[str, Any],
        regime: dict[str, Any],
        recent_performance: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ranked_candidates = rank_strategy_candidates(
            market_context,
            regime=regime,
            recent_performance=recent_performance
            if isinstance(recent_performance, list)
            else [],
        )
        return {
            "market_context": market_context,
            "regime": regime,
            "candidate_strategies": ranked_candidates[:6],
        }

    def infer_market_regime(
        self, context: dict[str, Any], strict: bool = False
    ) -> dict[str, Any]:
        prompt = (
            "You are an options market regime classifier for Indian index options (NIFTY/BANKNIFTY).\n\n"
            "TASK: Analyze the market context and classify the current market regime.\n\n"
            "INPUT ANALYSIS:\n"
            "- VIX level: < 14 = low volatility, 14-18 = normal, 18-25 = elevated, > 25 = high volatility\n"
            "- Trend direction and strength from price action\n"
            "- IV skew pattern (put skew vs call skew vs smile)\n"
            "- Volume patterns and OI buildup (PCR, OI walls)\n"
            "- Technical indicators: RSI, MACD, Bollinger Bands, ADR\n"
            "- Support/resistance levels\n\n"
            "REGIME CLASSIFICATION:\n"
            "1. TRENDING: Clear directional movement (uptrend/downtrend) with sustained momentum; VIX usually moderate\n"
            "2. VOLATILE: High VIX (>25) with large price swings; sharp IV skew changes; panic or uncertainty\n"
            "3. RANGE: Sideways price action with defined support/resistance; low-to-moderate VIX; mean-reverting behavior\n"
            "4. MIXED: Conflicting signals; could be transitioning between regimes\n\n"
            "OUTPUT FORMAT (strict JSON):\n"
            "{\n"
            '  "regime": "trending|volatile|range|mixed",\n'
            '  "confidence": float (0.0-1.0; 0.8+ for clear regimes, 0.5-0.7 for mixed),\n'
            '  "summary": "1-2 sentence explanation of your classification and key factors"\n'
            "}\n\n"
            "Consider:\n"
            "- Trend + low VIX = trending regime\n"
            "- High VIX + sharp moves = volatile regime\n"
            "- Sideways price + low VIX = range regime\n"
            "- Conflicting indicators = mixed regime\n"
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": _json_dumps(context)},
        ]
        if not self._llm_enabled():
            LOGGER.warning("No LLM provider configured; using heuristic regime.")
            if strict:
                LOGGER.warning("No LLM provider configured; aborting strategy cycle.")
                raise RuntimeError("No LLM provider configured.")
            return self._heuristic_regime(context)
        try:
            content = self._chat(messages)
            return _normalize_regime_payload(_load_json_payload(content))
        except Exception as exc:
            if strict:
                LOGGER.warning("Regime inference failed; aborting strategy cycle.")
                raise RuntimeError(f"Regime inference failed: {exc}") from exc
            LOGGER.warning("Regime inference failed; using heuristic fallback.")
            return self._heuristic_regime(context)

    def propose_strategies(
        self, context: dict[str, Any], regime: dict[str, Any], strict: bool = False
    ) -> dict[str, Any]:
        market_context = self._market_context(context)
        recent_performance = context.get("recent_strategy_performance", [])
        ranked_candidates = rank_strategy_candidates(
            market_context,
            regime=regime,
            recent_performance=recent_performance
            if isinstance(recent_performance, list)
            else [],
        )
        candidate_names = [str(item["strategy"]) for item in ranked_candidates[:6]]
        prompt = (
            "You are an expert options trading strategist for Indian index options (NIFTY/BANKNIFTY).\n\n"
            "TASK: Select exactly TWO strategies from the provided candidate list - one as PRIMARY and one as SECONDARY.\n\n"
            "INPUTS:\n"
            "1. Market Context: Current prices, volatility (VIX), trend, IV skew, OI analysis, technical indicators, liquidity metrics\n"
            "2. Market Regime: Classified regime with confidence and summary\n"
            "3. Candidate Strategies: Ranked list of 6 strategies with scores based on regime fit and recent performance\n"
            "4. Recent Performance: Historical performance metrics for strategies over the last 20 days\n"
            "5. Portfolio State: Current exposure (delta, vega, theta) if provided\n\n"
            "SELECTION CRITERIA (in order of importance):\n"
            "a) REGIME ALIGNMENT: How well the strategy matches the current market regime (trend/range/volatile)\n"
            "b) RECENT PERFORMANCE: Recent win rates and Sharpe ratios for the strategy\n"
            "c) RISK-REWARD: Probability of profit, max loss potential, and risk-adjusted returns\n"
            "d) VOLATILITY SUITABILITY: appropriateness given current VIX levels\n"
            "e) STRIKE SELECTION LOGIC: Rationale for chosen strikes based on support/resistance, IV skew, and delta targets\n"
            "f) CAPITAL EFFICIENCY: Appropriate position sizing for the strategy type and risk budget\n"
            "g) PORTFOLIO FIT: Avoid over-concentration in Greeks when portfolio state is available\n"
            "h) DIVERSIFICATION: Secondary strategy should differ from primary (e.g., not two income strategies simultaneously)\n\n"
            "OUTPUT FORMAT (strict JSON):\n"
            "{\n"
            '  "primary": {\n'
            '    "strategy": "strategy_name",\n'
            '    "confidence": float (0.0-1.0),\n'
            '    "reason": "detailed explanation including: regime fit, strike rationale, risk-reward, capital justification",\n'
            '    "capital_to_use": float (total capital allocated),\n'
            "    // Exact strike plan for the strategy, preferred over legacy keys:\n"
            '    "strike_plan": {\n'
            '      "CE": {"SELL": int, "BUY": int},\n'
            '      "PE": {"SELL": int, "BUY": int}\n'
            "    },\n"
            "    // Legacy fallbacks:\n"
            '    "ce_strike": int (call strike price) [legacy],\n'
            '    "pe_strike": int (put strike price) [legacy],\n'
            "    // Param-based overrides (preferred):\n"
            '    "ce_delta": float (0.05-0.5) for CE delta target,\n'
            '    "pe_delta": float (0.05-0.5) for PE delta target,\n'
            '    "width": int (spread width in points) for spreads/condors,\n'
            '    "atm_offset_ce": int (offset from ATM for CE),\n'
            '    "atm_offset_pe": int (offset from ATM for PE)\n'
            "  },\n"
            '  "secondary": { /* same fields as primary */ },\n'
            '  "candidates": [primary, secondary, ...],\n'
            '  "rejection_reason": "optional: why others rejected"\n'
            "}\n\n"
            "REQUIREMENTS:\n"
            "- primary and secondary MUST be different strategies from the candidate list\n"
            "- Provide specific strike rationales (e.g., 'ATM for delta neutrality', '25-delta for directionality', '50% retracement level')\n"
            "- When using strike_plan, ensure the buy and sell legs are on different strikes for the same option type unless the strategy explicitly requires an at-the-money straddle/strangle\n"
            "- Prefer param-based controls (ce_delta, pe_delta, width) over explicit ce_strike/pe_strike to respect strategy-specific logic\n"
            "- ce_strike/pe_strike are fallbacks for direct override (bypasses strategy defaults)\n"
            "- confidence should reflect both regime fit and your certainty about the selection (0.6-0.9 typical)\n"
            "- capital_to_use should respect max_capital_per_trade and not exceed available capital\n"
            "- Each strategy in candidates must be one of: ["
            + ", ".join(candidate_names)
            + "]\n"
            "- If market conditions are uncertain, lower confidence and suggest cash/near-cash strategies\n"
            "- Consider expiry proximity when selecting strikes (prefer nearer expiries for income, further for directional)\n"
        )
        payload = self._build_candidate_payload(
            market_context,
            regime,
            recent_performance if isinstance(recent_performance, list) else [],
        )
        payload["available_strategies"] = candidate_names
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": _json_dumps(payload)},
        ]
        if not self._llm_enabled():
            LOGGER.warning(
                "No LLM provider configured; using deterministic fallback proposal."
            )
            if strict:
                LOGGER.warning("No LLM provider configured; aborting strategy cycle.")
                raise RuntimeError("No LLM provider configured.")
            return self._fallback_proposal(market_context, regime, candidate_names)
        try:
            content = self._chat(messages)
            decision = _load_json_payload(content)
            candidates = decision.get("candidates")
            if not isinstance(candidates, list) or len(candidates) < 2:
                if isinstance(decision.get("strategy"), str) and decision.get("strategy"):
                    candidates = [decision]
                else:
                    primary = decision.get("primary") or {}
                    secondary = decision.get("secondary") or {}
                    candidates = [primary, secondary]
            normalized: list[dict[str, Any]] = []
            for item in candidates[:2]:
                if not isinstance(item, dict):
                    continue
                candidate = dict(item)
                candidate["strategy"] = canonical_strategy_name(
                    str(candidate.get("strategy", ""))
                )
                if candidate_names and candidate["strategy"] not in candidate_names:
                    candidate["strategy"] = candidate_names[0]
                normalized.append(
                    _normalize_decision_payload(
                        candidate,
                        market_context,
                        fallback_strategy=candidate["strategy"],
                    )
                )
            if len(normalized) == 1:
                normalized.append(normalized[0])
            if not normalized:
                raise ValueError("No valid strategy candidates returned by LLM")
            primary = normalized[0]
            secondary = normalized[1]
            if primary["strategy"] == secondary["strategy"] and len(candidate_names) > 1:
                secondary["strategy"] = candidate_names[1]
            return {
                "primary": primary,
                "secondary": secondary,
                "candidates": normalized,
                "regime": regime,
            }
        except Exception as exc:
            LOGGER.warning(
                "Strategy proposal failed; using deterministic fallback proposal. error=%s",
                exc,
            )
            fallback = self._fallback_proposal(market_context, regime, candidate_names)
            return fallback

    def choose_strategy(
        self, context: dict[str, Any], regime: dict[str, Any], strict: bool = False
    ) -> dict[str, Any]:
        proposal = self.propose_strategies(context, regime, strict=strict)
        if isinstance(proposal, dict) and isinstance(proposal.get("primary"), dict):
            return proposal["primary"]
        raise RuntimeError("No AI proposal available.")

    def _normalize_decision(
        self, decision: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        return _normalize_decision_payload(decision, context)

    def _fallback_proposal(
        self,
        market_context: dict[str, Any],
        regime: dict[str, Any],
        candidate_names: list[str],
    ) -> dict[str, Any]:
        ranked = rank_strategy_candidates(
            market_context,
            regime=regime,
            recent_performance=[],
        )
        ranked_names = [
            canonical_strategy_name(str(item.get("strategy", "")))
            for item in ranked
            if isinstance(item, dict)
        ]
        ranked_names = [name for name in ranked_names if name]
        merged: list[str] = []
        for name in candidate_names + ranked_names:
            clean = canonical_strategy_name(str(name))
            if clean and clean not in merged:
                merged.append(clean)
        if not merged:
            raise RuntimeError("No fallback strategy candidates available.")

        primary_name = merged[0]
        secondary_name = merged[1] if len(merged) > 1 else merged[0]
        summary = str(regime.get("summary", "")).strip() if isinstance(regime, dict) else ""
        regime_name = str(regime.get("regime", "range")).strip() if isinstance(regime, dict) else "range"
        base_reason = (
            f"LLM unavailable; using deterministic fallback aligned to {regime_name} regime."
        )
        if summary:
            base_reason = f"{base_reason} {summary}"

        primary = _normalize_decision_payload(
            {
                "strategy": primary_name,
                "confidence": 0.55,
                "reason": base_reason,
                "capital_to_use": 50000,
            },
            market_context,
        )
        secondary = _normalize_decision_payload(
            {
                "strategy": secondary_name,
                "confidence": 0.5,
                "reason": "Secondary deterministic fallback candidate.",
                "capital_to_use": 50000,
            },
            market_context,
        )
        return {
            "primary": primary,
            "secondary": secondary,
            "candidates": [primary, secondary],
            "regime": regime,
        }

    def optimize_strategy_rankings(
        self, stats: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        prompt = (
            "You are a quantitative options strategy optimizer. "
            "Given strategy metrics, return JSON with key 'rankings' as ordered list. "
            "Each item keys: strategy, rank_score, note."
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": _json_dumps({"stats": stats})},
        ]
        if not self._llm_enabled():
            LOGGER.warning(
                "No LLM provider configured; keeping quantitative ranking."
            )
            return []
        try:
            content = self._chat(messages)
            out = _load_json_payload(content)
            rankings = out.get("rankings", [])
            if isinstance(rankings, list):
                normalized: list[dict[str, Any]] = []
                for row in rankings:
                    if not isinstance(row, dict):
                        continue
                    item = dict(row)
                    item["strategy"] = canonical_strategy_name(
                        str(item.get("strategy", ""))
                    )
                    normalized.append(item)
                return normalized
            return []
        except Exception:
            LOGGER.warning("Ranking optimization failed; keeping quantitative ranking.")
            return []
