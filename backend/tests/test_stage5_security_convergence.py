"""Stage 5 / SR-046 + SR-047: capped uploads, docx structure checks, login throttle + lock oracle removal."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401 - register all models on Base
from app.api._uploads import read_upload_capped
from app.api.plm import _require_docx_bytes
from app.db.models import Base
from app.db.models_identity import UserRow
from app.db.models_plm import AuditLogRow
from app.middleware.rate_limit import _RATE_RULES
from app.services.identity import accounts


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_docx_check_accepts_a_real_docx_and_rejects_zip_shaped_non_docx():
    from docx import Document

    buf = io.BytesIO()
    Document().save(buf)
    _require_docx_bytes(buf.getvalue())  # no raise
    # PK prefix + valid zip, but no OOXML content types: not a docx.
    with pytest.raises(HTTPException) as exc:
        _require_docx_bytes(_zip({"hello.txt": b"hi"}))
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException):
        _require_docx_bytes(b"not a zip at all")


def test_docx_check_rejects_a_corrupt_entry():
    data = bytearray(_zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>"}))
    # Corrupt a payload byte inside the archive without fixing its CRC.
    idx = data.find(b"<w:document/>")
    assert idx > 0
    data[idx] ^= 0xFF
    with pytest.raises(HTTPException) as exc:
        _require_docx_bytes(bytes(data))
    assert exc.value.status_code == 422


class _FakeUpload:
    def __init__(self, data, size=None):
        self._buf = io.BytesIO(data)
        self.size = len(data) if size is None else size

    async def read(self, n=-1):
        return self._buf.read(n)


async def _read(data, limit):
    return await read_upload_capped(_FakeUpload(data), "x.bin", limit=limit)


def test_read_upload_capped_refuses_past_the_limit_while_reading():
    import asyncio

    assert asyncio.run(_read(b"a" * 100, 100)) == b"a" * 100
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read(b"a" * 101, 100))
    assert exc.value.status_code == 413
    # A lying size header is refused before any chunk is read.
    async def _sized():
        return await read_upload_capped(_FakeUpload(b"a" * 10, size=10**9), "x.bin", limit=100)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_sized())
    assert exc.value.status_code == 413


def test_login_rate_rule_exists():
    assert ("POST", "/api/identity/login", 60, 60.0) in _RATE_RULES


def test_locked_login_answers_generic_and_audits_the_lock():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 11, 2, 12, 0, 0)
    with Session(engine) as session:
        user = UserRow(id="u1", username="u1", is_active=True, password_hash=None,
                       failed_logins=5, locked_until=now + timedelta(minutes=15))
        session.add(user)
        session.commit()
        with pytest.raises(accounts.AuthError) as exc:
            accounts._check_password(session, user, "whatever", action="login", now=now)
        assert str(exc.value) == "用户名或密码错误"
        session.commit()
        rows = session.execute(select(AuditLogRow).where(AuditLogRow.action == "login.locked")).scalars().all()
        assert len(rows) == 1 and rows[0].detail.get("locked") is True
