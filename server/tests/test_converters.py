"""네이티브 어댑터 변환기 및 시연 시드 테스트 (Phase 1 단위 및 통합 검증)."""

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import select

from ontology_map.db.schema import Claim, Document, Edge, Node
from ontology_map.services.converters import (
    convert_excel_native,
    convert_gov_native,
    convert_native_bundle,
    convert_news_native,
    excel_fingerprint,
)
from ontology_map.services.demo_seed import ensure_demo_seed

DEMO_DATA_DIR = Path(__file__).resolve().parent.parent / "demo_data"


def load_demo_json(name: str) -> dict:
    fpath = DEMO_DATA_DIR / name
    assert fpath.exists(), f"시연 데이터 파일이 없습니다: {fpath}"
    return json.loads(fpath.read_text(encoding="utf-8"))


# --- 1. Demo Seed Tests ---

def test_demo_seed_creation_and_idempotency(db_session: Session):
    """시연 기준 베이스라인 데이터(한결정밀, 부산공장, 2024 실적) 생성 및 멱등성 검증."""
    # 1차 시드 실행
    res1 = ensure_demo_seed(db_session)
    db_session.commit()

    assert res1["status"] == "success"
    doc_id = res1["document_id"]
    company_id = res1["company_node_id"]
    facility_id = res1["facility_node_id"]
    edge_id = res1["edge_id"]

    company = db_session.get(Node, company_id)
    facility = db_session.get(Node, facility_id)
    edge = db_session.get(Edge, edge_id)

    assert company.name == "한결정밀"
    assert company.properties["metrics"]["revenue"]["value"] == 100
    assert len(company.claim_ids) == 2

    assert facility.name == "부산공장"
    assert len(facility.claim_ids) == 1

    assert edge.source_node_id == company_id
    assert edge.target_node_id == facility_id
    assert edge.relation.code == "OPERATES"
    assert edge.claim_id is not None

    # 2차 시드 실행 (중복 생성 없이 멱등 유지)
    res2 = ensure_demo_seed(db_session)
    db_session.commit()

    assert res2["company_node_id"] == company_id
    assert res2["facility_node_id"] == facility_id
    assert res2["edge_id"] == edge_id

    # 한결정밀 노드가 1개만 존재하는지 확인
    companies = db_session.execute(select(Node).where(Node.name == "한결정밀")).scalars().all()
    assert len(companies) == 1


# --- 2. News Converter Tests ---

def test_convert_news_native_success():
    """News Agent 네이티브 응답 변환 정상 케이스 검증."""
    news_data = load_demo_json("news.native.json")
    bundle = convert_news_native(news_data)

    assert bundle["producer"] == "news"
    assert bundle["profile"] == "external-news.article-detail.v1"
    assert len(bundle["documents"]) == 1
    assert len(bundle["claims"]) == 2

    # Claim 1: 계약 체결 FACT
    c1 = bundle["claims"][0]
    assert c1["modality"] == "FACT"
    assert "계약을 체결했다" in c1["quote"]
    assert c1["evidenceRefs"][0]["sentenceIndex"] == 10

    # Claim 2: 증산 검토 PLAN (계획 카드)
    c2 = bundle["claims"][1]
    assert c2["modality"] == "PLAN"
    assert c2["mapDestination"] == "node_panel_only"
    assert c2["attributedTo"] == "한결정밀"

    # Edge: 누리소재 -> 한결정밀 SUPPLY_CONTRACT_WITH
    assert len(bundle["edges"]) == 1
    edge = bundle["edges"][0]
    assert edge["source"] == "누리소재"
    assert edge["relationCode"] == "SUPPLY_CONTRACT_WITH"
    assert edge["target"] == "한결정밀"
    assert edge["claimRefs"] == ["n-c1"]

    # Rejected 항목 확인
    assert len(bundle["rejected"]) == 1
    assert "납품 중" in bundle["rejected"][0]["proposal"]


def test_convert_news_native_missing_evidence_rejection():
    """존재하지 않는 문장 번호를 참조하는 뉴스 응답 거절 검증."""
    bad_news = load_demo_json("news-missing-evidence.native.json")
    with pytest.raises(ValueError, match="MISSING_EVIDENCE_REFERENCE"):
        convert_news_native(bad_news)


def test_convert_news_native_wrong_source_rejection():
    """분석 대상 ID 불일치(analysisArticleId != id) 뉴스 응답 거절 검증."""
    bad_news = load_demo_json("news-wrong-source.native.json")
    with pytest.raises(ValueError, match="ANALYSIS_SOURCE_MISMATCH"):
        convert_news_native(bad_news)


# --- 3. Gov Converter Tests ---

