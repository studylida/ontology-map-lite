"""핵심 API 엔드포인트 통합 테스트."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ontology_map.db.schema import Classification


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
