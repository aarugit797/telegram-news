This folder is currently empty except for `versions/`.

When we reach the database build step, we'll run `alembic init` properly
(or hand-write `env.py`) to wire it to `app.db.models_news` and
`app.core.config.settings`, so it can autogenerate migrations by
diffing our SQLAlchemy models against the actual database state.

Not done yet - this is a structural placeholder only.
