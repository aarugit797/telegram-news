"""timezone aware timestamps

Revision ID: c2ec3cd46234
Revises: c34ae0e67061
Create Date: 2026-09-11 17:41:14.649403

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import postgresql

revision: str = 'c2ec3cd46234'
down_revision: Union[str, None] = 'c34ae0e67061'
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
    op.alter_column('users', 'registered_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using="\"registered_at\" AT TIME ZONE 'UTC'")
    op.alter_column('users', 'last_active_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True,
               postgresql_using="\"last_active_at\" AT TIME ZONE 'UTC'")
    op.alter_column('messages', 'timestamp',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using="\"timestamp\" AT TIME ZONE 'UTC'")
    op.alter_column('summaries', 'covers_from',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using="\"covers_from\" AT TIME ZONE 'UTC'")
    op.alter_column('summaries', 'covers_to',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using="\"covers_to\" AT TIME ZONE 'UTC'")
    op.alter_column('summaries', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               postgresql_using="\"created_at\" AT TIME ZONE 'UTC'")
    op.alter_column('daily_costs', 'date',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.Date(),
               existing_nullable=False,
               postgresql_using="\"date\"::date")


def downgrade() -> None:
    op.alter_column('daily_costs', 'date',
               existing_type=sa.Date(),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using="\"date\"::timestamp")
    op.alter_column('summaries', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using="\"created_at\" AT TIME ZONE 'UTC'")
    op.alter_column('summaries', 'covers_to',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using="\"covers_to\" AT TIME ZONE 'UTC'")
    op.alter_column('summaries', 'covers_from',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using="\"covers_from\" AT TIME ZONE 'UTC'")
    op.alter_column('messages', 'timestamp',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using="\"timestamp\" AT TIME ZONE 'UTC'")
    op.alter_column('users', 'last_active_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True,
               postgresql_using="\"last_active_at\" AT TIME ZONE 'UTC'")
    op.alter_column('users', 'registered_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               postgresql_using="\"registered_at\" AT TIME ZONE 'UTC'")
