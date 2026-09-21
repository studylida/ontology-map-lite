from datetime import UTC, datetime
from hashlib import sha256
from struct import pack

import sqlalchemy as sa
from sqlalchemy import Connection

from ontology_map.db.schema import (
    agent_attempt,
    claim,
    claim_observation,
    claim_relation,
    event_temporal_basis,
    event_temporal_extent,
    evidence_group,
    followup_question,
    knowledge_item,
    lint_policy_rule,
    lint_policy_version,
    lint_rule,
    model_task,
    node,
    node_alias,
    node_alias_evidence,
    node_context,
    node_insight,
    node_insight_claim,
    node_search_document,
    node_type,
    observation,
    output_schema_definition,
    promotion_batch,
    publication_affected_node,
    relation,
    relation_endpoint_rule,
    relation_type,
    relation_type_revision,
    search_document_basis,
    source_document,
)
from ontology_map.db.session import get_engine
from ontology_map.settings import get_settings

FIXTURE_MARKER = "hbf-fixture:sk-hbf"
AS_OF_AT = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)

NODE_DEFINITIONS = (
    ("sk_hynix", "SK하이닉스", "COMPANY", "sk_hbf"),
    ("sandisk", "SanDisk", "COMPANY", "sandisk_hbf"),
    ("hbf", "HBF", "TECHNOLOGY", "sk_hbf"),
    ("ucie", "UCIe", "TECHNOLOGY", "hbf_ucie"),
    ("fms_2026", "FMS 2026 HBF 발표", "EVENT", "sk_fms"),
)

DOCUMENT_DEFINITIONS = (
    (
        "sk_hbf",
        FIXTURE_MARKER,
        "SK하이닉스 HBF 공개 자료",
        "SK하이닉스는 HBF 기술을 AI 메모리 생태계에 공개했다.",
        datetime(2026, 8, 12, tzinfo=UTC),
    ),
    (
        "sandisk_hbf",
        "hbf-fixture:sandisk-hbf",
        "SanDisk HBF 공개 자료",
        "SanDisk는 HBF 생태계 논의에 참여했다.",
        datetime(2026, 8, 13, tzinfo=UTC),
    ),
    (
        "hbf_ucie",
        "hbf-fixture:hbf-ucie",
        "HBF와 UCIe 공개 자료",
        "HBF와 UCIe는 계층형 메모리 연결 맥락에서 함께 논의됐다.",
        datetime(2026, 8, 14, tzinfo=UTC),
    ),
    (
        "sk_fms",
        "hbf-fixture:sk-fms-2026",
        "FMS 2026 HBF 발표 자료",
        "SK하이닉스의 FMS 2026 HBF 발표가 2026년 8월 5일 공개됐다.",
        datetime(2026, 8, 15, tzinfo=UTC),
    ),
)

RELATION_DEFINITIONS = (
    ("sk_hynix_hbf", "sk_hynix", "hbf", "sk_hbf"),
    ("sandisk_hbf", "sandisk", "hbf", "sandisk_hbf"),
    ("hbf_ucie", "hbf", "ucie", "hbf_ucie"),
    ("sk_hynix_fms", "sk_hynix", "fms_2026", "sk_fms"),
)


def _digest(value: str) -> bytes:
    return sha256(value.encode()).digest()


def _frame_text(value: str) -> bytes:
    encoded = value.encode()
    return pack(">Q", len(encoded)) + encoded


def _relation_key(source_node_id: int, revision_id: int, target_node_id: int) -> bytes:
    source, target = sorted((source_node_id, target_node_id))
    return sha256(b"REL1" + pack(">qqq", source, revision_id, target)).digest()


def _search_document_hash(
    node_id: int,
    identity_text: str,
    knowledge_text: str,
    basis_ids: list[int],
) -> bytes:
    payload = (
        b"NSD1"
        + pack(">q", node_id)
        + _frame_text(identity_text)
        + _frame_text(knowledge_text)
        + pack(">Q", len(basis_ids))
        + b"".join(pack(">q", value) for value in sorted(basis_ids))
    )
    return sha256(payload).digest()


