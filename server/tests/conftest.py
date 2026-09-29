"""테스트 공통 픽스처."""

import os
from collections.abc import Generator
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ontology_map.db.session import get_engine, open_session
from ontology_map.main import app
from ontology_map.settings import get_settings


@pytest.fixture(scope="session", autouse=True)
def ensure_test_isolation():
    """테스트가 반드시 격리된 test 환경 및 test DB에서만 수행되도록 강제합니다 (T13)."""
    settings = get_settings()
    db_url_str = str(settings.database_url).lower()
    if settings.environment != "test" or "test" not in db_url_str:
        raise RuntimeError(
            f"TEST_ISOLATION_ERROR: 테스트 DB 격리 검증 실패! "
            f"environment={settings.environment}, database_url={settings.database_url}. "
            f"테스트는 반드시 ONTOLOGY_MAP_ENVIRONMENT=test 및 test 전용 DB에서 실행되어야 합니다."
        )


@pytest.fixture(autouse=True)
def mock_external_llm_calls(monkeypatch):
    """테스트 중 실제 외부 LLM API(OpenAI 등)로의 예기치 않은 네트워크 호출을 원천 차단합니다."""
    # 만약 개별 테스트에서 mock하지 않은 경우 기본 가짜 응답을 반환하도록 보호
    def fake_post(*args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        json_data = kwargs.get("json", {})
        messages = json_data.get("messages", [])
        system_content = messages[0].get("content", "") if messages else ""

        if "지식 애널리스트" in system_content or "qa_pairs" in system_content:
            content = (
                '{\n'
                '  "recent_history_summary": "최근 이력: 2026년 차세대 가속기 개발 및 공급 파트너십 구축",\n'
                '  "overall_insight": "글로벌 AI 인프라 시장에서 핵심적인 공급자 위상을 공고히 하고 있습니다.",\n'
                '  "issues": ["공급망 다변화", "차세대 패키징 수율"],\n'
                '  "qa_pairs": [\n'
                '    {"question": "주요 제품은 무엇인가요?", "answer": "Blackwell Ultra GPU 가속기입니다."},\n'
                '    {"question": "핵심 파트너사는 어디인가요?", "answer": "TSMC 등 주요 파운드리입니다."}\n'
                '  ]\n'
                '}'
            )
        else:
            content = '{"nodes": [], "edges": [], "claims": [], "insights": {"summary": "mock summary"}}'

        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": content
                    }
                }
            ]
        }
        return mock_resp

    # httpx.Client.post 호출 보호
    monkeypatch.setattr("httpx.Client.post", fake_post)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """각 테스트마다 독립된 트랜잭션을 열고, 종료 시 롤백하여 DB를 원상복구합니다."""
    connection = get_engine().connect()
    transaction = connection.begin()

    # join_transaction_mode="create_savepoint" 추가!
    # 백엔드 내부의 session.commit()이 실행되어도 최상위 트랜잭션이 닫히지 않고 롤백되도록 보호합니다.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """DB 세션이 트랜잭션 롤백으로 오버라이드된 FastAPI TestClient."""
    app.dependency_overrides[open_session] = lambda: db_session

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
