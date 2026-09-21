from __future__ import annotations

import argparse
import os
import re
import runpy
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = Path("server/src/ontology_map/db/metadata.py")
REFERENCE_PATH = Path("docs/data/schema-reference.md")
DECISIONS_PATH = Path("docs/architecture/decisions")
SKIPPED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
REQUIRED_ADR_FIELDS = {
    "id",
    "title",
    "status",
    "decision_date",
    "recorded_date",
    "evidence",
    "supersedes",
    "superseded_by",
    "affected_docs",
}
LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
REFERENCE_LINK_PATTERN = re.compile(r"^\s*\[[^\]]+]:\s*(\S+)")
HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
ADR_ID_PATTERN = re.compile(r"ADR-(\d{4})")
ADR_FILE_PATTERN = re.compile(r"(\d{4})-[a-z0-9-]+\.md")
BACKTICK = chr(96)
FENCE_PREFIXES = (BACKTICK * 3, "~" * 3)
DIALECT = postgresql.dialect()
WRITE_COMMAND = "uv run --project server --frozen python scripts/check_docs.py --write"
CHECK_COMMAND = "uv run --project server --frozen python scripts/check_docs.py --check"


def load_metadata(root: Path) -> sa.MetaData:
    namespace = runpy.run_path(str(root / SCHEMA_PATH))
    metadata = namespace.get("metadata")
    if not isinstance(metadata, sa.MetaData):
        raise RuntimeError(f"{SCHEMA_PATH}에서 SQLAlchemy metadata를 찾지 못했습니다.")
    return metadata


def markdown_cell(value: object | None) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", r"\|").replace("\n", "<br>")


def markdown_code(value: object | None) -> str:
    if value is None or value == "":
        return "—"
    text = markdown_cell(value).replace(BACKTICK, "")
    return f"{BACKTICK}{text}{BACKTICK}"


def compile_sql(value: object) -> str:
    if hasattr(value, "compile"):
        return str(
            value.compile(
                dialect=DIALECT,
                compile_kwargs={"literal_binds": True},
            )
        )
    return str(value)


def column_default(column: sa.Column[object]) -> str | None:
    if column.server_default is None or column.identity is not None:
        return None
    return str(getattr(column.server_default, "arg", column.server_default))


def column_identity(column: sa.Column[object]) -> str | None:
    if column.identity is None:
        return None
    mode = "ALWAYS" if column.identity.always else "BY DEFAULT"
    return f"GENERATED {mode} AS IDENTITY"


def constraint_kind(constraint: sa.Constraint) -> str:
    if isinstance(constraint, sa.PrimaryKeyConstraint):
        return "PRIMARY KEY"
    if isinstance(constraint, sa.ForeignKeyConstraint):
        return "FOREIGN KEY"
    if isinstance(constraint, sa.UniqueConstraint):
        return "UNIQUE"
    if isinstance(constraint, sa.CheckConstraint):
        return "CHECK"
    raise TypeError(f"지원하지 않는 constraint: {type(constraint).__name__}")


def constraint_definition(constraint: sa.Constraint) -> str:
    if isinstance(constraint, sa.CheckConstraint):
        return f"CHECK ({compile_sql(constraint.sqltext)})"

    columns = ", ".join(column.name for column in constraint.columns)
    if isinstance(constraint, sa.PrimaryKeyConstraint):
        return f"PRIMARY KEY ({columns})"
    if isinstance(constraint, sa.UniqueConstraint):
        return f"UNIQUE ({columns})"
    if isinstance(constraint, sa.ForeignKeyConstraint):
        targets = ", ".join(element.column.name for element in constraint.elements)
        target_table = constraint.elements[0].column.table.fullname
        definition = f"FOREIGN KEY ({columns}) REFERENCES {target_table} ({targets})"
        first = constraint.elements[0]
        if first.ondelete:
            definition += f" ON DELETE {first.ondelete}"
        if first.onupdate:
            definition += f" ON UPDATE {first.onupdate}"
        return definition
    raise TypeError(f"지원하지 않는 constraint: {type(constraint).__name__}")


def index_expressions(table: sa.Table, index: sa.Index) -> str:
    expressions: list[str] = []
    for expression in index.expressions:
        if isinstance(expression, sa.Column) and expression.table is table:
            expressions.append(expression.name)
        else:
            expressions.append(compile_sql(expression))
    return ", ".join(expressions)


