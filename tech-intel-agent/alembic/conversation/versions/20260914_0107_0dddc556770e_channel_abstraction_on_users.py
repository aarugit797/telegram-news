"""channel abstraction on users

Revision ID: 0dddc556770e
Revises: 39fa928b6fcc
Create Date: 2026-09-14 01:07:49.275725

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0dddc556770e'
down_revision: Union[str, None] = '39fa928b6fcc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # HAND-EDITED. Autogenerate produced three statements that would each
    # have failed or lost data on a table that already has rows:
    #
    #   1. add_column(..., nullable=False) with no server_default, on a
    #      populated table - Postgres rejects that outright, because the
    #      existing rows would have no value.
    #   2. drop_column('whatsapp_number') with no backfill - that column
    #      IS the user's identity. Dropping it straight would orphan
    #      every existing user from their message history.
    #   3. The two in an order where the data is gone before the new
    #      columns can be populated from it.
    #
    # The corrected sequence is add-nullable, backfill, enforce, drop.

    # 1. Nullable first, so existing rows are valid while empty.
    op.add_column('users', sa.Column('channel', sa.String(length=20), nullable=True))
    op.add_column('users', sa.Column('channel_user_id', sa.String(length=100), nullable=True))

    # 2. Backfill from the column being replaced. Every pre-existing row
    #    was reachable only over WhatsApp, so that is its channel, and
    #    its phone number is its id on that channel.
    op.execute(
        "UPDATE users SET channel = 'whatsapp', channel_user_id = whatsapp_number "
        "WHERE channel_user_id IS NULL"
    )

    # 3. Only now can NOT NULL hold.
    op.alter_column('users', 'channel', nullable=False)
    op.alter_column('users', 'channel_user_id', nullable=False)

    # 4. Swap the uniqueness rule. The old constraint has to go before
    #    the column it covers can be dropped.
    op.drop_constraint('users_whatsapp_number_key', 'users', type_='unique')
    op.create_unique_constraint('uq_users_channel_user', 'users', ['channel', 'channel_user_id'])

    op.drop_column('users', 'whatsapp_number')


def downgrade() -> None:
    # Mirrors the upgrade, and likewise refuses to lose data: the phone
    # number is restored from channel_user_id before anything is dropped.
    #
    # Rows whose channel is NOT whatsapp have no phone number to restore
    # - a Telegram chat_id is not one - so they are DELETED rather than
    # given a fabricated value. That is lossy and deliberate: the old
    # schema simply cannot represent them, and inventing a number to
    # satisfy NOT NULL would corrupt the whitelist.
    op.add_column('users', sa.Column('whatsapp_number', sa.VARCHAR(length=20), nullable=True))
    op.execute("UPDATE users SET whatsapp_number = channel_user_id WHERE channel = 'whatsapp'")
    op.execute("DELETE FROM users WHERE whatsapp_number IS NULL")
    op.alter_column('users', 'whatsapp_number', nullable=False)

    op.drop_constraint('uq_users_channel_user', 'users', type_='unique')
    op.create_unique_constraint('users_whatsapp_number_key', 'users', ['whatsapp_number'])

    op.drop_column('users', 'channel_user_id')
    op.drop_column('users', 'channel')
