"""Synthetic bodies only. Never call a product model or connect to a database."""

import json
import socket
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from ontology_map.extraction_contracts import ClaimReviewBatch, KnowledgeProposals
from ontology_map.extraction_provider import KimiGenerationAdapter
from ontology_map.kimi_response_archive import response_task
from ontology_map.kimi_transport import KimiStructuredTransport
from ontology_map.llm_config import MODEL_VERSION, SCHEMA_ROLES, role_model
from ontology_map.llm_contracts import Budget, CallFailed, CallLimits
from ontology_map.llm_diagnostics import failure_diagnostic
from ontology_map.model_studio import KimiModels
from ontology_map.pilot_budget import PilotBudget

LIMITS = CallLimits(10_000, 8192, 100_000)
PRIVATE = "private-response-not-for-log"
KEY = "secret-key-not-in-body"


@pytest.fixture(autouse=True)
def isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_MAP_OPENAI_RESPONSE_DIR", str(tmp_path / "responses"))

    def forbidden(*args, **kwargs):
        pytest.fail("NETWORK_FORBIDDEN")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def body(content='{"claims":[]}', **changes):
    value = {
        "model": MODEL_VERSION,
        "usage": {
            "prompt_tokens": 3900,
            "completion_tokens": 2294,
            "total_tokens": 6194,
        },
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
    }
    value.update(changes)
    return (" \n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def generation_request():
    return SimpleNamespace(
        model=MODEL_VERSION,
        output_schema=KnowledgeProposals,
        prompt="synthetic task",
        execution=SimpleNamespace(corrective_input=None),
        payload=KnowledgeProposals(claims=[]),
        limits=LIMITS,
    )


def capture_files(tmp_path):
    return list((tmp_path / "responses").glob("*/metadata.json"))


def transport(raw, status=200):
    return KimiStructuredTransport(
        SecretStr(KEY),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(status, request=req, content=raw)
        ),
    )


def prepare(client, name="KnowledgeProposals", schema=None):
    return client.prepare(
        model=role_model(SCHEMA_ROLES[name]),
        messages=[{"role": "system", "content": "synthetic"}],
        schema_name=name,
        schema=schema or KnowledgeProposals.model_json_schema(),
        limits=LIMITS,
    )


def assert_private(meta_path, raw):
    metadata = json.loads(meta_path.read_text())
    assert (meta_path.parent / "response.body").read_bytes() == raw
    assert metadata["response_sha256"] == sha256(raw).hexdigest()
    assert metadata["response_bytes"] == len(raw)
    assert meta_path.parent.stat().st_mode & 0o777 == 0o700
    for f in meta_path.parent.iterdir():
        assert f.stat().st_mode & 0o777 == 0o600
        if f.name != "response.body":
            assert PRIVATE not in f.read_text()
            assert KEY not in f.read_text()
    return metadata


def test_success_is_saved_before_product_validation_and_linked(tmp_path):
    raw = body()
    client = transport(raw)
    pilot = PilotBudget("archive", 2, Decimal("3"), tmp_path / "pilot.jsonl")
    try:
        with pilot.activate(), response_task(101, 2):
            call = prepare(client)

            def parse(content):
                paths = capture_files(tmp_path)
                assert len(paths) == 1  # Full bytes already present before parser.
                metadata = assert_private(paths[0], raw)
                assert metadata["role"] == "generation"
                assert metadata["model_task_id"] == 101
                assert metadata["provider_slot_no"] == 2
                assert metadata["pilot_sequence"] == 1
                assert metadata["request_sha256"] == call.request_hash
                return KnowledgeProposals.model_validate_json(content, strict=True)

            assert call.parse(parse).claims == []
            assert not pilot.stopped
            assert call.archive.status == "SAVED"
    finally:
        pilot.close()
        client.close()


