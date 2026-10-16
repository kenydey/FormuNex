"""Load a versioned GB 30000 rule set (``rulesets/*.yaml``)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RULESET_DIR = Path(__file__).resolve().parent / "rulesets"
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
