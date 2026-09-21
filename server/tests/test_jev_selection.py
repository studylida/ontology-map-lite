import json

import httpx
import pytest
from pydantic import SecretStr

from ontology_map.extraction_contracts import SourceSpan, digest
from ontology_map.jev_selection import (
    DROP_THRESHOLD,
    INJECTION_THRESHOLD,
    JevSelectionError,
    select_body_sources,
)
from ontology_map.pilot_budget import PilotBudget


def _spans(count: int) -> tuple[SourceSpan, ...]:
    return tuple(
        SourceSpan(
            source_id=f"s{index}",
            start=index * 2,
            end=index * 2 + 1,
            quote=str(index),
            quote_hash=digest(str(index)),
            paragraph_id=f"p{index}",
        )
        for index in range(count)
    )


def test_jev_keeps_ambiguous_spans_and_batches_atomic_questions(monkeypatch) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == "jev-1.13.0"
        assert len(payload["questions"]) <= 64
        assert all(
            "`spans[" in item["instructions"]
            for key, item in payload["questions"].items()
            if key != "prompt_injection"
        )
        answers = {
            key: {
                "type": "noul",
                "noul": DROP_THRESHOLD if key == "span_0" else 0.949,
            }
            for key in payload["questions"]
        }
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        )

    result = select_body_sources(
        _spans(65),
        title="문서",
        publisher="발행처",
        api_key=SecretStr("private"),
        transport=httpx.MockTransport(handle),
    )
    assert result.calls == len(requests) == 2
    assert "s0" not in result.selection.source_ids
    assert len(result.selection.source_ids) == 64
    assert result.input_tokens == 20 and result.output_tokens == 2
    assert all(
        request.headers["authorization"] == "Bearer private" for request in requests
    )


def test_jev_blocks_prompt_injection_before_body_selection() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    key: {
                        "type": "noul",
                        "noul": (
                            INJECTION_THRESHOLD if key == "prompt_injection" else 0.1
                        ),
                    }
                    for key in payload["questions"]
                },
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    with pytest.raises(JevSelectionError, match="PROMPT_INJECTION_DETECTED"):
        select_body_sources(
            _spans(1),
            title="문서",
            publisher="발행처",
            api_key=SecretStr("private"),
            transport=httpx.MockTransport(handle),
        )


def test_jev_retries_only_rate_limit_or_overload(monkeypatch) -> None:
    calls = 0
    monkeypatch.setattr("ontology_map.jev_selection.time.sleep", lambda _: None)

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429 if calls == 1 else 529)
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    key: {"type": "noul", "noul": 0.1} for key in payload["questions"]
                },
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    assert (
        select_body_sources(
            _spans(1),
            title="문서",
            publisher="발행처",
            api_key=SecretStr("private"),
            transport=httpx.MockTransport(handle),
        ).calls
        == 1
    )
    assert calls == 3

    with pytest.raises(JevSelectionError, match="JEV_REQUEST_REJECTED"):
        select_body_sources(
            _spans(1),
            title="문서",
            publisher="발행처",
            api_key=SecretStr("private"),
            transport=httpx.MockTransport(lambda _: httpx.Response(401)),
        )


def test_jev_records_usage_in_demo_ledger(tmp_path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    key: {"type": "noul", "noul": 0.1} for key in payload["questions"]
                },
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    path = tmp_path / "pilot.jsonl"
    pilot = PilotBudget("jev-ledger", None, None, path, demo_uncapped=True)
    try:
        with pilot.activate():
            select_body_sources(
                _spans(1),
                title="문서",
                publisher="발행처",
                api_key=SecretStr("private"),
                transport=httpx.MockTransport(handle),
            )
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        assert lines[1]["kind"] == "unpriced_reserved"
        event = lines[2]
        assert event["kind"] == "unpriced_confirmed"
        assert event["status"] == "SUCCESS"
        assert event["input_tokens"] == 10
    finally:
        pilot.close()
