"""M1 마일스톤 필수 검증 시나리오 테스트 (T01 ~ T04)."""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ontology_map.db.schema import Claim, Document, Edge, Node
from ontology_map.schemas import IntakeClaim, IntakeEdge, IntakeNode, IntakePayload
from ontology_map.services.ingestion_agent import extract_ontology_from_text


def test_t01_e2e_single_document_intake_and_explicit_claim_details(client: TestClient, db_session: Session):
    """T01: 1건 문서 적재 후 /nodes/{id}/details 에서 이름 매칭이 아닌 명시적 Claim 연결로 조회되는지 검증."""
    run_id = uuid.uuid4().hex[:6]
    doc_title = f"[M1_T01_{run_id}] 차세대 신경망 가속기 특허 분석"
    doc_content = "본 발명은 온톨로지 기반 추론 엔진과 하드웨어 가속기(NPU)의 결합 기술에 관한 것이다."

    payload = {
        "source_project": "patent-analysis",
        "document_title": doc_title,
        "document_content": doc_content,
        "document_uri": f"https://patents.example.com/{run_id}",
        "claims": [
            {
                "ref_id": "c0",
                "quote": "온톨로지 기반 추론 엔진과 하드웨어 가속기(NPU)의 결합",
                "claim_text": "추론 엔진과 NPU 하드웨어 결합",
                "start_offset": 8,
                "end_offset": 37,
            }
        ],
        "nodes": [
            {
                "ref_id": "n0",
                "name": f"추론엔진_{run_id}",
                "classification": "TECH",
                "description": "온톨로지 지식 처리 엔진",
            },
            {
                "ref_id": "n1",
                "name": f"NPU가속기_{run_id}",
                "classification": "TECH",
                "description": "인공신경망 가속 하드웨어",
            },
        ],
        "edges": [
            {
                "source_ref": "n0",
                "target_ref": "n1",
                "source_name": f"추론엔진_{run_id}",
                "target_name": f"NPU가속기_{run_id}",
                "relation": "ACCELERATES",
                "claim_ref": "c0",
            }
        ],
    }

    # 1. 지식그래프 적재
    res = client.post("/api/v1/intake", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["nodes_created"] == 2
    assert data["edges_created"] == 1
    assert data["claims_created"] == 1
    assert len(data["affected_node_ids"]) == 2

    # 2. DB 상의 명시적 Claim ID 연결 검증
    stmt_node = select(Node).where(Node.name == f"추론엔진_{run_id}")
    node_obj = db_session.execute(stmt_node).scalar_one()
    assert len(node_obj.claim_ids) == 1
    claim_id = node_obj.claim_ids[0]

    claim_in_db = db_session.get(Claim, claim_id)
    assert claim_in_db is not None
    assert claim_in_db.quote_text == "온톨로지 기반 추론 엔진과 하드웨어 가속기(NPU)의 결합"
    assert claim_in_db.start_offset == 8
    assert claim_in_db.end_offset == 37

    # 3. /nodes/{id}/details 조회 시 명시적으로 연결된 Claim 목록이 반환되는지 확인
    res_details = client.get(f"/api/v1/nodes/{node_obj.id}/details")
    assert res_details.status_code == 200
    details = res_details.json()
    assert details["node_id"] == node_obj.id
    assert len(details["claims"]) == 1
    assert details["claims"][0]["id"] == claim_id
    assert details["claims"][0]["quote"] == claim_in_db.quote_text
    assert details["claims"][0]["document_title"] == doc_title


def test_t02_offset_and_document_size_limits(monkeypatch):
    """T02: 전체 본문 보존, [start, end) 오프셋 정확도, 50,000자 초과 거절 검증."""
    full_text = "서두입니다. 중요한 사실: 알파고는 이세돌 9단과 대국을 진행했습니다. 결론입니다."
    
    # 가짜 LLM 응답: 본문 중간의 구문을 정확히 발췌
    def mock_llm_post(*args, **kwargs):
        class MockResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": """{
                                    "nodes": [{"name": "알파고", "classification": "TECH"}],
                                    "edges": [],
                                    "claims": [{"quote": "알파고는 이세돌 9단과 대국을 진행했습니다.", "claim_text": "대국 진행 사실"}],
                                    "insights": {"summary": "대국 요약"}
                                }"""
                            }
                        }
                    ]
                }
        return MockResp()

    monkeypatch.setattr("httpx.Client.post", mock_llm_post)

    payload = extract_ontology_from_text(
        title="대국 기록",
        content=full_text,
        api_key="mock-key",
    )

    # 1. 2,000자 축약 없이 전체 원문 보존 확인
    assert payload.document_content == full_text
    # 2. Claim 인용 오프셋 검증
    assert len(payload.claims) == 1
    c = payload.claims[0]
    expected_start = full_text.find("알파고는 이세돌 9단과 대국을 진행했습니다.")
    assert c.start_offset == expected_start
    assert c.end_offset == expected_start + len(c.quote)
    assert full_text[c.start_offset:c.end_offset] == c.quote

    # 3. 50,000자 초과 시 명시적 거절 확인 (지시서 4절)
    huge_content = "A" * 50001
    with pytest.raises(ValueError, match="50,000자"):
        extract_ontology_from_text(title="과대문서", content=huge_content, api_key="mock-key")


def test_t03_candidate_ref_integrity_and_unresolved_endpoint_rejection(client: TestClient):
    """T03: 임시 ref_id 기반 참조 일관성 유지 및 미해결 끝점의 임의 GENERAL 자동 생성 차단 검증."""
    run_id = uuid.uuid4().hex[:6]
    
    # 1. 올바른 ref_id로 연결된 페이로드 (성공해야 함)
    valid_payload = {
        "source_project": "ref-test",
        "document_title": f"Ref Test {run_id}",
        "nodes": [
            {"ref_id": "n0", "name": f"엔티티A_{run_id}", "classification": "COMPANY"},
            {"ref_id": "n1", "name": f"엔티티B_{run_id}", "classification": "TECH"},
        ],
        "edges": [
            {
                "source_ref": "n0",
                "target_ref": "n1",
                "source_name": "잘못된이름_A",  # 이름이 달라도 ref_id가 우선 해소되어야 함!
                "target_name": "잘못된이름_B",
                "relation": "DEVELOPS",
            }
        ],
    }

    res_valid = client.post("/api/v1/intake", json=valid_payload)
    assert res_valid.status_code == 200
    assert res_valid.json()["edges_created"] == 1

    # 2. 미해결 끝점을 가진 페이로드: 임의 노드 자동 생성하지 않고 400 Bad Request 또는 거절
    invalid_payload = {
        "source_project": "ref-test-invalid",
        "document_title": f"Invalid Ref {run_id}",
        "nodes": [
            {"ref_id": "n0", "name": f"엔티티단독_{run_id}", "classification": "COMPANY"},
        ],
        "edges": [
            {
                "source_ref": "n0",
                "target_ref": "n_missing",
                "source_name": f"엔티티단독_{run_id}",
                "target_name": f"유령엔티티_{run_id}",
                "relation": "CALLS",
            }
        ],
    }

    res_invalid = client.post("/api/v1/intake", json=invalid_payload)
    # 미해결 끝점이 존재하므로 트랜잭션 차단 및 상세 미해결 노드명 고지 확인
    assert res_invalid.status_code == 400
    detail_msg = res_invalid.json()["detail"]
    assert "유령엔티티" in detail_msg
    assert "CALLS" in detail_msg


def test_t04_transaction_atomicity_on_failure(client: TestClient, db_session: Session):
    """T04: 적재 중 에러 발생 시 부분 저장이 남지 않고 전체 롤백되는 원자성 검증."""
    run_id = uuid.uuid4().hex[:6]
    unique_node_name = f"원자성테스트노드_{run_id}"

    failing_payload = {
        "source_project": "atomic-test",
        "document_title": f"Atomic Test {run_id}",
        "nodes": [
            {"ref_id": "n0", "name": unique_node_name, "classification": "COMPANY"},
        ],
        "edges": [
            {
                # 타겟 노드가 없어서 실패를 유발
                "source_ref": "n0",
                "target_ref": "non_existent_ref",
                "source_name": unique_node_name,
                "target_name": "존재하지않는노드",
                "relation": "FAIL_REL",
            }
        ],
        "claims": [
            {"quote": "원자성 테스트용 인용문구", "claim_text": "원자성 확인"}
        ],
    }

    res = client.post("/api/v1/intake", json=failing_payload)
    assert res.status_code in (400, 500)

    # 롤백되었으므로 unique_node_name을 가진 노드가 DB에 없어야 함
    stmt = select(Node).where(Node.name == unique_node_name)
    assert db_session.execute(stmt).scalars().first() is None


def test_t05_graceful_handling_of_duplicate_documents_in_db(client: TestClient, db_session: Session):
    """기존 DB에 동일 URI/내용의 문서나 클레임이 중복 존재해도 MultipleResultsFound 에러 없이 정상 처리되는지 검증."""
    run_id = uuid.uuid4().hex[:6]
    dup_uri = f"sample://dup-test-{run_id}"

    # 1. DB에 동일한 source_uri를 가진 문서를 의도적으로 2개 생성
    doc1 = Document(
        title="중복 문서 1",
        normalized_content="동일한 본문 내용",
        source_uri=dup_uri,
        metadata_json={},
        status="COMPLETED",
    )
    doc2 = Document(
        title="중복 문서 2",
        normalized_content="동일한 본문 내용",
        source_uri=dup_uri,
        metadata_json={},
        status="COMPLETED",
    )
    db_session.add_all([doc1, doc2])
    db_session.flush()

    # 동일 문서에 동일한 Claim도 2개 생성
    claim1 = Claim(document_id=doc2.id, quote_text="공통 인용", statement="공통 진술")
    claim2 = Claim(document_id=doc2.id, quote_text="공통 인용", statement="공통 진술")
    db_session.add_all([claim1, claim2])
    db_session.commit()

    # 2. 동일한 source_uri와 claim으로 intake 호출
    payload = {
        "source_project": "dup-test",
        "document_title": "중복 문서 2",
        "document_uri": dup_uri,
        "document_content": "동일한 본문 내용",
        "nodes": [
            {"ref_id": "n0", "name": f"중복테스트엔티티_{run_id}", "classification": "COMPANY"},
        ],
        "edges": [],
        "claims": [
            {"ref_id": "c0", "quote": "공통 인용", "claim_text": "공통 진술"},
        ],
    }

    res = client.post("/api/v1/intake", json=payload)
    assert res.status_code == 200, f"Failed with {res.text}"
    body = res.json()
    assert body["status"] == "success"
    assert body["duplicate_claims_reused"] >= 1

