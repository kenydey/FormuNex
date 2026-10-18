"""Sensitivity-graded LLM routing (开发方案 §4.5).

Exact compositions are trade secrets. Every LLM call is classified:

- ``secret`` — the payload carries exact weight percentages (a precise
  ``weight_pct`` on any ingredient, or an exact ``12.5%``-style figure in a
  formulation prompt). ``secret`` may ONLY go to the private model
  (``FORMUNEX_PRIVATE_LLM_ENDPOINT`` / ``FORMUNEX_PRIVATE_LLM_API_KEY``).
  With no private model configured the request is refused with
  :class:`SensitiveDataRefusal` — never silently forwarded to Anthropic /
  OpenAI / any external supplier.
- ``internal`` — formulation / ingredient names without exact percentages.
  May use the configured external provider.
- ``public`` — everything else. May use the configured external provider.

The classifier accepts a structured payload (``dict`` with an ``ingredients``
list) or a raw prompt string (the ``_call_llm`` entry point only sees text,
so exact percentages are detected with a formulation-context regex).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal

log = logging.getLogger(__name__)

Sensitivity = Literal["public", "internal", "secret"]

# Exact weight in a prompt, e.g. "12.5%", "12.5 wt%", "weight_pct: 12.5".
# A bare percentage alone is NOT enough ("95% confidence") — the prompt must
# also carry formulation context (see _FORMULATION_HINT).
_WEIGHT_RE = re.compile(
    r"(?<![\d.])\d+(?:\.\d+)?\s*(?:wt\s?%|weight\s?%|wt\.?\s*pct|weight\s*pct)"
    r"|(?<![\d.])\d+(?:\.\d+)?\s*%(?=\s*(?:,|;|\)|\n|$|\s*(?:and|与|，)))",
    re.IGNORECASE,
)
# A percentage followed by a unit-like token is a formulation figure too:
# "12.5 % 氢氧化钠", "NaOH 12.5%".
_WEIGHT_RE2 = re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*%", re.IGNORECASE)

_FORMULATION_HINT = re.compile(
    r"配方|配比|组分|成分|ingredients?|formulation|formula|composition|"
    r"weight_pct|wt_pct|配[方方]|原料|添加量|含量",
    re.IGNORECASE,
)

# Keys whose presence marks a payload as formulation-related.
_FORMULATION_KEYS = {
    "ingredients", "components", "formula", "formulation",
    "formulation_name", "product_name", "recipe", "composition",
}


class SensitiveDataRefusal(Exception):
    """A secret payload with no private model configured — refuse, never forward."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            "请求含精确配比（secret 级敏感数据），未配置私有模型 "
            "(FORMUNEX_PRIVATE_LLM_ENDPOINT / FORMUNEX_PRIVATE_LLM_API_KEY)，拒绝外发。"
            + (f" {detail}" if detail else "")
        )
        self.detail = detail


def _is_exact_number(value: Any) -> bool:
    """True when ``value`` is a precise numeric percentage (not a range)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        s = value.strip()
        # "10-15", "10～20", "10~20", "<1", "≤0.5" are ranges/bounds, not exact.
        if re.search(r"[-～~<>≤≥]", s):
            return False
        try:
            float(s)
            return True
        except ValueError:
            return False
    return False


def _ingredients_have_exact(ingredients: Any) -> bool:
    if not isinstance(ingredients, list):
        return False
    for ing in ingredients:
        if not isinstance(ing, dict):
            continue
        for key in ("weight_pct", "wt_pct", "percentage", "pct", "content_pct"):
            if key in ing and _is_exact_number(ing[key]):
                return True
    return False


def classify_payload(payload: Any) -> Sensitivity:
    """Classify a structured payload or a raw prompt string."""
    if payload is None:
        return "public"
    if isinstance(payload, dict):
        keys = set(payload.keys())
        if any(k in _FORMULATION_KEYS for k in keys):
            for k in ("ingredients", "components", "composition"):
                if k in payload and _ingredients_have_exact(payload[k]):
                    return "secret"
            return "internal"
        # Nested formulation under a sub-key, e.g. {"formulation": {...}}.
        for k in ("formulation", "formula", "recipe"):
            if isinstance(payload.get(k), dict):
                sub = classify_payload(payload[k])
                if sub == "secret":
                    return "secret"
                return "internal"
        return "public"
    if isinstance(payload, str):
        if not _FORMULATION_HINT.search(payload):
            return "public"
        if _WEIGHT_RE.search(payload) or _WEIGHT_RE2.search(payload):
            return "secret"
        return "internal"
    return "public"


def private_llm_configured(settings: Any | None = None) -> bool:
    """True when a private endpoint + key are configured."""
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    endpoint = str(getattr(settings, "private_llm_endpoint", "") or "").strip()
    api_key = str(getattr(settings, "private_llm_api_key", "") or "").strip()
    return bool(endpoint and api_key)


def resolve_route(sensitivity: Sensitivity, settings: Any | None = None) -> tuple[str, str]:
    """Return (destination, reason): ``private`` or ``external``.

    Raises :class:`SensitiveDataRefusal` for ``secret`` with no private model.
    """
    if sensitivity == "secret":
        if not private_llm_configured(settings):
            raise SensitiveDataRefusal()
        return "private", "secret payload -> private model"
    return "external", f"{sensitivity} payload -> configured provider"


def call_private_llm(
    prompt: str,
    max_tokens: int | None = None,
    *,
    model: str | None = None,
    settings: Any | None = None,
) -> str | None:
    """POST to the private OpenAI-compatible endpoint. Returns None on failure."""
    import httpx

    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    endpoint = str(getattr(settings, "private_llm_endpoint", "") or "").strip().rstrip("/")
    api_key = str(getattr(settings, "private_llm_api_key", "") or "").strip()
    if not endpoint or not api_key:
        return None
    model_name = (model or str(getattr(settings, "private_llm_model", "") or "").strip()
                  or "private-default")
    url = endpoint if endpoint.endswith("/chat/completions") else f"{endpoint}/chat/completions"
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens or settings.llm_max_tokens,
                "temperature": 0.0,
            },
            timeout=float(getattr(settings, "llm_timeout_seconds", 60.0)),
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        return str(content) if content else None
    except Exception as exc:
        log.warning("private LLM call failed: %s", exc)
        return None