def _task_cache_key(
    task_kind: str,
    input_hash: bytes,
    output_contract_id: int | None,
    model_version: str,
    prompt_version: str | None,
) -> bytes:
    payload = (
        b"TASK1"
        + _frame_text(task_kind)
        + input_hash
        + pack(">q", output_contract_id or 0)
        + _frame_text(model_version)
        + _frame_text(prompt_version or "")
    )
    return sha256(payload).digest()


_FIXTURE_NODE_TYPES = (
    ("PERSON", "사람"),
    ("COMPANY", "회사"),
    ("TECHNOLOGY", "기술"),
    ("TOPIC", "주제"),
    ("EVENT", "사건"),
)
_FIXTURE_NODE_CREATION_RULE = "공개 원문 근거와 대표 alias가 필요하다."


def _fixture_node_types(connection: Connection) -> dict[str, int]:
    node_type_ids: dict[str, int] = {}
    for code, display_name in _FIXTURE_NODE_TYPES:
        row = (
            connection.execute(
                sa.select(
                    node_type.c.node_type_id,
                    node_type.c.display_name,
                    node_type.c.creation_rule,
                    node_type.c.is_active,
                ).where(node_type.c.node_type_code == code)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            node_type_id = int(
                connection.execute(
                    node_type.insert()
                    .values(
                        node_type_code=code,
                        display_name=display_name,
                        creation_rule=_FIXTURE_NODE_CREATION_RULE,
                        is_active=True,
                    )
                    .returning(node_type.c.node_type_id)
                ).scalar_one()
            )
        else:
            if (
                str(row["display_name"]) != display_name
                or str(row["creation_rule"]) != _FIXTURE_NODE_CREATION_RULE
                or not bool(row["is_active"])
            ):
                raise RuntimeError(
                    f"기존 node_type {code}가 HBF fixture prerequisite와 "
                    "호환되지 않습니다."
                )
            node_type_id = int(row["node_type_id"])
        node_type_ids[code] = node_type_id
    return node_type_ids


def _seed_reference_data(
    connection: Connection,
) -> tuple[dict[str, int], int, dict[str, int], int]:
    node_type_ids = _fixture_node_types(connection)

    lint_rule_id = int(
        connection.execute(
            lint_rule.insert()
            .values(
                rule_code="EVIDENCE_TRACE_COMPLETE",
                display_name="근거 경로 완결성",
                description="공개 지식이 원문 근거까지 이어지는지 검사한다.",
                evaluation_scope="BOTH",
            )
            .returning(lint_rule.c.lint_rule_id)
        ).scalar_one()
    )
    policy_id = int(
        connection.execute(
            lint_policy_version.insert()
            .values(
                version_no=1,
                validator_version="hbf-fixture-v1",
                is_active=True,
                activated_at=AS_OF_AT,
            )
            .returning(lint_policy_version.c.lint_policy_version_id)
        ).scalar_one()
    )
    connection.execute(
        lint_policy_rule.insert().values(
            lint_policy_version_id=policy_id,
            lint_rule_id=lint_rule_id,
            severity="BLOCKING",
        )
    )

    relation_type_id = int(
        connection.execute(
            relation_type.insert()
            .values(relation_code="PUBLICLY_ASSOCIATED_WITH")
            .returning(relation_type.c.relation_type_id)
        ).scalar_one()
    )
    relation_revision_id = int(
        connection.execute(
            relation_type_revision.insert()
            .values(
                relation_type_id=relation_type_id,
                version_no=1,
                display_name="공개 관계",
                directionality="SYMMETRIC",
                is_active=True,
            )
            .returning(relation_type_revision.c.relation_type_revision_id)
        ).scalar_one()
    )
    endpoint_pairs = {
        tuple(sorted((node_type_ids[source], node_type_ids[target])))
        for source, target in (
            ("COMPANY", "COMPANY"),
            ("COMPANY", "TECHNOLOGY"),
            ("COMPANY", "EVENT"),
            ("TECHNOLOGY", "TECHNOLOGY"),
            ("EVENT", "TECHNOLOGY"),
        )
    }
    connection.execute(
        relation_endpoint_rule.insert(),
        [
            {
                "relation_type_revision_id": relation_revision_id,
                "source_node_type_id": source,
                "target_node_type_id": target,
            }
            for source, target in sorted(endpoint_pairs)
        ],
    )

    contract_ids: dict[str, int] = {}
    for task_kind in ("NODE_CONTEXT", "FOLLOWUP_QUESTIONS", "NODE_INSIGHT"):
        existing = connection.scalar(
            sa.select(output_schema_definition.c.output_schema_definition_id).where(
                output_schema_definition.c.task_kind == task_kind,
                output_schema_definition.c.is_active,
            )
        )
        if existing is not None:
            contract_ids[task_kind] = int(existing)
            continue
        contract_ids[task_kind] = int(
            connection.execute(
                output_schema_definition.insert()
                .values(
                    task_kind=task_kind,
                    version_no=1,
                    schema_json={"type": "object"},
                    is_active=True,
                )
                .returning(output_schema_definition.c.output_schema_definition_id)
            ).scalar_one()
        )

    batch_id = int(
        connection.execute(
            promotion_batch.insert()
            .values(
                lint_policy_version_id=policy_id,
                promotion_status="COMMITTED",
                publication_status="READY",
                started_at=AS_OF_AT,
                committed_at=AS_OF_AT,
                ready_at=AS_OF_AT,
            )
            .returning(promotion_batch.c.promotion_batch_id)
        ).scalar_one()
    )
    return node_type_ids, relation_revision_id, contract_ids, batch_id


def _seed_evidence(connection: Connection) -> dict[str, int]:
    observation_ids: dict[str, int] = {}
    for key, source_key, title, body, published_at in DOCUMENT_DEFINITIONS:
        group_id = int(
            connection.execute(
                evidence_group.insert().returning(evidence_group.c.evidence_group_id)
            ).scalar_one()
        )
        document_id = int(
            connection.execute(
                source_document.insert()
                .values(
                    evidence_group_id=group_id,
                    source_key=source_key,
                    version_no=1,
                    canonical_url=f"https://example.com/ontology-map/{key}",
                    publisher_name="ontology-map 개발 fixture",
                    title=title,
                    original_language="ko",
                    normalized_body=body,
                    body_hash=_digest(body),
                    published_at=published_at,
                    published_precision="DAY",
                    modified_precision="UNKNOWN",
                    last_checked_at=AS_OF_AT,
                    last_check_status="SUCCESS",
                )
                .returning(source_document.c.source_document_id)
            ).scalar_one()
        )
        observation_ids[key] = int(
            connection.execute(
                observation.insert()
                .values(
                    source_document_id=document_id,
                    start_char=0,
                    end_char=len(body),
                    quote_text=body,
                    quote_hash=_digest(body),
                    paragraph_number=1,
                    observed_at=AS_OF_AT,
                )
                .returning(observation.c.observation_id)
            ).scalar_one()
        )
    return observation_ids


def _seed_nodes(
    connection: Connection,
    node_type_ids: dict[str, int],
    observation_ids: dict[str, int],
    batch_id: int,
) -> tuple[dict[str, int], dict[str, str]]:
    node_ids: dict[str, int] = {}
    node_names: dict[str, str] = {}
    for key, name, type_code, evidence_key in NODE_DEFINITIONS:
        node_id = int(
            connection.execute(
                knowledge_item.insert()
                .values(
                    item_kind="NODE",
                    current_state="EVIDENCE_VERIFIED",
                    promotion_batch_id=batch_id,
                )
                .returning(knowledge_item.c.knowledge_item_id)
            ).scalar_one()
        )
        connection.execute(
            node.insert().values(
                node_id=node_id,
                node_type_id=node_type_ids[type_code],
            )
        )
        alias_id = int(
            connection.execute(
                node_alias.insert()
                .values(
                    node_id=node_id,
                    alias_text=name,
                    language="ko"
                    if any("가" <= char <= "힣" for char in name)
                    else "en",
                    is_preferred=True,
                )
                .returning(node_alias.c.node_alias_id)
            ).scalar_one()
        )
        connection.execute(
            node_alias_evidence.insert().values(
                node_alias_id=alias_id,
                observation_id=observation_ids[evidence_key],
            )
        )
        node_ids[key] = node_id
        node_names[key] = name

    connection.execute(
        event_temporal_extent.insert().values(
            event_node_id=node_ids["fms_2026"],
            start_at=datetime(2026, 8, 5, tzinfo=UTC),
            end_at=datetime(2026, 8, 5, tzinfo=UTC),
            start_precision="DAY",
            end_precision="DAY",
        )
    )
    return node_ids, node_names


def _seed_relations(
    connection: Connection,
    node_ids: dict[str, int],
    node_names: dict[str, str],
    observation_ids: dict[str, int],
    relation_revision_id: int,
    batch_id: int,
) -> tuple[dict[str, int], dict[str, int]]:
    relation_ids: dict[str, int] = {}
    claim_ids: dict[str, int] = {}
    for key, source_key, target_key, evidence_key in RELATION_DEFINITIONS:
        source_name = node_names[source_key]
        target_name = node_names[target_key]
        relation_id = int(
            connection.execute(
                knowledge_item.insert()
                .values(
                    item_kind="RELATION",
                    current_state="EVIDENCE_VERIFIED",
                    promotion_batch_id=batch_id,
                )
                .returning(knowledge_item.c.knowledge_item_id)
            ).scalar_one()
        )
        source_node_id, target_node_id = sorted(
            (node_ids[source_key], node_ids[target_key])
        )
        connection.execute(
            relation.insert().values(
                relation_id=relation_id,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relation_type_revision_id=relation_revision_id,
                relation_identity_key=_relation_key(
                    source_node_id,
                    relation_revision_id,
                    target_node_id,
                ),
            )
        )

        claim_id = int(
            connection.execute(
                knowledge_item.insert()
                .values(
                    item_kind="CLAIM",
                    current_state="EVIDENCE_VERIFIED",
                    promotion_batch_id=batch_id,
                )
                .returning(knowledge_item.c.knowledge_item_id)
            ).scalar_one()
        )
        connection.execute(
            claim.insert().values(
                claim_id=claim_id,
                statement_text=f"{source_name}와 {target_name}의 공개 관계가 관측됐다.",
                language="ko",
                modality="FACT",
                asserted_from_precision="UNKNOWN",
                asserted_to_precision="UNKNOWN",
            )
        )
        connection.execute(
            claim_relation.insert().values(
                claim_id=claim_id,
                relation_id=relation_id,
                stance="SUPPORT",
            )
        )
        connection.execute(
            claim_observation.insert().values(
                claim_id=claim_id,
                observation_id=observation_ids[evidence_key],
            )
        )
        relation_ids[key] = relation_id
        claim_ids[key] = claim_id

    event_claim_id = int(
        connection.execute(
            knowledge_item.insert()
            .values(
                item_kind="CLAIM",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=batch_id,
            )
            .returning(knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    connection.execute(
        claim.insert().values(
            claim_id=event_claim_id,
            statement_text="FMS 2026 HBF 발표는 2026년 8월 5일에 열렸다.",
            language="ko",
            modality="FACT",
            asserted_from=datetime(2026, 8, 5, tzinfo=UTC),
            asserted_to=datetime(2026, 8, 5, tzinfo=UTC),
            asserted_from_precision="DAY",
            asserted_to_precision="DAY",
        )
    )
    connection.execute(
        claim_observation.insert().values(
            claim_id=event_claim_id,
            observation_id=observation_ids["sk_fms"],
        )
    )
    connection.execute(
        event_temporal_basis.insert().values(
            event_node_id=node_ids["fms_2026"],
            claim_id=event_claim_id,
        )
    )
    claim_ids["fms_time"] = event_claim_id
    return relation_ids, claim_ids


def _insert_successful_task(
    connection: Connection,
    task_kind: str,
    input_hash: bytes,
    output_contract_id: int | None,
    model_version: str,
    prompt_version: str | None,
) -> int:
    task_id = int(
        connection.execute(
            model_task.insert()
            .values(
                task_kind=task_kind,
                input_hash=input_hash,
                output_schema_definition_id=output_contract_id,
                model_version=model_version,
                prompt_version=prompt_version,
                cache_key=_task_cache_key(
                    task_kind,
                    input_hash,
                    output_contract_id,
                    model_version,
                    prompt_version,
                ),
                status="SUCCESS",
                attempt_count=1,
                finished_at=AS_OF_AT,
            )
            .returning(model_task.c.model_task_id)
        ).scalar_one()
    )
    connection.execute(
        agent_attempt.insert().values(
            model_task_id=task_id,
            attempt_no=1,
            outcome="SUCCESS",
            attempted_at=AS_OF_AT,
        )
    )
    return task_id


def _graph_inputs(
    node_ids: dict[str, int],
    relation_ids: dict[str, int],
    claim_ids: dict[str, int],
) -> tuple[dict[str, list[str]], dict[str, list[int]], dict[str, int]]:
    neighbor_keys: dict[str, list[str]] = {key: [] for key in node_ids}
    basis_ids = {key: [value] for key, value in node_ids.items()}
    insight_claim_ids: dict[str, int] = {}
    for relation_key, source_key, target_key, _evidence_key in RELATION_DEFINITIONS:
        relation_id = relation_ids[relation_key]
        claim_id = claim_ids[relation_key]
        neighbor_keys[source_key].append(target_key)
        neighbor_keys[target_key].append(source_key)
        basis_ids[source_key].extend((relation_id, claim_id))
        basis_ids[target_key].extend((relation_id, claim_id))
        insight_claim_ids.setdefault(source_key, claim_id)
        insight_claim_ids.setdefault(target_key, claim_id)
    basis_ids["fms_2026"].append(claim_ids["fms_time"])
    return neighbor_keys, basis_ids, insight_claim_ids


def _seed_node_artifacts(
    connection: Connection,
    node_ids: dict[str, int],
    node_names: dict[str, str],
    relation_ids: dict[str, int],
    claim_ids: dict[str, int],
    contract_ids: dict[str, int],
    batch_id: int,
) -> None:
    neighbor_keys, basis_ids_by_node, insight_claim_ids = _graph_inputs(
        node_ids,
        relation_ids,
        claim_ids,
    )
    for node_key, node_name, _type_code, _evidence_key in NODE_DEFINITIONS:
        neighbors = neighbor_keys[node_key]
        neighbor_names = [node_names[key] for key in neighbors]
        identity_text = node_name
        knowledge_text = (
            f"{node_name}의 공개 근거와 "
            + ", ".join(neighbor_names)
            + " 연결을 다룬다."
        )
        basis_ids = sorted(set(basis_ids_by_node[node_key]))
        search_document_id = int(
            connection.execute(
                node_search_document.insert()
                .values(
                    node_id=node_ids[node_key],
                    identity_text=identity_text,
                    knowledge_text=knowledge_text,
                    input_hash=_search_document_hash(
                        node_ids[node_key],
                        identity_text,
                        knowledge_text,
                        basis_ids,
                    ),
                    generator_version="hbf-fixture-v1",
                )
                .returning(node_search_document.c.node_search_document_id)
            ).scalar_one()
        )
        connection.execute(
            search_document_basis.insert(),
            [
                {
                    "node_search_document_id": search_document_id,
                    "knowledge_item_id": basis_id,
                }
                for basis_id in basis_ids
            ],
        )

        context_task_id = _insert_successful_task(
            connection,
            "NODE_CONTEXT",
            _digest(f"context:{search_document_id}"),
            contract_ids["NODE_CONTEXT"],
            "fixture:node-context-v1",
            "hbf-fixture-v1",
        )
        context_id = int(
            connection.execute(
                node_context.insert()
                .values(
                    node_id=node_ids[node_key],
                    node_search_document_id=search_document_id,
                    model_task_id=context_task_id,
                    language="ko",
                    context_text=(
                        f"{node_name} 중심의 공개 관측과 확인된 관계를 탐색합니다."
                    ),
                )
                .returning(node_context.c.node_context_id)
            ).scalar_one()
        )

        question_task_id = _insert_successful_task(
            connection,
            "FOLLOWUP_QUESTIONS",
            _digest(f"questions:{context_id}"),
            contract_ids["FOLLOWUP_QUESTIONS"],
            "fixture:followup-questions-v1",
            "hbf-fixture-v1",
        )
        target_keys = (neighbors + [node_key, node_key])[:2]
        connection.execute(
            followup_question.insert(),
            [
                {
                    "node_context_id": context_id,
                    "model_task_id": question_task_id,
                    "slot": slot,
                    "question_text": (
                        f"{node_names[target_key]} 중심의 공개 근거를 살펴보기"
                    ),
                    "target_node_id": node_ids[target_key],
                }
                for slot, target_key in enumerate(target_keys, start=1)
            ],
        )

        insight_task_id = _insert_successful_task(
            connection,
            "NODE_INSIGHT",
            _digest(f"insight:{node_ids[node_key]}:{search_document_id}"),
            contract_ids["NODE_INSIGHT"],
            "fixture:node-insight-v1",
            "hbf-fixture-v1",
        )
        for time_window, window_label in (
            ("RECENT_90_DAYS", "최근 90일"),
            ("RECENT_1_YEAR", "최근 1년"),
        ):
            insight_id = int(
                connection.execute(
                    node_insight.insert()
                    .values(
                        node_id=node_ids[node_key],
                        node_search_document_id=search_document_id,
                        model_task_id=insight_task_id,
                        time_window=time_window,
                        as_of_at=AS_OF_AT,
                        slot=1,
                        title=f"{node_name}의 {window_label} 공개 흐름",
                        summary_text=(
                            f"{node_name}와 연결된 공개 근거를 {window_label} 범위에서 "
                            "확인할 수 있습니다."
                        ),
                        synthesis_text=(
                            "연결된 근거는 탐색 방향을 제시하지만 관계의 중요도나 "
                            "확신 점수를 뜻하지 않습니다."
                        ),
                        caveat_text=(
                            "개발 fixture이므로 실제 사업 성과나 미래 결과를 판단할 "
                            "수 없습니다."
                        ),
                    )
                    .returning(node_insight.c.node_insight_id)
                ).scalar_one()
            )
            connection.execute(
                node_insight_claim.insert().values(
                    node_insight_id=insight_id,
                    claim_id=insight_claim_ids[node_key],
                    role="KEY_CLAIM",
                    display_order=1,
                )
            )

        connection.execute(
            publication_affected_node.insert().values(
                promotion_batch_id=batch_id,
                node_id=node_ids[node_key],
                node_search_document_id=search_document_id,
                node_context_id=context_id,
                node_insight_model_task_id=insight_task_id,
            )
        )


def _current_fixture_nodes(connection: Connection) -> dict[str, int]:
    rows = connection.execute(
        sa.select(node_alias.c.alias_text, node_alias.c.node_id).where(
            node_alias.c.alias_text.in_(
                [definition[1] for definition in NODE_DEFINITIONS]
            )
        )
    )
    ids_by_name = {str(name): int(node_id) for name, node_id in rows}
    node_ids = {
        key: ids_by_name[name]
        for key, name, _type_code, _evidence_key in NODE_DEFINITIONS
        if name in ids_by_name
    }
    if len(node_ids) != len(NODE_DEFINITIONS):
        raise RuntimeError("기존 HBF fixture가 불완전합니다. 개발 DB를 다시 만드세요.")
    return node_ids


def _load_hbf_fixture(connection: Connection) -> tuple[bool, dict[str, int]]:
    marker_exists = connection.scalar(
        sa.select(source_document.c.source_document_id).where(
            source_document.c.source_key == FIXTURE_MARKER
        )
    )
    if marker_exists is not None:
        return False, _current_fixture_nodes(connection)

    node_type_ids, relation_revision_id, contract_ids, batch_id = _seed_reference_data(
        connection
    )
    observation_ids = _seed_evidence(connection)
    node_ids, node_names = _seed_nodes(
        connection,
        node_type_ids,
        observation_ids,
        batch_id,
    )
    relation_ids, claim_ids = _seed_relations(
        connection,
        node_ids,
        node_names,
        observation_ids,
        relation_revision_id,
        batch_id,
    )
    _seed_node_artifacts(
        connection,
        node_ids,
        node_names,
        relation_ids,
        claim_ids,
        contract_ids,
        batch_id,
    )
    return True, node_ids


def load_hbf_fixture() -> tuple[bool, dict[str, int]]:
    if get_settings().environment != "development":
        raise RuntimeError("HBF fixture는 development 환경에서만 실행할 수 있습니다.")

    with get_engine().begin() as connection:
        return _load_hbf_fixture(connection)


def main() -> None:
    created, node_ids = load_hbf_fixture()
    message = (
        "HBF 개발 fixture를 생성했습니다."
        if created
        else "HBF 개발 fixture가 이미 존재합니다."
    )
    print(message)
    for key, node_id in node_ids.items():
        print(f"{key}={node_id}")


if __name__ == "__main__":
    main()
