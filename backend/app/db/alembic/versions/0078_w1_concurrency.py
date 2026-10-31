"""W1-4 concurrency hardening (schema half).

1. ``e_signatures`` gains a unique constraint on (step_id, user_id,
   meaning): a sign-off step cannot be signed twice by the same user
   with the same meaning, even when two requests race past the
   "step is pending" application check. NULL step_id rows (document
   review, withdraw) stay distinct under both SQLite and Postgres
   NULL semantics, so they are excluded from the duplicate guard.
2. ``release_packages`` gains a PARTIAL unique index on revision_id
   covering only ``status = 'under_review'``: at most one package
   under review per revision (closes the double-submit race), while
   a rejected package still returns its revision to candidate and
   resubmission stays possible — a plain unique constraint on
   revision_id would break that flow.

Both guards fail loudly with counts instead of deleting data: the
operator resolves the duplicates and re-runs.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _guard_duplicates() -> None:
    bind = op.get_bind()
    row = bind.execute(text(
        "SELECT COUNT(*) FROM ("
        "SELECT step_id, user_id, meaning FROM e_signatures "
        "WHERE step_id IS NOT NULL GROUP BY step_id, user_id, meaning HAVING COUNT(*) > 1)"
    )).first()
    dup_steps = int(row[0]) if row else 0
    if dup_steps:
        raise RuntimeError(
            f"e_signatures has {dup_steps} (step_id, user_id, meaning) groups with duplicates; "
            "resolve them before this migration can add the unique constraint")
    row = bind.execute(text(
        "SELECT COUNT(*) FROM ("
        "SELECT revision_id FROM release_packages WHERE status = 'under_review' "
        "GROUP BY revision_id HAVING COUNT(*) > 1)"
    )).first()
    dup_pkgs = int(row[0]) if row else 0
    if dup_pkgs:
        raise RuntimeError(
            f"release_packages has {dup_pkgs} revisions with more than one under_review package; "
            "resolve them before this migration can add the partial unique index")


def upgrade() -> None:
    # 0068 convention: on a fresh database the 0001 baseline already built
    # these from the current models, so the upgrade is a no-op there;
    # every step checks first and is safe to repeat.
    _guard_duplicates()
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(bind)
    uqs = {c["name"]: list(c["column_names"]) for c in insp.get_unique_constraints("e_signatures")}
    if uqs.get("uq_esignature_step_user_meaning") != ["step_id", "user_id", "meaning"]:
        with op.batch_alter_table("e_signatures") as batch:
            batch.create_unique_constraint("uq_esignature_step_user_meaning",
                                           ["step_id", "user_id", "meaning"])
    indexes = {i["name"] for i in insp.get_indexes("release_packages")}
    if "uq_active_package_per_revision" not in indexes:
        op.create_index("uq_active_package_per_revision", "release_packages", ["revision_id"], unique=True,
                        sqlite_where=text("status = 'under_review'"),
                        postgresql_where=text("status = 'under_review'"))


def downgrade() -> None:
    from sqlalchemy import inspect as sa_inspect

    bind = op.get_bind()
    insp = sa_inspect(bind)
    indexes = {i["name"] for i in insp.get_indexes("release_packages")}
    if "uq_active_package_per_revision" in indexes:
        op.drop_index("uq_active_package_per_revision", table_name="release_packages")
    uqs = {c["name"] for c in insp.get_unique_constraints("e_signatures")}
    if "uq_esignature_step_user_meaning" in uqs:
        with op.batch_alter_table("e_signatures") as batch:
            batch.drop_constraint("uq_esignature_step_user_meaning", type_="unique")