def render_table(table: sa.Table) -> list[str]:
    lines = [
        f"## {BACKTICK}{table.name}{BACKTICK}",
        "",
        markdown_cell(table.comment),
        "",
        "### Columns",
        "",
        "| 이름 | PostgreSQL type | nullable | default | identity | 설명 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for column in table.columns:
        lines.append(
            "| "
            + " | ".join(
                (
                    markdown_code(column.name),
                    markdown_code(column.type.compile(dialect=DIALECT)),
                    "예" if column.nullable else "아니요",
                    markdown_code(column_default(column)),
                    markdown_code(column_identity(column)),
                    markdown_cell(column.comment),
                )
            )
            + " |"
        )

    lines.extend(
        (
            "",
            "### Constraints",
            "",
            "| 종류 | 이름 | 정의 |",
            "| --- | --- | --- |",
        )
    )
    constraints = sorted(
        table.constraints,
        key=lambda value: (constraint_kind(value), value.name or ""),
    )
    for constraint in constraints:
        lines.append(
            f"| {constraint_kind(constraint)} | "
            f"{markdown_code(constraint.name)} | "
            f"{markdown_code(constraint_definition(constraint))} |"
        )

    lines.extend(
        (
            "",
            "### Indexes",
            "",
            "| 이름 | unique | column 또는 expression | 조건 |",
            "| --- | --- | --- | --- |",
        )
    )
    indexes = sorted(table.indexes, key=lambda value: value.name or "")
    if not indexes:
        lines.append("| — | — | — | — |")
    for index in indexes:
        predicate = index.dialect_options["postgresql"].get("where")
        condition = compile_sql(predicate) if predicate is not None else None
        lines.append(
            f"| {markdown_code(index.name)} | "
            f"{'예' if index.unique else '아니요'} | "
            f"{markdown_code(index_expressions(table, index))} | "
            f"{markdown_code(condition)} |"
        )
    lines.append("")
    return lines


def render_schema_reference(metadata: sa.MetaData) -> str:
    tables = sorted(metadata.tables.values(), key=lambda value: value.name)
    lines = [
        "<!-- scripts/check_docs.py가 생성합니다. 직접 수정하지 마세요. -->",
        "",
        "# ontology-map PostgreSQL 스키마 참고 문서",
        "",
        "이 문서는 [완성 SQLAlchemy metadata](../../server/src/ontology_map/db/metadata.py)의 "
        "실제 table, column, constraint와 index를 이름순으로 보여 주는 생성 결과다. "
        "데이터 의미와 수명주기는 [논리 스키마](logical-schema.md), PostgreSQL 공통 "
        "표현 규칙은 [물리 스키마](physical-schema.md)가 소유한다.",
        "",
        f"- table 수: {len(tables)}",
        f"- 생성 명령: {BACKTICK}{WRITE_COMMAND}{BACKTICK}",
        f"- 검사 명령: {BACKTICK}{CHECK_COMMAND}{BACKTICK}",
        "",
        "## Table 목차",
        "",
    ]
    lines.extend(
        f"- [{BACKTICK}{table.name}{BACKTICK}](#{table.name})" for table in tables
    )
    lines.append("")
    for table in tables:
        lines.extend(render_table(table))
    return "\n".join(lines)


def iter_markdown_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in SKIPPED_DIRECTORIES
        )
        base = Path(directory)
        files.extend(
            base / name for name in sorted(names) if name.lower().endswith(".md")
        )
    return files


def markdown_targets(path: Path) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []
    in_fence = False
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        stripped = line.lstrip()
        if stripped.startswith(FENCE_PREFIXES):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        targets.extend(
            (line_number, match.group(1)) for match in LINK_PATTERN.finditer(line)
        )
        reference_match = REFERENCE_LINK_PATTERN.match(line)
        if reference_match:
            targets.append((line_number, reference_match.group(1)))
    return targets


def split_link_target(raw_target: str) -> tuple[str, str | None]:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    target = unquote(target)
    path_part, separator, fragment = target.partition("#")
    return path_part.split("?", 1)[0], fragment if separator else None


def heading_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith(FENCE_PREFIXES):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_PATTERN.match(line)
        if not match:
            continue
        heading = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", match.group(1))
        base = re.sub(r"[^\w -]", "", heading.lower()).strip().replace(" ", "-")
        base = re.sub(r"-+", "-", base)
        duplicate = counts.get(base, 0)
        counts[base] = duplicate + 1
        anchors.add(f"{base}-{duplicate}" if duplicate else base)
    return anchors


