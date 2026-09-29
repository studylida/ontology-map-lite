from pathlib import Path

import sqlalchemy as sa
from check_docs import (
    DECISIONS_PATH,
    REFERENCE_PATH,
    ROOT,
    check_adr_relationships,
    check_markdown_links,
    check_schema_reference,
    load_metadata,
    render_schema_reference,
    render_table,
)


def test_current_schema_and_document_drift(tmp_path: Path) -> None:
    metadata = load_metadata(ROOT)
    assert set(metadata.tables) == {
        "claims",
        "classifications",
        "documents",
        "edges",
        "node_insights",
        "node_qa_pairs",
        "nodes",
        "relations",
    }
    expected = render_schema_reference(metadata)
    foreign_keys = [
        line
        for line in render_table(metadata.tables["edges"])
        if line.startswith("| FOREIGN KEY")
    ]
    assert foreign_keys == sorted(foreign_keys)
    reference = tmp_path / REFERENCE_PATH
    assert check_schema_reference(tmp_path, expected)
    reference.parent.mkdir(parents=True)
    reference.write_text(expected, encoding="utf-8")
    assert check_schema_reference(tmp_path, expected) == []
    metadata.tables["nodes"].append_column(sa.Column("test_column", sa.Text))
    assert check_schema_reference(tmp_path, render_schema_reference(metadata))


def test_broken_links_and_missing_adr_index_still_fail(tmp_path: Path) -> None:
    assert check_adr_relationships(tmp_path, {}) == []
    (tmp_path / DECISIONS_PATH).mkdir(parents=True)
    assert check_adr_relationships(tmp_path, {})
    (tmp_path / "README.md").write_text("[없어진 문서](missing.md)\n", encoding="utf-8")
    assert check_markdown_links(tmp_path)
