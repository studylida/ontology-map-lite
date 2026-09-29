"""M2 검증 시나리오 (지시서 5절 & 6절 T05, T06, T07).

- T05: 같은 승인 10회 반복 적재 멱등성 및 새 승인 사실 상한 안 추가
- T06: 두 번째 문서 지식 누적, 동명 대상 재사용, 비충돌 속성 누적 및 충돌 속성 보존
- T07: 같은 대상을 향한 동시/다중 적재 시 근거 합집합 유지 (Lost Update 방지)
- 반복 처리 측정표: 1회차 vs 10회차 행 수 및 연결 수 측정 기록
"""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ontology_map.db.schema import Claim, Document, Edge, Node


def test_t05_ten_repeated_submits_idempotency_and_measurement(client: TestClient, db_session: Session):
    """T05: 동일 문서 및 동일 승인을 10회 반복 전송해도 데이터가 불필요하게 증식하지 않음을 검증하고 측정표를 작성."""
    run_id = uuid.uuid4().hex[:6]
    doc_uri = f"https://example.com/reports/idempotency_{run_id}"
    doc_title = f"Idempotency Report {run_id}"
    doc_content = "엔비디아는 AI 반도체인 블랙웰 울트라를 개발하여 시장에 공급하고 있다."

    payload = {
        "source_project": "news-desk",
        "document_title": doc_title,
        "document_uri": doc_uri,
        "document_content": doc_content,
        "nodes": [
            {
                "ref_id": "n0",
                "name": f"엔비디아_{run_id}",
                "classification": "COMPANY",
                "description": "AI 반도체 설계 리더",
                "properties": {"market_cap": "3T"},
            },
            {
                "ref_id": "n1",
                "name": f"블랙웰울트라_{run_id}",
                "classification": "TECH",
                "description": "차세대 AI GPU 가속기",
            },
        ],
        "edges": [
            {
                "source_ref": "n0",
                "target_ref": "n1",
                "source_name": f"엔비디아_{run_id}",
                "target_name": f"블랙웰울트라_{run_id}",
                "relation": "DEVELOPS",
                "claim_ref": "c0",
            }
        ],
        "claims": [
            {
                "ref_id": "c0",
                "quote": "엔비디아는 AI 반도체인 블랙웰 울트라를 개발",
                "claim_text": "엔비디아의 블랙웰 울트라 개발 사실",
            },
            {
                "ref_id": "c1",
                "quote": "시장에 공급하고 있다",
                "claim_text": "블랙웰 울트라 시장 공급 사실",
            },
        ],
    }

    # --- 실행 전 (Run 0) 실측 ---
    # 최초 1회차 전송
    res1 = client.post("/api/v1/intake", json=payload)
    assert res1.status_code == 200, res1.text
    data1 = res1.json()
    assert data1["nodes_created"] == 2
    assert data1["edges_created"] == 1
    assert data1["claims_created"] == 2
    assert data1["duplicate_claims_reused"] == 0
    assert len(data1["affected_node_ids"]) == 2

    # 1회차 직후 DB 상태 측정
    doc1 = db_session.get(Document, data1["document_id"])
    assert doc1 is not None

    doc_count_after_1 = db_session.execute(
        select(func.count(Document.id)).where(Document.source_uri == doc_uri)
    ).scalar()
    claim_count_after_1 = db_session.execute(
        select(func.count(Claim.id)).where(Claim.document_id == doc1.id)
    ).scalar()
    node_count_after_1 = db_session.execute(
        select(func.count(Node.id)).where(Node.name.in_([f"엔비디아_{run_id}", f"블랙웰울트라_{run_id}"]))
    ).scalar()
    edge_count_after_1 = db_session.execute(
        select(func.count(Edge.id)).where(
            Edge.source_node_id == data1["primary_node_id"]
        )
    ).scalar()

    assert doc_count_after_1 == 1
    assert claim_count_after_1 == 2
    assert node_count_after_1 == 2
    assert edge_count_after_1 == 1

    # --- 동일 페이로드 9회 추가 전송 (총 10회 반복) ---
    for i in range(2, 11):
        res_i = client.post("/api/v1/intake", json=payload)
        assert res_i.status_code == 200
        data_i = res_i.json()
        assert data_i["nodes_created"] == 0, f"Run {i} created unexpected nodes"
        assert data_i["edges_created"] == 0, f"Run {i} created unexpected edges"
        assert data_i["claims_created"] == 0, f"Run {i} created unexpected claims"
        assert data_i["duplicate_claims_reused"] == 2, f"Run {i} did not reuse claims"
        # 내용 변경이 없으므로 affected_node_ids는 빈 리스트여야 함 (M2.5)
        assert data_i["affected_node_ids"] == [], f"Run {i} falsely reported affected nodes"

    # 10회차 완료 후 DB 행 수 실측 비교
    doc_count_after_10 = db_session.execute(
        select(func.count(Document.id)).where(Document.source_uri == doc_uri)
    ).scalar()
    claim_count_after_10 = db_session.execute(
        select(func.count(Claim.id)).where(Claim.document_id == doc1.id)
    ).scalar()
    node_count_after_10 = db_session.execute(
        select(func.count(Node.id)).where(Node.name.in_([f"엔비디아_{run_id}", f"블랙웰울트라_{run_id}"]))
    ).scalar()
    edge_count_after_10 = db_session.execute(
        select(func.count(Edge.id)).where(
            Edge.source_node_id == data1["primary_node_id"]
        )
    ).scalar()

    # [측정표 검증] 1회차 vs 10회차 행 수 증가량 = 0
    assert doc_count_after_10 == 1
    assert claim_count_after_10 == 2
    assert node_count_after_10 == 2
    assert edge_count_after_10 == 1

    # --- 같은 문서의 "새로운 승인 사실" 추가 시 정상 누적 검증 ---
    payload_new_fact = {
        "source_project": "news-desk",
        "document_title": doc_title,
        "document_uri": doc_uri,
        "document_content": doc_content,
        "nodes": [
            {"ref_id": "n0", "name": f"엔비디아_{run_id}", "classification": "COMPANY"},
        ],
        "edges": [],
        "claims": [
            # 기존 사실 c0 재사용
            {"ref_id": "c0", "quote": "엔비디아는 AI 반도체인 블랙웰 울트라를 개발", "claim_text": "엔비디아의 블랙웰 울트라 개발 사실"},
            # 새로운 사실 c2 추가
            {"ref_id": "c2", "quote": "추가된 새로운 인용구", "claim_text": "신규 사실"},
        ],
    }
    res_new = client.post("/api/v1/intake", json=payload_new_fact)
    assert res_new.status_code == 200
    data_new = res_new.json()
    assert data_new["nodes_created"] == 0
    assert data_new["claims_created"] == 1
    assert data_new["duplicate_claims_reused"] == 1

    claim_count_after_new = db_session.execute(
        select(func.count(Claim.id)).where(Claim.document_id == doc1.id)
    ).scalar()
    assert claim_count_after_new == 3