def resolve_repository_link(
    root: Path,
    source: Path,
    raw_target: str,
) -> tuple[Path | None, str | None, str | None]:
    path_part, fragment = split_link_target(raw_target)
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", path_part):
        return None, None, None
    if not path_part:
        return source, fragment, None
    if path_part.startswith("/"):
        return None, fragment, "저장소 상대 경로가 아닙니다"
    candidate = (source.parent / path_part).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None, fragment, "저장소 밖을 가리킵니다"
    if not candidate.exists():
        return candidate, fragment, "대상이 없습니다"
    if candidate.is_dir():
        readme = candidate / "README.md"
        if fragment and readme.exists():
            candidate = readme
    return candidate, fragment, None


def check_markdown_links(root: Path) -> list[str]:
    errors: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}
    for source in iter_markdown_files(root):
        for line_number, raw_target in markdown_targets(source):
            target, fragment, error = resolve_repository_link(root, source, raw_target)
            location = f"{source.relative_to(root)}:{line_number}"
            if error:
                errors.append(f"{location}: {raw_target!r} 링크는 {error}.")
                continue
            if not target or not fragment or target.suffix.lower() != ".md":
                continue
            anchors = anchor_cache.setdefault(target, heading_anchors(target))
            if fragment not in anchors:
                errors.append(
                    f"{location}: {raw_target!r} 링크의 heading을 찾지 못했습니다."
                )
    return errors


