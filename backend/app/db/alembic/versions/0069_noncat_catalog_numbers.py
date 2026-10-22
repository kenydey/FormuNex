"""Null the falsely seeded catalogue numbers of 29 non-catalogue substances (P1-23 data repair).

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-22

Before P1-23, ``seed._catalog_no`` fell back to the bundled file's row
number (``index``) when an entry had no official ``catalog_no``. The 29
entries explicitly marked ``source`` as 非目录条目 (not in the
《危险化学品目录》(2015 版)) therefore seeded
``substances.hazardous_catalog_no`` with their file row numbers, and SDS
§15 rendered the false claim "列入《危险化学品目录（2015 版）》，序号 N"
for substances that are not listed at all (e.g. 黄樟素 94-59-7 as 2730).

P1-23 fixed the seed logic (non-catalogue entries now seed NULL), but
rows already seeded under the old logic keep the wrong number — the
seed's substance path is insert-only, so a re-seed does not correct
them. This migration NULLs exactly those rows.

The match is by CAS number, not by the old index value: these 29 CAS
numbers are not in the catalogue at all, so any non-NULL
``hazardous_catalog_no`` on them is wrong by definition, whatever value
it holds. Rows for genuine catalogue substances are untouched (the
control in the test proves it). Classification rows need no repair:
``_replace_catalog_classifications`` deletes and re-inserts all
unlocked ``cn_catalog`` rows on every seed run, so they refresh
themselves; only the insert-only substances table carries the stale
numbers forward.

The CAS list is frozen here on purpose: migrations must not read the
bundled JSON at upgrade time (the file can change). The list below is
the 29 entries marked 非目录条目 in
``backend/app/resources/regulatory/hazard_chemical_info.json`` at the
time of writing, with their old file row numbers in comments.

Upgrade is idempotent (re-running NULLs already-NULL rows) and a no-op
on a fresh database (no rows match). Downgrade is a deliberate no-op:
the old values were false claims, and restoring them would reintroduce
the SDS §15 misstatement; there is nothing correct to restore to.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

# 29 entries marked 非目录条目 in hazard_chemical_info.json. Comments
# give the old file row number each one was wrongly seeded with.
_NONCATALOGUE_CAS = [
    "94-59-7",  # 黄樟素（旧行号 2730）
    "120-58-1",  # 异黄樟素（旧行号 2731）
    "89-52-1",  # N-乙酰邻氨基苯酸（旧行号 2732）
    "118-92-3",  # 邻氨基苯甲酸（旧行号 2733）
    "82-58-6",  # 麦角酸＊（旧行号 2734）
    "113-15-5",  # 麦角胺＊（旧行号 2735）
    "60-79-7",  # 麦角新碱＊（旧行号 2736）
    "321-98-2",  # 麻黄素类物质＊（旧行号 2737）
    "120-57-0",  # 胡椒醛（旧行号 2738）
    "8006-80-2",  # 黄樟油（旧行号 2739）
    "103-79-7",  # 1-苯基-2-丙酮（旧行号 2740）
    "4676-39-5",  # 3,4-亚甲基二氧苯基-2-丙酮（旧行号 2741）
    "39742-60-4",  # N-苯乙基-4-哌啶酮（旧行号 2742）
    "21409-26-7",  # 4-苯胺基-N-苯乙基哌啶（旧行号 2743）
    "5558-29-2",  # 3-氧-2-苯基丁腈（旧行号 2744）
    "6740-85-8",  # 邻氯苯基环戊酮（旧行号 2745）
    "103-82-2",  # 苯乙酸（旧行号 2746）
    "114-70-5",  # 苯乙酸钠（旧行号 2747）
    "13005-36-2",  # 苯乙酸钾（旧行号 2748）
    "93-55-0",  # 1-苯基-1-丙酮（旧行号 2754）
    "16648-44-5",  # 3-氧-2-苯基丁酸甲（旧行号 2755）
    "102-01-2",  # 3-氧-2-苯基丁酰胺（旧行号 2756）
    "125541-22-2",  # 1-叔丁氧羰基-4-(N-苯基氨基)哌啶（旧行号 2757）
    "1609-66-1",  # N-苯基-N-(4-哌啶基)丙酰胺（旧行号 2758）
    "13956-29-1",  # 大麻二酚（旧行号 2759）
    "25547-51-7",  # 2-甲基-3-苯基缩水甘油酸及其酯类（旧行号 2760）
    "4433-88-9",  # 3-氧-2-苯基丁酸及其酯类（旧行号 2761）
    "96-48-0",  # γ-丁内酯（旧行号 2768）
    "115-77-5",  # 2，2-双(羟甲基)1，3-丙二醇（旧行号 2857）
]


def upgrade() -> None:
    bind = op.get_bind()
    if "substances" not in sa.inspect(bind).get_table_names():
        return
    placeholders = ", ".join(f":cas_{i}" for i in range(len(_NONCATALOGUE_CAS)))
    params = {f"cas_{i}": cas for i, cas in enumerate(_NONCATALOGUE_CAS)}
    bind.execute(
        text(
            "UPDATE substances SET hazardous_catalog_no = NULL "
            f"WHERE cas_no IN ({placeholders}) AND hazardous_catalog_no IS NOT NULL"
        ),
        params,
    )


def downgrade() -> None:
    # Deliberate no-op: the pre-0069 values were false catalogue claims
    # (file row numbers presented as official 序号). Restoring them would
    # reintroduce the SDS §15 misstatement, so there is nothing to restore.
    pass
