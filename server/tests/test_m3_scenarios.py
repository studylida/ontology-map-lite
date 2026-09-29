"""M3 검증 시나리오 (지시서 5절 M3 & 6절 T08, T09, T10).

- T08: 같은 노드 명시적 분석 10회·근거 부족·생성 실패 (결과 1묶음 및 QA 상한 유지, 실패 시 정상 결과 보존)
- T09: 입력 변경 전의 늦은 분석 결과 방어 (Race Condition 차단)
- T10: GET 반복 및 동일 fingerprint 자동 처리 시 모델 호출 0회 보장
"""

import uuid
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ontology_map.db.schema import Claim, Classification, Document, Edge, Node, NodeInsight, NodeQAPair
from ontology_map.services.insight_service import generate_node_insight


def test_t08_repeated_analysis_lack_of_evidence_and_failure_resilience(client: TestClient, db_session: Session, monkeypatch):
    """T08: 동일 노드 명시적 분석 10회 호출 시 단일 결과 유지, 근거 부족 분기, 모델 실패 시 결과 보존 검증."""
    run_id = uuid.uuid4().hex[:6]

    # 1. Classification & Document & Claim & Node 생성
    cls_obj = Classification(code=f"COMP_{run_id}", display_name="Company", is_active=True)
    doc_obj = Document(title="테스트 문서", normalized_content="테스트 본문", status="COMPLETED")
    db_session.add_all([cls_obj, doc_obj])
    db_session.flush()

    claim_1 = Claim(document_id=doc_obj.id, quote_text="인용구 1", statement="사실 1")
    claim_2 = Claim(document_id=doc_obj.id, quote_text="인용구 2", statement="사실 2")
    db_session.add_all([claim_1, claim_2])
    db_session.flush()

    node = Node(
        name=f"분석대상_{run_id}",
        classification_id=cls_obj.id,
        description="인공지능 가속기 선도 기업",
        properties={"country": "KR"},
        claim_ids=[claim_1.id, claim_2.id],
    )
    db_session.add(node)
    db_session.commit()

    # --- 동일 노드에 대해 명시적 분석 10회 반복 실행 ---
    for i in range(10):
        insight, qas = generate_node_insight(db_session, node.id, force=True)
        assert insight is not None
        assert len(qas) <= 5

    # 10회 실행 후에도 NodeInsight는 정확히 1행이어야 함
    stmt_count = select(func.count(NodeInsight.id)).where(NodeInsight.node_id == node.id)
    assert db_session.execute(stmt_count).scalar() == 1

    # NodeQAPair도 상한(최대 5개) 이하 유지
    stmt_qa_count = select(func.count(NodeQAPair.id)).where(NodeQAPair.node_id == node.id)
    qa_count = db_session.execute(stmt_qa_count).scalar()
    assert 0 < qa_count <= 5

    initial_insight_text = insight.overall_insight
    assert len(initial_insight_text) > 0

    # --- 근거 부족 판단 검증 (승인 Claim 0개이고 설명도 없는 노드) ---
    blank_node = Node(
        name=f"빈노드_{run_id}",
        classification_id=cls_obj.id,
        description="",
        properties={},
        claim_ids=[],
    )
    db_session.add(blank_node)
    db_session.commit()

    blank_insight, blank_qas = generate_node_insight(db_session, blank_node.id)
    assert blank_insight is not None
    # 사용자 피드백 반영: 부드러운 안내 문구 확인
    assert "아직 관련 소식이나 확인된 근거가 충분하지 않아 분석 요약을 생성하지 않았습니다." in blank_insight.overall_insight
    assert blank_qas == []

    # --- 실패 격리 검증: LLM 호출 시 500 에러 발생 시 기존 정상 결과 보존 ---
    def mock_fail_post(*args, **kwargs):
        raise RuntimeError("LLM API 일시적 500 장애")

    monkeypatch.setattr("httpx.Client.post", mock_fail_post)

    # 실패 시 기존 insight를 보존하고 예외를 억제하여 마지막 정상 결과를 반환하는지 확인
    res_insight, res_qas = generate_node_insight(db_session, node.id, force=True)
    assert res_insight.overall_insight == initial_insight_text
    assert len(res_qas) == qa_count


