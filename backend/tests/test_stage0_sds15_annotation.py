"""Stage 0 / SR-004: SDS §15 stored catalogue numbers carry a risk caveat."""
from __future__ import annotations

from app.config import get_settings


def test_catalog_line_carries_unverified_caveat(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'stage0_09.db'}")
    get_settings.cache_clear()
    import app.db.database as db_mod

    db_mod._default.clear()
    from app.db.database import default_session_factory
    from app.db.models_regulatory import SubstanceRow
    from app.services.plm.documents.service import _listings

    with default_session_factory()() as session:
        session.add(SubstanceRow(id="s1", cas_no="67-56-1", name_zh="甲醇",
                                 hazardous_catalog_no=2289))
        session.add(SubstanceRow(id="s2", cas_no="7732-18-5", name_zh="水",
                                 hazardous_catalog_no=None))
        session.commit()
        lines = _listings(session, ["67-56-1", "7732-18-5"])

    catalog = [line for line in lines if "危险化学品目录" in line]
    assert len(catalog) == 1
    assert "序号 2289" in catalog[0]
    assert "尚未与官方文本逐条核对，仅供参考" in catalog[0]
    # No-number rows still render no catalogue claim at all.
    assert not any("水" in line and "危险化学品目录" in line for line in lines)
