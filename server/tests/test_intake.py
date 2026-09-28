"""동기 3개 프로젝트(GovInsight, News Signal Desk, Excel AI Agent) 실제 산출물 기반 Intake API 무적 통합 테스트."""

import uuid
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ontology_map.db.schema import Claim, Document, Node


def test_intake_gov_insight_real_output(client: TestClient, db_session: Session):
    """1. GovInsight 실제 산출물: 공고문, 심사 요건 근거, 공공기관-사업망 적재 및 멱등성 검증."""
    run_id = uuid.uuid4().hex[:6]
    agency_name = f"중소벤처기업부_{run_id}"
    project_name = f"스마트제조혁신_{run_id}"
    target_name = f"중소제조기업_{run_id}"
    tech_name = f"스마트팩토리_{run_id}"

    payload = {
        "source_project": "gov-insight",
        "document_title": f"[공고_{run_id}] 2026년도 스마트 제조혁신 지원사업 공고",
        "document_uri": f"https://www.mss.go.kr/notice/{run_id}",
        "raw_metadata": {
            "department": "제조혁신과",
            "importance": "HIGH",
            "eligibility": "ELIGIBLE",
            "deadline": "2026-04-15",
        },
        "claims": [
            {
                "quote": "국내 중소·중견 제조기업의 스마트공장 구축 및 고도화 지원",
                "claim_text": "지원 대상: 국내 중소·중견 제조기업",
            },
            {
                "quote": "정부지원금 총 사업비의 50% 이내, 최대 2.5억원 지원",
                "claim_text": "지원 규모: 기업당 최대 2.5억원(50% 매칭)",
            },
        ],
        "nodes": [
            {"name": agency_name, "classification": "AGENCY", "description": "주무 부처"},
            {"name": project_name, "classification": "PROJECT", "description": "정부 지원과제"},
            {"name": target_name, "classification": "TARGET_GROUP", "description": "지원 대상 수혜자"},
            {"name": tech_name, "classification": "TECH", "description": "도입 핵심 기술"},
        ],
        "edges": [
            {"source_name": agency_name, "target_name": project_name, "relation": "ORGANIZES"},
            {"source_name": project_name, "target_name": target_name, "relation": "TARGETS"},
            {"source_name": project_name, "target_name": tech_name, "relation": "PROVIDES"},
        ],
    }

    # 1회차 호출: 100% 신규 노드/엣지/근거 생성 보장
    res1 = client.post("/api/v1/intake", json=payload)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "success"
    assert data1["nodes_created"] == 4
    assert data1["edges_created"] == 3
    assert data1["claims_created"] == 2

    # DB 실제 적재 확인
    doc = db_session.get(Document, data1["document_id"])
    assert doc is not None
    assert doc.metadata_json["importance"] == "HIGH"
    
    stmt_claims = select(Claim).where(Claim.document_id == doc.id)
    assert len(db_session.execute(stmt_claims).scalars().all()) == 2

    # 2회차 호출: 똑같은 데이터를 다시 보냈을 때 엣지가 중복 생성되지 않는지(0개) 철통 검증
    res2 = client.post("/api/v1/intake", json=payload)
    assert res2.status_code == 200
    assert res2.json()["edges_created"] == 0


def test_intake_news_agent_real_output(client: TestClient, db_session: Session):
    """2. News Signal Desk 실제 산출물: 기사 메타, EvidenceBullet 인용구, 기업-기술망 적재 및 멱등성 검증."""
    run_id = uuid.uuid4().hex[:6]
    corp_a = f"OpenAI_{run_id}"
    corp_b = f"Microsoft_{run_id}"
    tech_model = f"ModelO3_{run_id}"
    platform = f"Azure_{run_id}"

    payload = {
        "source_project": "news-agent",
        "document_title": f"[{run_id}] 차세대 멀티모달 추론 모델 공개 보도",
        "document_uri": f"https://www.reuters.com/news/{run_id}",
        "raw_metadata": {
            "publisher": "Reuters",
            "topic": "생성형 AI 생태계",
            "groundedness": "grounded",
        },
        "claims": [
            {
                "quote": "복합 추론 벤치마크에서 기존 대비 40% 성능 향상 달성",
                "claim_text": "차세대 모델 성능 40% 향상",
            },
            {
                "quote": "클라우드 인프라에 독점 우선 배포 파트너십 체결",
                "claim_text": "인프라 독점 우선 배포",
            },
        ],
        "nodes": [
            {"name": corp_a, "classification": "COMPANY"},
            {"name": corp_b, "classification": "COMPANY"},
            {"name": tech_model, "classification": "TECH"},
            {"name": platform, "classification": "PLATFORM"},
        ],
        "edges": [
            {"source_name": corp_a, "target_name": tech_model, "relation": "DEVELOPS"},
            {"source_name": corp_b, "target_name": corp_a, "relation": "INVESTS_IN"},
            {"source_name": corp_b, "target_name": platform, "relation": "OPERATES"},
            {"source_name": platform, "target_name": tech_model, "relation": "HOSTS"},
        ],
    }

    # 1회차 호출: 신규 생성 보장 (어떤 DB에서도 4개 엣지 생성)
    res1 = client.post("/api/v1/intake", json=payload)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["nodes_created"] == 4
    assert data1["edges_created"] == 4
    assert data1["claims_created"] == 2

    # 2회차 호출: 엣지 중복 방지 검증
    res2 = client.post("/api/v1/intake", json=payload)
    assert res2.status_code == 200
    assert res2.json()["edges_created"] == 0


