"""
STUB - to be built.

WHAT: Creates the async SQLAlchemy engine and session factory for
the Conversation DB, connected through PgBouncer Pool 2.

WHY: Same reasoning as session_news.py, but for the separate
Conversation DB - kept as its own engine/pool entirely.

INPUT: A second database URL (Conversation DB connection string).

OUTPUT: An async session factory for Conversation DB access.

CONNECTS TO: Used by db/repository_conversation.py exclusively.
"""
