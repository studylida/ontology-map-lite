"""Synthetic #111 source intake regressions against real migrated PostgreSQL.

These tests prove deterministic artifact validation and source_document persistence.
They do not prove real #131 qualification, lineage, representative selection,
semantic invariants, or a completed #111 handoff.
"""

import gzip
import json
import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from ontology_map.db.source_materialization import (
    SourceDocumentInput,
    materialize_source_document,
)
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
def engine():
    url = sa.engine.make_url(DATABASE_URL)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_source111_test")
    current = sa.create_engine(url)
    prefix = f"source111-test:{uuid4()}:"
    try:
        yield current, prefix
    finally:
        with current.begin() as connection:
            connection.execute(
                sa.text(
                    "DELETE FROM observation WHERE source_document_id IN ("
                    "SELECT source_document_id FROM source_document "
                    "WHERE source_key LIKE :pattern)"
                ),
                {"pattern": f"{prefix}%"},
            )
            group_ids = list(
                connection.execute(
                    sa.text(
                        "SELECT DISTINCT evidence_group_id FROM source_document "
                        "WHERE source_key LIKE :pattern"
                    ),
                    {"pattern": f"{prefix}%"},
                ).scalars()
            )
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


def create_group(current: sa.Engine) -> int:
    with current.begin() as connection:
        return int(
            connection.execute(
                sa.text(
                    "INSERT INTO evidence_group DEFAULT VALUES "
                    "RETURNING evidence_group_id"
                )
            ).scalar_one()
        )


def artifact(root: Path, key: str, body: str) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body.encode("utf-8"), mtime=0))


def item(
    *,
    prefix: str,
    name: str,
    body: str,
    title: str | None = None,
    artifact_key: str | None = None,
) -> dict[str, object]:
    normalized = normalize_source_body(body)
    return {
        "test_item_id": name,
        "discovery_key": f"gdelt-webngrams:{name}",
        "source_key": f"{prefix}{name}",
        "artifact_key": artifact_key or f"bodies/{name}.txt.gz",
        "canonical_url": f"https://example.com/{name}",
        "title": title or f"합성 {name}",
        "publisher": "합성 발행처",
        "original_language": "ko",
        "published_at": "2026-08-20",
        "published_precision": "DAY",
        "body_sha256": sha256(normalized.encode()).hexdigest(),
        "qualification_ref": f"qualification:{name}",
        "lineage_ref": f"lineage:{name}",
    }


def approval(name: str, group_id: int) -> MaterializationApproval:
    return MaterializationApproval(
        qualification_ref=f"qualification:{name}",
        lineage_ref=f"lineage:{name}",
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
                    "version_no, canonical_url, title, normalized_body, body_hash, "
                    "last_checked_at, last_check_status, created_at "
                    "FROM source_document "
                    "WHERE source_key = :source_key ORDER BY version_no"
                ),
                {"source_key": source_key},
            ).mappings()
        ]


def observation_count(current: sa.Engine, document_ids: list[int]) -> int:
    if not document_ids:
        return 0
    with current.connect() as connection:
        return int(
            connection.execute(
                sa.text(
                    "SELECT count(*) FROM observation "
                    "WHERE source_document_id = ANY(:document_ids)"
                ),
                {"document_ids": document_ids},
            ).scalar_one()
        )


def test_new_materialization_and_idempotent_rerun(engine, tmp_path: Path) -> None:
    current, prefix = engine
    group_id = create_group(current)
    record = item(prefix=prefix, name="new", body="새 합성 본문")
    artifact(tmp_path, str(record["artifact_key"]), "새 합성 본문")
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [record])
    approvals = {"new": approval("new", group_id)}
    checked = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)

    first = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals=approvals,
        checked_at=checked,
    )
    second = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals=approvals,
        checked_at=checked + timedelta(minutes=1),
    )

    assert [(result.status, result.version_no) for result in first] == [("CREATED", 1)]
    assert [(result.status, result.version_no) for result in second] == [("REUSED", 1)]
    assert first[0].source_document_id == second[0].source_document_id
    rows = source_rows(current, str(record["source_key"]))
    assert len(rows) == 1
    assert rows[0]["last_checked_at"] == checked + timedelta(minutes=1)


def test_checksum_mismatch_and_missing_artifact_write_nothing(
    engine, tmp_path: Path
) -> None:
    current, prefix = engine
    mismatch_group = create_group(current)
    missing_group = create_group(current)
    mismatch = item(prefix=prefix, name="mismatch", body="승인 본문")
    missing = item(prefix=prefix, name="missing", body="없는 본문")
    artifact(tmp_path, str(mismatch["artifact_key"]), "변조 본문")
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [mismatch, missing])

    results = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals={
            "mismatch": approval("mismatch", mismatch_group),
            "missing": approval("missing", missing_group),
        },
    )

    assert [result.failure_code for result in results] == [
        "CHECKSUM_MISMATCH",
        "ARTIFACT_MISSING",
    ]
    assert source_rows(current, str(mismatch["source_key"])) == []
    assert source_rows(current, str(missing["source_key"])) == []


