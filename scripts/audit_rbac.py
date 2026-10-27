"""RBAC audit: every write route + sensitive read must be covered by exactly one layer.

Usage: ``backend/.venv/bin/python scripts/audit_rbac.py`` (from the repo root).
Exit 0 when every write route and sensitive read is covered; exit 1 otherwise.

Layers (see ``app/api/_rbac_guard.py``):
1. guard (``_rbac_guard.required_permission``) — incl. read-permission rules
   for read-compute POSTs and ``_SENSITIVE_READS``.
2. admin guard (``_admin_guard.required_permission``).
3. endpoint-enforced (``ENDPOINT_ENFORCED`` / ``ENDPOINT_SENSITIVE_READS``).
4. policy-exempt (``POLICY_EXEMPT``) — deliberate, documented exemptions.

A route covered by TWO layers is an error (double enforcement can contradict
OR-logic endpoints). A write route covered by NONE is an error
(permission-less write). Plain GETs are open by design and are not flagged;
the script lists the gated GETs for visibility.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

WRITE = {"POST", "PUT", "PATCH", "DELETE"}


def _permission_atoms() -> set[str]:
    """Every permission code the guard tables can hand to ``Principal.can``.

    W0 (P0-2's survival cause): the audit used to count layers but never
    checked that the permission strings themselves exist in the catalogue, so
    ``chemical.read`` / ``plm.document.read`` shipped unmapped and every JWT
    call to those routes died with 500 (KeyError, no handler). A code that is
    not in ``PERMISSIONS`` now fails this audit instead of production.
    """
    import re

    from app.api import _admin_guard as admin_guard_mod
    from app.api import _rbac_guard as rbac
    from app.services.identity.rbac import PERMISSIONS

    atoms: set[str] = set()
    for _pattern, permission in rbac._RULES:
        atoms.add(permission)
    for _pattern, permission in rbac._SENSITIVE_READS:
        atoms.add(permission)
    for _pattern, permission, _exempt in admin_guard_mod._RULES:
        atoms.add(permission)
    # ENDPOINT_ENFORCED notes are free text ("a OR b", "ownership (...)"): only
    # dotted tokens can be permission codes; the rest is prose by construction.
    for _pattern, note in rbac.ENDPOINT_ENFORCED:
        atoms.update(re.findall(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+", note))
    for _pattern, note in rbac.ENDPOINT_SENSITIVE_READS:
        atoms.update(re.findall(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+", note))
    unknown = sorted(atoms - set(PERMISSIONS))
    if unknown:
        print(f"UNKNOWN permission atoms ({len(unknown)}):")
        for u in unknown:
            print(f"  {u}")
        print("RBAC audit: FAIL — guard tables reference permissions outside PERMISSIONS")
        return None
    return atoms


def main() -> int:
    from app.api import _admin_guard as admin_guard_mod
    from app.api import _rbac_guard as rbac
    from app.main import app

    if _permission_atoms() is None:
        return 1
    print("permission atoms: all in PERMISSIONS")

    openapi = app.openapi()
    uncovered: list[str] = []
    double: list[str] = []
    covered = 0
    gated_gets: list[str] = []
    for path, methods in sorted(openapi["paths"].items()):
        for method in sorted(methods):
            m = method.upper()
            label = f"{m} {path}"
            layers: list[str] = []
            if rbac.required_permission(m, path)[0] is not None:
                layers.append("guard")
            if m in WRITE and admin_guard_mod.required_permission(m, path) is not None:
                layers.append("admin")
            if m in WRITE and any(p.match(path) for p, _ in rbac.ENDPOINT_ENFORCED):
                layers.append("endpoint")
            if m == "GET" and any(p.match(path) for p, _ in rbac.ENDPOINT_SENSITIVE_READS):
                layers.append("endpoint")
            if m in WRITE and any(p.match(path) for p, _ in rbac.POLICY_EXEMPT):
                layers.append("exempt")
            if m in WRITE:
                if len(layers) == 0:
                    uncovered.append(label)
                elif len(layers) > 1:
                    double.append(f"{label} ({'+'.join(layers)})")
                else:
                    covered += 1
            elif m == "GET" and layers:
                gated_gets.append(f"{label} ({'+'.join(layers)})")
    print(f"write routes covered: {covered}")
    print(f"gated GETs ({len(gated_gets)}):")
    for g in gated_gets:
        print(f"  {g}")
    if uncovered:
        print(f"UNCOVERED write routes ({len(uncovered)}):")
        for u in uncovered:
            print(f"  {u}")
    if double:
        print(f"DOUBLE-covered routes ({len(double)}):")
        for d in double:
            print(f"  {d}")
    if uncovered or double:
        return 1
    print("RBAC audit: OK — every write route covered by exactly one layer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
