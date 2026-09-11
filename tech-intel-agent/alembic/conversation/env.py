"""
Alembic environment for the CONVERSATION database - and nothing else.

Mirror image of alembic/news/env.py: hardcoded to exactly one database
(settings.conversation_database_url) and exactly one metadata object
(models_conversation.Base.metadata). See that file's docstring for why
the two setups are kept completely separate rather than parameterised
into one.

models_conversation defines its own Base, distinct from the one in
models_news, precisely so these two metadata objects never see each
other's tables.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.models_conversation import Base

config = context.config

# The URL comes from Settings (which reads .env), never from the ini
# file - so a real password never sits in a tracked file.
config.set_main_option("sqlalchemy.url", settings.conversation_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL to stdout without connecting (alembic upgrade --sql)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    CONVERSATION_DATABASE_URL uses the asyncpg driver, so the engine must
    be an async one. Alembic's migration machinery is synchronous, so the
    actual work runs inside connection.run_sync().
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
