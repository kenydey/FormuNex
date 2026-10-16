"""Backward compatibility for the FormuMind → FormuNex rename.

Every setting moved from the ``FORMUMIND_`` prefix to ``FORMUNEX_``. For one
release, a legacy ``FORMUMIND_X`` — in the process environment or in a
``.env`` file — is copied to ``FORMUNEX_X`` when the new name is not set, and
a deprecation warning names each key so operators can rename them.

The new name always wins; nothing is copied over an explicit ``FORMUNEX_X``.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterable, MutableMapping
from pathlib import Path

LEGACY_PREFIX = "FORMUMIND_"
PREFIX = "FORMUNEX_"

log = logging.getLogger(__name__)

# Keys already reported, so repeated settings rebuilds do not spam the log.
_reported: set[str] = set()


def _report(keys: list[str], origin: str) -> None:
    fresh = [k for k in keys if k not in _reported]
    if not fresh:
        return
    _reported.update(fresh)
    log.warning(
        "Deprecated FORMUMIND_* settings in %s were applied as FORMUNEX_*: %s. "
        "Rename them; the old prefix will stop working in the next major release.",
        origin,
        ", ".join(sorted(fresh)),
    )


def alias_legacy_environ(environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Copy ``FORMUMIND_X`` → ``FORMUNEX_X`` in *environ* where the new key is unset."""
    env = os.environ if environ is None else environ
    applied: list[str] = []
    for key in [k for k in env if k.startswith(LEGACY_PREFIX)]:
        new_key = PREFIX + key[len(LEGACY_PREFIX):]
        if new_key not in env:
            env[new_key] = env[key]
            applied.append(key)
    if applied:
        _report(applied, "the environment")
    return applied


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def alias_legacy_env_files(
    paths: Iterable[str | Path], environ: MutableMapping[str, str] | None = None
) -> list[str]:
    """Apply legacy keys found in ``.env`` files.

    pydantic-settings only reads ``FORMUNEX_*`` from these files, so a legacy
    key there would be silently ignored. It is promoted into *environ* unless
    the new key is set in the environment or in any of the files.
    """
    env = os.environ if environ is None else environ
    merged: dict[str, str] = {}
    for p in paths:
        path = Path(p)
        if path.is_file():
            merged.update(_parse_env_file(path))
    applied: list[str] = []
    for key, value in merged.items():
        if not key.startswith(LEGACY_PREFIX):
            continue
        new_key = PREFIX + key[len(LEGACY_PREFIX):]
        if new_key in env or new_key in merged:
            continue
        env[new_key] = value
        applied.append(key)
    if applied:
        _report(applied, "the .env file")
    return applied