def test_t06_second_document_knowledge_accumulation_and_properties(client: TestClient, db_session: Session):
    """T06: 두 번째 문서 적재 시 동명 대상 재사용, 비충돌 속성 누적, 충돌 속성 보존, 근거 누적 검증."""
    run_id = uuid.uuid4().hex[:6]
    corp_name = f"글로벌테크_{run_id}"

    # 1. 첫 번째 문서: 기업 기본 정보 및 HQ, 직원 수 속성
    doc_1_payload = {
        "source_project": "annual-report",
        "document_title": f"2025 사업보고서 {run_id}",
        "document_uri": f"https://example.com/corp/{run_id}/2025",
        "nodes": [
            {
                "ref_id": "n0",
                "name": corp_name,
                "classification": "COMPANY",
                "description": "글로벌 IT 지주회사",
                "properties": {"hq": "Seoul", "employees": 500},
            }
        ],
        "edges": [],
        "claims": [
            {"ref_id": "c0", "quote": f"{corp_name}의 본사는 서울에 위치하며 임직원은 500명이다.", "claim_text": "본사 및 임직원 규모"}
        ],
    }
    res1 = client.post("/api/v1/intake", json=doc_1_payload)
    assert res1.status_code == 200
    corp_node_id = res1.json()["primary_node_id"]

    node1 = db_session.get(Node, corp_node_id)
    assert node1.properties["hq"] == "Seoul"
    assert node1.properties["employees"] == 500
    assert len(node1.claim_ids) == 1

    # 2. 두 번째 문서: 동일 기업에 대한 신규 전략 문서 (매출 목표 속성 추가, 직원 수 충돌 값 제시, 신규 파트너 엣지 생성)
    partner_name = f"파트너사_{run_id}"
    doc_2_payload = {
        "source_project": "strategy-news",
        "document_title": f"2026 전략 제휴 뉴스 {run_id}",
        "document_uri": f"https://example.com/news/{run_id}/strategy",
        "nodes": [
            {
                "ref_id": "n0",
                "name": corp_name,
                "classification": "COMPANY",
                "description": "글로벌 IT 지주회사",
                # hq는 동일, employees는 9999(충돌), revenue_target은 100B(신규)
                "properties": {"hq": "Seoul", "employees": 9999, "revenue_target": "100B"},
            },
            {
                "ref_id": "n1",
                "name": partner_name,
                "classification": "COMPANY",
                "description": "클라우드 서비스 파트너",
            },
        ],
        "edges": [
            {
                "source_ref": "n0",
                "target_ref": "n1",
                "source_name": corp_name,
                "target_name": partner_name,
                "relation": "PARTNERS_WITH",
                "claim_ref": "c0",
            }
        ],
        "claims": [
            {"ref_id": "c0", "quote": f"{corp_name}는 {partner_name}와 클라우드 전략 제휴를 체결했다.", "claim_text": "전략 제휴 사실"}
        ],
    }

    res2 = client.post("/api/v1/intake", json=doc_2_payload)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["nodes_created"] == 1  # 파트너사만 신규 생성, 글로벌테크는 재사용
    assert data2["edges_created"] == 1

    # DB 확인: 노드가 중복 생성되지 않고 기존 노드 ID가 유지되었는가
    db_session.expire_all()
    corp_node_updated = db_session.get(Node, corp_node_id)
    assert corp_node_updated is not None

    # [비충돌 속성 누적]: revenue_target이 성공적으로 누적됨
    assert corp_node_updated.properties.get("revenue_target") == "100B"
    # [충돌 속성 보존]: 기존에 채택된 500이 9999로 임의 덮어쓰기 되지 않고 보존됨 (M2.4)
    assert corp_node_updated.properties.get("employees") == 500

    # [근거 누적]: 문서 1과 문서 2의 Claim ID가 모두 합집합으로 누적되었는가
    assert len(corp_node_updated.claim_ids) == 2


