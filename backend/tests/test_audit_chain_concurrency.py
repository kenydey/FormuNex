"""P1-26: audit-chain appends must serialise read-prev -> write -> commit.

Regression tests for the fork in ``hash_chain.append``: the old code held the
process lock only around read -> flush and released it before the caller
committed, so two transactions that both read the same previous hash before
either committed produced two rows with the same ``prev_hash`` and
``verify_chain`` failed at the second one. (This is the fork *mechanism* only;
it is not claimed to be the cause of any particular historical chain break.)

The tests below are written so a regression fails fast instead of hanging:
every "must block" assertion polls for a bounded time while the holder is
still uncommitted, and the holder is always committed/rolled back afterwards
so a blocked thread/process is released during teardown.
"""
from __future__ import annotations

import multiprocessing as mp
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401  (registers all models on Base)
from app.db.models import Base
from app.db.models_plm import AuditLogRow
from app.services.audit import hash_chain


def _engine(db_path: Path):
    """File SQLite engine with the production pragmas that matter here."""
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30}
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=60000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "audit_chain.db"


def _append(session, actor, entity_id):
    return hash_chain.append(
        session, actor=actor, action="test", entity_type="e", entity_id=entity_id
    )


def _rows(session):
    return session.execute(select(AuditLogRow).order_by(AuditLogRow.seq)).scalars().all()


def _wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_uncommitted_append_blocks_second_session(db_path):
    """While session A holds an uncommitted append, session B's append waits.

    Under the old lock-until-flush behaviour B's append returned immediately
    (reading the same prev hash); under the fix it blocks until A commits and
    then chains onto A's row, so the chain verifies.
    """
    engine = _engine(db_path)
    try:
        done, error = threading.Event(), []

        def worker_b():
            try:
                with Session(engine) as s:
                    row_b = _append(s, "tB", "b")
                    s.commit()
                    # Capture plain strings before the session closes: the
                    # row object itself is detached (and expired) afterwards.
                    done.prev_hash = row_b.prev_hash
            except Exception as exc:  # noqa: BLE001 - surfaced to the test
                error.append(exc)
            finally:
                done.set()

        with Session(engine) as s_a:
            row_a = _append(s_a, "tA", "a")
            hash_a = row_a.hash
            thread_b = threading.Thread(target=worker_b)
            thread_b.start()
            try:
                assert not _wait_until(done.is_set, timeout=2.0), (
                    "second session's append returned while the first "
                    "transaction was still uncommitted (fork window open)"
                )
                s_a.commit()
            finally:
                thread_b.join(timeout=15)
            assert not thread_b.is_alive(), "second session's append never finished"
            assert not error, error
            assert done.prev_hash == hash_a
        with Session(engine) as s:
            assert hash_chain.verify_chain(s).ok
            assert [r.actor for r in _rows(s)] == ["tA", "tB"]
    finally:
        engine.dispose()


def test_rollback_releases_lock_and_session_stays_usable(db_path):
    engine = _engine(db_path)
    try:
        with Session(engine) as s:
            _append(s, "tA", "a")
            s.rollback()
            # The rolled-back row must not pin the lock, and the same session
            # must be able to append again in a fresh transaction.
            row = _append(s, "tB", "b")
            s.commit()
            assert row.prev_hash == hash_chain.GENESIS
        with Session(engine) as s:
            assert hash_chain.verify_chain(s) == hash_chain.ChainCheck(ok=True, rows=1)
    finally:
        engine.dispose()


def test_append_after_commit_reacquires_lock(db_path):
    """Commit clears the transaction-scoped lock; the next append re-takes it."""
    engine = _engine(db_path)
    try:
        with Session(engine) as s:
            _append(s, "tA", "a")
            s.commit()
            _append(s, "tB", "b")
            s.commit()
        with Session(engine) as s:
            rows = _rows(s)
            assert len(rows) == 2
            assert rows[1].prev_hash == rows[0].hash
            assert hash_chain.verify_chain(s).ok
    finally:
        engine.dispose()