@pytest.mark.parametrize(
    "raw,stage,reason",
    [
        (b"not-json " + PRIVATE.encode(), "RESPONSE_JSON", "JSON_INVALID"),
        (b"[1,2]", "RESPONSE_JSON", "ENVELOPE_NOT_OBJECT"),
        (body(choices=[]), "OUTPUT", "CHOICES_SHAPE"),
        (
            body(choices=[{"finish_reason": "length"}]),
            "OUTPUT",
            "FINISH_REASON",
        ),
        (
            body(choices=[{"finish_reason": "stop", "message": None}]),
            "OUTPUT",
            "MESSAGE_SHAPE",
        ),
        (
            body(choices=[{"finish_reason": "stop", "message": {"content": {}}}]),
            "OUTPUT",
            "CONTENT_SHAPE",
        ),
        (body(model="unexpected"), "RESPONSE_MODEL", "MODEL_MISMATCH"),
        (body(usage=None), "USAGE", "USAGE_MISSING"),
    ],
)
def test_envelope_failures_keep_full_body_and_existing_errors(
    raw, stage, reason, tmp_path
):
    client = transport(raw)
    try:
        call = prepare(client)
        with pytest.raises(CallFailed) as caught:
            call()
        details = failure_diagnostic(caught.value)
        assert details["stage"] == stage and details["reason"] == reason
        assert details["response_archive"] == "SAVED"
        meta_path = capture_files(tmp_path)[0]
        meta = assert_private(meta_path, raw)
        assert details["response_id"] == meta["response_id"]
        stored = json.loads((meta_path.parent / "failure.json").read_text())
        assert stored["response_id"] == details["response_id"]
    finally:
        client.close()


@pytest.mark.parametrize(
    "content,stage,path",
    [
        ("```json\n{}\n```", "OUTPUT_JSON", "$"),
        ('{"claims":[{}]}', "OUTPUT_SCHEMA", "$.claims[0].candidate_id"),
        (
            '{"claims":[],"' + PRIVATE + '":1}',
            "OUTPUT_SCHEMA",
            "$.<unknown>",
        ),
    ],
)
def test_real_generation_parser_diagnoses_json_and_schema(
    content, stage, path, tmp_path
):
    raw = body(content)
    adapter = KimiGenerationAdapter(
        SecretStr(KEY),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, request=req, content=raw)
        ),
    )
    pilot = PilotBudget("parse", 1, Decimal("3"), tmp_path / "pilot.jsonl")
    try:
        with pilot.activate(), response_task(102, 1):
            with pytest.raises(CallFailed, match="OUTPUT_CONTRACT_ERROR") as caught:
                adapter.prepare(generation_request())()
            assert not pilot.stopped  # Usage confirmed before product rejection.
            details = failure_diagnostic(caught.value)
            assert details["stage"] == stage
            assert path in details["validation_paths"]
            assert details["usage_confirmed"] is True
            assert PRIVATE not in json.dumps(details)
        meta_path = capture_files(tmp_path)[0]
        assert_private(meta_path, raw)
        assert len(pilot.path.read_text().splitlines()) == 3
    finally:
        adapter.close()
        pilot.close()


def test_mention_rule_is_not_relaxed_and_field_path_is_preserved(tmp_path):
    claim = {
        "candidate_id": "c1",
        "statement": PRIVATE,
        "modality": "FACT",
        "source_ids": ["s1"],
        "bindings": [],
        "mentions": [
            {
                "mention_id": "m1",
                "text": "synthetic",
                "node_type": "COMPANY",
                "source_ids": ["s1"],
                "topic_name": "invalid-for-company",
            }
        ],
    }
    raw = body(json.dumps({"claims": [claim]}))
    adapter = KimiGenerationAdapter(
        SecretStr(KEY),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, request=req, content=raw)
        ),
    )
    try:
        with pytest.raises(CallFailed, match="OUTPUT_CONTRACT_ERROR") as caught:
            adapter.prepare(generation_request())()
        details = failure_diagnostic(caught.value)
        assert "$.claims[0].mentions[0]" in details["validation_paths"]
        assert_private(capture_files(tmp_path)[0], raw)
    finally:
        adapter.close()


def test_timeout_has_no_original_and_never_creates_empty_body(tmp_path):
    calls = []

    def timeout(req):
        calls.append(req)
        raise httpx.ReadTimeout(PRIVATE, request=req)

    client = KimiStructuredTransport(
        SecretStr(KEY), transport=httpx.MockTransport(timeout)
    )
    try:
        with pytest.raises(httpx.ReadTimeout) as caught:
            prepare(client)()
        details = failure_diagnostic(caught.value)
        assert details["response_archive"] == "NO_RESPONSE"
        assert details["response_id"] is None
        assert not capture_files(tmp_path) and len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("status", [400, 429, 503])
