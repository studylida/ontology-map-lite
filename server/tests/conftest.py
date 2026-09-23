"""테스트 공통 픽스처."""

from collections.abc import Generator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ontology_map.db.session import get_engine, open_session
from ontology_map.main import app


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
