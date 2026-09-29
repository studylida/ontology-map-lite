import pytest
from pydantic import ValidationError

from ontology_map.settings import Settings

TEST_DATABASE_URL = "postgresql+psycopg://test:test@127.0.0.1:1/ontology_map_test"


def test_current_settings_ignore_retired_processing_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONTOLOGY_MAP_SOURCE_PROCESSING_ENABLED", "true")
    monkeypatch.setenv("ONTOLOGY_MAP_DEMO_UNBOUNDED_PROVIDER", "false")
    settings = Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        environment="test",
        OPENAI_API_KEY="test-only",
    )
    assert str(settings.database_url) == TEST_DATABASE_URL
    assert settings.environment == "test"
    assert settings.openai_api_key == "test-only"


def test_required_database_and_environment_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ONTOLOGY_MAP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ONTOLOGY_MAP_ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError) as missing:
        Settings(_env_file=None)
    assert {error["loc"] for error in missing.value.errors()} == {
        ("database_url",),
        ("environment",),
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="not-a-dsn", environment="test")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=TEST_DATABASE_URL, environment="invalid")
