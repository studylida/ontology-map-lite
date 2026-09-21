import gzip
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from ontology_map.source_materialization import (
    MaterializationApproval,
    PreparationFailure,
    normalize_source_body,
    parse_selection_item,
    prepare_selection_item,
)


def manifest_record(
    body: str, *, artifact_key: str = "bodies/item.txt.gz"
) -> dict[str, object]:
    normalized = normalize_source_body(body)
    return {
        "test_item_id": "core-001",
        "discovery_key": "gdelt-webngrams:synthetic-001",
        "source_key": "e2e:synthetic-001",
        "artifact_key": artifact_key,
        "canonical_url": "https://example.com/article/1",
        "title": "합성 시험 기사",
        "publisher": "합성 발행처",
        "original_language": "ko",
        "published_at": "2026-08-20",
        "published_precision": "DAY",
        "body_sha256": sha256(normalized.encode()).hexdigest(),
        "qualification_ref": "qualification:approved-001",
        "lineage_ref": "lineage:approved-001",
    }


def approval() -> MaterializationApproval:
    return MaterializationApproval(
        qualification_ref="qualification:approved-001",
        lineage_ref="lineage:approved-001",
        evidence_group_id=7,
    )


def write_artifact(root: Path, key: str, body: str) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    if key.endswith(".txt.gz"):
        path.write_bytes(gzip.compress(body.encode(), mtime=0))
    else:
        path.write_text(body, encoding="utf-8")


def test_normalize_source_body_matches_131_contract() -> None:
    decomposed = "가".encode().decode()
    raw = f"  첫 줄  \r\n{decomposed}\u0301   \r\n\r\n\r\n끝   \n"
    expected = "첫 줄\n가́\n\n끝"
    assert normalize_source_body(raw) == expected


def test_prepare_selection_reads_gzip_and_verifies_checksum(tmp_path: Path) -> None:
    body = "첫 문장입니다.  \r\n\r\n\r\n둘째 문장입니다.\n"
    record = manifest_record(body)
    write_artifact(tmp_path, str(record["artifact_key"]), body)

    prepared = prepare_selection_item(
        item=parse_selection_item(record),
        artifact_root=tmp_path,
        approval=approval(),
    )

    assert prepared.document.normalized_body == normalize_source_body(body)
    assert prepared.document.body_hash.hex() == record["body_sha256"]
    assert prepared.evidence_group_id == 7


def test_prepare_selection_missing_artifact_fails_closed(tmp_path: Path) -> None:
    item = parse_selection_item(manifest_record("본문"))
    with pytest.raises(PreparationFailure, match="artifact") as raised:
        prepare_selection_item(item=item, artifact_root=tmp_path, approval=approval())
    assert raised.value.code == "ARTIFACT_MISSING"


def test_prepare_selection_checksum_mismatch_fails_closed(tmp_path: Path) -> None:
    record = manifest_record("승인 본문")
    write_artifact(tmp_path, str(record["artifact_key"]), "변경된 본문")
    with pytest.raises(PreparationFailure) as raised:
        prepare_selection_item(
            item=parse_selection_item(record),
            artifact_root=tmp_path,
            approval=approval(),
        )
    assert raised.value.code == "CHECKSUM_MISMATCH"


def test_prepare_selection_requires_external_approval(tmp_path: Path) -> None:
    record = manifest_record("본문")
    write_artifact(tmp_path, str(record["artifact_key"]), "본문")
    with pytest.raises(PreparationFailure) as raised:
        prepare_selection_item(
            item=parse_selection_item(record), artifact_root=tmp_path, approval=None
        )
    assert raised.value.code == "APPROVAL_MISSING"


def test_new_group_requires_explicit_approval_flag(tmp_path: Path) -> None:
    record = manifest_record("본문")
    write_artifact(tmp_path, str(record["artifact_key"]), "본문")
    missing_group = MaterializationApproval(
        qualification_ref="qualification:approved-001",
        lineage_ref="lineage:approved-001",
        evidence_group_id=None,
    )
    with pytest.raises(PreparationFailure) as raised:
        prepare_selection_item(
            item=parse_selection_item(record),
            artifact_root=tmp_path,
            approval=missing_group,
        )
    assert raised.value.code == "APPROVAL_INVALID"


def test_prepare_selection_rejects_mismatched_lineage_ref(tmp_path: Path) -> None:
    record = manifest_record("본문")
    write_artifact(tmp_path, str(record["artifact_key"]), "본문")
    wrong = MaterializationApproval(
        qualification_ref="qualification:approved-001",
        lineage_ref="lineage:different",
        evidence_group_id=7,
    )
    with pytest.raises(PreparationFailure) as raised:
        prepare_selection_item(
            item=parse_selection_item(record), artifact_root=tmp_path, approval=wrong
        )
    assert raised.value.code == "LINEAGE_NOT_CONFIRMED"


def test_prepare_selection_rejects_path_traversal(tmp_path: Path) -> None:
    record = manifest_record("본문", artifact_key="../outside.txt")
    with pytest.raises(PreparationFailure) as raised:
        prepare_selection_item(
            item=parse_selection_item(record),
            artifact_root=tmp_path,
            approval=approval(),
        )
    assert raised.value.code == "ARTIFACT_PATH_INVALID"


def test_manifest_requires_reproducible_source_metadata() -> None:
    record = manifest_record("본문")
    del record["source_key"]
    with pytest.raises(PreparationFailure) as raised:
        parse_selection_item(record)
    assert raised.value.code == "MANIFEST_INVALID"


def test_manifest_timestamp_precision_must_match() -> None:
    record = manifest_record("본문")
    record["published_at"] = None
    with pytest.raises(PreparationFailure) as raised:
        parse_selection_item(record)
    assert raised.value.code == "MANIFEST_INVALID"


def test_unknown_optional_modified_time_maps_to_schema_unknown() -> None:
    item = parse_selection_item(manifest_record("본문"))
    assert item.source_modified_at is None
    assert item.modified_precision == "UNKNOWN"
    assert item.published_at == datetime(2026, 8, 20, tzinfo=UTC)


def test_manifest_example_is_plain_json_serializable() -> None:
    line = json.dumps(manifest_record("본문"), ensure_ascii=False)
    assert json.loads(line)["test_item_id"] == "core-001"
