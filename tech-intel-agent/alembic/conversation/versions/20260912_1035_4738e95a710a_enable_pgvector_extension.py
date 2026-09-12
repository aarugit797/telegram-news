"""enable pgvector extension

Revision ID: 4738e95a710a
Revises: 85096b1b5eeb
Create Date: 2026-09-12 10:35:45.029213

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '4738e95a710a'
down_revision: Union[str, None] = '85096b1b5eeb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hand-written: an extension is neither a table nor a column, so
    # autogenerate will never emit this no matter what the models say.
    #
    # The Conversation DB has no Vector column today - this is here so
    # the two databases are provisioned identically and a future vector
    # column (e.g. embedding conversation summaries for recall) does not
    # need an extension migration at the moment it is least convenient.
    #
    # Kept in the migration rather than scripts/init-second-db.sh
    # because that container init script never runs on RDS.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Deliberately NOT dropping the extension, matching the News DB's
    # initial migration: DROP EXTENSION is a database-level operation
    # and would cascade into any other vector column in this database.
    pass
