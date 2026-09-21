"""support product Topic reference lifecycle

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15 09:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_KNOWLEDGE_ITEM_COMMENT = (
    "node·relation·claim의 공유 ID, 현재 지식 상태와 생성 batch를 관리하는 "
    "상위 엔터티. 정확히 한 subtype은 승격 서비스가 커밋 전에 검증한다."
)
KNOWLEDGE_ITEM_COMMENT = (
    "node·relation·claim의 공유 ID와 수명주기를 관리하는 상위 엔터티. "
    "EVIDENCE_BACKED는 기존 state·promotion을 필수로 사용하고, "
    "PRODUCT_REFERENCE는 승인된 reference Node에만 제한한다."
)

APPROVED_TOPIC_CHECK = " OR ".join(
    (
        "(topic_code = 'SEMICONDUCTOR' AND canonical_display_name = '반도체')",
        "(topic_code = 'MEMORY_SEMICONDUCTOR' "
        "AND canonical_display_name = '메모리 반도체')",
        "(topic_code = 'ADVANCED_PACKAGING' "
        "AND canonical_display_name = '첨단 패키징')",
        "(topic_code = 'ARTIFICIAL_INTELLIGENCE' "
        "AND canonical_display_name = '인공지능')",
        "(topic_code = 'DATA_CENTER' AND canonical_display_name = '데이터센터')",
        "(topic_code = 'MANUFACTURING_PROCESS' "
        "AND canonical_display_name = '제조 공정')",
        "(topic_code = 'INVESTMENT' AND canonical_display_name = '투자')",
        "(topic_code = 'COMMERCIALIZATION' AND canonical_display_name = '상용화')",
        "(topic_code = 'REGULATION_POLICY' AND canonical_display_name = '규제·정책')",
    )
)


def _create_integrity_functions_and_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION assert_topic_reference_integrity(p_node_id bigint)
        RETURNS void
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_item_kind text;
            v_lifecycle_kind text;
            v_node_type_code text;
            v_has_reference boolean;
        BEGIN
            SELECT item_kind, lifecycle_kind
              INTO v_item_kind, v_lifecycle_kind
              FROM knowledge_item
             WHERE knowledge_item_id = p_node_id;

            IF NOT FOUND THEN
                RETURN;
            END IF;

            SELECT nt.node_type_code
              INTO v_node_type_code
              FROM node AS n
              JOIN node_type AS nt ON nt.node_type_id = n.node_type_id
             WHERE n.node_id = p_node_id;

            IF NOT FOUND THEN
                IF v_lifecycle_kind = 'PRODUCT_REFERENCE' THEN
                    RAISE EXCEPTION
                        'PRODUCT_REFERENCE knowledge_item % must own a TOPIC node',
                        p_node_id;
                END IF;
                RETURN;
            END IF;

            SELECT EXISTS (
                SELECT 1 FROM topic_reference WHERE node_id = p_node_id
            ) INTO v_has_reference;

            IF v_lifecycle_kind = 'PRODUCT_REFERENCE' OR v_has_reference THEN
                IF v_item_kind <> 'NODE'
                   OR v_node_type_code <> 'TOPIC'
                   OR v_lifecycle_kind <> 'PRODUCT_REFERENCE'
                   OR NOT v_has_reference THEN
                    RAISE EXCEPTION
                        'PRODUCT_REFERENCE must be a TOPIC node with topic_reference: %',
                        p_node_id;
                END IF;
            END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_knowledge_item_topic_reference()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.item_kind = 'NODE' OR NEW.lifecycle_kind = 'PRODUCT_REFERENCE' THEN
                PERFORM assert_topic_reference_integrity(NEW.knowledge_item_id);
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_knowledge_item__topic_reference_integrity
        AFTER INSERT OR UPDATE OF item_kind, lifecycle_kind,
            current_state, promotion_batch_id
        ON knowledge_item
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION enforce_knowledge_item_topic_reference()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_node_topic_reference()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM assert_topic_reference_integrity(NEW.node_id);
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_node__topic_reference_integrity
        AFTER INSERT OR UPDATE OF node_type_id
        ON node
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION enforce_node_topic_reference()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_topic_reference_node()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                PERFORM assert_topic_reference_integrity(OLD.node_id);
            ELSE
                PERFORM assert_topic_reference_integrity(NEW.node_id);
                IF TG_OP = 'UPDATE' AND OLD.node_id <> NEW.node_id THEN
                    PERFORM assert_topic_reference_integrity(OLD.node_id);
                END IF;
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_topic_reference__node_integrity
        AFTER INSERT OR UPDATE OR DELETE
        ON topic_reference
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION enforce_topic_reference_node()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_has_topic_active_reference()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_relation_code text;
        BEGIN
            SELECT rt.relation_code
              INTO v_relation_code
              FROM relation_type_revision AS rtr
              JOIN relation_type AS rt
                ON rt.relation_type_id = rtr.relation_type_id
             WHERE rtr.relation_type_revision_id = NEW.relation_type_revision_id;

            IF v_relation_code = 'HAS_TOPIC' AND NOT EXISTS (
                SELECT 1
                  FROM topic_reference AS tr
                 WHERE tr.node_id = NEW.target_node_id
                   AND tr.is_active
            ) THEN
                RAISE EXCEPTION
                    'new HAS_TOPIC relation requires an active Topic reference target: %',
                    NEW.target_node_id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_relation__has_topic_active_reference
        BEFORE INSERT OR UPDATE OF relation_type_revision_id, target_node_id
        ON relation
        FOR EACH ROW
        EXECUTE FUNCTION enforce_has_topic_active_reference()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_product_reference_publication_output()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM knowledge_item AS ki
                 WHERE ki.knowledge_item_id = NEW.node_id
                   AND ki.lifecycle_kind = 'PRODUCT_REFERENCE'
            ) THEN
                RAISE EXCEPTION
                    'product Reference Topic % cannot use general publication outputs',
                    NEW.node_id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_node_search_document__no_product_reference
        BEFORE INSERT OR UPDATE OF node_id
        ON node_search_document
        FOR EACH ROW
        EXECUTE FUNCTION reject_product_reference_publication_output()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_publication_affected_node__no_product_reference
        BEFORE INSERT OR UPDATE OF node_id
        ON publication_affected_node
        FOR EACH ROW
        EXECUTE FUNCTION reject_product_reference_publication_output()
        """
    )