def test_missing_qualification_or_lineage_approval_writes_nothing(
    engine, tmp_path: Path
) -> None:
    current, prefix = engine
    record = item(prefix=prefix, name="unapproved", body="본문")
    artifact(tmp_path, str(record["artifact_key"]), "본문")
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [record])

    results = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals={},
    )

    assert results[0].failure_code == "APPROVAL_MISSING"
    assert source_rows(current, str(record["source_key"])) == []


def test_two_documents_keep_source_identity_separate_and_create_no_observations(
    engine, tmp_path: Path
) -> None:
    current, prefix = engine
    group_id = create_group(current)
    first = item(prefix=prefix, name="identity-a", body="첫 문서 본문")
    second = item(prefix=prefix, name="identity-c", body="둘째 문서 본문")
    artifact(tmp_path, str(first["artifact_key"]), "첫 문서 본문")
    artifact(tmp_path, str(second["artifact_key"]), "둘째 문서 본문")
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [first, second])

    results = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals={
            "identity-a": approval("identity-a", group_id),
            "identity-c": approval("identity-c", group_id),
        },
    )

    assert [result.status for result in results] == ["CREATED", "CREATED"]
    ids = [result.source_document_id for result in results]
    assert ids[0] is not None and ids[1] is not None and ids[0] != ids[1]
    document_ids = [int(value) for value in ids if value is not None]
    assert observation_count(current, document_ids) == 0
    assert len(source_rows(current, str(first["source_key"]))) == 1
    assert len(source_rows(current, str(second["source_key"]))) == 1


def test_partial_success_then_retry_only_failed_item(engine, tmp_path: Path) -> None:
    current, prefix = engine
    groups = {name: create_group(current) for name in ("a", "b", "c")}
    records = {
        name: item(prefix=prefix, name=name, body=f"{name} 본문") for name in groups
    }
    artifact(tmp_path, str(records["a"]["artifact_key"]), "a 본문")
    artifact(tmp_path, str(records["b"]["artifact_key"]), "잘못된 b 본문")
    artifact(tmp_path, str(records["c"]["artifact_key"]), "c 본문")
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [records["a"], records["b"], records["c"]])
    approvals = {name: approval(name, group_id) for name, group_id in groups.items()}

    first = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals=approvals,
        checked_at=datetime(2026, 9, 16, 1, 0, tzinfo=UTC),
    )
    assert [(result.test_item_id, result.status) for result in first] == [
        ("a", "CREATED"),
        ("b", "FAILED"),
        ("c", "CREATED"),
    ]
    assert first[1].failure_code == "CHECKSUM_MISMATCH"

    before_a = source_rows(current, str(records["a"]["source_key"]))[0]
    before_c = source_rows(current, str(records["c"]["source_key"]))[0]
    artifact(tmp_path, str(records["b"]["artifact_key"]), "b 본문")
    retry = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals=approvals,
        checked_at=datetime(2026, 9, 16, 2, 0, tzinfo=UTC),
        only_test_item_ids=frozenset({"b"}),
    )

    assert [(result.test_item_id, result.status) for result in retry] == [
        ("b", "CREATED")
    ]
    assert len(source_rows(current, str(records["b"]["source_key"]))) == 1
    after_a = source_rows(current, str(records["a"]["source_key"]))[0]
    after_c = source_rows(current, str(records["c"]["source_key"]))[0]
    assert after_a == before_a
    assert after_c == before_c


def test_changed_immutable_content_creates_next_version_without_rewriting_old(
    engine, tmp_path: Path
) -> None:
    current, prefix = engine
    group_id = create_group(current)
    first_record = item(prefix=prefix, name="versioned", body="첫 본문")
    artifact(tmp_path, str(first_record["artifact_key"]), "첫 본문")
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [first_record])
    approvals = {"versioned": approval("versioned", group_id)}
    materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals=approvals,
        checked_at=datetime(2026, 9, 16, 3, 0, tzinfo=UTC),
    )
    first_row = source_rows(current, str(first_record["source_key"]))[0]

    second_record = item(
        prefix=prefix,
        name="versioned",
        body="수정된 둘째 본문",
        title="수정된 합성 제목",
    )
    artifact(tmp_path, str(second_record["artifact_key"]), "수정된 둘째 본문")
    write_manifest(manifest, [second_record])
    result = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals=approvals,
        checked_at=datetime(2026, 9, 16, 4, 0, tzinfo=UTC),
    )

    rows = source_rows(current, str(first_record["source_key"]))
    assert [(row["version_no"], row["title"]) for row in rows] == [
        (1, "합성 versioned"),
        (2, "수정된 합성 제목"),
    ]
    assert rows[0] == first_row
    assert result[0].status == "CREATED"
    assert result[0].version_no == 2