def test_intake_excel_agent_real_output(client: TestClient, db_session: Session):
    """3. Excel AI Agent 실제 산출물: Provenance 셀/수식 근거, 지표 계산 흐름 적재 및 멱등성 검증."""
    run_id = uuid.uuid4().hex[:6]
    metric_sales = f"매출액_{run_id}"
    metric_profit = f"영업이익_{run_id}"
    metric_margin = f"영업이익률_{run_id}"
    dept_name = f"AI사업부_{run_id}"

    payload = {
        "source_project": "excel-agent",
        "document_title": f"[{run_id}] 2026_경영계획_손익분석.xlsx",
        "document_uri": f"file://corp/finance/{run_id}.xlsx",
        "raw_metadata": {
            "analyzer": "dependency_graph",
            "sheet_name": "손익계산서",
            "validation_status": "verified",
        },
        "claims": [
            {
                "quote": "손익계산서!C15 (수식: =C10/C5, 결과값: 15.2%)",
                "claim_text": "2026년 목표 영업이익률 15.2%",
            },
            {
                "quote": "투자계획!D8 (수식: =SUM(D3:D7), 결과값: 50억원)",
                "claim_text": "AI 인프라 투자 총액 50억원",
            },
        ],
        "nodes": [
            {"name": metric_sales, "classification": "METRIC"},
            {"name": metric_profit, "classification": "METRIC"},
            {"name": metric_margin, "classification": "METRIC"},
            {"name": dept_name, "classification": "DEPARTMENT"},
        ],
        "edges": [
            {"source_name": metric_sales, "target_name": metric_margin, "relation": "CALCULATES"},
            {"source_name": metric_profit, "target_name": metric_margin, "relation": "CALCULATES"},
        ],
    }

    # 1회차 호출: 100% 신규 생성
    res1 = client.post("/api/v1/intake", json=payload)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["nodes_created"] == 4
    assert data1["edges_created"] == 2
    assert data1["claims_created"] == 2

    # 2회차 호출: 엣지 중복 방지 검증
    res2 = client.post("/api/v1/intake", json=payload)
    assert res2.status_code == 200
    assert res2.json()["edges_created"] == 0