def parse_adr(path: Path) -> tuple[dict[str, str], list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []
    if not lines or lines[0] != "---":
        return {}, [f"{path}: ADR metadata 시작 구분자가 없습니다."]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, [f"{path}: ADR metadata 종료 구분자가 없습니다."]

    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            errors.append(f"{path}: 잘못된 ADR metadata 행: {line!r}")
            continue
        key = key.strip()
        if key in metadata:
            errors.append(f"{path}: ADR metadata {key!r}가 중복됐습니다.")
        metadata[key] = value.strip()
    return metadata, errors


def adr_references(value: str) -> set[str]:
    return {match.group(0) for match in ADR_ID_PATTERN.finditer(value)}


def validate_adr_file(
    path: Path,
    relative: Path,
    folder: str,
    expected_status: str,
) -> tuple[dict[str, str], list[str]]:
    metadata, errors = parse_adr(path)
    if not ADR_FILE_PATTERN.fullmatch(path.name):
        errors.append(f"{relative}: ADR 파일 이름 형식이 잘못됐습니다.")
    missing = REQUIRED_ADR_FIELDS - metadata.keys()
    if missing:
        fields = ", ".join(sorted(missing))
        errors.append(f"{relative}: ADR metadata가 없습니다: {fields}.")
        return metadata, errors

    match = ADR_ID_PATTERN.fullmatch(metadata["id"])
    if not match or not path.name.startswith(f"{match.group(1)}-"):
        errors.append(f"{relative}: ADR id와 파일 번호가 일치하지 않습니다.")
    if metadata["status"] != expected_status:
        errors.append(
            f"{relative}: {folder} ADR status는 {expected_status}여야 합니다."
        )
    if folder == "current" and metadata["superseded_by"] != "none":
        errors.append(f"{relative}: current ADR은 superseded_by가 none이어야 합니다.")
    if folder == "superseded" and metadata["superseded_by"] == "none":
        errors.append(f"{relative}: superseded ADR에는 superseded_by가 필요합니다.")
    for field in ("decision_date", "recorded_date"):
        try:
            date.fromisoformat(metadata[field])
        except ValueError:
            errors.append(f"{relative}: {field}는 YYYY-MM-DD여야 합니다.")
    if metadata["evidence"] == "none" or metadata["affected_docs"] == "none":
        errors.append(f"{relative}: evidence와 affected_docs는 비어 있을 수 없습니다.")
    return metadata, errors


def check_adr_metadata(
    root: Path,
) -> tuple[list[str], dict[str, tuple[Path, dict[str, str]]]]:
    errors: list[str] = []
    records: dict[str, tuple[Path, dict[str, str]]] = {}
    decisions = root / DECISIONS_PATH
    for folder, expected_status in (
        ("current", "accepted"),
        ("superseded", "superseded"),
    ):
        for path in sorted((decisions / folder).glob("*.md")):
            if path.name == "README.md":
                continue
            relative = path.relative_to(root)
            metadata, file_errors = validate_adr_file(
                path, relative, folder, expected_status
            )
            errors.extend(file_errors)
            if REQUIRED_ADR_FIELDS - metadata.keys():
                continue
            adr_id = metadata["id"]
            if adr_id in records:
                errors.append(f"{relative}: ADR id {adr_id}가 중복됐습니다.")
            records[adr_id] = (relative, metadata)
    return errors, records


def check_adr_reference_field(
    adr_id: str,
    path: Path,
    field: str,
    reverse_field: str,
    value: str,
    records: dict[str, tuple[Path, dict[str, str]]],
) -> list[str]:
    errors: list[str] = []
    references = adr_references(value)
    if value != "none" and not references:
        errors.append(f"{path}: {field}에 유효한 ADR id가 없습니다.")
    if adr_id in references:
        errors.append(f"{path}: ADR은 자신을 {field}로 참조할 수 없습니다.")
    for target_id in references:
        target = records.get(target_id)
        if target is None:
            errors.append(f"{path}: {field} 대상 {target_id}가 없습니다.")
            continue
        if adr_id not in adr_references(target[1][reverse_field]):
            errors.append(
                f"{path}: {target_id}의 {reverse_field}가 {adr_id}를 가리키지 않습니다."
            )
    return errors


def check_adr_relationships(
    root: Path,
    records: dict[str, tuple[Path, dict[str, str]]],
) -> list[str]:
    errors: list[str] = []
    index_path = root / DECISIONS_PATH / "README.md"
    if not index_path.exists():
        return [f"{DECISIONS_PATH}/README.md: ADR 색인이 없습니다."]
    indexed_paths: set[Path] = set()
    for _, raw_target in markdown_targets(index_path):
        target, _, error = resolve_repository_link(root, index_path, raw_target)
        if not error and target is not None:
            indexed_paths.add(target.relative_to(root))
    for adr_id, (path, metadata) in records.items():
        if path not in indexed_paths:
            errors.append(f"{path}: ADR 색인에 {adr_id} 링크가 없습니다.")
        errors.extend(
            check_adr_reference_field(
                adr_id,
                path,
                "supersedes",
                "superseded_by",
                metadata["supersedes"],
                records,
            )
        )
        errors.extend(
            check_adr_reference_field(
                adr_id,
                path,
                "superseded_by",
                "supersedes",
                metadata["superseded_by"],
                records,
            )
        )
    return errors


def check_current_links_to_superseded(root: Path) -> list[str]:
    errors: list[str] = []
    decisions = (root / DECISIONS_PATH).resolve()
    superseded = (decisions / "superseded").resolve()
    for source in iter_markdown_files(root):
        if source.resolve().is_relative_to(decisions):
            continue
        for line_number, raw_target in markdown_targets(source):
            target, _, error = resolve_repository_link(root, source, raw_target)
            if error or target is None:
                continue
            if target.resolve().is_relative_to(superseded):
                errors.append(
                    f"{source.relative_to(root)}:{line_number}: 현재 문서가 "
                    "superseded ADR을 현재 결정으로 참조합니다."
                )
    return errors


def check_schema_reference(root: Path, expected_reference: str) -> list[str]:
    reference = root / REFERENCE_PATH
    if (
        reference.exists()
        and reference.read_text(encoding="utf-8") == expected_reference
    ):
        return []
    return [
        f"{REFERENCE_PATH}: SQLAlchemy metadata와 다릅니다. "
        "scripts/check_docs.py --write로 갱신하세요."
    ]


def run_checks(root: Path, expected_reference: str) -> list[str]:
    errors = check_schema_reference(root, expected_reference)
    errors.extend(check_markdown_links(root))
    metadata_errors, records = check_adr_metadata(root)
    errors.extend(metadata_errors)
    errors.extend(check_adr_relationships(root, records))
    errors.extend(check_current_links_to_superseded(root))
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ontology-map 생성 스키마 문서와 문서 계약을 관리합니다."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--write", action="store_true", help="스키마 참고 문서를 갱신합니다."
    )
    mode.add_argument("--check", action="store_true", help="문서 계약을 검사합니다.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metadata = load_metadata(ROOT)
    expected_reference = render_schema_reference(metadata)
    if args.write:
        destination = ROOT / REFERENCE_PATH
        destination.write_text(expected_reference, encoding="utf-8")
        print(
            f"{destination.relative_to(ROOT)} 갱신 완료: table {len(metadata.tables)}개"
        )
        return 0

    errors = run_checks(ROOT, expected_reference)
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors))
        return 1
    adr_count = sum(
        1 for path in (ROOT / DECISIONS_PATH).glob("*/*.md") if path.name != "README.md"
    )
    print(
        "문서 검사 통과: "
        f"table {len(metadata.tables)}개, "
        f"Markdown {len(iter_markdown_files(ROOT))}개, ADR {adr_count}개"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
