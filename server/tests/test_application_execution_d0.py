"""D0 guards: the offline plan cannot touch product state or send a call."""

import json
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

import pytest

from ontology_map import application_execution as app
from ontology_map import initial_publication_handoff as handoff
from ontology_map.db import runtime_bootstrap as bootstrap
from ontology_map.pilot_budget import PilotBudget, PilotBudgetError


def _unexpected(*_args, **_kwargs):
    raise AssertionError("dry run reached a database or provider boundary")


def test_dry_run_uses_real_contracts_without_io(monkeypatch, capsys):
    monkeypatch.setattr(app, "Session", _unexpected)
    monkeypatch.setattr(app, "run_extraction", _unexpected)
    monkeypatch.setattr(app, "run_initial_publication", _unexpected)
    monkeypatch.setattr(
        app, "finalize_extraction_with_initial_publication", _unexpected
    )
    assert app.main(["--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["database_reads"] == plan["database_writes"] == 0
    assert plan["provider_sends"] == 0
    assert plan["blocking_gap"] is None
    assert set(plan["output_schema_sha256"]) == {
        "KNOWLEDGE_EXTRACTION",
        "NODE_CONTEXT",
        "FOLLOWUP_QUESTIONS",
        "NODE_INSIGHT",
    }
    assert all(
        plan["output_schema_sha256"][kind]
        == sha256(
            json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        and schema != {"type": "object"}
        for kind, schema in bootstrap.output_schemas().items()
    )


def test_missing_product_policy_blocks_execution_before_enqueue(monkeypatch):
    monkeypatch.setattr(bootstrap, "_require_ontology", lambda _session: None)
    monkeypatch.setattr(bootstrap, "_require_output_schemas", lambda _session: None)

    def missing(_session):
        raise ValueError("PRODUCT_LINT_DEFINITION_MISSING")

    monkeypatch.setattr(bootstrap.product_lint, "require_product_policy", missing)
    with pytest.raises(
        bootstrap.RuntimeNotReady, match="PRODUCT_LINT_DEFINITION_MISSING"
    ):
        bootstrap.require_runtime_ready(object())


def test_schema_bootstrap_refuses_active_placeholder_without_writes(monkeypatch):
    class ActiveResult:
        def mappings(self):
            return self

        def all(self):
            return [
                {"is_active": True, "schema_json": {"type": "object"}, "version_no": 1}
            ]

    class FakeSession:
        calls = 0

        def in_transaction(self):
            return True

        def execute(self, _statement):
            self.calls += 1
            if self.calls != 1:
                raise AssertionError("attempted a definition write")
            return ActiveResult()

    monkeypatch.setattr(
        bootstrap, "output_schemas", lambda: {"NODE_CONTEXT": {"real": True}}
    )
    session = FakeSession()
    with pytest.raises(
        bootstrap.RuntimeNotReady, match="ACTIVE_OUTPUT_SCHEMA_MISMATCH"
    ):
        bootstrap.ensure_output_schemas(session)
    assert session.calls == 1


@pytest.mark.parametrize(
    ("disposition", "finalizable", "pilot_stopped"),
    [
        ("NOT_CLAIMED", False, False),
        ("AWAITING_RECLAIM", False, False),
        ("LEASE_LOST", False, False),
        ("FAILED", False, False),
        ("ZERO_RESULT", True, False),
        ("ALL_BLOCKED", True, False),
        ("VERIFIED_RUNTIME", True, False),
        ("VERIFIED_RUNTIME", False, True),
    ],
)
def test_application_only_finalizes_claimed_runtime_results(
    monkeypatch, tmp_path, disposition, finalizable, pilot_stopped
):
    class FakeSession:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def begin(self):
            return self

    calls = []
    runner = SimpleNamespace(disposition=disposition)
    runtime = SimpleNamespace(
        document=SimpleNamespace(document_id="7"),
        ontology=object(),
        identity_settings=lambda: {"include_structure": False},
    )
    execution = SimpleNamespace(
        runtime_settings={"extraction_runner": runtime.identity_settings()}
    )
    monkeypatch.setattr(app, "_require_ready", lambda *_args: None)
    monkeypatch.setattr(app, "Session", FakeSession)
    monkeypatch.setattr(
        app.extraction_tasks,
        "enqueue_extraction",
        lambda *_args: SimpleNamespace(task_id=11),
    )
    pilot = PilotBudget("offline", 1, Decimal("1"), tmp_path / "pilot.jsonl")

    def fake_run(*_args):
        if pilot_stopped:
            pilot.stop()
        return runner

    monkeypatch.setattr(app, "run_extraction", fake_run)

    def finalize(*_args, **_kwargs):
        calls.append("finalize")
        return "product"

    monkeypatch.setattr(app, "finalize_extraction_with_initial_publication", finalize)
    try:

        def run():
            return app.run_document(
                object(),
                7,
                "worker",
                execution,
                runtime,
                object(),
                _unexpected,
                _unexpected,
                _unexpected,
                _unexpected,
                _unexpected,
                pilot=pilot,
            )

        if pilot_stopped:
            with pytest.raises(PilotBudgetError, match="PILOT_STOPPED"):
                run()
        else:
            result = run()
            assert result == ("product" if finalizable else runner)
    finally:
        pilot.close()
    assert calls == (["finalize"] if finalizable else [])


def test_committed_batch_callback_runs_before_publication(monkeypatch) -> None:
    product = SimpleNamespace(disposition="SUCCESS", promotion_batch_id=17)
    order = []
    monkeypatch.setattr(
        handoff.extraction_promotion,
        "finalize_extraction",
        lambda *_args, **_kwargs: product,
    )

    def publish(*_args, **_kwargs):
        assert order == [17]
        return "published"

    monkeypatch.setattr(handoff, "handoff_committed_promotion", publish)
    result = handoff.finalize_extraction_with_initial_publication(
        object(),
        object(),
        object(),
        object(),
        object(),
        "worker",
        prepare_node_context_provider=_unexpected,
        prepare_followup_provider=_unexpected,
        prepare_insight_provider=_unexpected,
        after_promotion=lambda committed: order.append(committed.promotion_batch_id),
    )

    assert result.product is product
    assert result.publication == "published"
