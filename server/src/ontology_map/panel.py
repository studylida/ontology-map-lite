"""저장된 질문·근거·보고서를 현재 공개 선택 안에서 읽는다."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ontology_map.db import insights as insight_queries
from ontology_map.db import panel as queries
from ontology_map.db.exploration import is_public_node
from ontology_map.exploration import TimeWindow
from ontology_map.insights import get_insight, list_node_insights
from ontology_map.pagination import InvalidCursorError, decode_cursor, encode_cursor


class PanelNotFoundError(Exception):
    pass


class PanelNotReadyError(Exception):
    pass


def _context(session: Session, node_id: int) -> tuple[dict[str, Any], list[int], bool]:
    if not is_public_node(session, node_id):
        raise PanelNotFoundError
    context = queries.context(session, node_id)
    if context is None:
        raise PanelNotReadyError
    ids, complete = queries.basis(session, context["node_search_document_id"])
    return context, ids, complete and node_id in ids


def _start(as_of_at: datetime, window: TimeWindow) -> datetime | None:
    return window.start_at(as_of_at)


def _position(
    cursor: str | None, kind: str, scope: dict[str, int | str], as_of_at: datetime
) -> tuple[int, datetime]:
    if cursor is None:
        return 0, as_of_at
    values = decode_cursor(cursor, kind=kind, scope=scope)
    if (
        len(values) != 2
        or type(values[0]) is not int
        or values[0] < 1
        or not isinstance(values[1], str)
    ):
        raise InvalidCursorError
    try:
        stamp = datetime.fromisoformat(values[1])
        if stamp.tzinfo is None or stamp.year < 2:
            raise ValueError
    except ValueError as error:
        raise InvalidCursorError from error
    return values[0], stamp


def _next(
    rows: list[dict[str, Any]],
    limit: int,
    key: str,
    kind: str,
    scope: dict[str, int | str],
    as_of_at: datetime,
) -> str | None:
    if len(rows) <= limit:
        return None
    return encode_cursor(kind, scope, [int(rows[limit - 1][key]), as_of_at.isoformat()])


def _claim(row: dict[str, Any], as_of_at: datetime) -> dict[str, Any]:
    return {
        "claim_id": str(row["claim_id"]),
        "claim_text": row["statement_text"],
        "modality": row["modality"],
        "knowledge_state": row["current_state"],
        "evidence_group_count": row["evidence_group_count"],
        "as_of_at": as_of_at,
        "role": row.get("role"),
        "connections": row.get("connections", []),
    }


def _connection(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": row["kind"],
        "target_id": str(row["target_id"]),
        "position": row["position"],
        "label": row["label"],
        "relation": None,
    }
    if row["kind"] != "RELATION":
        return result

    def node(prefix: str) -> dict[str, Any]:
        return {
            "node_id": str(row[f"{prefix}_node_id"]),
            "name": row[f"{prefix}_node_name"],
            "node_type": {
                "code": row[f"{prefix}_node_type_code"],
                "display_name": row[f"{prefix}_node_type_display_name"],
            },
        }

    result["relation"] = {
        "relation_id": str(row["target_id"]),
        "display_name": row["relation_display_name"],
        "directionality": row["directionality"],
        "source_node": node("source"),
        "target_node": node("target"),
        "other_node": node("other"),
        "stance": row["position"],
    }
    return result


def _connections(
    session: Session,
    *,
    node_id: int,
    basis_ids: list[int],
    start_at: datetime | None,
    as_of_at: datetime,
    claim_id: int,
) -> list[dict[str, Any]]:
    rows = queries.claim_connections(
        session,
        {
            "node_id": node_id,
            "basis_ids": basis_ids,
            "start_at": start_at,
            "as_of_at": as_of_at,
            "claim_id": claim_id,
        },
    )
    return [_connection(row) for row in rows]


def list_claims(
    session: Session, node_id: int, window: TimeWindow, cursor: str | None, limit: int
) -> dict[str, Any]:
    context, ids, complete = _context(session, node_id)
    if not complete:
        raise PanelNotReadyError
    scope = {
        "node_id": node_id,
        "window": window.value,
        "document_id": context["node_search_document_id"],
    }
    after_id, as_of_at = _position(cursor, "panel-claims", scope, datetime.now(UTC))
    params: dict[str, Any] = {
        "node_id": node_id,
        "basis_ids": ids,
        "start_at": _start(as_of_at, window),
        "as_of_at": as_of_at,
        "after_id": after_id,
        "limit": limit + 1,
    }
    rows = queries.node_claims(session, params)
    items = []
    for row in rows[:limit]:
        row["connections"] = _connections(
            session,
            node_id=node_id,
            basis_ids=ids,
            start_at=params["start_at"],
            as_of_at=as_of_at,
            claim_id=row["claim_id"],
        )
        items.append(_claim(row, as_of_at))
    return {
        "items": items,
        "next_cursor": _next(rows, limit, "claim_id", "panel-claims", scope, as_of_at),
    }


def _trace(
    row: dict[str, Any], start_at: datetime | None, as_of_at: datetime
) -> dict[str, Any]:
    published = row["published_at"]
    return {
        "trace_id": str(row["observation_id"]),
        "source": {
            k: row[k]
            for k in (
                "title",
                "publisher_name",
                "published_at",
                "published_precision",
                "canonical_url",
            )
        },
        "quote_text": row["quote_text"],
        "locator": {k: row[k] for k in ("paragraph_number", "start_char", "end_char")},
        "period_role": "UNKNOWN"
        if published is None
        else (
            "IN_WINDOW"
            if (start_at is None or start_at <= published) and published < as_of_at
            else "BACKGROUND"
        ),
    }


def claim_evidence(
    session: Session,
    node_id: int,
    claim_id: int,
    window: TimeWindow,
    as_of_at: datetime,
    cursor: str | None,
) -> dict[str, Any]:
    context, ids, complete = _context(session, node_id)
    if not complete:
        raise PanelNotReadyError
    if claim_id not in ids:
        raise PanelNotFoundError
    scope = {
        "node_id": node_id,
        "claim_id": claim_id,
        "document_id": context["node_search_document_id"],
        "window": window.value,
        "as_of_at": as_of_at.isoformat(),
    }
    after_id, _ = _position(cursor, "panel-traces", scope, as_of_at)
    rows = queries.trace_rows(session, claim_id, after_id)
    return {
        "items": [_trace(row, _start(as_of_at, window), as_of_at) for row in rows[:20]],
        "next_cursor": _next(
            rows, 20, "observation_id", "panel-traces", scope, as_of_at
        ),
    }


def _question_set(
    session: Session, node_id: int, window: TimeWindow
) -> tuple[dict[str, Any], list[int]]:
    if window is TimeWindow.ALL_TIME:
        raise PanelNotReadyError
    context, ids, complete = _context(session, node_id)
    group = queries.question_set(session, context["node_context_id"], window.value)
    if not complete or group is None:
        raise PanelNotReadyError
    window_start = _start(group["as_of_at"], window)
    if window_start is None or not queries.valid_questions(
        session,
        group["question_set_id"],
        ids,
        window_start,
        group["as_of_at"],
    ):
        raise PanelNotReadyError
    return group, ids


def list_questions(
    session: Session, node_id: int, window: TimeWindow, cursor: str | None
) -> dict[str, Any]:
    group, _ = _question_set(session, node_id, window)
    scope = {
        "set_id": group["question_set_id"],
        "node_id": node_id,
        "window": window.value,
    }
    after_order, stamp = _position(cursor, "panel-questions", scope, group["as_of_at"])
    if stamp != group["as_of_at"]:
        raise InvalidCursorError
    rows = queries.questions(session, group["question_set_id"], after_order, 5)
    return {
        "items": [
            {
                "question_id": str(row["question_id"]),
                "question_text": row["question_text"],
            }
            for row in rows[:4]
        ],
        "next_cursor": _next(rows, 4, "display_order", "panel-questions", scope, stamp),
    }


def _referenced_claims(
    session: Session,
    node_id: int,
    basis_ids: list[int],
    rows: list[dict[str, Any]],
    window: TimeWindow,
    as_of_at: datetime,
) -> list[dict[str, Any]]:
    start_at = _start(as_of_at, window)
    if start_at is None:
        raise PanelNotReadyError
    claims: list[dict[str, Any]] = []
    for row in rows:
        enriched = row | queries.claim_counts(
            session, row["claim_id"], start_at, as_of_at
        )
        enriched["connections"] = _connections(
            session,
            node_id=node_id,
            basis_ids=basis_ids,
            start_at=start_at,
            as_of_at=as_of_at,
            claim_id=row["claim_id"],
        )
        claims.append(_claim(enriched, as_of_at))
    return claims


def read_question(session: Session, question_id: int) -> dict[str, Any]:
    row = queries.question(session, question_id)
    if row is None:
        raise PanelNotFoundError
    window = TimeWindow(row["time_window"])
    group, ids = _question_set(session, row["node_id"], window)
    if row["question_set_id"] != group["question_set_id"]:
        raise PanelNotFoundError
    section_id = None
    if row["section_id"] is not None:
        section_id = _visible_section(
            session, row["node_id"], window, row["section_id"]
        )
    return {
        "question_id": str(question_id),
        "question_text": row["question_text"],
        "answer": row["answer_text"],
        "caveat": row["caveat_text"],
        "node_id": str(row["node_id"]),
        "time_window": window.value,
        "as_of_at": row["as_of_at"],
        "section_id": section_id,
        "claims": _referenced_claims(
            session,
            row["node_id"],
            ids,
            queries.question_claims(session, question_id),
            window,
            row["as_of_at"],
        ),
    }


def _report(
    session: Session, node_id: int, window: TimeWindow
) -> tuple[dict[str, Any], Any, list[dict[str, Any]], list[int]]:
    context, ids, complete = _context(session, node_id)
    bundle = insight_queries.get_bundle(session, node_id)
    if bundle is None or not complete:
        raise PanelNotReadyError
    group = queries.report_window(
        session, node_id, context["node_search_document_id"], bundle[1], window.value
    )
    if group is None:
        raise PanelNotReadyError
    if group["node_insight_id"] is None:
        return group, None, [], ids
    reports = list_node_insights(session, node_id, window)
    report = next(
        (r for r in reports if r.node_insight_id == group["node_insight_id"]), None
    )
    if report is None or report.evidence_group_count == 0:
        raise PanelNotReadyError
    sections = queries.sections(session, report.node_insight_id)
    _, evidence = get_insight(session, report.node_insight_id)
    parent_claims = {r.claim_id for r in evidence}
    if not sections:
        raise PanelNotReadyError
    for section in sections:
        refs = queries.section_claims(session, section["section_id"])
        if not refs or not any(r["role"] == "KEY_CLAIM" for r in refs):
            raise PanelNotReadyError
        if any(
            r["claim_id"] not in parent_claims or r["claim_id"] not in ids for r in refs
        ):
            raise PanelNotReadyError
        section["claims"] = refs
    return group, report, sections, ids


def _visible_section(
    session: Session, node_id: int, window: TimeWindow, section_id: int
) -> str | None:
    try:
        _, _, sections, _ = _report(session, node_id, window)
    except PanelNotReadyError:
        return None
    return (
        str(section_id)
        if any(s["section_id"] == section_id for s in sections)
        else None
    )


def read_report(
    session: Session, node_id: int, window: TimeWindow, *, detail: bool
) -> dict[str, Any]:
    group, report, sections, ids = _report(session, node_id, window)
    if report is None:
        return {"items": [], "next_cursor": None}
    item = {
        "report_id": str(report.node_insight_id),
        "node_id": str(node_id),
        "title": report.title,
        "summary": report.summary_text,
        "as_of_at": group["as_of_at"],
        "time_window": window.value,
        "evidence_group_count": report.evidence_group_count,
        "sections": [
            {"section_id": str(s["section_id"]), "title": s["title"]} for s in sections
        ],
    }
    if detail:
        item["conclusion"] = report.synthesis_text
        item["caveat"] = report.caveat_text
        item["sections"] = [
            {
                "section_id": str(s["section_id"]),
                "title": s["title"],
                "synthesis": s["synthesis_text"],
                "caveat": s["caveat_text"],
                "claims": _referenced_claims(
                    session,
                    node_id,
                    ids,
                    s["claims"],
                    window,
                    group["as_of_at"],
                ),
            }
            for s in sections
        ]
    return {"items": [item], "next_cursor": None}
