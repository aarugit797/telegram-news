"""
STUB - to be built.

WHAT: Creates the async SQLAlchemy engine and session factory for
the News DB, connected through PgBouncer Pool 1.

WHY: Every component that touches the News DB needs a database
session. This file centralizes how that session is created and
configured (pool size, timeout) so it is consistent everywhere.

INPUT: settings.database_url (News DB connection string) from config.py.

OUTPUT: An async session factory - other files call
`async with get_news_session() as session:` to get a usable connection.

CONNECTS TO: Used by db/repository_news.py exclusively - no other
file should talk to the News DB session directly.
"""
