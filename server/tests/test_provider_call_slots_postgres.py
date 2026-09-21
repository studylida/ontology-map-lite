"Real PostgreSQL crash/concurrency contracts; synthetic isolated reference data."

import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import model_tasks as tasks
from ontology_map.durable_provider import ConfirmedProviderFailure, execute_call

URL = os.environ.get("ONTOLOGY_MAP_KE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="isolated migrated KE PostgreSQL URL not supplied"
)


def scalar(engine, query, **values):
    with engine.connect() as c:
        return c.execute(sa.text(query), values).scalar_one()


@pytest.fixture
def database():
    url = sa.engine.make_url(URL)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_ke127_test")
    engine = sa.create_engine(url)
    with engine.begin() as c:
        contract = c.execute(
            sa.text("""
            INSERT INTO output_schema_definition (task_kind, version_no,
            schema_json, is_active)
            SELECT 'KNOWLEDGE_EXTRACTION', coalesce(max(version_no), 0)+1,
            '{"type":"object"}'::jsonb, false
            FROM output_schema_definition WHERE task_kind='KNOWLEDGE_EXTRACTION'
            RETURNING output_schema_definition_id
        """)
        ).scalar_one()
        task_id = c.execute(
            sa.text("""
            INSERT INTO model_task
              (task_kind, input_hash, output_schema_definition_id, model_version,
            prompt_version, cache_key)
            VALUES ('KNOWLEDGE_EXTRACTION', :key, :contract, 'test-only-provider',
            'ke127-test', :key)
            RETURNING model_task_id
        """),
            {"key": sha256(uuid4().bytes).digest(), "contract": contract},
        ).scalar_one()
    try:
        yield engine, task_id
    finally:
        with engine.begin() as c:
            for table in ("agent_attempt", "provider_call_slot", "model_task"):
                c.execute(
                    sa.text(f"DELETE FROM {table} WHERE model_task_id=:id"),
                    {"id": task_id},
                )
            c.execute(
                sa.text(
                    (
                        "DELETE FROM output_schema_definition WHERE "
                        "output_schema_definition_id=:id"
                    )
                ),
                {"id": contract},
            )
        engine.dispose()


def claim(engine, task_id, name="worker"):
    with Session(engine) as s, s.begin():
        return tasks.claim_task(s, task_id, name)


def reserve(engine, lease):
    with Session(engine) as s, s.begin():
        return tasks.reserve_slot(s, lease)


def complete(engine, slot, outcome="SUCCESS", **kwargs):
    reason = None if outcome == "SUCCESS" else outcome
    with Session(engine) as s, s.begin():
        return tasks.record_terminal(
            s, slot, tasks.TerminalResult(outcome, datetime.now(UTC), reason, **kwargs)
        )


def expire(engine, task_id):
    with engine.begin() as c:
        c.execute(
            sa.text(
                (
                    "UPDATE model_task SET "
                    "lease_expires_at=clock_timestamp()-interval '1 second' WHERE"
                    " model_task_id=:id"
                )
            ),
            {"id": task_id},
        )


def due(engine, task_id):
    with engine.begin() as c:
        c.execute(
            sa.text(
                (
                    "UPDATE model_task SET "
                    "next_attempt_at=clock_timestamp()-interval '1 second' WHERE "
                    "model_task_id=:id"
                )
            ),
            {"id": task_id},
        )


def count(engine, task_id, table):
    return scalar(
        engine, f"SELECT count(*) FROM {table} WHERE model_task_id=:id", id=task_id
    )


def state(engine, task_id):
    return scalar(
        engine, "SELECT status FROM model_task WHERE model_task_id=:id", id=task_id
    )


def test_preflight_failure_consumes_no_budget(database):
    engine, task_id = database
    lease = claim(engine, task_id)

    def invalid_input():
        raise ValueError("LOCAL_INPUT_INVALID")

    with pytest.raises(ValueError, match="LOCAL_INPUT_INVALID"):
        execute_call(engine, lease, invalid_input)
    assert count(engine, task_id, "provider_call_slot") == 0
    assert count(engine, task_id, "agent_attempt") == 0
    assert state(engine, task_id) == "FINAL_FAILED"


def _reserve_and_crash(url, lease):
    engine = sa.create_engine(url)
    reserve(engine, lease)
    os._exit(17)


