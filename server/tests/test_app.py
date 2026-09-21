import asyncio

from ontology_map.main import app


def test_app_starts_with_database_connection() -> None:
    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert app.title == "ontology-map API"

    asyncio.run(run_lifespan())