def test_convert_gov_native_success():
    """GovInsight 네이티브 응답 변환 정상 케이스 검증."""
    gov_data = load_demo_json("gov.native.json")
    supplement = load_demo_json("lite-supplement.json")
    bundle = convert_gov_native(gov_data, supplement=supplement)

    assert bundle["producer"] == "gov"
    assert bundle["profile"] == "govinsight.document-analysis.v1"

    # Edge: 새봄산업지원원 -> 실증지원 ORGANIZES (한결정밀과의 거짓 수령/선정 관계 없음)
    assert len(bundle["edges"]) == 1
    edge = bundle["edges"][0]
    assert edge["source"] == "새봄산업지원원"
    assert edge["relationCode"] == "ORGANIZES"
    assert edge["target"] == "2026 제조데이터 실증지원 사업"

    # 한결정밀과의 엣지가 전혀 없어야 함!
    for e in bundle["edges"]:
        assert e["source"] != "한결정밀"
        assert e["target"] != "한결정밀"

    # 추천 메모는 external_analysis_only로 분류
    rec_claim = next(c for c in bundle["claims"] if c["ref"] == "g-note1")
    assert rec_claim["modality"] == "RECOMMENDATION"
    assert rec_claim["mapDestination"] == "external_analysis_only"


def test_convert_gov_native_without_body_rejection():
    """원문 스냅샷이 누락된 공고 분석 거절 검증."""
    gov_analysis_only = load_demo_json("gov-analysis-only.native.json")
    with pytest.raises(ValueError, match="MISSING_NOTICE_BODY"):
        convert_gov_native(gov_analysis_only, supplement={"gov": {"sourceSnapshots": []}})


# --- 4. Excel Converter Tests ---

def test_convert_excel_native_success():
    """Excel Agent 네이티브 응답 변환 정상 케이스 검증."""
    excel_data = load_demo_json("excel.native.json")
    bundle = convert_excel_native(excel_data)

    assert bundle["producer"] == "excel"
    assert bundle["profile"] == "excel.analysis-export.v1"

    # Excel은 새로운 엣지를 생성하지 않음 (0개)
    assert len(bundle["edges"]) == 0

    # 수치 Claim 1: 120억원
    c1 = bundle["claims"][0]
    assert c1["value"] == 120
    assert c1["scope"]["metric"] == "revenue"
    assert c1["scope"]["period"] == "2025"

    # 계산 Claim 2: 20% 증가
    c2 = bundle["claims"][1]
    assert c2["modality"] == "DERIVED_FACT"
    assert c2["calculation"]["result"] == 20

    # 속성 변경: 100억 -> 120억
    pchange = bundle["propertyChanges"][0]
    assert pchange["node"] == "한결정밀"
    assert pchange["before"]["value"] == 100
    assert pchange["after"]["value"] == 120


def test_convert_excel_replay_fingerprint():
    """전송 시각만 변경된 재전송 fixture 해시 일치 검증."""
    excel_orig = load_demo_json("excel.native.json")
    excel_replay = load_demo_json("excel-replay.native.json")

    assert excel_orig["exportedAt"] != excel_replay["exportedAt"]
    assert excel_fingerprint(excel_orig) == excel_fingerprint(excel_replay)


def test_convert_excel_missing_period_rejection():
    """연도/기간 정보 누락 Excel 응답 거절 검증."""
    bad_excel = load_demo_json("excel-missing-period.native.json")
    with pytest.raises(ValueError, match="MISSING_PERIOD_REFERENCE"):
        convert_excel_native(bad_excel)


# --- 5. API End-to-End Integration Tests ---

def test_api_demo_seed_and_converter_flow(client: TestClient, db_session: Session):
    """API 엔드포인트를 통한 시드 -> 변환 -> 승인 반영 E2E 검증."""
    # 1. 시드 적재
    seed_res = client.post("/api/v1/demo/seed")
    assert seed_res.status_code == 200
    assert seed_res.json()["status"] == "success"

    # 2. News 변환 요청
    news_data = load_demo_json("news.native.json")
    convert_res = client.post(
        "/api/v1/adapters/convert",
        json={"producer": "news", "raw_json": news_data},
    )
    assert convert_res.status_code == 200
    bundle = convert_res.json()
    assert bundle["producer"] == "news"

    # 3. News 승인 반영 (main + second)
    commit_res = client.post(
        "/api/v1/adapters/commit",
        json={"bundle": bundle, "enabled_ids": ["main", "second"]},
    )
    assert commit_res.status_code == 200
    assert commit_res.json()["status"] == "success"

    # 4. DB 반영 확인
    db_session.expire_all()
    nuri = db_session.execute(select(Node).where(Node.name == "누리소재")).scalars().first()
    hangyeol = db_session.execute(select(Node).where(Node.name == "한결정밀")).scalars().first()
    assert nuri is not None
    assert hangyeol is not None

    # 누리소재 -> 한결정밀 공급 계약 엣지 생성 확인
    edge = db_session.execute(
        select(Edge).where(
            Edge.source_node_id == nuri.id,
            Edge.target_node_id == hangyeol.id,
        )
    ).scalars().first()
    assert edge is not None
    assert edge.relation.code == "SUPPLY_CONTRACT_WITH"
