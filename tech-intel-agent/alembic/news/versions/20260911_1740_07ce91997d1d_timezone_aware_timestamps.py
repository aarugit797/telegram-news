"""timezone aware timestamps

Revision ID: 07ce91997d1d
Revises: d80efae9a0c9
Create Date: 2026-09-11 17:40:58.984140

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# The News DB's signals table has a pgvector embedding column.
# Autogenerate emits it fully qualified, as
#   pgvector.sqlalchemy.vector.VECTOR(dim=1024)
# but never adds the import itself, so the generated file would raise
# NameError on import. Kept in the template so every News migration
# has it available rather than needing it hand-added each time.
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql

revision: str = '07ce91997d1d'
down_revision: Union[str, None] = 'd80efae9a0c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every alter_column below carries an explicit postgresql_using clause.
# Without one, Postgres converts `timestamp` -> `timestamptz` by assuming
# the existing naive values are in the SESSION timezone. This application
# always wrote UTC (via datetime.utcnow, which returns a naive UTC
# reading), so on any server not running in UTC that default would shift
# every stored row by the local offset, silently. "AT TIME ZONE 'UTC'"
# states the assumption the data was actually written under.


def upgrade() -> None:
    op.alter_column('batches', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using="\"created_at\" AT TIME ZONE 'UTC'")
    op.alter_column('batches', 'sent_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True,
               postgresql_using="\"sent_at\" AT TIME ZONE 'UTC'")
    op.alter_column('signals', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using="\"created_at\" AT TIME ZONE 'UTC'")
    op.alter_column('signals', 'updated_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using="\"updated_at\" AT TIME ZONE 'UTC'")
    op.alter_column('signals', 'sent_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True,
               postgresql_using="\"sent_at\" AT TIME ZONE 'UTC'")
    op.alter_column('stats', 'date',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.Date(),
               existing_nullable=False,
               postgresql_using="\"date\"::date")


def downgrade() -> None:
    op.alter_column('stats', 'date',
               existing_type=sa.Date(),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using="\"date\"::timestamp")
    op.alter_column('signals', 'sent_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True,
               postgresql_using="\"sent_at\" AT TIME ZONE 'UTC'")
    op.alter_column('signals', 'updated_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using="\"updated_at\" AT TIME ZONE 'UTC'")
    op.alter_column('signals', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using="\"created_at\" AT TIME ZONE 'UTC'")
    op.alter_column('batches', 'sent_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True,
               postgresql_using="\"sent_at\" AT TIME ZONE 'UTC'")
    op.alter_column('batches', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using="\"created_at\" AT TIME ZONE 'UTC'")