def test_cross_project_knowledge_fusion(client: TestClient, db_session: Session):
    """4. 크로스 프로젝트 지식 융합 완벽 검증: 3개 프로젝트가 단일 테스트 안에서 하나의 지식망으로 결합."""
    run_id = uuid.uuid4().hex[:6]
    
    # 3개 프로젝트가 공유할 핵심 엔티티
    shared_company = f"GlobalAI_{run_id}"
    shared_tech = f"NextLLM_{run_id}"

    # Step 1: News Agent가 기업과 기술 모델을 먼저 등록
    news_payload = {
        "source_project": "news-agent",
        "document_title": f"[{run_id}] 글로벌 AI 테크 동향",
        "nodes": [
            {"name": shared_company, "classification": "COMPANY"},
            {"name": shared_tech, "classification": "TECH"},
            {"name": f"Investor_{run_id}", "classification": "COMPANY"},
        ],
        "edges": [
            {"source_name": f"Investor_{run_id}", "target_name": shared_company, "relation": "INVESTS_IN"},
            {"source_name": shared_company, "target_name": shared_tech, "relation": "DEVELOPS"},
        ],
    }
    client.post("/api/v1/intake", json=news_payload)

    # Step 2: GovInsight가 정부 공고를 보내며 동일한 shared_company를 지원 대상(수혜자)으로 연결
    gov_payload = {
        "source_project": "gov-insight",
        "document_title": f"[{run_id}] 혁신 기업 바우처 공고",
        "nodes": [
            {"name": f"GovAgency_{run_id}", "classification": "AGENCY"},
            {"name": f"GovProject_{run_id}", "classification": "PROJECT"},
            # News에서 이미 생성된 shared_company를 다시 전달!
            {"name": shared_company, "classification": "COMPANY"},
        ],
        "edges": [
            {"source_name": f"GovAgency_{run_id}", "target_name": f"GovProject_{run_id}", "relation": "ORGANIZES"},
            {"source_name": f"GovProject_{run_id}", "target_name": shared_company, "relation": "SUPPORTS"},
        ],
    }
    client.post("/api/v1/intake", json=gov_payload)

    # Step 3: Excel Agent가 해당 기업의 재무 지표를 보내며 연결
    excel_payload = {
        "source_project": "excel-agent",
        "document_title": f"[{run_id}] 기업_손익_시뮬레이션.xlsx",
        "nodes": [
            {"name": shared_company, "classification": "COMPANY"},
            {"name": f"영업이익_{run_id}", "classification": "METRIC"},
        ],
        "edges": [
            {"source_name": shared_company, "target_name": f"영업이익_{run_id}", "relation": "REPORTS"},
        ],
    }
    client.post("/api/v1/intake", json=excel_payload)

    # ⭐ 검증 1: 3개 프로젝트에서 모두 언급된 shared_company는 DB에 정확히 1개만 존재해야 함 (중복 생성 방지)
    stmt = select(Node).where(Node.name == shared_company)
    nodes = db_session.execute(stmt).scalars().all()
    assert len(nodes) == 1
    shared_node = nodes[0]

    # ⭐ 검증 2: shared_company의 3D 서브그래프를 조회했을 때, 
    # News(투자자, 모델), Gov(정부과제), Excel(영업이익)이 모두 하나의 노드에 융합되어 연결되어야 함!
    graph_res = client.get(f"/api/v1/nodes/{shared_node.id}/graph")
    assert graph_res.status_code == 200
    neighbor_names = [n["name"] for n in graph_res.json()["nodes"]]

    assert f"Investor_{run_id}" in neighbor_names   # News 출처 연결 확인
    assert shared_tech in neighbor_names             # News 출처 연결 확인
    assert f"GovProject_{run_id}" in neighbor_names # GovInsight 출처 연결 확인
    assert f"영업이익_{run_id}" in neighbor_names    # Excel 출처 연결 확인


def test_cross_knowledge_linkage_to_existing_db_node(client: TestClient, db_session: Session):
    """5. 동적 지식 교차 연결 검증: payload.nodes에 미포함된 기존 DB 엔티티와도 엣지가 정상 연결되는지 검증."""
    run_id = uuid.uuid4().hex[:6]
    existing_corp = f"CoreEnterprise_{run_id}"

    # Step 1: 기존 기업 노드를 먼저 DB에 생성
    seed_payload = {
        "source_project": "seed",
        "document_title": "기초 데이터",
        "nodes": [{"name": existing_corp, "classification": "COMPANY"}],
        "edges": [],
    }
    client.post("/api/v1/intake", json=seed_payload)

    # Step 2: 신규 문서가 유입될 때, payload.nodes에는 existing_corp가 없지만 edges에서 existing_corp를 참조
    new_doc_payload = {
        "source_project": "agent-ingestion",
        "document_title": "신규 파트너십 발표",
        "nodes": [
            {"name": f"Startup_{run_id}", "classification": "COMPANY"},
        ],
        "edges": [
            {
                "source_name": f"Startup_{run_id}",
                "target_name": existing_corp,  # DB에 이미 존재하는 노드!
                "relation": "PARTNERS_WITH",
            }
        ],
    }
    res = client.post("/api/v1/intake", json=new_doc_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["nodes_created"] == 1  # Startup만 생성
    assert data["edges_created"] == 1  # Startup -> CoreEnterprise 연결 성공!

    # 서브그래프에서 교차 연결 확인
    startup_node = db_session.execute(select(Node).where(Node.name == f"Startup_{run_id}")).scalar_one()
    graph_res = client.get(f"/api/v1/nodes/{startup_node.id}/graph")
    assert graph_res.status_code == 200
    neighbor_names = [n["name"] for n in graph_res.json()["nodes"]]
    assert existing_corp in neighbor_names

