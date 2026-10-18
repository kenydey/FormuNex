"""Identity and RBAC: users, roles, user_roles, project_members.

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-16

Seeds the seven built-in roles with their permission sets, and turns every distinct legacy ``owner_id``
(the per-token owner of the FormuMind multi-user mode) into a ``users`` row with ``auth_provider =
'legacy_token'`` and no password, so existing data keeps an owner an administrator can later activate.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


# Frozen copy of the permission matrix at this revision (app/services/identity/rbac.py owns the live one).
_ROLES: list[tuple[str, str, str, list[str]]] = [
    ("rd_engineer", "研发工程师", "R&D Engineer", [
        "knowledge.read", "knowledge.write", "project.write", "spec.read", "formulation.read", "formulation.read_exact",
        "formulation.write", "doe.write", "optimize.run", "lab.execute", "regulatory.read",
        "plm.document.write",
    ]),
    ("project_lead", "项目负责人", "Project Lead", [
        "knowledge.read", "knowledge.write", "project.write", "spec.read", "spec.write", "formulation.read",
        "formulation.read_exact", "formulation.write", "formulation.freeze", "doe.write", "optimize.run",
        "lab.execute", "lab.review", "regulatory.read", "plm.document.write", "release.submit",
        "release.sign",
    ]),
    ("lab_technician", "实验员", "Lab Technician", [
        "knowledge.read", "spec.read", "formulation.read", "lab.execute",
    ]),
    ("ehs_regulatory", "EHS/法规专员", "EHS / Regulatory", [
        "knowledge.read", "spec.read", "formulation.read", "formulation.read_exact", "regulatory.read",
        "regulatory.review", "plm.document.write", "plm.document.review", "release.sign",
    ]),
    ("quality", "质量", "Quality", [
        "knowledge.read", "spec.read", "formulation.read", "lab.review", "regulatory.read", "qc.write",
        "coa.sign", "plm.document.review", "release.sign",
    ]),
    ("release_manager", "发布管理员", "Release Manager", [
        "knowledge.read", "spec.read", "formulation.read", "regulatory.read", "plm.document.review",
        "release.sign", "release.publish", "release.withdraw", "audit.read",
    ]),
    ("sys_admin", "系统管理员", "System Administrator", [
        "admin.users", "admin.templates", "admin.companies", "admin.llm", "admin.connectors", "audit.read",
    ]),
]

# Tables that carried a per-token owner before FormuNex introduced accounts.
_OWNER_TABLES = ("experiments", "campaigns", "task_outbox")

def _create_tables() -> None:
    if not _has_table('roles'):
        op.create_table('roles',
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('name_zh', sa.String(length=80), nullable=False),
        sa.Column('name_en', sa.String(length=80), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=False),
        sa.Column('builtin', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('code')
        )
    if not _has_table('users'):
        op.create_table('users',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('display_name', sa.String(length=120), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=True),
        sa.Column('password_hash', sa.String(length=255), nullable=True),
        sa.Column('auth_provider', sa.String(length=16), nullable=False),
        sa.Column('external_id', sa.String(length=200), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('failed_logins', sa.Integer(), nullable=False),
        sa.Column('locked_until', sa.DateTime(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_users_auth_provider'), 'users', ['auth_provider'], unique=False)
        op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=False)
        op.create_index(op.f('ix_users_external_id'), 'users', ['external_id'], unique=False)
        op.create_index(op.f('ix_users_is_active'), 'users', ['is_active'], unique=False)
        op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    if not _has_table('project_members'):
        op.create_table('project_members',
        sa.Column('project_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('role_code', sa.String(length=40), nullable=False),
        sa.Column('added_by', sa.String(length=36), nullable=True),
        sa.Column('added_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['role_code'], ['roles.code'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('project_id', 'user_id')
        )
        op.create_index(op.f('ix_project_members_role_code'), 'project_members', ['role_code'], unique=False)
        op.create_index(op.f('ix_project_members_user_id'), 'project_members', ['user_id'], unique=False)
    if not _has_table('user_roles'):
        op.create_table('user_roles',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('role_code', sa.String(length=40), nullable=False),
        sa.Column('granted_by', sa.String(length=36), nullable=True),
        sa.Column('granted_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['role_code'], ['roles.code'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'role_code')
        )


def _drop_tables() -> None:
    if _has_table('user_roles'):
        op.drop_table('user_roles')
    if _has_table('project_members'):
        op.drop_table('project_members')
    if _has_table('users'):
        op.drop_table('users')
    if _has_table('roles'):
        op.drop_table('roles')


def _seed_roles() -> None:
    bind = op.get_bind()
    roles = sa.table(
        "roles",
        sa.column("code", sa.String), sa.column("name_zh", sa.String), sa.column("name_en", sa.String),
        sa.column("permissions", sa.JSON), sa.column("builtin", sa.Boolean), sa.column("created_at", sa.DateTime),
    )
    existing = {r[0] for r in bind.execute(sa.text("SELECT code FROM roles"))}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        {"code": c, "name_zh": zh, "name_en": en, "permissions": perms, "builtin": True, "created_at": now}
        for c, zh, en, perms in _ROLES
        if c not in existing
    ]
    if rows:
        op.bulk_insert(roles, rows)


def _import_legacy_owners() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    owners: set[str] = set()
    for table in _OWNER_TABLES:
        if table not in insp.get_table_names():
            continue
        if "owner_id" not in {c["name"] for c in insp.get_columns(table)}:
            continue
        for (owner,) in bind.execute(sa.text(f"SELECT DISTINCT owner_id FROM {table} WHERE owner_id IS NOT NULL")):
            if str(owner).strip():
                owners.add(str(owner).strip())
    if not owners:
        return
    taken = {r[0] for r in bind.execute(sa.text("SELECT username FROM users"))}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    users = sa.table(
        "users",
        sa.column("id", sa.String), sa.column("username", sa.String), sa.column("display_name", sa.String),
        sa.column("auth_provider", sa.String), sa.column("is_active", sa.Boolean),
        sa.column("failed_logins", sa.Integer), sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    rows = [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"formunex:legacy-owner:{o}")),
            "username": o[:80], "display_name": o[:120], "auth_provider": "legacy_token",
            "is_active": False, "failed_logins": 0, "created_at": now, "updated_at": now,
        }
        for o in sorted(owners)
        if o[:80] not in taken
    ]
    if rows:
        op.bulk_insert(users, rows)


def upgrade() -> None:
    _create_tables()
    _seed_roles()
    _import_legacy_owners()


def downgrade() -> None:
    _drop_tables()
