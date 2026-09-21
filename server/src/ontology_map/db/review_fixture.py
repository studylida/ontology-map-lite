"""개발용 화면 검토 자료. daa0bf2의 구조를 재현하며 실제 주장이 아니다."""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Connection

from ontology_map.db import schema as s
from ontology_map.db.fixture import (
    _digest,
    _insert_successful_task,
    _relation_key,
    _search_document_hash,
    load_hbf_fixture,
)
from ontology_map.db.session import get_engine
from ontology_map.settings import get_settings

PREFIX = "map-review-v1:"
ORIGINAL_NODES = [
    ("sk", "SK하이닉스", "COMPANY"),
    ("sandisk", "SanDisk", "COMPANY"),
    ("fms", "FMS 2026 HBF 발표", "EVENT"),
    ("standard", "HBF 표준화 착수", "EVENT"),
    ("kim", "김천성", "PERSON"),
    ("kang", "강욱성", "PERSON"),
    ("nand-event", "375단 4D NAND 공개", "EVENT"),
    ("ai-memory", "AI 메모리", "TOPIC"),
    ("tiered-memory", "계층형 메모리", "TECHNOLOGY"),
    ("hbf", "HBF", "TECHNOLOGY"),
    ("ucie", "UCIe", "TECHNOLOGY"),
    ("google", "Google", "COMPANY"),
    ("tenstorrent", "Tenstorrent", "COMPANY"),
    ("nand", "375단 4D NAND", "TECHNOLOGY"),
    ("ocp", "OCP 공개 표준", "TOPIC"),
    ("hbm", "HBM", "TECHNOLOGY"),
    ("ssd", "SSD", "TECHNOLOGY"),
    ("cpu", "CPU", "TECHNOLOGY"),
    ("gpu", "GPU", "TECHNOLOGY"),
    ("isolated", "미연결 공개 기술", "TECHNOLOGY"),
]
ORIGINAL_RELATIONS = [
    ("sk", "sandisk", 3, False),
    ("sk", "fms", 3, False),
    ("sk", "standard", 3, False),
    ("sk", "kim", 1, False),
    ("sk", "kang", 1, False),
    ("sk", "nand-event", 3, False),
    ("sk", "ai-memory", 6, False),
    ("sk", "tiered-memory", 3, False),
    ("fms", "hbf", 1, False),
    ("hbf", "ucie", 1, False),
    ("sandisk", "google", 1, False),
    ("sandisk", "tenstorrent", 1, False),
    ("nand-event", "nand", 1, False),
    ("standard", "ocp", 1, True),
    ("hbf", "sandisk", 3, False),
    ("hbf", "hbm", 6, False),
    ("hbf", "ssd", 3, False),
    ("hbf", "standard", 1, False),
    ("hbf", "ocp", 1, False),
    ("hbf", "ai-memory", 3, False),
    ("ucie", "cpu", 1, False),
    ("ucie", "gpu", 1, False),
    ("ai-memory", "tiered-memory", 3, False),
    ("tiered-memory", "hbm", 1, False),
]


def catalog() -> tuple[list[tuple[str, str, str]], list[tuple[str, str, int, bool]]]:
    nodes = ORIGINAL_NODES + [
        (f"extra-{i}", f"검토 노드 {i + 1:02}", "TOPIC") for i in range(80)
    ]
    relations = list(ORIGINAL_RELATIONS)
    for i in range(70):
        parent = "standard" if i < 10 else f"extra-{(i - 10) // 2}"
        relations.append((parent, f"extra-{i}", 1, False))
    # 별개 공개 node와 별개 연결 묶음도 포함한다.
    relations.extend(
        (f"extra-{i}", f"extra-{i + 1}", 1, False) for i in range(70, 78, 2)
    )
    return nodes, relations


def insert_id(c: Connection, table: sa.Table, **values: object) -> int:
    return int(
        c.execute(
            table.insert().values(**values).returning(*table.primary_key)
        ).scalar_one()
    )


