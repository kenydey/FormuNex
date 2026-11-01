"""Add W0's two new permissions to the stored built-in role rows.

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-27

W0 (2026-10-27) added ``chemical.read`` and ``plm.document.read`` to the
permission catalogue (29 -> 31) and updated migration 0051's frozen copy in
place, so a fresh database seeds the new permissions directly. This
migration carries the same change to databases that already ran 0051: it
adds the missing permission strings to the stored built-in role rows,
idempotently, and touches no custom role row. Downgrade removes exactly
these two strings from the same seven rows.

The delta is frozen here on purpose (migrations must not import live
business code, and must not read the live role definition at upgrade time;
it can drift). It matches the live ``BUILTIN_ROLES`` definition at W0:

- all seven built-in roles gain ``chemical.read`` (input-only compute);
- the five business roles gain ``plm.document.read`` (render previews);
- ``lab_technician`` and ``sys_admin`` do NOT gain ``plm.document.read``
  (previews can render real document content, which is business data).

``0001_baseline`` builds a fresh database with ``create_all`` from the
*current* models, so on a fresh database the tables already exist; every
step checks first and is safe to repeat.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "roles"

# code -> permission strings this migration adds to that built-in role row.
# Custom role rows are never touched (they are not keys here).
_DELTA: dict[str, tuple[str, ...]] = {
    "rd_engineer": ("chemical.read", "plm.document.read"),
    "project_lead": ("chemical.read", "plm.document.read"),
    "lab_technician": ("chemical.read",),
    "ehs_regulatory": ("chemical.read", "plm.document.read"),
    "quality": ("chemical.read", "plm.document.read"),
    "release_manager": ("chemical.read", "plm.document.read"),
    "sys_admin": ("chemical.read",),
}

# The full set this migration ever adds (used by downgrade).
_ADDED: tuple[str, ...] = ("chemical.read", "plm.document.read")


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _load_permissions(bind, code: str) -> list[str] | None:
    row = bind.execute(
        sa.text(f"SELECT permissions FROM {_TABLE} WHERE code = :code"),
        {"code": code},
    ).first()
    if row is None:
        return None
    perms = row[0]
    if isinstance(perms, str):
        try:
            perms = json.loads(perms)
        except (TypeError, ValueError):
            return None
    if not isinstance(perms, list):
        return None
    return [str(p) for p in perms]


def _store_permissions(bind, code: str, perms: list[str]) -> None:
    bind.execute(
        sa.text(f"UPDATE {_TABLE} SET permissions = :perms WHERE code = :code"),
        {"perms": json.dumps(perms), "code": code},
    )


def upgrade() -> None:
    if not _has_table(_TABLE):
        return
    bind = op.get_bind()
    for code, added in _DELTA.items():
        perms = _load_permissions(bind, code)
        if perms is None:
            continue
        missing = [p for p in added if p not in perms]
        if not missing:
            continue
        perms.extend(missing)
        _store_permissions(bind, code, perms)


def _frozen_permissions() -> dict[str, set[str]]:
    """0051's frozen role copy is the expected post-0074 state: W0
    updated it in place, so it already carries the two new strings
    (and 0066's knowledge.read for sys_admin)."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "m0051_for_0074_guard", Path(__file__).resolve().parent / "0051_identity_rbac.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {code: set(perms) for code, _zh, _en, perms in mod._ROLES}


def downgrade() -> None:
    if not _has_table(_TABLE):
        return
    bind = op.get_bind()
    # Stage 2 / SR-038 (0080 pattern): the upgrade cannot tell a string
    # it added from the same string an admin granted manually (a manual
    # grant predating 0074 is indistinguishable once present). Stripping
    # blindly would destroy such grants, so refuse whenever a built-in
    # row no longer matches the frozen post-upgrade state — i.e. someone
    # edited it by hand since. Untouched rows strip exactly as before.
    # Upgrade untouched.
    frozen = _frozen_permissions()
    for code in _DELTA:
        perms = _load_permissions(bind, code)
        if perms is None:
            continue
        if set(perms) != frozen.get(code, set()):
            raise RuntimeError(
                f"0074 downgrade refused: role '{code}' permissions were manually changed "
                "after the upgrade; stripping the two W0 strings would destroy manual grants")
        stripped = [p for p in perms if p not in _ADDED]
        if len(stripped) == len(perms):
            continue
        _store_permissions(bind, code, stripped)
