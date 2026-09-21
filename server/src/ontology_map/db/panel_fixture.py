"""패널 읽기 검토용 가상 자료. 실제 정보나 모델 생성 결과가 아니다."""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import Connection

from ontology_map.db import schema as s
from ontology_map.db.fixture import (
    _digest,
    _insert_successful_task,
    _relation_key,
    load_hbf_fixture,
)
from ontology_map.db.review_fixture import (
    artifacts,
    evidence,
    insert_id,
    knowledge,
    make_claim,
)
from ontology_map.db.session import get_engine
from ontology_map.settings import get_settings

PREFIX = "panel-review-v1:"
NAMES = {
    "gaon": "[패널 검토] 가온로보틱스",
    "nuri": "[패널 검토] 누리센서",
    "empty": "[패널 검토] 자료 부족 사례",
}
FACTS = [
    ("가온은 무엇을 담당하나?", "가온은 공동 사업의 제어 소프트웨어를 담당한다."),
    ("누리는 어떤 기술을 제공하나?", "누리는 공동 사업의 센서 모듈을 제공한다."),
    (
        "공동 시연에서는 무엇을 확인했나?",
        ("6월 공동 시연에서 센서 입력에 따른 제어 소프트웨어의 동작을 확인했다."),
    ),
    (
        "현장 실증은 언제 시작하나?",
        ("8월 실증 계획서는 시작일을 9월로 명시했지만 별도 일정표는 10월로 명시했다."),
    ),
    (
        "실증은 어디에서 진행하나?",
        "실증 계획서에는 가상 물류센터 한 곳을 검토 장소로 적었다.",
    ),
    (
        "실증에서 확인하려는 것은 무엇인가?",
        (
            "실증 계획서는 현장 조도 변화에 따른 센서 "
            "입력과 제어 반응의 안정성을 평가 대상으로 "
            "정했다."
        ),
    ),
]


def _question_results(
    c: Connection,
    context: int,
    claims: list[int],
    section: int | None,
    window: str,
    as_of: datetime,
    task: int,
) -> None:
    group = insert_id(
        c,
        s.node_question_set,
        node_context_id=context,
        model_task_id=task,
        time_window=window,
        as_of_at=as_of,
    )
    for index, (question, answer) in enumerate(FACTS if claims else []):
        qid = insert_id(
            c,
            s.node_question,
            question_set_id=group,
            display_order=index + 1,
            question_text=question,
            answer_text=answer,
            caveat_text=(
                "두 자료가 같은 실증의 시작일을 다르게 "
                "제시하므로 날짜를 확정할 수 없습니다."
            )
            if index == 3
            else None,
            section_id=section if index in (2, 3) else None,
        )
        chosen = [claims[index]] + ([claims[6]] if index == 3 else [])
        c.execute(
            s.node_question_claim.insert(),
            [
                dict(
                    question_id=qid,
                    claim_id=cl,
                    role="KEY_CLAIM" if i == 0 else "CONTRASTING_CLAIM",
                    display_order=i + 1,
                )
                for i, cl in enumerate(chosen)
            ],
        )


