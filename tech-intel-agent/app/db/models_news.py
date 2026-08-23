"""
STUB - to be built.

WHAT: SQLAlchemy table definitions for the News DB - the "signals",
"batches", and "stats" tables we designed.

WHY: This is the single source of truth for what a "signal" (an
approved piece of news) looks like in the database, and how batches
of signals sent together are tracked.

TABLES:
- signals: id, source, title, url, full_content, summary,
  novelty_score, relevance_score, applicability_score, composite_score,
  filter_justification, created_at, updated_at, is_sent, sent_at,
  batch_id, is_deleted, embedding (vector column via pgvector)
- batches: id, created_at, sent_at, user_count, signal_ids, delivery_status
- stats: date, source, signals_fetched, signals_passed_rules,
  signals_passed_llm, signals_sent, total_tokens_used, estimated_cost

CONNECTS TO: Used by db/repository_news.py to build queries. Alembic
reads this file to auto-generate migrations.
"""
