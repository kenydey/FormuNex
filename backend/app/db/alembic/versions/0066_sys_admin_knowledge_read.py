"""Sync the ``sys_admin`` seed row with the live role definition (knowledge.read).

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-19

``6db1a65`` gave the live ``sys_admin`` role the ``knowledge.read`` floor (the
gate on every retrieval path; without it an administrator holding only that
role got 403 on every search), but did not touch the 0051 seed. Migration
0051's file copy of ``_ROLES`` was updated in place to match (approved
exception, 2026-10-19 — editing an applied migration's file does not re-run
it, Alembic tracks applied revisions by id), so a fresh database seeds the
new permission directly. This migration carries the same change to databases
that already ran 0051: it adds ``knowledge.read`` to the stored
``sys_admin`` permission set, idempotently. Downgrade removes it again.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "roles"
_CODE = "sys_admin"
_PERM = "knowledge.read"


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _load_permissions(bind) -> list[str] | None:
    row = bind.execute(
        sa.text(f"SELECT permissions FROM {_TABLE} WHERE code = :code"),
        {"code": _CODE},
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


def upgrade() -> None:
    if not _has_table(_TABLE):
        return
    bind = op.get_bind()
    perms = _load_permissions(bind)
    if perms is None or _PERM in perms:
        return
    perms.append(_PERM)
    bind.execute(
        sa.text(f"UPDATE {_TABLE} SET permissions = :perms WHERE code = :code"),
        {"perms": json.dumps(perms), "code": _CODE},
    )


def downgrade() -> None:
    if not _has_table(_TABLE):
        return
    bind = op.get_bind()
    perms = _load_permissions(bind)
    if perms is None or _PERM not in perms:
        return
    perms = [p for p in perms if p != _PERM]
    bind.execute(
        sa.text(f"UPDATE {_TABLE} SET permissions = :perms WHERE code = :code"),
        {"perms": json.dumps(perms), "code": _CODE},
    )