def _report(
    c: Connection,
    node_id: int,
    document: int,
    task: int,
    window: str,
    as_of: datetime,
    claims: list[int],
) -> int | None:
    report = None
    section = None
    if claims:
        report = insert_id(
            c,
            s.node_insight,
            node_id=node_id,
            node_search_document_id=document,
            model_task_id=task,
            time_window=window,
            as_of_at=as_of,
            slot=2,
            title="공동 시연 이후 현장 실증을 준비하는 가온로보틱스",
            summary_text=(
                "시연 기록과 실증 계획이 있으며, 두 회사의 담당 "
                "기술은 구분됩니다. 실증 시작일은 자료 사이에 "
                "차이가 있습니다."
            ),
            synthesis_text=(
                "저장된 자료로는 공동 시연과 실증 준비를 "
                "확인할 수 있습니다. 현장 실증의 완료나 사업 "
                "성과로 확대 해석할 수는 없습니다."
            ),
            caveat_text=(
                "UX 검토용 가상 자료입니다. 실제 사실이나 모델 품질의 증거가 아닙니다."
            ),
        )
        c.execute(
            s.node_insight_claim.insert(),
            [
                dict(
                    node_insight_id=report,
                    claim_id=cl,
                    role="KEY_CLAIM" if i == 0 else "SUPPORTING_CLAIM",
                    display_order=i + 1,
                )
                for i, cl in enumerate(claims)
            ],
        )
        for order, (title, body, selected) in enumerate(
            [
                (
                    "시연 이후 현장 검증을 준비하고 있다",
                    (
                        "공동 시연 기록과 현장 실증 계획을 함께 보면, "
                        "문서에 나타난 다음 검토 단계는 현장 "
                        "검증입니다. 실증 완료나 양산 여부는 확인되지 "
                        "않습니다."
                    ),
                    [2, 3, 6],
                ),
                (
                    "제어 소프트웨어와 센서의 역할이 나뉜다",
                    (
                        "가온의 제어 소프트웨어와 누리의 센서가 공동 "
                        "사업의 서로 다른 부분을 담당합니다. 담당 "
                        "범위만으로 기술적 우열이나 협력의 독점성을 "
                        "판단할 수 없습니다."
                    ),
                    [0, 1],
                ),
            ],
            1,
        ):
            sid = insert_id(
                c,
                s.node_insight_section,
                node_insight_id=report,
                display_order=order,
                title=title,
                synthesis_text=body,
                caveat_text="실증 시작일은 9월과 10월로 엇갈립니다."
                if order == 1
                else None,
            )
            if order == 1:
                section = sid
            c.execute(
                s.node_insight_section_claim.insert(),
                [
                    dict(
                        section_id=sid,
                        claim_id=claims[i],
                        role="CONTRASTING_CLAIM" if i == 6 else "KEY_CLAIM",
                        display_order=j + 1,
                    )
                    for j, i in enumerate(selected)
                ],
            )
    insert_id(
        c,
        s.node_insight_window,
        node_id=node_id,
        node_search_document_id=document,
        model_task_id=task,
        time_window=window,
        as_of_at=as_of,
        node_insight_id=report,
    )
    return section