def _drop_integrity_functions_and_triggers() -> None:
    op.execute(
        "DROP TRIGGER trg_publication_affected_node__no_product_reference "
        "ON publication_affected_node"
    )
    op.execute(
        "DROP TRIGGER trg_node_search_document__no_product_reference "
        "ON node_search_document"
    )
    op.execute("DROP FUNCTION reject_product_reference_publication_output()")
    op.execute("DROP TRIGGER trg_relation__has_topic_active_reference ON relation")
    op.execute("DROP FUNCTION enforce_has_topic_active_reference()")
    op.execute("DROP TRIGGER trg_topic_reference__node_integrity ON topic_reference")
    op.execute("DROP FUNCTION enforce_topic_reference_node()")
    op.execute("DROP TRIGGER trg_node__topic_reference_integrity ON node")
    op.execute("DROP FUNCTION enforce_node_topic_reference()")
    op.execute(
        "DROP TRIGGER trg_knowledge_item__topic_reference_integrity ON knowledge_item"
    )
    op.execute("DROP FUNCTION enforce_knowledge_item_topic_reference()")
    op.execute("DROP FUNCTION assert_topic_reference_integrity(bigint)")


def upgrade() -> None:
    op.add_column(
        "knowledge_item",
        sa.Column(
            "lifecycle_kind",
            sa.Text(),
            server_default=sa.text("'EVIDENCE_BACKED'"),
            nullable=False,
            comment=(
                "EVIDENCE_BACKED는 기존 promotion·state·Evidence/publication "
                "수명주기, PRODUCT_REFERENCE는 승인된 제품 reference Node "
                "수명주기다."
            ),
        ),
    )
    op.alter_column(
        "knowledge_item",
        "current_state",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.alter_column(
        "knowledge_item",
        "promotion_batch_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_knowledge_item__lifecycle_kind",
        "knowledge_item",
        "lifecycle_kind IN ('EVIDENCE_BACKED', 'PRODUCT_REFERENCE')",
    )
    op.create_check_constraint(
        "ck_knowledge_item__lifecycle_shape",
        "knowledge_item",
        "(lifecycle_kind = 'EVIDENCE_BACKED' "
        "AND current_state IS NOT NULL AND promotion_batch_id IS NOT NULL) OR "
        "(lifecycle_kind = 'PRODUCT_REFERENCE' AND item_kind = 'NODE' "
        "AND current_state IS NULL AND promotion_batch_id IS NULL)",
    )

    op.create_table(
        "topic_reference",
        sa.Column("node_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_code", sa.Text(), nullable=False),
        sa.Column("canonical_display_name", sa.Text(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment=(
                "새 HAS_TOPIC mapping의 target으로 사용할 수 있는지 나타낸다. "
                "비활성화는 기존 relation의 의미·공개 여부를 바꾸지 않는다."
            ),
        ),
        sa.CheckConstraint(
            "btrim(topic_code) <> ''",
            name="ck_topic_reference__code_nonblank",
        ),
        sa.CheckConstraint(
            "btrim(canonical_display_name) <> ''",
            name="ck_topic_reference__display_name_nonblank",
        ),
        sa.CheckConstraint(
            f"({APPROVED_TOPIC_CHECK})",
            name="ck_topic_reference__approved_definition",
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["node.node_id"],
            name="fk_topic_reference__node",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("node_id", name="pk_topic_reference"),
        sa.UniqueConstraint("topic_code", name="uq_topic_reference__code"),
        comment=(
            "TOPIC Node identity에 연결된 제품 controlled vocabulary 정의. canonical "
            "이름과 active mapping 여부의 source of truth이며 Evidence가 아니다."
        ),
    )
    op.create_table_comment(
        "knowledge_item",
        KNOWLEDGE_ITEM_COMMENT,
        existing_comment=LEGACY_KNOWLEDGE_ITEM_COMMENT,
    )
    _create_integrity_functions_and_triggers()


def downgrade() -> None:
    bind = op.get_bind()
    product_reference_exists = bind.scalar(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM knowledge_item
                 WHERE lifecycle_kind = 'PRODUCT_REFERENCE'
                UNION ALL
                SELECT 1 FROM topic_reference
            )
            """
        )
    )
    if product_reference_exists:
        raise RuntimeError(
            "PRODUCT_REFERENCE Topic이 존재합니다. 0003은 이 lifecycle을 표현할 "
            "수 없으므로 reference identity를 보존한 채 downgrade할 수 없습니다."
        )

    _drop_integrity_functions_and_triggers()
    op.drop_table("topic_reference")
    op.drop_constraint(
        "ck_knowledge_item__lifecycle_shape",
        "knowledge_item",
        type_="check",
    )
    op.drop_constraint(
        "ck_knowledge_item__lifecycle_kind",
        "knowledge_item",
        type_="check",
    )
    op.alter_column(
        "knowledge_item",
        "promotion_batch_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.alter_column(
        "knowledge_item",
        "current_state",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_column("knowledge_item", "lifecycle_kind")
    op.create_table_comment(
        "knowledge_item",
        LEGACY_KNOWLEDGE_ITEM_COMMENT,
        existing_comment=KNOWLEDGE_ITEM_COMMENT,
    )
