from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text

from ontology_map.api import (
    APIError,
    api_error_handler,
    router,
    validation_error_handler,
)
from ontology_map.db.session import get_engine
from ontology_map.pagination import InvalidCursorError
from ontology_map.panel import PanelNotFoundError, PanelNotReadyError
from ontology_map.panel_api import panel_error_handler
from ontology_map.panel_api import router as panel_router
from ontology_map.settings import get_settings
from ontology_map.topic_api import router as topic_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
    settings = get_settings()
    worker = None
    if settings.source_processing_enabled:
        from ontology_map.source_worker import SourceWorker

        worker = SourceWorker(get_engine(), settings)
        worker.start()
        application.state.source_worker = worker
    try:
        yield
    finally:
        if worker is not None:
            worker.shutdown()


def create_app() -> FastAPI:
    get_settings()
    application = FastAPI(title="ontology-map API", version="1.0.0", lifespan=lifespan)
    application.add_exception_handler(APIError, api_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.include_router(router)
    application.include_router(panel_router)
    application.include_router(topic_router)
    for error_type in (PanelNotFoundError, PanelNotReadyError, InvalidCursorError):
        application.add_exception_handler(error_type, panel_error_handler)
    return application


app = create_app()