def test_committed_reservation_survives_process_death_and_reclaims_unknown(database):
    engine, task_id = database
    old = claim(engine, task_id)
    process = multiprocessing.get_context("spawn").Process(
        target=_reserve_and_crash, args=(URL, old)
    )
    process.start()
    process.join(15)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("crash worker did not terminate")
    assert process.exitcode == 17
    assert count(engine, task_id, "provider_call_slot") == 1
    assert claim(engine, task_id, "early-reclaim") is None
    expire(engine, task_id)
    new = claim(engine, task_id, "worker")
    assert new.owner != old.owner
    assert (
        scalar(
            engine,
            (
                "SELECT state FROM provider_call_slot WHERE model_task_id=:id"
                " AND slot_no=1"
            ),
            id=task_id,
        )
        == "UNKNOWN"
    )
    assert count(engine, task_id, "agent_attempt") == 0
    assert reserve(engine, new).slot_no == 2


def test_send_observes_durable_reserved_then_atomic_terminal_result(database):
    engine, task_id = database
    lease = claim(engine, task_id)

    def send():
        assert (
            scalar(
                engine,
                "SELECT state FROM provider_call_slot WHERE model_task_id=:id",
                id=task_id,
            )
            == "RESERVED"
        )
        assert (
            scalar(
                engine,
                "SELECT attempt_count FROM model_task WHERE model_task_id=:id",
                id=task_id,
            )
            == 0
        )
        return "runtime-only-result"

    result = execute_call(engine, lease, lambda: send)
    assert result.value == "runtime-only-result"
    assert result.status == state(engine, task_id) == "RUNNING"
    assert (
        scalar(
            engine,
            "SELECT state FROM provider_call_slot WHERE model_task_id=:id",
            id=task_id,
        )
        == "COMPLETED"
    )
    assert count(engine, task_id, "agent_attempt") == 1
    assert (
        scalar(
            engine,
            "SELECT attempt_count FROM model_task WHERE model_task_id=:id",
            id=task_id,
        )
        == 1
    )
    with Session(engine) as s, s.begin():
        tasks.finish_product(s, lease, valid=True)
    assert state(engine, task_id) == "SUCCESS"
    assert claim(engine, task_id) is None


def test_terminal_transaction_rollback_keeps_all_three_records_unchanged(database):
    engine, task_id = database
    slot = reserve(engine, claim(engine, task_id))
    with pytest.raises(RuntimeError, match="abort-result-transaction"):
        with Session(engine) as s, s.begin():
            tasks.record_terminal(
                s, slot, tasks.TerminalResult("SUCCESS", datetime.now(UTC))
            )
            raise RuntimeError("abort-result-transaction")
    assert count(engine, task_id, "agent_attempt") == 0
    assert (
        scalar(
            engine,
            "SELECT attempt_count FROM model_task WHERE model_task_id=:id",
            id=task_id,
        )
        == 0
    )
    assert (
        scalar(
            engine,
            "SELECT state FROM provider_call_slot WHERE model_task_id=:id",
            id=task_id,
        )
        == "RESERVED"
    )
    assert complete(engine, slot) == "RUNNING"


def test_unknown_gap_uses_slot_number_not_attempt_count(database):
    engine, task_id = database
    reserve(engine, claim(engine, task_id))
    expire(engine, task_id)
    second = reserve(engine, claim(engine, task_id))
    assert second.slot_no == 2
    assert complete(engine, second, "TIMEOUT") == "RETRY_WAIT"
    due(engine, task_id)
    third = reserve(engine, claim(engine, task_id))
    assert third.slot_no == 3
    assert complete(engine, third) == "RUNNING"
    with Session(engine) as s, s.begin():
        tasks.finish_product(s, third.lease, valid=True)
    with engine.connect() as c:
        rows = (
            c.execute(
                sa.text(
                    (
                        "SELECT attempt_no FROM agent_attempt WHERE model_task_id=:id"
                        " ORDER BY attempt_no"
                    )
                ),
                {"id": task_id},
            )
            .scalars()
            .all()
        )
    assert rows == [2, 3]
    assert count(engine, task_id, "provider_call_slot") == 3
    assert (
        scalar(
            engine,
            "SELECT attempt_count FROM model_task WHERE model_task_id=:id",
            id=task_id,
        )
        == 2
    )
    assert state(engine, task_id) == "SUCCESS"