def test_nested_transaction_commit_does_not_release_lock(db_path):
    """A SAVEPOINT commit (lab measurements record inside ``begin_nested``)
    must not free the lock: the outer transaction is still uncommitted."""
    engine = _engine(db_path)
    try:
        done, error = threading.Event(), []

        def worker_b():
            try:
                with Session(engine) as s:
                    _append(s, "tC", "c")
                    s.commit()
            except Exception as exc:  # noqa: BLE001 - surfaced to the test
                error.append(exc)
            finally:
                done.set()

        with Session(engine) as s:
            _append(s, "tA", "a")
            with s.begin_nested():
                _append(s, "tB", "b")
            # Nested commit done, outer transaction still open: B must block.
            thread_b = threading.Thread(target=worker_b)
            thread_b.start()
            try:
                assert not _wait_until(done.is_set, timeout=2.0), (
                    "nested (SAVEPOINT) commit released the chain lock while "
                    "the outer transaction was still uncommitted"
                )
                s.commit()
            finally:
                thread_b.join(timeout=15)
            assert not thread_b.is_alive()
            assert not error, error
        with Session(engine) as s:
            rows = _rows(s)
            assert [r.actor for r in rows] == ["tA", "tB", "tC"]
            assert hash_chain.verify_chain(s).ok
    finally:
        engine.dispose()


def test_concurrent_append_commit_cycles_do_not_fork(db_path):
    """Hammer: several threads appending + committing never fork the chain."""
    engine = _engine(db_path)
    try:
        errors: list = []

        def worker(idx, count):
            try:
                for i in range(count):
                    with Session(engine) as s:
                        _append(s, f"t{idx}", f"{idx}-{i}")
                        s.commit()
            except Exception as exc:  # noqa: BLE001 - surfaced to the test
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i, 10)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not any(t.is_alive() for t in threads)
        assert not errors, errors
        with Session(engine) as s:
            rows = _rows(s)
            assert len(rows) == 40
            assert len({r.prev_hash for r in rows}) == 40, "duplicate prev_hash: fork"
            assert hash_chain.verify_chain(s).ok
    finally:
        engine.dispose()


# ── cross-process worker (module level: picklable, so it runs under spawn) ──

def _child_append(db_path_str: str, queue):
    """Append + commit in a fresh process; report (prev_hash, hash) or error."""
    try:
        engine = _engine(Path(db_path_str))
        try:
            with Session(engine) as s:
                row = _append(s, "tChild", "child")
                s.commit()
                queue.put(("ok", row.prev_hash, row.hash))
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001 - surfaced to the test
        queue.put(("error", repr(exc), ""))


def test_cross_process_append_blocks_until_commit(db_path):
    """The file lock serialises processes: a second process's append waits
    for the first process's transaction, then chains onto its row.

    Spawn (not fork) is deliberate: a forked child would inherit the
    parent's locked in-process lock object, which no real second process
    ever does.
    """
    engine = _engine(db_path)
    try:
        ctx = mp.get_context("spawn")
        queue = ctx.Queue()
        with Session(engine) as s_parent:
            row_parent = _append(s_parent, "tParent", "parent")
            proc = ctx.Process(target=_child_append, args=(str(db_path), queue))
            proc.start()
            try:
                assert not _wait_until(lambda: not queue.empty(), timeout=3.0), (
                    "second process's append returned while the first "
                    "transaction was still uncommitted (cross-process fork)"
                )
                s_parent.commit()
                proc.join(timeout=60)
                assert proc.exitcode == 0, proc.exitcode
                status, prev_hash, _ = queue.get(timeout=10)
                assert status == "ok", prev_hash
                assert prev_hash == row_parent.hash
            finally:
                if proc.is_alive():
                    proc.terminate()
                proc.join(timeout=10)
        with Session(engine) as s:
            rows = _rows(s)
            assert [r.actor for r in rows] == ["tParent", "tChild"]
            assert hash_chain.verify_chain(s).ok
    finally:
        engine.dispose()
