from dataclasses import dataclass
from unicodedata import normalize

from sqlalchemy.orm import Session

from ontology_map.db.search import (
    SearchNodeRow,
    list_exact_alias_matches,
    list_identity_text_matches,
    list_knowledge_text_matches,
)
from ontology_map.exploration import NodeType

MAX_SEARCH_LIMIT = 20


class InvalidSearchQueryError(Exception):
    pass


@dataclass(frozen=True)
class SearchNode:
    node_id: int
    name: str
    node_type: NodeType


def _search_node(row: SearchNodeRow) -> SearchNode:
    return SearchNode(
        node_id=row.node_id,
        name=row.name,
        node_type=NodeType(
            code=row.node_type_code,
            display_name=row.node_type_display_name,
        ),
    )


def search_nodes(session: Session, query: str, limit: int = 5) -> list[SearchNode]:
    normalized_query = normalize("NFC", query).strip()
    if not normalized_query or not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise InvalidSearchQueryError

    seen_node_ids: set[int] = set()
    results: list[SearchNode] = []
    buckets = (
        list_exact_alias_matches(session, normalized_query, limit),
        list_identity_text_matches(session, normalized_query, limit),
        list_knowledge_text_matches(session, normalized_query, limit),
    )
    for rows in buckets:
        for row in rows:
            if row.node_id in seen_node_ids:
                continue
            seen_node_ids.add(row.node_id)
            results.append(_search_node(row))
            if len(results) == limit:
                return results
    return results
