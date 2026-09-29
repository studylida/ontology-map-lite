"""FastAPI 진입점 및 애플리케이션 초기화."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from ontology_map.api import router
from ontology_map.db.session import get_engine
from ontology_map.settings import get_settings


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    # 서버 기동 시 DB 연결 상태 확인 (Health check)
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))

    yield


def create_app() -> FastAPI:
    get_settings()
    application = FastAPI(
        title="Ontology Map API",
        version="2.0.0",
        description="경량화된 8개 코어 스키마 기반 지식그래프 API",
        lifespan=lifespan,
    )
    # 신규 코어 라우터 등록
    application.include_router(router)
    return application


app = create_app()
