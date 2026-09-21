from sqlalchemy.orm import Session

from ontology_map.db import insights as queries
from ontology_map.db.exploration import is_public_node
from ontology_map.exploration import (
    ExplorationNotFoundError,
    PublicationNotReadyError,
    TimeWindow,
)


class InsightNotFoundError(Exception):
    pass


def list_node_insights(
    session: Session, node_id: int, time_window: TimeWindow
) -> list[queries.InsightRow]:
    if not is_public_node(session, node_id):
        raise ExplorationNotFoundError
    bundle = queries.get_bundle(session, node_id)
    if bundle is None:
        raise PublicationNotReadyError
    return queries.list_insights(session, node_id, time_window.value, bundle)


def get_insight(
    session: Session, insight_id: int
) -> tuple[queries.InsightRow, list[queries.InsightTraceRow]]:
    owner = queries.get_owner(session, insight_id)
    if owner is None:
        raise InsightNotFoundError
    try:
        items = list_node_insights(session, owner[0], TimeWindow(owner[1]))
    except ExplorationNotFoundError as error:
        raise InsightNotFoundError from error
    item = next((i for i in items if i.node_insight_id == insight_id), None)
    if item is None:
        raise InsightNotFoundError
    # HTTP read session은 REPEATABLE READ이므로 공개 검사와 Trace는 같은 snapshot이다.
    return item, queries.list_traces(session, insight_id)