def knowledge(c: Connection, kind: str, batch: int) -> int:
    return insert_id(
        c,
        s.knowledge_item,
        item_kind=kind,
        current_state="EVIDENCE_VERIFIED",
        promotion_batch_id=batch,
    )


def evidence(c: Connection, key: str, text: str, now: datetime) -> int:
    group = insert_id(c, s.evidence_group)
    doc = insert_id(
        c,
        s.source_document,
        evidence_group_id=group,
        source_key=PREFIX + key,
        version_no=1,
        canonical_url="https://example.invalid/map-review/" + key,
        publisher_name="개발용 지식맵 검토 자료 — 실제 주장 아님",
        title="[검토 자료] " + key,
        original_language="ko",
        normalized_body=text,
        body_hash=_digest(text),
        published_at=now,
        published_precision="DAY",
        modified_precision="UNKNOWN",
        last_checked_at=now,
        last_check_status="SUCCESS",
    )
    return insert_id(
        c,
        s.observation,
        source_document_id=doc,
        start_char=0,
        end_char=len(text),
        quote_text=text,
        quote_hash=_digest(text),
        paragraph_number=1,
        observed_at=now,
    )


def make_claim(c: Connection, batch: int, text: str, observations: list[int]) -> int:
    claim = knowledge(c, "CLAIM", batch)
    c.execute(
        s.claim.insert().values(
            claim_id=claim,
            statement_text=text,
            language="ko",
            modality="FACT",
            asserted_from_precision="UNKNOWN",
            asserted_to_precision="UNKNOWN",
        )
    )
    c.execute(
        s.claim_observation.insert(),
        [{"claim_id": claim, "observation_id": o} for o in observations],
    )
    return claim


def make_revision(c: Connection, types: dict[str, int], directed: bool) -> int:
    type_id = insert_id(
        c,
        s.relation_type,
        relation_code=PREFIX + ("DIRECTED" if directed else "SYMMETRIC"),
    )
    revision = insert_id(
        c,
        s.relation_type_revision,
        relation_type_id=type_id,
        version_no=1,
        display_name="검토 대상 연결" if directed else "검토 관계",
        directionality="DIRECTED" if directed else "SYMMETRIC",
        is_active=True,
    )
    pairs = [
        (a, b) for a in types.values() for b in types.values() if directed or a <= b
    ]
    c.execute(
        s.relation_endpoint_rule.insert(),
        [
            {
                "relation_type_revision_id": revision,
                "source_node_type_id": a,
                "target_node_type_id": b,
            }
            for a, b in pairs
        ],
    )
    return revision


