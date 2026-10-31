"""Stage 0 / SR-003: supplier SDS import must not move the approved pointer."""
from __future__ import annotations

import uuid

from app.config import get_settings

METHANOL_SDS = """安全数据表

第1部分 化学品及企业标识
产品名称：甲醇
供应商：测试化工有限公司

第2部分 危险性概述
GHS 分类：易燃液体,类别 2
危险说明：H225
防范说明：P210

第3部分 成分/组成信息
甲醇 CAS：67-56-1 含量：99.9%
"""


def test_supplier_import_leaves_latest_approved_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'stage0_08.db'}")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    import app.db.database as db_mod

    db_mod._default.clear()
    from app.db.database import default_session_factory
    from app.db.models_regulatory import RegulatoryDataRelease
    from app.services.plm.ghs.service import latest_data_release
    from app.services.plm.regulatory.supplier_sds import import_sds, parse_sds_text

    with default_session_factory()() as session:
        approved = RegulatoryDataRelease(id=str(uuid.uuid4()), label="baseline",
                                         source="seed", status="approved")
        session.add(approved)
        session.commit()
        assert latest_data_release(session) == approved.id

        result = import_sds(session, parse_sds_text(METHANOL_SDS),
                            filename="methanol.txt", digest="d-stage0")
        session.commit()

        import_row = session.get(RegulatoryDataRelease, result["release_id"])
        assert import_row is not None
        assert import_row.status == "import"
        # The pointer did not move: existing documents keep passing the gate.
        assert latest_data_release(session) == approved.id
