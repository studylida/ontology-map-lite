"""Runtime contracts shared by the #215 initial-publication modules."""

from dataclasses import dataclass
from datetime import datetime

from ontology_map.followup_generation_contracts import PreparedFollowup
from ontology_map.insight_generation_contracts import PreparedInsightBundle


class InitialPublicationError(ValueError):
    """Base deterministic failure for initial publication integration."""


class PublicationStateError(InitialPublicationError):
    """The requested operation is incompatible with publication lifecycle state."""


class SearchDocumentPreparationError(InitialPublicationError):
    """The deterministic current search-document basis cannot be prepared."""


class NodeContextTaskError(InitialPublicationError):
    """NODE_CONTEXT task/result identity or generation validation failed."""


class PublicationNotReady(InitialPublicationError):
    """The existing #46/#163 completeness contract is not yet satisfied."""

    def __init__(self, reasons: tuple[str, ...]):
        super().__init__("publication is incomplete: " + "; ".join(reasons))
        self.reasons = reasons


@dataclass(frozen=True)
class InitialPublicationStart:
    promotion_batch_id: int
    affected_node_ids: tuple[int, ...]
    started: bool
    publication_status: str


@dataclass(frozen=True)
class SearchDocumentSnapshot:
    node_id: int
    identity_text: str
    knowledge_text: str
    basis_ids: tuple[int, ...]
    input_hash: bytes


@dataclass(frozen=True)
class NodeContextTaskRef:
    model_task_id: int
    status: str


@dataclass(frozen=True)
class PreparedDerivedWork:
    promotion_batch_id: int
    node_id: int
    as_of_at: datetime
    followup_bundle: PreparedFollowup
    insight_bundle: PreparedInsightBundle


@dataclass(frozen=True)
class PublicationReadiness:
    ready: bool
    reasons: tuple[str, ...]