def test_concurrent_reservation_serializes_on_task_lock(database):
    engine, task_id = database
    lease = claim(engine, task_id)
    barrier = Barrier(2)

    def contender():
        barrier.wait(timeout=5)
        try:
            return reserve(engine, lease).slot_no
        except tasks.SlotBusy:
            return "busy"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert set(results) == {1, "busy"}
    assert count(engine, task_id, "provider_call_slot") == 1
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as c:
            c.execute(
                sa.text(
                    (
                        "INSERT INTO provider_call_slot SELECT * FROM "
                        "provider_call_slot WHERE model_task_id=:id"
                    )
                ),
                {"id": task_id},
            )


def test_three_unknown_slots_exhaust_without_fabricated_attempts(database):
    engine, task_id = database
    for expected in range(1, 4):
        slot = reserve(engine, claim(engine, task_id))
        assert slot.slot_no == expected
        expire(engine, task_id)
    assert claim(engine, task_id) is None
    assert state(engine, task_id) == "FINAL_FAILED"
    assert count(engine, task_id, "provider_call_slot") == 3
    assert count(engine, task_id, "agent_attempt") == 0
    assert (
        scalar(
            engine,
            (
                "SELECT count(*) FROM provider_call_slot WHERE "
                "model_task_id=:id AND state='UNKNOWN'"
            ),
            id=task_id,
        )
        == 3
    )


@pytest.mark.parametrize("outcome", ["AUTHENTICATION_ERROR", "INVALID_REQUEST"])
def test_terminal_provider_error_leaves_remaining_slots_unused(database, outcome):
    engine, task_id = database
    lease = claim(engine, task_id)

    def send():
        raise ConfirmedProviderFailure(outcome)

    result = execute_call(engine, lease, lambda: send)
    assert result.status == state(engine, task_id) == "FINAL_FAILED"
    assert claim(engine, task_id) is None
    assert count(engine, task_id, "provider_call_slot") == 1
    assert count(engine, task_id, "agent_attempt") == 1


def test_stale_worker_cannot_complete_unknown_slot_or_finish_task(database):
    engine, task_id = database
    old_slot = reserve(engine, claim(engine, task_id, "same-worker-name"))
    expire(engine, task_id)
    current = claim(engine, task_id, "same-worker-name")
    with pytest.raises(tasks.LeaseLost):
        complete(engine, old_slot)
    with pytest.raises(tasks.LeaseLost):
        with Session(engine) as s, s.begin():
            tasks.finish_product(s, old_slot.lease, valid=True)
    assert (
        scalar(
            engine,
            "SELECT state FROM provider_call_slot WHERE model_task_id=:id",
            id=task_id,
        )
        == "UNKNOWN"
    )
    assert count(engine, task_id, "agent_attempt") == 0
    assert state(engine, task_id) == "RUNNING"
    assert reserve(engine, current).slot_no == 2


def test_backoff_and_output_contract_extra_retry_limit(database):
    engine, task_id = database
    first = reserve(engine, claim(engine, task_id))
    retry_after = timedelta(seconds=93)
    assert (
        complete(engine, first, "OUTPUT_CONTRACT_ERROR", retry_after=retry_after)
        == "RETRY_WAIT"
    )
    delay = scalar(
        engine,
        (
            "SELECT extract(epoch FROM next_attempt_at-clock_timestamp())"
            " FROM model_task WHERE model_task_id=:id"
        ),
        id=task_id,
    )
    assert 85 < delay <= 93
    assert claim(engine, task_id) is None
    due(engine, task_id)
    second = reserve(engine, claim(engine, task_id))
    assert complete(engine, second, "OUTPUT_CONTRACT_ERROR") == "FINAL_FAILED"
    assert count(engine, task_id, "provider_call_slot") == 2


def test_transient_promotion_failure_replays_only_with_remaining_slots(database):
    engine, task_id = database
    for expected in range(1, 4):
        lease = claim(engine, task_id)
        slot = reserve(engine, lease)
        assert slot.slot_no == expected
        complete(engine, slot)
        with Session(engine) as s, s.begin():
            status = tasks.fail_execution(s, lease, transient=True)
        assert status == ("FINAL_FAILED" if expected == 3 else "RETRY_WAIT")
        if expected < 3:
            due(engine, task_id)
    assert count(engine, task_id, "provider_call_slot") == 3
    assert count(engine, task_id, "agent_attempt") == 3