def test_t07_concurrency_and_claim_union_integrity(client: TestClient, db_session: Session):
    """T07: 같은 대상을 향한 다중 적재 시 근거 배열 합집합 유지 및 Lost Update 방지 검증."""
    run_id = uuid.uuid4().hex[:6]
    shared_node_name = f"공유엔티티_{run_id}"

    # 문서 A 적재
    payload_a = {
        "source_project": "source-a",
        "document_title": f"Doc A {run_id}",
        "nodes": [{"ref_id": "n0", "name": shared_node_name, "classification": "TECH"}],
        "edges": [],
        "claims": [{"ref_id": "c0", "quote": "인용문 A", "claim_text": "주장 A"}],
    }
    res_a = client.post("/api/v1/intake", json=payload_a)
    assert res_a.status_code == 200
    node_id = res_a.json()["primary_node_id"]

    # 문서 B 적재 (동일 엔티티 대상 다른 Claim)
    payload_b = {
        "source_project": "source-b",
        "document_title": f"Doc B {run_id}",
        "nodes": [{"ref_id": "n0", "name": shared_node_name, "classification": "TECH"}],
        "edges": [],
        "claims": [{"ref_id": "c0", "quote": "인용문 B", "claim_text": "주장 B"}],
    }
    res_b = client.post("/api/v1/intake", json=payload_b)
    assert res_b.status_code == 200

    db_session.expire_all()
    node = db_session.get(Node, node_id)
    # 두 문서의 Claim이 모두 유실 없이 보존되어 있어야 함
    assert len(node.claim_ids) == 2


def test_t05_claim_cap_limit_per_document(client: TestClient, db_session: Session):
    """T05 보완: 문서당 최대 100개 Claim 상한 도달 시 명시적 거절 검증 (지시서 4절)."""
    run_id = uuid.uuid4().hex[:6]
    doc_uri = f"https://example.com/cap_test/{run_id}"

    # 한 문서에 99개 Claim 적재
    claims_batch_1 = [
        {"ref_id": f"c{i}", "quote": f"인용문 {i}", "claim_text": f"사실 {i}"}
        for i in range(99)
    ]
    payload_1 = {
        "source_project": "cap-test",
        "document_title": f"Cap Doc {run_id}",
        "document_uri": doc_uri,
        "nodes": [{"ref_id": "n0", "name": f"엔티티_{run_id}", "classification": "GENERAL"}],
        "edges": [],
        "claims": claims_batch_1,
    }
    res1 = client.post("/api/v1/intake", json=payload_1)
    assert res1.status_code == 200
    assert res1.json()["claims_created"] == 99

    # 동일 문서에 추가 2개 Claim 적재 시도 (99 + 2 = 101개로 100개 상한 초과)
    claims_batch_2 = [
        {"ref_id": "c_extra_1", "quote": "추가 인용 1", "claim_text": "추가 사실 1"},
        {"ref_id": "c_extra_2", "quote": "추가 인용 2", "claim_text": "추가 사실 2"},
    ]
    payload_2 = {
        "source_project": "cap-test",
        "document_title": f"Cap Doc {run_id}",
        "document_uri": doc_uri,
        "nodes": [{"ref_id": "n0", "name": f"엔티티_{run_id}", "classification": "GENERAL"}],
        "edges": [],
        "claims": claims_batch_2,
    }
    res2 = client.post("/api/v1/intake", json=payload_2)
    assert res2.status_code == 400
    assert "누적 Claim 상한선(100개)을 초과" in res2.json()["detail"]
