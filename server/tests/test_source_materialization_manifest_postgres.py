"""Cross-row #111 manifest regressions against real migrated PostgreSQL."""

import gzip
import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from ontology_map.source_materialization import (
    MaterializationApproval,
    materialize_selection_manifest,
    normalize_source_body,
)

DATABASE_URL = os.environ.get("ONTOLOGY_MAP_SOURCE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="isolated migrated PostgreSQL URL was not supplied",
)


@pytest.fixture
def database():
    url = sa.engine.make_url(DATABASE_URL)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_source111_test")
    current = sa.create_engine(url)
    prefix = f"source111-duplicate-test:{uuid4()}:"
    group_ids: list[int] = []
    try:
        yield current, prefix, group_ids
    finally:
        with current.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM source_document WHERE source_key LIKE :pattern"),
                {"pattern": f"{prefix}%"},
            )
            if group_ids:
                connection.execute(
                    sa.text(
                        "DELETE FROM evidence_group WHERE evidence_group_id = ANY(:ids)"
                    ),
                    {"ids": group_ids},
                )
        current.dispose()


def create_group(current: sa.Engine, group_ids: list[int]) -> int:
    with current.begin() as connection:
        group_id = int(
            connection.execute(
                sa.text(
                    "INSERT INTO evidence_group DEFAULT VALUES "
                    "RETURNING evidence_group_id"
                )
            ).scalar_one()
        )
    group_ids.append(group_id)
    return group_id


def write_artifact(root: Path, artifact_key: str, body: str) -> None:
    path = root / artifact_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body.encode("utf-8"), mtime=0))


def manifest_item(
    *,
    prefix: str,
    test_item_id: str,
    source_suffix: str,
    body: str,
) -> dict[str, object]:
    normalized = normalize_source_body(body)
    return {
        "test_item_id": test_item_id,
        "discovery_key": f"gdelt-webngrams:{source_suffix}",
        "source_key": f"{prefix}{source_suffix}",
        "artifact_key": f"bodies/{source_suffix}.txt.gz",
        "canonical_url": f"https://example.com/{source_suffix}",
        "title": f"합성 {source_suffix}",
        "publisher": "합성 발행처",
        "original_language": "ko",
        "published_at": "2026-08-20",
        "published_precision": "DAY",
        "body_sha256": sha256(normalized.encode()).hexdigest(),
        "qualification_ref": f"qualification:{test_item_id}",
        "lineage_ref": f"lineage:{test_item_id}",
    }


def approval(test_item_id: str, group_id: int) -> MaterializationApproval:
    return MaterializationApproval(
        qualification_ref=f"qualification:{test_item_id}",
        lineage_ref=f"lineage:{test_item_id}",
        evidence_group_id=group_id,
    )


def write_manifest(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def source_rows(current: sa.Engine, source_key: str) -> list[dict[str, object]]:
    with current.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT source_document_id, evidence_group_id, source_key, "
                    "version_no, normalized_body, body_hash, last_checked_at, "
                    "created_at FROM source_document "
                    "WHERE source_key = :source_key ORDER BY version_no"
                ),
                {"source_key": source_key},
            ).mappings()
        ]


def test_duplicate_item_preflight_blocks_all_duplicate_rows_only(
    database, tmp_path: Path
) -> None:
    current, prefix, group_ids = database
    groups = {name: create_group(current, group_ids) for name in ("a", "b", "c")}
    records = [
        manifest_item(
            prefix=prefix,
            test_item_id="a",
            source_suffix="a",
            body="a 본문",
        ),
        manifest_item(
            prefix=prefix,
            test_item_id="b",
            source_suffix="b-first",
            body="첫 b 본문",
        ),
        manifest_item(
            prefix=prefix,
            test_item_id="b",
            source_suffix="b-second",
            body="둘째 b 본문",
        ),
        manifest_item(
            prefix=prefix,
            test_item_id="c",
            source_suffix="c",
            body="c 본문",
        ),
    ]
    for record, body in zip(
        records,
        ("a 본문", "첫 b 본문", "둘째 b 본문", "c 본문"),
        strict=True,
    ):
        write_artifact(tmp_path, str(record["artifact_key"]), body)
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, records)
    approvals = {name: approval(name, group_id) for name, group_id in groups.items()}

    first = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals=approvals,
        checked_at=datetime(2026, 9, 16, 5, 0, tzinfo=UTC),
    )

    assert [
        (result.test_item_id, result.status, result.failure_code) for result in first
    ] == [
        ("a", "CREATED", None),
        ("b", "FAILED", "MANIFEST_DUPLICATE_ITEM"),
        ("b", "FAILED", "MANIFEST_DUPLICATE_ITEM"),
        ("c", "CREATED", None),
    ]
    assert source_rows(current, f"{prefix}b-first") == []
    assert source_rows(current, f"{prefix}b-second") == []
    before_a = source_rows(current, f"{prefix}a")
    before_c = source_rows(current, f"{prefix}c")
    assert len(before_a) == 1
    assert len(before_c) == 1

    retry_b = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals=approvals,
        checked_at=datetime(2026, 9, 16, 6, 0, tzinfo=UTC),
        only_test_item_ids=frozenset({"b"}),
    )

    assert [result.failure_code for result in retry_b] == [
        "MANIFEST_DUPLICATE_ITEM",
        "MANIFEST_DUPLICATE_ITEM",
    ]
    assert source_rows(current, f"{prefix}b-first") == []
    assert source_rows(current, f"{prefix}b-second") == []
    assert source_rows(current, f"{prefix}a") == before_a
    assert source_rows(current, f"{prefix}c") == before_c
