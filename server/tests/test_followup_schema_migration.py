import importlib.util
from pathlib import Path

from ontology_map.followup_generation import output_schema


def test_migration_uses_exact_runtime_bundle_schema() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/0008_bundle_followup_questions.py"
    )
    spec = importlib.util.spec_from_file_location("followup_migration_0008", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.down_revision == "0007"
    assert module._SCHEMA_JSON == output_schema()
