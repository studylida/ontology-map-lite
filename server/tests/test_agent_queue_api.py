"""비동기 대기열 및 에이전트 추출 API 엔드포인트 통합 테스트."""

import asyncio
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

from ontology_map.main import app
from ontology_map.schemas import IntakeClaim, IntakeEdge, IntakeNode, IntakePayload
from ontology_map.services.task_queue import task_queue_manager


@pytest.fixture
def client():
    return TestClient(app)


def test_async_extraction_pipeline_flow(client: TestClient):
    """비동기 작업 등록 -> 백그라운드 처리 -> 작업 목록 조회 -> 상세 결과 조회 -> 작업 삭제 전체 흐름 검증."""
    mock_payload = IntakePayload(
        source_project="agent-ingestion",
        document_title="AI 기술 동향 리포트",
        document_content="OpenAI와 MS의 협력 내용",
        nodes=[
            IntakeNode(name="Microsoft", classification="COMPANY", description="글로벌 IT 기업"),
            IntakeNode(name="OpenAI", classification="COMPANY", description="생성형 AI 연구소"),
        ],
        edges=[
            IntakeEdge(source_name="Microsoft", target_name="OpenAI", relation="INVESTS_IN"),
        ],
        claims=[
            IntakeClaim(quote="MS는 OpenAI에 대규모 지분 투자를 단행했다.", claim_text="MS-OpenAI 투자"),
        ],
        insights={"summary": "AI 동맹 전략 분석"},
    )

    # LLM 및 파싱을 Mocking하여 즉각 반환되도록 패치
    with patch(
        "ontology_map.services.task_queue.extract_ontology_from_text",
        return_value=mock_payload,
    ):
        # 1. POST /agent/extract-async (텍스트 비동기 의뢰)
        res_post = client.post(
            "/api/v1/agent/extract-async",
            json={
                "source_type": "text",
                "title": "AI 기술 동향 리포트",
                "content": "MS는 OpenAI에 대규모 지분 투자를 단행했다.",
            },
        )
        assert res_post.status_code == 202
        task_data = res_post.json()
        task_id = task_data["task_id"]
        assert task_id is not None
        assert task_data["status"] == "pending"

        # 2. 백그라운드 워커가 작업을 마칠 때까지 최대 2초간 대기
        for _ in range(20):
            res_detail = client.get(f"/api/v1/agent/tasks/{task_id}")
            assert res_detail.status_code == 200
            status_now = res_detail.json()["status"]
            if status_now == "completed":
                break
            asyncio.run(asyncio.sleep(0.1))

        # 3. 상세 결과(IntakePayload) 검증
        detail = res_detail.json()
        assert detail["status"] == "completed"
        assert detail["title"] == "AI 기술 동향 리포트"
        assert detail["result"] is not None
        assert len(detail["result"]["nodes"]) == 2
        assert detail["result"]["nodes"][0]["name"] == "Microsoft"
        assert len(detail["result"]["edges"]) == 1
        assert detail["result"]["edges"][0]["relation"] == "INVESTS_IN"

        # 4. GET /agent/tasks (대기열 목록 조회)
        res_list = client.get("/api/v1/agent/tasks")
        assert res_list.status_code == 200
        items = res_list.json()
        matching = [t for t in items if t["id"] == task_id]
        assert len(matching) == 1
        assert matching[0]["node_count"] == 2

        # 5. DELETE /agent/tasks/{task_id} (완료 작업 닫기/정리)
        res_del = client.delete(f"/api/v1/agent/tasks/{task_id}")
        assert res_del.status_code == 200

        # 삭제 후 404 확인
        res_after = client.get(f"/api/v1/agent/tasks/{task_id}")
        assert res_after.status_code == 404


def test_async_extraction_file_upload(client: TestClient):
    """파일 업로드(multipart/form-data) 비동기 접수 검증."""
    file_content = b"Mock document content for testing"
    files = {"file": ("sample_report.txt", file_content, "text/plain")}

    res = client.post("/api/v1/agent/extract-async/file", files=files)
    assert res.status_code == 202
    data = res.json()
    assert "task_id" in data
    assert data["status"] == "pending"

    task = task_queue_manager.get_task(data["task_id"])
    assert task is not None
    assert task.source_type == "file"
    assert task.title == "sample_report.txt"

    # 테스트 후 작업 정리
    task_queue_manager.dismiss_task(data["task_id"])
