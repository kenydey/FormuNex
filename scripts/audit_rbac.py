"""RBAC audit: every write route + sensitive read must be covered by exactly one layer.

Usage: ``backend/.venv/bin/python scripts/audit_rbac.py`` (from the repo root).
Exit 0 when every write route and sensitive read is covered; exit 1 otherwise.

Layers (see ``app/api/_rbac_guard.py``):
1. guard (``_rbac_guard.required_permission``) — incl. read-permission rules
   for read-compute POSTs and ``_SENSITIVE_READS``.
2. admin guard (``_admin_guard.required_permission``).
3. endpoint-enforced (``ENDPOINT_ENFORCED`` / ``ENDPOINT_SENSITIVE_READS``)
   — VERIFIED, not just listed: the audit reads the endpoint function's
   source and requires an enforcement call (``.require(``, ``.can(``,
   ``current_user``, ``_require_admin(``, ``assert_owner(``). A route that
   is listed but whose endpoint never checks is reported as
   UNVERIFIED and fails the audit — a table entry is not enforcement.

``POLICY_EXEMPT`` is deliberately NOT a layer (stage 2 rule): an exemption
means "no permission required by design", which is the opposite of
coverage. Exempt routes with no other layer are listed separately for
visibility and do not count as covered, do not fail, and must never be
mistaken for a gap. A route covered by TWO layers is an error (double
enforcement can contradict OR-logic endpoints). A non-exempt write route
covered by NONE is an error (permission-less write). Plain GETs are open
by design and are not flagged; the script lists the gated GETs.
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


# Enforcement markers: substrings that must appear in an
# endpoint-enforced route's own function source (signature included, so
# Depends(current_user) counts). Anything else is prose, not a check.
_ENFORCE_MARKERS = (".require(", ".can(", "current_user", "_require_admin(", "assert_owner(")


def _walk_routes(routes):
    """Yield real routes, expanding FastAPI's lazy ``_IncludedRouter``
    placeholders via ``effective_candidates()`` (same walk as
    scripts/audit/contract/dump_openapi.py — app.routes alone only holds
    the placeholders, which carry no path or endpoint)."""
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            yield from _walk_routes(route.effective_candidates())
        else:
            yield route


def _endpoint_source(app, method: str, path: str) -> str | None:
    """Source of the route function for (method, path), or None when the
    route cannot be resolved / its source cannot be read."""
    import inspect

    for route in _walk_routes(app.routes):
        route_methods = getattr(route, "methods", None) or set()
        if method not in route_methods:
            continue
        if getattr(route, "path", None) != path:
            continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            return None
        try:
            return inspect.getsource(endpoint)
        except (OSError, TypeError):
            return None
    return None


def _endpoint_verified(app, method: str, path: str) -> bool | None:
    """True = endpoint source contains an enforcement call;
    False = source found but no enforcement call;
    None = route/source unresolvable (reported, not assumed)."""
    src = _endpoint_source(app, method, path)
    if src is None:
        return None
    return any(marker in src for marker in _ENFORCE_MARKERS)


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
    unverified: list[str] = []
    unresolvable: list[str] = []
    exempt: list[str] = []
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
            endpoint_claimed = (
                (m in WRITE and any(p.match(path) for p, _ in rbac.ENDPOINT_ENFORCED))
                or (m == "GET" and any(p.match(path) for p, _ in rbac.ENDPOINT_SENSITIVE_READS))
            )
            if endpoint_claimed:
                verified = _endpoint_verified(app, m, path)
                if verified is True:
                    layers.append("endpoint")
                elif verified is False:
                    unverified.append(label)
                else:
                    unresolvable.append(label)
            is_exempt = m in WRITE and any(p.match(path) for p, _ in rbac.POLICY_EXEMPT)
            if m in WRITE:
                if layers:
                    if len(layers) > 1:
                        double.append(f"{label} ({'+'.join(layers)})")
                    else:
                        covered += 1
                elif is_exempt:
                    exempt.append(label)
                elif endpoint_claimed:
                    # Already reported in unverified / unresolvable above;
                    # not double-counted as uncovered.
                    pass
                else:
                    uncovered.append(label)
            elif m == "GET" and layers:
                gated_gets.append(f"{label} ({'+'.join(layers)})")
    print(f"write routes covered: {covered}")
    print(f"policy-exempt write routes (deliberate, not coverage): {len(exempt)}")
    for e in exempt:
        print(f"  {e}")
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
    if unverified:
        print(f"ENDPOINT-CLAIMED BUT UNVERIFIED routes ({len(unverified)}):")
        for u in unverified:
            print(f"  {u}")
    if unresolvable:
        print(f"ENDPOINT-CLAIMED BUT UNRESOLVABLE routes ({len(unresolvable)}):")
        for u in unresolvable:
            print(f"  {u}")
    if uncovered or double or unverified or unresolvable:
        return 1
    print("RBAC audit: OK — every non-exempt write route covered by exactly one verified layer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
