"""support multiple allowed units for NUMBER attributes

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14 14:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ATTRIBUTE_REVISION_COMMENT = (
    "속성의 대상 유형·값 종류·canonical 단위를 보존하는 불변 규칙 버전."
)
ATTRIBUTE_REVISION_COMMENT = (
    "속성의 대상 유형·값 종류와 활성 수명주기를 보존하는 불변 규칙 버전. "
    "NUMBER 허용 단위는 attribute_revision_allowed_unit이 소유한다."
)
LEGACY_UNIT_RULE_COMMENT = (
    "NUMBER revision이 허용하는 canonical 단위 code 하나. 자동 단위 환산 규칙이나 "
    "표시 문자열 목록이 아니다."
)


def upgrade() -> None:
    op.create_table(
        "attribute_revision_allowed_unit",
        sa.Column("attribute_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("allowed_value_kind", sa.Text(), nullable=False),
        sa.Column("unit_code", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "allowed_value_kind = 'NUMBER'",
            name="ck_attribute_revision_allowed_unit__number_kind",
        ),
        sa.CheckConstraint(
            "btrim(unit_code) <> ''",
            name="ck_attribute_revision_allowed_unit__unit_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["attribute_revision_id", "allowed_value_kind"],
            [
                "attribute_revision.attribute_revision_id",
                "attribute_revision.allowed_value_kind",
            ],
            name="fk_attribute_revision_allowed_unit__attribute_kind",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "attribute_revision_id",
            "unit_code",
            name="pk_attribute_revision_allowed_unit",
        ),
        comment=(
            "NUMBER attribute revision이 허용하는 원문 단위 code 집합. 단위 환산·"
            "정규화·비교 규칙을 뜻하지 않는다."
        ),
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO attribute_revision_allowed_unit (
                attribute_revision_id,
                allowed_value_kind,
                unit_code
            )
            SELECT
                attribute_revision_id,
                allowed_value_kind,
                unit_rule
            FROM attribute_revision
            WHERE allowed_value_kind = 'NUMBER'
            """
        )
    )

    incompatible_legacy_values = bind.scalar(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM claim_attribute_value AS cav
                JOIN attribute_revision AS ar
                  ON ar.attribute_revision_id = cav.attribute_revision_id
                WHERE cav.value_kind = 'NUMBER'
                  AND cav.unit_code IS DISTINCT FROM ar.unit_rule
            )
            """
        )
    )
    if incompatible_legacy_values:
        raise RuntimeError(
            "기존 NUMBER Claim에 attribute revision의 허용 단위와 다른 unit_code가 "
            "있어 0003으로 안전하게 승격할 수 없습니다. 데이터를 임의 환산하거나 "
            "정규화하지 말고 먼저 원문 단위 계약을 확인하세요."
        )

    op.create_foreign_key(
        "fk_claim_attribute_value__allowed_unit",
        "claim_attribute_value",
        "attribute_revision_allowed_unit",
        ["attribute_revision_id", "unit_code"],
        ["attribute_revision_id", "unit_code"],
        onupdate="RESTRICT",
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "ck_attribute_revision__unit_rule",
        "attribute_revision",
        type_="check",
    )
    op.drop_column("attribute_revision", "unit_rule")
    op.create_table_comment(
        "attribute_revision",
        ATTRIBUTE_REVISION_COMMENT,
        existing_comment=LEGACY_ATTRIBUTE_REVISION_COMMENT,
    )


def downgrade() -> None:
    bind = op.get_bind()
    non_single_unit_revision = bind.scalar(
        sa.text(
            """
            SELECT EXISTS (
                SELECT ar.attribute_revision_id
                FROM attribute_revision AS ar
                LEFT JOIN attribute_revision_allowed_unit AS allowed_unit
                  ON allowed_unit.attribute_revision_id = ar.attribute_revision_id
                WHERE ar.allowed_value_kind = 'NUMBER'
                GROUP BY ar.attribute_revision_id
                HAVING count(allowed_unit.unit_code) <> 1
            )
            """
        )
    )
    if non_single_unit_revision:
        raise RuntimeError(
            "복수 허용 단위 또는 허용 단위가 없는 NUMBER revision이 존재합니다. "
            "0002는 revision당 단위 하나만 표현할 수 있으므로 데이터를 보존한 채 "
            "앱을 되돌리세요."
        )

    op.add_column(
        "attribute_revision",
        sa.Column(
            "unit_rule",
            sa.Text(),
            nullable=True,
            comment=LEGACY_UNIT_RULE_COMMENT,
        ),
    )
    bind.execute(
        sa.text(
            """
            UPDATE attribute_revision AS ar
            SET unit_rule = allowed_unit.unit_code
            FROM attribute_revision_allowed_unit AS allowed_unit
            WHERE allowed_unit.attribute_revision_id = ar.attribute_revision_id
              AND ar.allowed_value_kind = 'NUMBER'
            """
        )
    )
    op.create_check_constraint(
        "ck_attribute_revision__unit_rule",
        "attribute_revision",
        "(allowed_value_kind = 'NUMBER' AND unit_rule IS NOT NULL AND "
        "btrim(unit_rule) <> '') OR "
        "(allowed_value_kind <> 'NUMBER' AND unit_rule IS NULL)",
    )
    op.drop_constraint(
        "fk_claim_attribute_value__allowed_unit",
        "claim_attribute_value",
        type_="foreignkey",
    )
    op.drop_table("attribute_revision_allowed_unit")
    op.create_table_comment(
        "attribute_revision",
        LEGACY_ATTRIBUTE_REVISION_COMMENT,
        existing_comment=ATTRIBUTE_REVISION_COMMENT,
    )