def test_http_error_body_saved_before_raise_for_status(status, tmp_path):
    raw = b"provider failure body: " + PRIVATE.encode()
    client = transport(raw, status)
    try:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            prepare(client)()
        assert caught.value.response.status_code == status
        assert_private(capture_files(tmp_path)[0], raw)
    finally:
        client.close()


@pytest.mark.parametrize("valid", [True, False])
def test_archive_io_failure_cannot_change_product_result(valid, tmp_path, monkeypatch):
    import ontology_map.kimi_response_archive as archive

    def fail(*args, **kwargs):
        raise OSError(PRIVATE)

    monkeypatch.setattr(archive, "_write", fail)
    raw = body('{"claims":[]}' if valid else '{"claims":[{}]}')
    adapter = KimiGenerationAdapter(
        SecretStr(KEY),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, request=req, content=raw)
        ),
    )
    pilot = PilotBudget("io", 1, Decimal("3"), tmp_path / "pilot.jsonl")
    try:
        with pilot.activate():
            if valid:
                assert adapter.prepare(generation_request())().claims == []
            else:
                with pytest.raises(CallFailed, match="OUTPUT_CONTRACT_ERROR") as e:
                    adapter.prepare(generation_request())()
                assert failure_diagnostic(e.value)["response_archive"] == "FAILED"
            assert not pilot.stopped
            assert len(pilot.path.read_text().splitlines()) == 3
    finally:
        adapter.close()
        pilot.close()


def test_same_request_twice_produces_separate_originals(tmp_path):
    client = transport(body())
    try:
        first, second = prepare(client), prepare(client)
        first()
        second()
        assert first.request_hash == second.request_hash
        assert first.archive.response_id != second.archive.response_id
        assert len(capture_files(tmp_path)) == 2
    finally:
        client.close()


@pytest.mark.parametrize("unsafe", ["symlink", "public", "git"])
def test_unsafe_archive_location_is_rejected_without_changing_result(
    unsafe, tmp_path, monkeypatch
):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    chosen = target
    if unsafe == "symlink":
        chosen = tmp_path / "link"
        chosen.symlink_to(target, target_is_directory=True)
    elif unsafe == "public":
        target.chmod(0o755)
    else:
        (target / ".git").write_text("gitdir: somewhere")
        chosen = target / "responses"
    monkeypatch.setenv("ONTOLOGY_MAP_OPENAI_RESPONSE_DIR", str(chosen))
    client = transport(body())
    try:
        call = prepare(client)
        assert call() == '{"claims":[]}'
        assert call.archive.status == "FAILED"
        assert not list(target.rglob("response.body"))
    finally:
        client.close()


def test_helper_uses_common_archive_without_fabricating_durable_attempt(tmp_path):
    raw = body('{"claims":[]}', model=role_model("claim_review"))
    helpers = KimiModels(
        SecretStr(KEY),
        Budget(1, Decimal("3")),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, request=req, content=raw)
        ),
    )
    try:
        with response_task(103):
            helpers.call(
                "claim_review",
                "synthetic",
                KnowledgeProposals(claims=[]),
                ClaimReviewBatch,
                LIMITS,
            )
        meta = assert_private(capture_files(tmp_path)[0], raw)
        assert meta["model_task_id"] == 103
        assert meta["provider_slot_no"] is None
        assert meta["role"] == "claim_review"
        assert meta["request_sha256"] == helpers.budget.records[0].request_hash
    finally:
        helpers.close()


def test_complete_large_body_is_not_truncated_or_logged(tmp_path, caplog, capsys):
    raw = body('{"claims":[]}', provider_note=PRIVATE * 100_000)
    client = transport(raw)
    try:
        prepare(client)()
        assert_private(capture_files(tmp_path)[0], raw)
        assert PRIVATE not in caplog.text + capsys.readouterr().out
        assert KEY not in caplog.text
    finally:
        client.close()
