"""Activate the bundled FOLLOWUP_QUESTIONS output contract.

Revision ID: 0008
Revises: 0007
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA_JSON = json.loads(
    r"""{"$defs":{"FollowupClaimReference":{"additionalProperties":false,"description":"Agent-proposed evidence role for one candidate question.","properties":{"claim_id":{"exclusiveMinimum":0,"title":"Claim Id","type":"integer"},"display_order":{"exclusiveMinimum":0,"title":"Display Order","type":"integer"},"role":{"enum":["KEY_CLAIM","SUPPORTING_CLAIM","CONTRASTING_CLAIM"],"title":"Role","type":"string"}},"required":["claim_id","role","display_order"],"title":"FollowupClaimReference","type":"object"},"FollowupQuestionCandidate":{"additionalProperties":false,"description":"One atomic candidate; invalid grounding excludes the complete question.","properties":{"answer_text":{"minLength":1,"pattern":"\\S","title":"Answer Text","type":"string"},"caveat_text":{"anyOf":[{"minLength":1,"pattern":"\\S","type":"string"},{"type":"null"}],"default":null,"title":"Caveat Text"},"claims":{"items":{"$ref":"#/$defs/FollowupClaimReference"},"title":"Claims","type":"array"},"display_order":{"exclusiveMinimum":0,"title":"Display Order","type":"integer"},"question_text":{"minLength":1,"pattern":"\\S","title":"Question Text","type":"string"}},"required":["display_order","question_text","answer_text","claims"],"title":"FollowupQuestionCandidate","type":"object"},"FollowupWindowProposal":{"additionalProperties":false,"description":"Questions for one time window; an empty tuple is a normal result.","properties":{"questions":{"items":{"$ref":"#/$defs/FollowupQuestionCandidate"},"maxItems":8,"title":"Questions","type":"array"}},"required":["questions"],"title":"FollowupWindowProposal","type":"object"}},"additionalProperties":false,"description":"One Structured Output response for the 90-day + 1-year bundle.","properties":{"recent_1_year":{"$ref":"#/$defs/FollowupWindowProposal"},"recent_90_days":{"$ref":"#/$defs/FollowupWindowProposal"}},"required":["recent_90_days","recent_1_year"],"title":"FollowupQuestionsProposal","type":"object"}"""
)


def _lock_and_require_idle(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE promotion_batch, model_task, output_schema_definition IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM promotion_batch WHERE publication_status = 'PREPARING')"
        )
    ):
        raise RuntimeError(
            "FOLLOWUP_BUNDLE_MIGRATION_REQUIRES_NO_PREPARING_PUBLICATION"
        )
    if bind.scalar(
        sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM model_task
            WHERE task_kind = 'FOLLOWUP_QUESTIONS'
              AND status IN ('PENDING', 'RUNNING', 'RETRY_WAIT')
        )
    """)
    ):
        raise RuntimeError("FOLLOWUP_BUNDLE_MIGRATION_REQUIRES_NO_NONTERMINAL_FOLLOWUP")


def upgrade() -> None:
    bind = op.get_bind()
    _lock_and_require_idle(bind)
    bind.execute(
        sa.text("""
        UPDATE output_schema_definition
        SET is_active = false
        WHERE task_kind = 'FOLLOWUP_QUESTIONS' AND is_active
    """)
    )
    bind.execute(
        sa.text("""
            INSERT INTO output_schema_definition
                (task_kind, version_no, schema_json, is_active)
            VALUES (
                'FOLLOWUP_QUESTIONS',
                COALESCE((SELECT max(version_no) + 1 FROM output_schema_definition WHERE task_kind = 'FOLLOWUP_QUESTIONS'), 1),
                CAST(:schema_json AS jsonb),
                true
            )
        """),
        {"schema_json": json.dumps(_SCHEMA_JSON, ensure_ascii=False)},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE model_task, output_schema_definition IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    new_id = bind.scalar(
        sa.text("""
            SELECT output_schema_definition_id
            FROM output_schema_definition
            WHERE task_kind = 'FOLLOWUP_QUESTIONS'
              AND schema_json = CAST(:schema_json AS jsonb)
            ORDER BY version_no DESC
            LIMIT 1
        """),
        {"schema_json": json.dumps(_SCHEMA_JSON, ensure_ascii=False)},
    )
    if new_id is None:
        raise RuntimeError("FOLLOWUP_BUNDLE_SCHEMA_NOT_FOUND")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM model_task WHERE output_schema_definition_id = :schema_id)"
        ),
        {"schema_id": new_id},
    ):
        raise RuntimeError("FOLLOWUP_BUNDLE_DOWNGRADE_REFUSES_REFERENCED_SCHEMA")
    previous_id = bind.scalar(
        sa.text("""
        SELECT output_schema_definition_id
        FROM output_schema_definition
        WHERE task_kind = 'FOLLOWUP_QUESTIONS'
          AND output_schema_definition_id <> :schema_id
        ORDER BY version_no DESC
        LIMIT 1
    """),
        {"schema_id": new_id},
    )
    bind.execute(
        sa.text(
            "DELETE FROM output_schema_definition WHERE output_schema_definition_id = :schema_id"
        ),
        {"schema_id": new_id},
    )
    if previous_id is not None:
        bind.execute(
            sa.text(
                "UPDATE output_schema_definition SET is_active = true WHERE output_schema_definition_id = :schema_id"
            ),
            {"schema_id": previous_id},
        )