def test_t09_stale_result_race_condition_protection(client: TestClient, db_session: Session, monkeypatch):
    """T09: 분석 생성 도중 입력(지문)이 변경된 경우 늦은 구버전 결과가 최신 상태를 덮어쓰지 않고 기각됨을 검증."""
    run_id = uuid.uuid4().hex[:6]

    cls_obj = Classification(code=f"TECH_{run_id}", display_name="Tech", is_active=True)
    doc_obj = Document(title="레이스 문서", normalized_content="레이스 본문", status="COMPLETED")
    db_session.add_all([cls_obj, doc_obj])
    db_session.flush()

    c1 = Claim(document_id=doc_obj.id, quote_text="인용 1", statement="초기 사실")
    c2 = Claim(document_id=doc_obj.id, quote_text="인용 2", statement="경쟁 도중 추가된 사실")
    db_session.add_all([c1, c2])
    db_session.flush()

    node = Node(
        name=f"경쟁노드_{run_id}",
        classification_id=cls_obj.id,
        description="경쟁 검증용 노드",
        claim_ids=[c1.id],
    )
    db_session.add(node)
    db_session.commit()

    # 1. 초기 정상 분석 생성
    orig_insight, _ = generate_node_insight(db_session, node.id, force=True)
    orig_fp = orig_insight.input_fingerprint

    # 2. LLM 호출 도중 노드에 새 Claim이 추가되어 지문이 바뀌는 경쟁 상황 모의
    def race_condition_post(*args, **kwargs):
        # LLM 응답이 생성되는 시점에 외부에서 새 Claim이 DB에 추가되었다고 가정
        with db_session.begin_nested():
            node_in_db = db_session.get(Node, node.id)
            node_in_db.claim_ids = [c1.id, c2.id]
            db_session.flush()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"recent_history_summary": "늦은 결과", "overall_insight": "구버전 늦은 분석", "issues": [], "qa_pairs": []}'
                    }
                }
            ]
        }
        return mock_resp

    monkeypatch.setattr("httpx.Client.post", race_condition_post)

    # force=False로 분석 실행 시, 생성 도중 지문이 변경되었으므로 늦은 구버전 결과가 기각되어야 함
    insight_after, _ = generate_node_insight(db_session, node.id, force=False)

    # 기존 정상 결과가 유지되고, '구버전 늦은 분석'으로 덮어쓰여지지 않았는가
    assert "구버전 늦은 분석" not in insight_after.overall_insight
    assert insight_after.input_fingerprint == orig_fp


def test_t10_zero_model_calls_on_get_and_fingerprint_caching(client: TestClient, db_session: Session, monkeypatch):
    """T10: GET 요청 시 모델 호출 0회 보장 및 동일 fingerprint 시 캐시 재사용 검증."""
    run_id = uuid.uuid4().hex[:6]

    cls_obj = Classification(code=f"CAT_{run_id}", display_name="Category", is_active=True)
    doc_obj = Document(title="캐시 문서", normalized_content="캐시 본문", status="COMPLETED")
    db_session.add_all([cls_obj, doc_obj])
    db_session.flush()

    c1 = Claim(document_id=doc_obj.id, quote_text="캐시 인용", statement="캐시 사실")
    db_session.add(c1)
    db_session.flush()

    node = Node(
        name=f"캐시노드_{run_id}",
        classification_id=cls_obj.id,
        description="캐시 검증용 노드",
        claim_ids=[c1.id],
    )
    db_session.add(node)
    db_session.commit()

    # 모델 호출 횟수 추적기
    model_call_count = 0

    def counting_post(*args, **kwargs):
        nonlocal model_call_count
        model_call_count += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"recent_history_summary": "이력", "overall_insight": "인사이트", "issues": ["이슈"], "qa_pairs": [{"question": "Q", "answer": "A"}]}'
                    }
                }
            ]
        }
        return mock_resp

    monkeypatch.setattr("httpx.Client.post", counting_post)

    # 1. 초기 1회 생성 (명시적 POST)
    res_gen = client.post(f"/api/v1/nodes/{node.id}/insights/generate")
    assert res_gen.status_code == 200
    assert model_call_count == 1

    # 2. 동일 지문 상태에서 다시 명시적 생성 요청 (force=False 기본값): 모델 호출 0회로 캐시 재사용
    res_gen_cached = client.post(f"/api/v1/nodes/{node.id}/insights/generate")
    assert res_gen_cached.status_code == 200
    assert model_call_count == 1  # 호출 수가 증가하지 않음 (0회 추가 호출)

    # 3. GET /nodes/{id}/details 10회 연속 호출: 모델 호출 0회 검증 (지시서 M3.6 & T10)
    for _ in range(10):
        res_details = client.get(f"/api/v1/nodes/{node.id}/details")
        assert res_details.status_code == 200
        body = res_details.json()
        assert body["overall_insight"] == "인사이트"
        assert len(body["qa_pairs"]) == 1

    # 4. GET /nodes/{id}/insights 10회 연속 호출: 모델 호출 0회 검증
    for _ in range(10):
        res_insights = client.get(f"/api/v1/nodes/{node.id}/insights")
        assert res_insights.status_code == 200
        body = res_insights.json()
        assert body["insight"]["overall_insight"] == "인사이트"

    # 전체 GET 20회 수행 후에도 모델 호출 수는 여전히 1회 유지
    assert model_call_count == 1
