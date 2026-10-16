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

with default_session_factory()() as s:
    sync_builtin_roles(s)
    s.flush()
    accounts.create_user(s, username="demo", password=PASSWORD, display_name="演示用户", roles=[
        "project_lead", "rd_engineer", "ehs_regulatory", "quality", "release_manager", "sys_admin"])
    xyl = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯", name_en="xylene", aliases=[],
                       hazardous_catalog_no=2289)
    s.add_all([xyl, SubstanceRow(id=str(uuid.uuid4()), cas_no="7732-18-5", name_zh="水", name_en="water", aliases=[])])
    s.flush()
    for hc, cat, h in [("flammable_liquid", "3", "H226"), ("skin_corrosion", "2", "H315")]:
        s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=xyl.id, hazard_class=hc, category=cat,
                                         h_code=h, source="cn_catalog"))
    s.add(FormulationVersion(id="fv-demo", lineage_id="lin-demo", version=1, domain="anticorrosion_coating",
                             name="FX-100 底漆", created_at=datetime(2026, 10, 1),
                             snapshot={"name": "FX-100 底漆", "ingredients": [
                                 {"name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 30, "role": "solvent"},
                                 {"name": "水", "cas_no": "7732-18-5", "weight_pct": 70, "role": "water"}]}))
    seed_methods(s)
    s.commit()
print("seeded")
