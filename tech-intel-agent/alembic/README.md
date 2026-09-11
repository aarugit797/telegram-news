# Migrations

Two completely independent Alembic setups, one per database. They share
no config, no `env.py`, and no version history.

```
alembic_news.ini            -> alembic/news/         -> techintel_news
alembic_conversation.ini    -> alembic/conversation/ -> techintel_conversation
```

Each `env.py` is hardcoded to exactly one database URL and one metadata
object:

| Setup          | URL from                              | Metadata                        |
| -------------- | ------------------------------------- | ------------------------------- |
| `news`         | `settings.database_url`               | `models_news.Base.metadata`     |
| `conversation` | `settings.conversation_database_url`  | `models_conversation.Base.metadata` |

## Why two setups and not one parameterised one

Autogenerate works by diffing a metadata object against a live database.
Point one `env.py` at both databases and each run sees the *other*
database's tables as unknown, and proposes dropping every one of them.
Two separate setups make that mistake impossible rather than merely
discouraged.

This is also why `models_news.py` and `models_conversation.py` each
declare their own `Base`.

## Usage

Run from `tech-intel-agent/`, with the venv active.

```bash
# News DB
alembic -c alembic_news.ini revision --autogenerate -m "what changed"
alembic -c alembic_news.ini upgrade head
alembic -c alembic_news.ini downgrade -1

# Conversation DB
alembic -c alembic_conversation.ini revision --autogenerate -m "what changed"
alembic -c alembic_conversation.ini upgrade head
```

`alembic -c <ini> check` exits non-zero when the models have drifted from
the database — useful in CI.

**A model change needs a migration generated against its own database.**
Changing `models_news.py` and running the conversation config will not
pick it up.

## Notes

- **Credentials.** `sqlalchemy.url` is blank in both ini files on
  purpose. Each `env.py` sets it at runtime from `app.core.config`, so a
  real password never lands in a tracked file.
- **pgvector.** The News DB's `signals.embedding` column needs the
  `vector` extension. The initial News migration runs
  `CREATE EXTENSION IF NOT EXISTS vector` itself rather than relying on
  `scripts/init-second-db.sh`, which only runs on first container init
  and never runs on RDS at all. Downgrade deliberately does not drop the
  extension — it is database-level, and dropping it would cascade into
  any other vector column.
- **Generated migrations import `pgvector.sqlalchemy`.** Autogenerate
  emits the column fully qualified as
  `pgvector.sqlalchemy.vector.VECTOR(dim=1024)` but does not add the
  import, so `alembic/news/script.py.mako` carries it.
- **Async.** Both URLs use the asyncpg driver, so each `env.py` builds an
  async engine and runs the (synchronous) migration machinery inside
  `connection.run_sync()`.
