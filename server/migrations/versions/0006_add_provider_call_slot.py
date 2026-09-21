"""Add the approved durable provider call budget (not result persistence).

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_COUNT = (
    "이 논리 작업에서 실제 provider를 호출한 누계. cache 적중은 증가시키지 "
    "않으며 agent_attempt 행과 같은 트랜잭션에서 유지한다."
)
NEW_COUNT = (
    "durable terminal agent_attempt 행 수. append와 같은 transaction에서 "
    "유지하며 실제 호출 hard budget은 provider_call_slot 소비 수로 판단한다."
)
OLD_TASK = (
    "재시도 전체를 묶는 논리 모델 작업. 실제 호출 누계와 현재 실행 상태를 "
    "소유하며 모델 응답 payload를 저장하지 않는다."
)
NEW_TASK = (
    "재시도 전체를 묶는 논리 모델 작업. terminal attempt 수와 현재 실행 상태를 "
    "소유하며 모델 응답 payload를 저장하지 않는다."
)
OLD_ATTEMPT = (
    "한 논리 모델 작업의 실제 provider 호출 한 번을 기록하는 append-only 이력."
)
NEW_ATTEMPT = (
    "확정된 provider terminal 결과의 append-only 이력. attempt_no는 slot_no이며 "
    "UNKNOWN slot 때문에 번호에 gap이 생길 수 있다."
)


def upgrade() -> None:
    op.create_table(
        "provider_call_slot",
        sa.Column("model_task_id", sa.BigInteger(), nullable=False),
        sa.Column("slot_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["model_task_id"],
            ["model_task.model_task_id"],
            name="fk_provider_call_slot__model_task",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "model_task_id", "slot_no", name="pk_provider_call_slot"
        ),
        sa.CheckConstraint(
            "slot_no BETWEEN 1 AND 3", name="ck_provider_call_slot__number"
        ),
        sa.CheckConstraint(
            "state IN ('RESERVED', 'COMPLETED', 'UNKNOWN')",
            name="ck_provider_call_slot__state",
        ),
        sa.CheckConstraint(
            "(state = 'RESERVED' AND resolved_at IS NULL) OR "
            "(state IN ('COMPLETED', 'UNKNOWN') AND resolved_at IS NOT NULL)",
            name="ck_provider_call_slot__state_shape",
        ),
        sa.CheckConstraint(
            "isfinite(reserved_at) AND (resolved_at IS NULL OR "
            "(isfinite(resolved_at) AND resolved_at >= reserved_at))",
            name="ck_provider_call_slot__timestamps",
        ),
        comment=(
            "전송 전 durable하게 소비하는 제품 provider 호출 슬롯. UNKNOWN도 예산을 "
            "소비하며 재사용하지 않는다. 모델 입력·응답·결과 payload는 저장하지 않는다."
        ),
    )
    op.alter_column(
        "model_task",
        "attempt_count",
        existing_type=sa.Integer(),
        comment=NEW_COUNT,
        existing_comment=OLD_COUNT,
    )
    op.create_table_comment("model_task", NEW_TASK, existing_comment=OLD_TASK)
    op.create_table_comment("agent_attempt", NEW_ATTEMPT, existing_comment=OLD_ATTEMPT)


def downgrade() -> None:
    # Never silently erase consumed budget and thereby authorize free replay.
    op.execute("LOCK TABLE model_task, provider_call_slot IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM provider_call_slot)")
    ):
        raise RuntimeError(
            "provider_call_slot contains execution history; downgrade refused"
        )
    op.drop_table("provider_call_slot")
    op.alter_column(
        "model_task",
        "attempt_count",
        existing_type=sa.Integer(),
        comment=OLD_COUNT,
        existing_comment=NEW_COUNT,
    )
    op.create_table_comment("model_task", OLD_TASK, existing_comment=NEW_TASK)
    op.create_table_comment("agent_attempt", OLD_ATTEMPT, existing_comment=NEW_ATTEMPT)