def test_existing_source_key_cannot_be_silently_reassigned(
    engine, tmp_path: Path
) -> None:
    current, prefix = engine
    first_group = create_group(current)
    conflicting_group = create_group(current)
    record = item(prefix=prefix, name="lineage-conflict", body="본문")
    artifact(tmp_path, str(record["artifact_key"]), "본문")
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [record])
    first_approval = {"lineage-conflict": approval("lineage-conflict", first_group)}
    materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals=first_approval,
    )
    before = source_rows(current, str(record["source_key"]))

    conflict = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals={"lineage-conflict": approval("lineage-conflict", conflicting_group)},
    )

    assert conflict[0].failure_code == "EVIDENCE_GROUP_CONFLICT"
    assert source_rows(current, str(record["source_key"])) == before


def test_missing_approved_group_fails_without_write(engine, tmp_path: Path) -> None:
    current, prefix = engine
    record = item(prefix=prefix, name="group-missing", body="본문")
    artifact(tmp_path, str(record["artifact_key"]), "본문")
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [record])
    impossible_group_id = 9_223_372_036_854_000_000

    result = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals={"group-missing": approval("group-missing", impossible_group_id)},
    )

    assert result[0].failure_code == "EVIDENCE_GROUP_NOT_FOUND"
    assert source_rows(current, str(record["source_key"])) == []


def test_approved_new_group_is_atomic_idempotent_and_rechecked(
    engine, tmp_path: Path
) -> None:
    current, prefix = engine
    first = item(prefix=prefix, name="first", body="공유 본문")
    copy = item(prefix=prefix, name="copy", body="공유 본문")
    unrelated = item(prefix=prefix, name="unrelated", body="독립 본문")
    for record, body in (
        (first, "공유 본문"),
        (copy, "공유 본문"),
        (unrelated, "독립 본문"),
    ):
        artifact(tmp_path, str(record["artifact_key"]), body)
    manifest = tmp_path / "manifest.jsonl"

    def approved(name: str) -> MaterializationApproval:
        return MaterializationApproval(
            qualification_ref=f"qualification:{name}",
            lineage_ref=f"lineage:{name}",
            evidence_group_id=None,
            create_new_group=True,
        )

    write_manifest(manifest, [first, copy, unrelated])
    approvals = {name: approved(name) for name in ("first", "copy", "unrelated")}
    created = materialize_selection_manifest(
        current, manifest_path=manifest, artifact_root=tmp_path, approvals=approvals
    )
    repeated = materialize_selection_manifest(
        current, manifest_path=manifest, artifact_root=tmp_path, approvals=approvals
    )

    assert [result.status for result in created] == ["CREATED"] * 3
    assert [result.status for result in repeated] == ["REUSED"] * 3
    assert [result.source_document_id for result in created] == [
        result.source_document_id for result in repeated
    ]
    first_group = source_rows(current, str(first["source_key"]))[0]["evidence_group_id"]
    copy_group = source_rows(current, str(copy["source_key"]))[0]["evidence_group_id"]
    other_group = source_rows(current, str(unrelated["source_key"]))[0][
        "evidence_group_id"
    ]
    assert first_group == copy_group != other_group

    conflict = materialize_selection_manifest(
        current,
        manifest_path=manifest,
        artifact_root=tmp_path,
        approvals={"copy": approval("copy", int(other_group))},
        only_test_item_ids=frozenset({"copy"}),
    )
    assert conflict[0].failure_code == "EVIDENCE_GROUP_CONFLICT"
    assert len(source_rows(current, str(copy["source_key"]))) == 1

    changed = item(prefix=prefix, name="first", body="공유 본문", title="수정 제목")
    write_manifest(manifest, [changed])
    version = materialize_selection_manifest(
        current, manifest_path=manifest, artifact_root=tmp_path, approvals=approvals
    )
    assert (version[0].status, version[0].version_no) == ("CREATED", 2)
    assert [
        row["evidence_group_id"]
        for row in source_rows(current, str(first["source_key"]))
    ] == [
        first_group,
        first_group,
    ]


def test_failed_source_insert_rolls_back_new_group(engine) -> None:
    current, prefix = engine
    with current.connect() as connection:
        before = connection.scalar(sa.text("SELECT count(*) FROM evidence_group"))
    invalid = SourceDocumentInput(
        source_key=f"{prefix}invalid",
        canonical_url="https://example.com/invalid",
        publisher_name="합성 발행처",
        title="",
        author_text=None,
        original_language="ko",
        normalized_body="합성 본문",
        body_hash=sha256("합성 본문".encode()).digest(),
        published_at=None,
        published_precision="UNKNOWN",
        source_modified_at=None,
        modified_precision="UNKNOWN",
    )
    with pytest.raises(sa.exc.IntegrityError):
        with current.begin() as connection:
            materialize_source_document(
                connection,
                document=invalid,
                evidence_group_id=None,
                checked_at=datetime.now(UTC),
            )
    with current.connect() as connection:
        assert (
            connection.scalar(sa.text("SELECT count(*) FROM evidence_group")) == before
        )
    assert source_rows(current, invalid.source_key) == []
