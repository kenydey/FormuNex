"""Project-existence guard for project-scoped writes (stage 2 / SR-032).

P1-2 made ``project_id`` non-empty at the DOE-plan and lab-task entries,
but a non-empty string can still dangle: the project may never have
existed, or it may have been truly deleted (stage 2 / SR-035). A write
that names a dead project would be invisible to every project-scoped
read. Fail closed here, at the store entry, instead of at read time.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import ProjectRow


def require_project_active(session: Session, project_id: str) -> ProjectRow:
    """Return the project row, or raise ``ValueError`` when it is missing
    or archived. Callers map the error to their own domain error
    (``ValueError`` → 422 at the DOE-plan API, ``LabError`` → 422 at the
    lab API)."""
    row = session.get(ProjectRow, project_id)
    if row is None:
        raise ValueError(f"项目不存在: {project_id}")
    if row.is_archived:
        raise ValueError(f"项目已归档: {project_id}")
    return row
