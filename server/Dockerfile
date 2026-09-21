FROM ghcr.io/astral-sh/uv:0.12.7 AS uv
FROM python:3.14.7-slim-bookworm

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src

EXPOSE 8000
CMD ["uvicorn", "ontology_map.main:app", "--host", "0.0.0.0", "--port", "8000"]
