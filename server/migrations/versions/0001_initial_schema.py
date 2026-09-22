"""initial schema squash

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-09-22

"""
from typing import Sequence, Union
from alembic import op
from ontology_map.db.metadata import metadata

# revision identifiers, used by Alembic.
revision: str = '0001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # metadata에 등록된 30여 개 이상의 모든 Table을 한 번에 CREATE TABLE
    bind = op.get_bind()
    metadata.create_all(bind)


def downgrade() -> None:
    # 롤백 시 모든 Table 일괄 DROP TABLE
    bind = op.get_bind()
    metadata.drop_all(bind)
