"""Role-based access control: the permission catalogue and the check.

Permissions are coarse, verb-style codes. A user's effective permissions are
the union of their global roles and — for project-scoped checks — their role
in that project. ``formulation.read_exact`` is deliberately separate from
``formulation.read``: exact compositions are trade secrets, and a user without
it sees composition ranges only (the same banding SDS section 3 uses).

Migration 0051 seeds a frozen copy of ``BUILTIN_ROLES``; this module owns the
live definition and :func:`sync_builtin_roles` brings an existing database in
line after a change.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

PERMISSIONS: dict[str, str] = {
    "knowledge.read": "查看知识库、检索与 Wiki",
    "knowledge.write": "入库文献 / 专利、编辑 Wiki",
    "project.write": "创建 / 编辑 / 删除项目",
    "spec.read": "查看项目规格与目标契约",
    "spec.write": "编辑项目规格、目标契约与合规红线",
    "formulation.read": "查看配方（组成以区间显示）",
    "formulation.read_exact": "查看配方精确配比（商业机密）",
    "formulation.write": "编辑配方",
    "formulation.freeze": "钦定定型配方、创建产品修订",
    "doe.write": "生成 / 编辑 DOE 计划",
    "optimize.run": "运行 AI 寻优",
    "lab.execute": "领取实验任务、录入测量值",
    "lab.review": "复核测量值",
    "regulatory.read": "查看法规数据与分类结果",
    "regulatory.review": "复核并锁定物质法规数据",
    "plm.document.write": "编制 SDS / TDS / 标签",
    "plm.document.review": "审核 SDS / TDS / 标签",
    "qc.write": "录入批次质检结果",
    "coa.sign": "签发 COA",
    "release.submit": "提交产品发布",
    "release.sign": "发布流程签批",
    "release.publish": "最终发布",
    "release.withdraw": "撤回 / 作废已发布产品",
    "admin.users": "管理用户与角色",
    "admin.templates": "管理文档模板",
    "admin.companies": "管理公司配置（文档供应商信息与 Logo）",
    "admin.llm": "管理大模型与敏感数据路由",
    "admin.connectors": "管理外部连接器",
    "audit.read": "查看审计日志",
}


@dataclass(frozen=True)
class RoleDef:
    code: str
    name_zh: str
    name_en: str
    permissions: frozenset[str]


def _role(code: str, zh: str, en: str, perms: Iterable[str]) -> RoleDef:
    perms = frozenset(perms)
    unknown = perms - PERMISSIONS.keys()
    if unknown:
        raise ValueError(f"role {code}: unknown permissions {sorted(unknown)}")
    return RoleDef(code, zh, en, perms)


BUILTIN_ROLES: tuple[RoleDef, ...] = (
    _role("rd_engineer", "研发工程师", "R&D Engineer", [
        "knowledge.read", "knowledge.write", "project.write", "spec.read", "formulation.read", "formulation.read_exact",
        "formulation.write", "doe.write", "optimize.run", "lab.execute", "regulatory.read",
        "plm.document.write",
    ]),
    _role("project_lead", "项目负责人", "Project Lead", [
        "knowledge.read", "knowledge.write", "project.write", "spec.read", "spec.write", "formulation.read",
        "formulation.read_exact", "formulation.write", "formulation.freeze", "doe.write", "optimize.run",
        "lab.execute", "lab.review", "regulatory.read", "plm.document.write", "release.submit",
        "release.sign",
    ]),
    _role("lab_technician", "实验员", "Lab Technician", [
        "knowledge.read", "spec.read", "formulation.read", "lab.execute",
    ]),
    _role("ehs_regulatory", "EHS/法规专员", "EHS / Regulatory", [
        "knowledge.read", "spec.read", "formulation.read", "formulation.read_exact", "regulatory.read",
        "regulatory.review", "plm.document.write", "plm.document.review", "release.sign",
    ]),
    _role("quality", "质量", "Quality", [
        "knowledge.read", "spec.read", "formulation.read", "lab.review", "regulatory.read", "qc.write",
        "coa.sign", "plm.document.review", "release.sign",
    ]),
    _role("release_manager", "发布管理员", "Release Manager", [
        "knowledge.read", "spec.read", "formulation.read", "regulatory.read", "plm.document.review",
        "release.sign", "release.publish", "release.withdraw", "audit.read",
    ]),
    _role("sys_admin", "系统管理员", "System Administrator", [
        # knowledge.read is the floor for the whole UI: search, the Knowledge Hub and
        # Wiki are unreachable without it, so an administrator who only holds this role
        # could log in and then get 403 on every retrieval call. Business-data
        # permissions (formulation.*, spec.*, doe.*, release.*) stay out on purpose —
        # trade secrets remain segregated by design; grant a second role for those.
        "knowledge.read",
        "admin.users", "admin.templates", "admin.companies", "admin.llm", "admin.connectors", "audit.read",
    ]),
)
BUILTIN_BY_CODE: dict[str, RoleDef] = {r.code: r for r in BUILTIN_ROLES}

# The four sign-off steps of a product release (plan §5.5.10), in order.
RELEASE_SIGNOFF_ROLES: tuple[str, ...] = ("project_lead", "ehs_regulatory", "quality", "release_manager")


@dataclass(frozen=True)
class Principal:
    """Who is acting: global roles plus per-project roles, resolved to permissions."""

    user_id: str
    global_permissions: frozenset[str]
    project_permissions: dict[str, frozenset[str]]

    def can(self, permission: str, project_id: str | None = None) -> bool:
        if permission not in PERMISSIONS:
            raise KeyError(f"unknown permission {permission!r}")
        if permission in self.global_permissions:
            return True
        if project_id is not None:
            return permission in self.project_permissions.get(project_id, frozenset())
        return False


class PermissionDenied(PermissionError):
    def __init__(self, permission: str, project_id: str | None = None) -> None:
        scope = f" in project {project_id}" if project_id else ""
        super().__init__(f"missing permission {permission}{scope}")
        self.permission = permission
        self.project_id = project_id


def permissions_for(role_codes: Iterable[str], role_permissions: dict[str, Iterable[str]]) -> frozenset[str]:
    out: set[str] = set()
    for code in role_codes:
        out.update(role_permissions.get(code, ()))
    return frozenset(out)


def build_principal(
    user_id: str,
    global_roles: Iterable[str],
    project_roles: dict[str, str],
    role_permissions: dict[str, Iterable[str]] | None = None,
) -> Principal:
    """Resolve role codes to permissions. ``role_permissions`` defaults to the built-ins
    (pass the ``roles`` table contents to honour custom roles)."""
    table = role_permissions or {r.code: r.permissions for r in BUILTIN_ROLES}
    return Principal(
        user_id=user_id,
        global_permissions=permissions_for(global_roles, table),
        project_permissions={pid: permissions_for([code], table) for pid, code in project_roles.items()},
    )


def require(principal: Principal, permission: str, project_id: str | None = None) -> None:
    if not principal.can(permission, project_id):
        raise PermissionDenied(permission, project_id)


def load_principal(session, user_id: str) -> Principal:
    """Build a :class:`Principal` from the database (roles table included, so custom
    roles and edited permission sets apply)."""
    from sqlalchemy import select

    from ...db.models_identity import ProjectMemberRow, RoleRow, UserRoleRow

    table = {r.code: list(r.permissions or []) for r in session.execute(select(RoleRow)).scalars()}
    global_roles = session.execute(
        select(UserRoleRow.role_code).where(UserRoleRow.user_id == user_id)
    ).scalars().all()
    project_roles = {
        m.project_id: m.role_code
        for m in session.execute(select(ProjectMemberRow).where(ProjectMemberRow.user_id == user_id)).scalars()
    }
    return build_principal(user_id, global_roles, project_roles, table)


def sync_builtin_roles(session) -> list[str]:
    """Insert missing built-in roles and reset built-in permission sets to the
    live definition. Returns the codes that changed."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from ...db.models_identity import RoleRow

    existing = {r.code: r for r in session.execute(select(RoleRow)).scalars()}
    changed: list[str] = []
    for role in BUILTIN_ROLES:
        perms = sorted(role.permissions)
        row = existing.get(role.code)
        if row is None:
            session.add(RoleRow(code=role.code, name_zh=role.name_zh, name_en=role.name_en, permissions=perms,
                                builtin=True, created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            changed.append(role.code)
        elif sorted(row.permissions or []) != perms or not row.builtin:
            row.permissions = perms
            row.builtin = True
            changed.append(role.code)
    return changed