def artifacts(
    c: Connection,
    node_id: int,
    name: str,
    basis: list[int],
    claims: list[int],
    neighbors: list[int],
    batch: int,
    contracts: dict[str, int],
) -> None:
    identity = name
    knowledge_text = (
        "개발용 배치·전환·근거 표시 시험 자료입니다. "
        "실제 사실이나 모델 출력이 아닙니다."
    )
    document = insert_id(
        c,
        s.node_search_document,
        node_id=node_id,
        identity_text=identity,
        knowledge_text=knowledge_text,
        input_hash=_search_document_hash(node_id, identity, knowledge_text, basis),
        generator_version=PREFIX,
    )
    c.execute(
        s.search_document_basis.insert(),
        [
            {"node_search_document_id": document, "knowledge_item_id": i}
            for i in sorted(set(basis))
        ],
    )
    context_task = _insert_successful_task(
        c,
        "NODE_CONTEXT",
        _digest(f"{PREFIX}context:{document}"),
        contracts["NODE_CONTEXT"],
        PREFIX,
        PREFIX,
    )
    context = insert_id(
        c,
        s.node_context,
        node_id=node_id,
        node_search_document_id=document,
        model_task_id=context_task,
        language="ko",
        context_text=knowledge_text,
    )
    question_task = _insert_successful_task(
        c,
        "FOLLOWUP_QUESTIONS",
        _digest(f"{PREFIX}questions:{document}"),
        contracts["FOLLOWUP_QUESTIONS"],
        PREFIX,
        PREFIX,
    )
    targets = (neighbors + [node_id, node_id])[:2]
    c.execute(
        s.followup_question.insert(),
        [
            {
                "node_context_id": context,
                "model_task_id": question_task,
                "slot": slot,
                "question_text": f"개발용 연결 경로 {slot}을 살펴보기",
                "target_node_id": target,
            }
            for slot, target in enumerate(targets, 1)
        ],
    )
    insight_task = _insert_successful_task(
        c,
        "NODE_INSIGHT",
        _digest(f"{PREFIX}insight:{document}"),
        contracts["NODE_INSIGHT"],
        PREFIX,
        PREFIX,
    )
    for window in ("RECENT_90_DAYS", "RECENT_1_YEAR"):
        insight = insert_id(
            c,
            s.node_insight,
            node_id=node_id,
            node_search_document_id=document,
            model_task_id=insight_task,
            time_window=window,
            as_of_at=datetime.now(UTC),
            slot=1,
            title=name + " 연결 검토",
            summary_text="개발용으로 구성한 연결과 근거입니다.",
            synthesis_text="개발용 근거를 함께 펼쳐 비교하는 화면을 검토합니다.",
            caveat_text="실제 사업 사실·모델 생성 품질을 뜻하지 않습니다.",
        )
        c.execute(
            s.node_insight_claim.insert(),
            [
                {
                    "node_insight_id": insight,
                    "claim_id": claim,
                    "role": "KEY_CLAIM" if i == 0 else "SUPPORTING_CLAIM",
                    "display_order": i + 1,
                }
                for i, claim in enumerate(claims[:3])
            ],
        )
    c.execute(
        s.publication_affected_node.insert().values(
            promotion_batch_id=batch,
            node_id=node_id,
            node_search_document_id=document,
            node_context_id=context,
            node_insight_model_task_id=insight_task,
        )
    )


