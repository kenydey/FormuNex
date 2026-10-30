"""EPA CTX (CompTox Chemicals Dashboard) enrichment fetcher (P1-1).

Uses the documented Chemical API 1.1.1 endpoint
``GET {BASE}/chemical/search/equal/{word}`` with an ``x-api-key`` header
(the in-repo key probe previously used a different, unconfirmed path that
returned 404; the probe is updated to this endpoint in the same change).

What the search endpoint actually returns — verified against the official
API docs, not assumed — is a single object with ``casrn``, ``dtxsid``,
``dtxcid``, ``preferredName`` and ``smiles``. It has NO molecular-formula
field and NO synonyms field, so this fetcher does not claim them: the
enrichment it stages is a DTXSID / preferred-name / SMILES crosswalk, and
the seed merge only fills a missing SMILES (plus any aliases/mf an
enrichment file from another source may carry). DTXSID is kept in the
staged enrichment file and the release stats, not in a new DB column —
the hazard adapter that needs DTXSID lookups can add one then.

Sandbox note: the sandbox cannot reach the CTX host, so live fetching is
a production-network operation; tests use a mocked transport.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .updates import UpdateError, create_package

logger = logging.getLogger(__name__)

BASE_URL = "https://comptox.epa.gov/ctx-api"
SEARCH_PATH = "/chemical/search/equal/{word}"


class CtxAuthError(UpdateError):
    """Key rejected (401/403) — abort the whole fetch, not per-CAS."""


class CtxRateLimited(UpdateError):
    """Rate limited (429) — abort the whole fetch, retry later."""


def fetch_by_cas(client: httpx.Client, cas: str, api_key: str) -> dict[str, Any] | None:
    """Fetch one CAS number. Returns the mapped entry, or None when not found.

    Raises CtxAuthError / CtxRateLimited (abort the batch) or httpx errors
    (recorded per-CAS by the caller, batch continues).
    """
    url = BASE_URL + SEARCH_PATH.format(word=cas)
    resp = client.get(url, headers={"x-api-key": api_key})
    if resp.status_code == 404:
        return None
    if resp.status_code in (401, 403):
        raise CtxAuthError(f"EPA CTX 拒绝了该密钥（HTTP {resp.status_code}）")
    if resp.status_code == 429:
        raise CtxRateLimited("EPA CTX 限流（429），请稍后再试")
    if resp.status_code != 200:
        raise UpdateError(f"EPA CTX 返回 HTTP {resp.status_code}（CAS {cas}）")
    data = resp.json()
    if isinstance(data, list):
        # Documented shape is a single object; tolerate a list by taking
        # the exact-CASRN match, else the first row, and say so in the entry.
        data = next((r for r in data if r.get("casrn") == cas), data[0] if data else None)
        if data is None:
            return None
    if not isinstance(data, dict):
        raise UpdateError(f"EPA CTX 返回了无法解析的结果（CAS {cas}）")
    entry: dict[str, Any] = {"cas": data.get("casrn") or cas}
    for key in ("dtxsid", "dtxcid", "smiles"):
        if data.get(key):
            entry[key] = data[key]
    if data.get("preferredName"):
        entry["preferred_name"] = data["preferredName"]
    entry["_raw"] = data
    return entry


def build_ctx_package(
    cas_list: list[str],
    *,
    created_by: str | None,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch CTX enrichment for CAS numbers and stage it as a draft package.

    With ``client`` given (tests), it is used directly; otherwise a client
    is built and the key resolved from settings. A package with zero
    fetched entries is refused — an empty enrichment is not an update.
    """
    from ...http_safe import make_client

    if client is None:
        key = api_key
        if not key:
            from ....config import get_settings
            from ...runtime_secrets import effective_setting

            key = effective_setting(get_settings(), "epa_ctx_api_key")
        if not key:
            raise UpdateError("EPA CTX key 未配置")
        use_client = make_client(timeout=15.0)
        own_client = True
    else:
        key = api_key or ""
        use_client = client
        own_client = False
    entries: dict[str, Any] = {}
    not_found: list[str] = []
    errors: list[dict[str, Any]] = []
    try:
        for cas in cas_list:
            cas = str(cas).strip()
            if not cas:
                continue
            try:
                entry = fetch_by_cas(use_client, cas, key)
            except (CtxAuthError, CtxRateLimited):
                raise
            except Exception as exc:  # per-CAS failure, batch continues
                errors.append({"cas": cas, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if entry is None:
                not_found.append(cas)
            else:
                entries[cas] = entry
    finally:
        if own_client:
            use_client.close()
    if not entries:
        raise UpdateError(
            f"EPA CTX 未取回任何物质（未找到 {len(not_found)} 个，失败 {len(errors)} 个）"
        )
    payload = {
        "source": "EPA CTX",
        "api": "ctx-api 1.1.1",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    manifest = create_package(
        source="epa_ctx",
        label=f"CTX {datetime.now(timezone.utc):%Y-%m-%d}",
        files={
            "ctx_enrichment.json": json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        },
        created_by=created_by,
    )
    from .updates import _package_dir, _write_manifest

    manifest["report"] = {
        "fetched": len(entries),
        "not_found": not_found,
        "errors": errors,
    }
    return _write_manifest(_package_dir(manifest["package_id"]), manifest)
