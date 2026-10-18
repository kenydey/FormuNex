"""Seed a throw-away database for the browser smoke test (run with the backend virtualenv).

    FORMUNEX_DB_URL=sqlite:////tmp/fx-e2e.db python e2e/seed.py
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.db.database import default_session_factory  # noqa: E402
from app.db.models import FormulationVersion  # noqa: E402
from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow  # noqa: E402
from app.services.identity import accounts  # noqa: E402
from app.services.identity.rbac import sync_builtin_roles  # noqa: E402
from app.services.lab.methods import seed_methods  # noqa: E402

PASSWORD = "Fx-Smoke-2026!"

# E2E users. demo creates everything; the others exist because the product
# enforces separation of duties: a document's approver must differ from its
# creator, one person cannot sign two steps of a release flow, and a
# measurement's reviewer must differ from its enterer.
E2E_USERS = [
    ("demo", "演示用户", ["project_lead", "rd_engineer", "ehs_regulatory", "quality", "release_manager", "sys_admin"]),
    ("checker", "复核用户", ["ehs_regulatory", "quality"]),  # approves docs (plm.document.review + coa.sign), reviews measurements
    ("signer_lead", "签批-项目负责人", ["project_lead"]),      # release step 1
    ("signer_ehs", "签批-EHS", ["ehs_regulatory"]),           # release step 2
    ("signer_qa", "签批-质量", ["quality"]),                  # release step 3
    ("signer_rm", "签批-发布管理员", ["release_manager"]),    # release step 4 (final)
]

with default_session_factory()() as s:
    sync_builtin_roles(s)
    s.flush()
    demo_id = None
    for username, display, roles in E2E_USERS:
        uid = accounts.create_user(s, username=username, password=PASSWORD, display_name=display, roles=roles).id
        if username == "demo":
            demo_id = uid
    xyl = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯", name_en="xylene", aliases=[],
                       hazardous_catalog_no=2289)
    water = SubstanceRow(id=str(uuid.uuid4()), cas_no="7732-18-5", name_zh="水", name_en="water", aliases=[])
    s.add_all([xyl, water])
    s.flush()
    # Locked manual classifications: the release gate (G2) requires key
    # components to be locked by an EHS specialist, and water needs an
    # explicit not_classified row (same shape as test_plm_release.py).
    locked = datetime(2026, 10, 1)
    for hc, cat, h in [("flammable_liquid", "3", "H226"), ("skin_corrosion", "2", "H315")]:
        s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=xyl.id, hazard_class=hc, category=cat,
                                         h_code=h, source="manual", locked_at=locked, locked_by=demo_id))
    s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=water.id, hazard_class="not_classified",
                                     category="-", source="manual", locked_at=locked, locked_by=demo_id))
    s.add(FormulationVersion(id="fv-demo", lineage_id="lin-demo", version=1, domain="anticorrosion_coating",
                             name="FX-100 底漆", created_at=datetime(2026, 10, 1),
                             snapshot={"name": "FX-100 底漆", "ingredients": [
                                 {"name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 30, "role": "solvent"},
                                 {"name": "水", "cas_no": "7732-18-5", "weight_pct": 70, "role": "water"}]}))
    seed_methods(s)
    s.commit()
print("seeded")
