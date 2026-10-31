"""W1-10 ④: broker probe speaks a Redis command; /health/detailed shares /health's verdict."""
from __future__ import annotations

import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.api import _dispatch
from app.config import get_settings
from app.main import app


# ── Minimal RESP2 server: just enough for redis-py's PING ─────────────────────

def _parse_command(buf: bytes):
    """Parse one `*N` array of bulk strings; (None, buf) when incomplete."""
    if not buf.startswith(b"*"):
        return None, buf
    try:
        nl = buf.index(b"\r\n")
        count = int(buf[1:nl])
    except (ValueError, IndexError):
        return None, buf
    pos = nl + 2
    parts: list[bytes] = []
    for _ in range(count):
        if buf[pos:pos + 1] != b"$":
            return None, buf
        try:
            nl = buf.index(b"\r\n", pos)
            ln = int(buf[pos + 1:nl])
        except (ValueError, IndexError):
            return None, buf
        start = nl + 2
        if len(buf) < start + ln + 2:
            return None, buf
        parts.append(buf[start:start + ln])
        pos = start + ln + 2
    return parts, buf[pos:]


class _FakeRedis(threading.Thread):
    """Modes: 'pong' answers PING (+OK to anything else, e.g. CLIENT SETINFO);
    'close' drops the connection at once; 'silent' never answers."""

    def __init__(self, mode: str):
        super().__init__(daemon=True)
        self.mode = mode
        self.received: list[list[bytes]] = []
        self.port: int | None = None
        self.ready = threading.Event()

    def run(self) -> None:
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.port = srv.getsockname()[1]
        self.ready.set()
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        try:
            if self.mode == "close":
                return
            if self.mode == "silent":
                time.sleep(5)
                return
            conn.settimeout(5)
            buf = b""
            while True:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    return
                if not chunk:
                    return
                buf += chunk
                while True:
                    cmd, buf = _parse_command(buf)
                    if cmd is None:
                        break
                    self.received.append(cmd)
                    if cmd and cmd[0].upper() == b"PING":
                        conn.sendall(b"+PONG\r\n")
                    else:
                        conn.sendall(b"+OK\r\n")
        finally:
            conn.close()
            srv.close()


@pytest.fixture()
def fake_redis(request):
    server = _FakeRedis(request.param)
    server.start()
    assert server.ready.wait(timeout=5) and server.port
    yield server


@pytest.mark.parametrize("fake_redis", ["pong"], indirect=True)
def test_ping_probe_reaches_a_working_broker_without_hello(monkeypatch, fake_redis):
    """The probe must not speak RESP3: an old server that does not know HELLO
    still answers PING, and the probe must never send HELLO at all."""
    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", False, raising=False)
    monkeypatch.setattr(settings, "redis_url", f"redis://127.0.0.1:{fake_redis.port}/0", raising=False)
    assert _dispatch.broker_reachable() is True
    flat = [part for cmd in fake_redis.received for part in cmd]
    assert b"PING" in flat
    assert all(part.upper() != b"HELLO" for part in flat)


@pytest.mark.parametrize("fake_redis", ["close"], indirect=True)
def test_broker_that_drops_the_connection_is_down(monkeypatch, fake_redis):
    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", False, raising=False)
    monkeypatch.setattr(settings, "redis_url", f"redis://127.0.0.1:{fake_redis.port}/0", raising=False)
    assert _dispatch.broker_reachable() is False


@pytest.mark.parametrize("fake_redis", ["silent"], indirect=True)
def test_broker_that_never_answers_is_down(monkeypatch, fake_redis):
    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", False, raising=False)
    monkeypatch.setattr(settings, "redis_url", f"redis://127.0.0.1:{fake_redis.port}/0", raising=False)
    started = time.monotonic()
    assert _dispatch.broker_reachable() is False
    assert time.monotonic() - started < 10  # 1s timeouts, not a Celery-style hang


# ── /health/detailed shares /health's degraded formula ────────────────────────

@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_detailed_is_degraded_when_the_broker_is_down(client, monkeypatch):
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: False)
    body = client.get("/health/detailed").json()
    assert body["status"] == "degraded"
    assert body["task_broker"]["reachable"] is False
    # The default deployment is eager, so the broker is not "required" here;
    # the verdict still degrades because the probe itself reports down.
    assert body["task_broker"]["required"] is False
    assert client.get("/health/detailed").status_code == 200


def test_detailed_is_ok_when_broker_and_parsers_are_up(client, monkeypatch):
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: True)
    monkeypatch.setattr("app.services.parsing.format_availability", lambda: {"pdf": True})
    body = client.get("/health/detailed").json()
    assert body["task_broker"]["reachable"] is True
    assert body["parsers"]["pdf"] is True
    assert body["status"] == "ok"


def test_detailed_is_degraded_when_the_pdf_parser_is_missing(client, monkeypatch):
    """The old detailed endpoint reported ok through a missing parser; the
    shared formula must not."""
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: True)
    monkeypatch.setattr("app.services.parsing.format_availability", lambda: {})
    body = client.get("/health/detailed").json()
    assert body["status"] == "degraded"
