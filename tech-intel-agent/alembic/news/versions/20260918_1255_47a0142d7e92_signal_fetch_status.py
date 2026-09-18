"""signal fetch status

Revision ID: 47a0142d7e92
Revises: d8464382c9a0
Create Date: 2026-09-18 12:55:20.406343

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


revision: str = '47a0142d7e92'
down_revision: Union[str, None] = 'd8464382c9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Records WHY a signal's full_content is thin.

    NOT NULL with a server default rather than nullable. Every existing
    row predates the column and none of them recorded a reason, so any
    backfilled value is a guess - but 'ok' is the right guess to make:
    the overwhelming majority did fetch cleanly, and the alternative,
    leaving them NULL, forces every reader to handle a third state that
    means "written before we tracked this".

    The server_default stays in place afterwards rather than being
    dropped. Rows are written by the ORM today, but the default is what
    keeps a psql insert or a future backfill from producing a row whose
    status is silently absent - which is the exact failure this column
    exists to remove.
    """
    op.add_column(
        "signals",
        sa.Column(
            "fetch_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'ok'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("signals", "fetch_status")
