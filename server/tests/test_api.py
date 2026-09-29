"""핵심 API 엔드포인트 통합 테스트."""

from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ontology_map.db.schema import Classification, Node


def test_health(client: TestClient):
    """서버가 정상 응답(200 OK)하는지 검증하는 스모크 테스트."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_classifications(client: TestClient, db_session: Session):
    """온톨로지 분류 목록 조회 API 규격 검증."""
    # 1. Arrange: 테스트용 분류 데이터 준비 (테스트 종료 시 롤백됨)
    test_classification = Classification(
        code="TEST_CAT",
        display_name="Test Category",
        is_active=True,
    )
    db_session.add(test_classification)
    db_session.flush()

    # 2. Act: API 호출
    response = client.get("/api/v1/classifications")

    # 3. Assert: 200 성공 및 응답 데이터 내 TEST_CAT 포함 여부 확인
    assert response.status_code == 200
    data = response.json()
    assert any(item["code"] == "TEST_CAT" for item in data)


def test_create_and_resolve_node(client: TestClient, db_session: Session):
    """엔티티 노드 신규 생성 및 동일 이름 요청 시 중복 방지(해소) 검증."""
    # 1. 'COMPANY' 분류가 이미 시드 데이터로 있는지 조회하고, 없으면 생성
    stmt = select(Classification).where(Classification.code == "COMPANY")
    company_cls = db_session.execute(stmt).scalar_one_or_none()
    if not company_cls:
        company_cls = Classification(code="COMPANY", display_name="Company", is_active=True)
        db_session.add(company_cls)
        db_session.flush()

    payload = {
        "name": "DeepSeek",
        "classification_code": "COMPANY",
        "description": "AI Research Lab",
    }

    # 2. 첫 번째 등록: 신규 생성되어야 함
    res1 = client.post("/api/v1/nodes", json=payload)
    assert res1.status_code == 200
    node1 = res1.json()
    assert node1["name"] == "DeepSeek"
    assert "id" in node1

    # 3. 동일한 이름으로 두 번째 등록: 새로운 노드가 아닌 기존 노드를 반환해야 함!
    res2 = client.post("/api/v1/nodes", json=payload)
    assert res2.status_code == 200
    node2 = res2.json()
    assert node2["name"] == "DeepSeek"
    assert node1["id"] == node2["id"]

    # 4. 잘못된 분류 코드를 전달했을 때의 예외 처리 검증
    bad_payload = {
        "name": "Another Entity",
        "classification_code": "NON_EXISTENT_CODE",
    }
    res_bad = client.post("/api/v1/nodes", json=bad_payload)
    assert res_bad.status_code == 400


def test_get_node_subgraph(client: TestClient, db_session: Session):
    """1-hop 서브그래프 조회 API 규격 검증 (3D GraphCanvas 연동)."""
    # 1. 존재하지 않는 노드 ID로 요청 시 404 Not Found 확인
    max_id = db_session.execute(select(func.max(Node.id))).scalar() or 0
    non_existent_id = max_id + 1

    res_404 = client.get(f"/api/v1/nodes/{non_existent_id}/graph")
    assert res_404.status_code == 404

    # 2. DB에 존재하는 노드 하나 조회
    stmt = select(Node).limit(1)
    existing_node = db_session.execute(stmt).scalars().first()

    # 3. 노드가 존재할 때 서브그래프 데이터 계약(Contract) 검증
    if existing_node:
        res = client.get(f"/api/v1/nodes/{existing_node.id}/graph")
        assert res.status_code == 200
        data = res.json()

        # [빈칸 A]: 3D 그래프 렌더링에 필수적인 3개 키가 data 딕셔너리에 포함되어 있는지 검증하세요.
        # (힌트: 중심 노드 'center_node', 노드 목록 'nodes', 연결선 목록 'edges')
        assert "center_node_id" in data
        assert "nodes" in data
        assert "edges" in data
        assert data["center_node_id"] == existing_node.id


def test_get_node_insights(client: TestClient, db_session: Session):
    """노드 AI 종합 인사이트 & Q&A 조회 API 규격 검증 (SidePanel 연동)."""
    # 1. 존재하지 않는 노드 ID로 요청 시 404 Not Found 확인
    max_id = db_session.execute(select(func.max(Node.id))).scalar() or 0
    non_existent_id = max_id + 1

    res_404 = client.get(f"/api/v1/nodes/{non_existent_id}/graph")
    assert res_404.status_code == 404

    # 2. DB에 존재하는 노드 하나 조회
    stmt = select(Node).limit(1)
    existing_node = db_session.execute(stmt).scalars().first()

    # 3. 노드가 존재할 때 사이드패널 데이터 계약 검증
    if existing_node:
        res = client.get(f"/api/v1/nodes/{existing_node.id}/insights")
        assert res.status_code == 200
        data = res.json()

        # [빈칸 B]: SidePanel에서 요약 카드와 Q&A 아코디언을 그리기 위해 필요한 
        #           두 가지 핵심 키('insight', 'qa_pairs')가 포함되어 있는지 검증하세요.
        assert "insight" in data
        assert "qa_pairs" in data
        assert data["node_id"] == existing_node.id


def test_get_node_details(client: TestClient, db_session: Session):
    """노드 상세 정보(속성 + 원천 근거 Claims) 종합 조회 API 검증."""
    stmt = select(Node).limit(1)
    existing_node = db_session.execute(stmt).scalars().first()

    if existing_node:
        res = client.get(f"/api/v1/nodes/{existing_node.id}/details")
        assert res.status_code == 200
        data = res.json()
        assert data["node_id"] == existing_node.id
        assert data["name"] == existing_node.name
        assert "properties" in data
        assert "claims" in data
        assert "qa_pairs" in data


def test_get_top_degree_node(client: TestClient, db_session: Session):
    """관계(Edge) 수가 가장 많은 대표 핵심 노드 단건 조회 API 검증."""
    # 테스트 분류 및 노드 준비
    cls = Classification(code="TOP_TEST", display_name="Top Test", is_active=True)
    db_session.add(cls)
    db_session.flush()
    node = Node(name="중심엔티티", classification_id=cls.id)
    db_session.add(node)
    db_session.flush()

    res = client.get("/api/v1/nodes/top-degree")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == node.id
    assert data["name"] == "중심엔티티"
    assert "edge_count" in data
    assert isinstance(data["edge_count"], int)

