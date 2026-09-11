"""
Alembic environment for the NEWS database - and nothing else.

This file is deliberately hardcoded to exactly one database
(settings.database_url) and exactly one metadata object
(models_news.Base.metadata). The Conversation DB has its own entirely
separate setup under alembic/conversation/.

Why two setups instead of one parameterised one: autogenerate works by
diffing a metadata object against a live database. Point a single
env.py at both and each run sees the *other* database's tables as
unknown, and cheerfully proposes dropping every one of them. Two
independent setups make that mistake impossible to make.

Importing Base from models_news also imports the model classes defined
alongside it, which is what registers them on Base.metadata - without
that import the metadata would be empty and autogenerate would propose
dropping the whole schema.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.models_news import Base

config = context.config

# The URL comes from Settings (which reads .env), never from the ini
# file - so a real password never sits in a tracked file.
config.set_main_option("sqlalchemy.url", settings.database_url)

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
        # compare_type catches a column whose TYPE changed (e.g.
        # String(50) -> String(100)), which alembic ignores by default.
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    DATABASE_URL uses the asyncpg driver, so the engine must be an async
    one. Alembic's migration machinery is synchronous, so the actual
    work runs inside connection.run_sync().
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
