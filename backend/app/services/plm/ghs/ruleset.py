"""Load a versioned GB 30000 rule set (``rulesets/*.yaml``).

Plan 1-1 (rule-set sign-off): a rule set must be EHS-signed-off before released
documents may cite classification results computed with it. The sign-off itself
is a sidecar record, ``rulesets/signoffs/<version>.yaml`` — the rule-set file's
``status`` stays ``draft`` until an EHS specialist reviews it, flips the status
to ``released`` *and* files the matching sign-off record. The release gate
(``services/plm/release/gate.py``) enforces both halves; neither alone suffices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RULESET_DIR = Path(__file__).resolve().parent / "rulesets"
SIGNOFF_DIR = RULESET_DIR / "signoffs"
DEFAULT_RULESET = "gb30000-2013.v1"


class RuleSet:
    """Read-only view over a rule-set document; ``rs["skin_corrosion"]["cat1_limit_pct"]``."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def version(self) -> str:
        return str(self._data["version"])

    @property
    def status(self) -> str:
        return str(self._data.get("status") or "draft")

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)


@lru_cache(maxsize=8)
def load_ruleset(name: str = DEFAULT_RULESET) -> RuleSet:
    path = RULESET_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"unknown GHS rule set {name!r}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.get("version") != name:
        raise ValueError(f"rule set file {path.name} declares version {data.get('version')!r}")
    return RuleSet(data)


def available_rulesets() -> list[str]:
    return sorted(p.stem for p in RULESET_DIR.glob("*.yaml"))


@dataclass
class RuleSetSignoff:
    """One EHS sign-off record for one rule-set version (plan 1-1).

    Filed as ``rulesets/signoffs/<version>.yaml`` by the EHS specialist who
    reviewed the rule set, e.g.::

        signer: 张三（EHS 法规专员）
        signed_at: 2026-10-20
        version: gb30000-2013.v1
        decision: approved
        comment: 逐条核对 GB 30000.18-.29 阈值，与 GHS Rev.4 一致。

    ``decision`` must be ``approved``; anything else (or a missing record)
    blocks release in the gate. A record for a *draft* rule set is
    inconsistent — sign-off means the status was flipped to ``released``.
    """

    signer: str = ""
    signed_at: str = ""  # ISO date YYYY-MM-DD
    version: str = ""
    decision: str = "approved"
    comment: str = ""

    def problems(self, expected_version: str) -> list[str]:
        """Every inconsistency that must block release; empty means valid."""
        out = []
        if not str(self.signer or "").strip():
            out.append("规则集签核记录缺少签核人（signer）")
        if not str(self.signed_at or "").strip():
            out.append("规则集签核记录缺少签核日期（signed_at）")
        else:
            try:
                parsed = date.fromisoformat(str(self.signed_at)) if len(str(self.signed_at)) == 10 \
                    else datetime.fromisoformat(str(self.signed_at)).date()
                if parsed > date.today():
                    out.append(f"规则集签核日期 {self.signed_at} 在未来")
            except ValueError:
                out.append(f"规则集签核日期 {self.signed_at!r} 不是合法的 ISO 日期")
        if str(self.version or "") != expected_version:
            out.append(f"规则集签核记录版本 {self.version!r} 与当前规则集 {expected_version!r} 不符")
        if str(self.decision or "") != "approved":
            out.append(f"规则集签核结论为 {self.decision!r}，不是 approved")
        return out


def load_signoff(version: str) -> RuleSetSignoff | None:
    """The EHS sign-off record for ``version``; None when not filed yet."""
    path = SIGNOFF_DIR / f"{version}.yaml"
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"rule-set sign-off {path.name} is not a mapping")
    return RuleSetSignoff(
        signer=str(data.get("signer") or ""),
        signed_at=str(data.get("signed_at") or ""),
        version=str(data.get("version") or ""),
        decision=str(data.get("decision") or "approved"),
        comment=str(data.get("comment") or ""),
    )