def seed(c: Connection) -> dict[str, int]:
    definitions, edges = catalog()
    types = {
        str(code): int(id_)
        for code, id_ in c.execute(
            sa.select(s.node_type.c.node_type_code, s.node_type.c.node_type_id)
        )
    }
    contracts = {
        str(kind): int(id_)
        for kind, id_ in c.execute(
            sa.select(
                s.output_schema_definition.c.task_kind,
                s.output_schema_definition.c.output_schema_definition_id,
            )
        )
    }
    now = datetime.now(UTC)
    policy = c.scalar(
        sa.select(s.lint_policy_version.c.lint_policy_version_id).where(
            s.lint_policy_version.c.is_active
        )
    )
    batch = insert_id(
        c,
        s.promotion_batch,
        lint_policy_version_id=policy,
        promotion_status="COMMITTED",
        publication_status="READY",
        started_at=now,
        committed_at=now,
        ready_at=now,
    )
    revisions = [make_revision(c, types, False), make_revision(c, types, True)]
    ids: dict[str, int] = {}
    bases: dict[str, list[int]] = {}
    claims: dict[str, list[int]] = {}
    neighbors: dict[str, list[int]] = {}
    for key, name, kind in definitions:
        id_ = knowledge(c, "NODE", batch)
        ids[key] = id_
        c.execute(s.node.insert().values(node_id=id_, node_type_id=types[kind]))
        alias = insert_id(
            c,
            s.node_alias,
            node_id=id_,
            alias_text="[검토] " + name,
            language="ko",
            is_preferred=True,
        )
        observation = evidence(
            c, "node-" + key, f"개발용 가상 node: {name}. 실제 주장 아님.", now
        )
        c.execute(
            s.node_alias_evidence.insert().values(
                node_alias_id=alias, observation_id=observation
            )
        )
        own_claim = make_claim(
            c, batch, f"{name}는 개발용 화면 검토 node입니다.", [observation]
        )
        bases[key] = [id_, own_claim]
        claims[key] = [own_claim]
        neighbors[key] = []
    for index, (source, target, count, conflict) in enumerate(edges):
        a, b = ids[source], ids[target]
        directed = index >= len(ORIGINAL_RELATIONS) and index % 5 == 0
        revision = revisions[int(directed)]
        if not directed:
            a, b = sorted((a, b))
        relation = knowledge(c, "RELATION", batch)
        c.execute(
            s.relation.insert().values(
                relation_id=relation,
                source_node_id=a,
                target_node_id=b,
                relation_type_revision_id=revision,
                relation_identity_key=_relation_key(a, revision, b),
            )
        )
        text = f"개발용 연결 {source} → {target}: 실제 관계 주장이 아닙니다."
        support = make_claim(
            c,
            batch,
            text,
            [
                evidence(c, f"edge-{index}-{i}", text + f" 독립 검토 자료 {i + 1}", now)
                for i in range(count)
            ],
        )
        c.execute(
            s.claim_relation.insert().values(
                claim_id=support, relation_id=relation, stance="SUPPORT"
            )
        )
        if conflict:
            oppose = make_claim(
                c,
                batch,
                "개발용 반박 사례입니다.",
                [evidence(c, f"conflict-{index}", "개발용 반박 사례입니다.", now)],
            )
            c.execute(
                s.claim_relation.insert().values(
                    claim_id=oppose, relation_id=relation, stance="DISPUTE"
                )
            )
            conflict_id = insert_id(
                c,
                s.conflict_set,
                relation_id=relation,
                modality="FACT",
                current_state="AGENT_PROPOSED",
                created_at=now,
            )
            c.execute(
                s.conflict_member.insert(),
                [
                    {
                        "conflict_set_id": conflict_id,
                        "claim_id": cl,
                        "position_key": stance,
                    }
                    for cl, stance in [(support, "support"), (oppose, "dispute")]
                ],
            )
            bases[source].append(oppose)
            bases[target].append(oppose)
        for key, other in [(source, target), (target, source)]:
            bases[key].extend([relation, support])
            claims[key].append(support)
            neighbors[key].append(ids[other])
    for key, name, _kind in definitions:
        artifacts(
            c,
            ids[key],
            "[검토] " + name,
            bases[key],
            claims[key],
            neighbors[key],
            batch,
            contracts,
        )
    return ids


def load_review_fixture() -> tuple[bool, dict[str, int]]:
    if get_settings().environment != "development":
        raise RuntimeError("검토 fixture는 development 환경에서만 실행할 수 있습니다.")
    load_hbf_fixture()
    with get_engine().begin() as c:
        c.execute(sa.text("SELECT pg_advisory_xact_lock(147148)"))
        marker = c.scalar(
            sa.select(s.source_document.c.source_document_id).where(
                s.source_document.c.source_key == PREFIX + "node-sk"
            )
        )
        if marker is not None:
            names = {"[검토] " + name: key for key, name, _kind in catalog()[0]}
            rows = c.execute(
                sa.select(s.node_alias.c.alias_text, s.node_alias.c.node_id).where(
                    s.node_alias.c.alias_text.in_(names)
                )
            )
            ids = {names[str(name)]: int(id_) for name, id_ in rows}
            if len(ids) != 100:
                raise RuntimeError(
                    "검토 자료가 불완전합니다. 기존 자료를 보존하고 원인을 확인하세요."
                )
            return False, ids
        return True, seed(c)


def main() -> None:
    created, ids = load_review_fixture()
    print(f"개발용 검토 자료: created={created}, nodes={len(ids)}, center={ids['sk']}")


if __name__ == "__main__":
    main()
