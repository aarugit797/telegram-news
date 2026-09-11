"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

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
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