def seed(c: Connection) -> dict[str, int]:
    now = datetime.now(UTC)
    published = now - timedelta(days=5)
    types = {
        str(code): int(value)
        for code, value in c.execute(
            sa.select(s.node_type.c.node_type_code, s.node_type.c.node_type_id)
        ).all()
    }
    contracts = {
        str(code): int(value)
        for code, value in c.execute(
            sa.select(
                s.output_schema_definition.c.task_kind,
                s.output_schema_definition.c.output_schema_definition_id,
            ).where(s.output_schema_definition.c.is_active)
        ).all()
    }
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
    ids = {}
    own_claims = {}
    for key, name in NAMES.items():
        node = knowledge(c, "NODE", batch)
        ids[key] = node
        c.execute(s.node.insert().values(node_id=node, node_type_id=types["COMPANY"]))
        alias = insert_id(
            c,
            s.node_alias,
            node_id=node,
            alias_text=name,
            language="ko",
            is_preferred=True,
        )
        obs = evidence(c, PREFIX + key, name + "는 개발용 가상 사례입니다.", published)
        c.execute(
            s.node_alias_evidence.insert().values(
                node_alias_id=alias, observation_id=obs
            )
        )
        own_claims[key] = make_claim(
            c, batch, name + "는 개발용 가상 사례입니다.", [obs]
        )
    revision = c.scalar(
        sa.select(s.relation_type_revision.c.relation_type_revision_id)
        .where(s.relation_type_revision.c.directionality == "SYMMETRIC")
        .order_by(s.relation_type_revision.c.relation_type_revision_id)
    )
    if revision is None:
        raise RuntimeError("검토 자료의 Relation revision이 없습니다.")
    relation = knowledge(c, "RELATION", batch)
    a, b = sorted([ids["gaon"], ids["nuri"]])
    c.execute(
        s.relation.insert().values(
            relation_id=relation,
            source_node_id=a,
            target_node_id=b,
            relation_type_revision_id=revision,
            relation_identity_key=_relation_key(a, revision, b),
        )
    )
    texts = [answer for _, answer in FACTS]
    texts[3] = "8월 실증 계획서는 공동 실증의 시작일을 9월로 명시했다."
    texts += ["별도 일정표는 같은 공동 실증의 시작일을 10월로 명시했다."]
    claims = []
    for i, text in enumerate(texts):
        cl = make_claim(
            c, batch, text, [evidence(c, PREFIX + f"claim-{i}", text, published)]
        )
        claims.append(cl)
        c.execute(
            s.claim_relation.insert().values(
                claim_id=cl,
                relation_id=relation,
                stance="DISPUTE" if i == 6 else "SUPPORT",
            )
        )
    conflict = insert_id(
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
            dict(conflict_set_id=conflict, claim_id=claims[i], position_key=label)
            for i, label in [(3, "SEPTEMBER"), (6, "OCTOBER")]
        ],
    )
    for key, node_id in ids.items():
        selected = [] if key == "empty" else claims
        basis = [node_id, own_claims[key]] + (
            [relation, *claims, a, b] if selected else []
        )
        artifacts(
            c,
            node_id,
            NAMES[key],
            basis,
            [own_claims[key]],
            [],
            batch,
            contracts,
        )
        public = (
            c.execute(
                sa.select(s.publication_affected_node).where(
                    s.publication_affected_node.c.node_id == node_id
                )
            )
            .mappings()
            .one()
        )
        # 신규 fixture의 context만 설정한다. 기존 공개 자료는 갱신하지 않는다.
        c.execute(
            s.node_context.update()
            .where(s.node_context.c.node_context_id == public["node_context_id"])
            .values(
                context_text=(
                    "제어 소프트웨어와 센서의 공동 시연·실증 "
                    "계획을 검토하는 가상 사례입니다. 실제 기업 "
                    "정보가 아닙니다."
                )
                if selected
                else "자료 부족 상태를 확인하는 개발용 가상 사례입니다."
            )
        )
        task = _insert_successful_task(
            c,
            "FOLLOWUP_QUESTIONS",
            _digest(f"{PREFIX}questions:{node_id}"),
            contracts["FOLLOWUP_QUESTIONS"],
            PREFIX,
            PREFIX,
        )
        for window in ("RECENT_90_DAYS", "RECENT_1_YEAR"):
            section = _report(
                c,
                node_id,
                public["node_search_document_id"],
                public["node_insight_model_task_id"],
                window,
                now,
                selected,
            )
            _question_results(
                c, public["node_context_id"], selected, section, window, now, task
            )
    return ids


def load_panel_fixture() -> tuple[bool, dict[str, int]]:
    if get_settings().environment != "development":
        raise RuntimeError("패널 검토 자료는 development에서만 적재할 수 있습니다.")
    load_hbf_fixture()
    with get_engine().begin() as c:
        c.execute(sa.text("SELECT pg_advisory_xact_lock(162163)"))
        rows = {
            str(name): int(value)
            for name, value in c.execute(
                sa.select(s.node_alias.c.alias_text, s.node_alias.c.node_id).where(
                    s.node_alias.c.alias_text.in_(NAMES.values())
                )
            ).all()
        }
        if rows:
            if len(rows) != len(NAMES):
                raise RuntimeError(
                    "패널 검토 자료가 불완전합니다. 기존 자료를 보존하고 확인하세요."
                )
            return False, {key: int(rows[name]) for key, name in NAMES.items()}
        return True, seed(c)


if __name__ == "__main__":
    created, ids = load_panel_fixture()
    print(f"패널 개발 자료: created={created}, nodes={ids}")
