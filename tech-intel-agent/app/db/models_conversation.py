"""
STUB - to be built.

WHAT: SQLAlchemy table definitions for the Conversation DB - "users",
"messages", "summaries", and "daily_costs" tables.

WHY: Completely separate database from News DB (per our locked
architecture) so news content and user conversation data never mix,
and can be scaled/secured independently.

TABLES:
- users: id, whatsapp_number, display_name, registered_at, is_active,
  is_whitelisted, daily_message_count, last_active_at
- messages: id, user_id, direction, message_text, intent_classification,
  guardrail_result, tool_used, langsmith_run_id, tokens_used,
  estimated_cost, timestamp, is_deleted
- summaries: id, user_id, summary_text, covers_from, covers_to, created_at
- daily_costs: date, user_id, llm_calls, total_tokens, estimated_cost_usd

CONNECTS TO: Used by db/repository_conversation.py. Alembic reads
this file to auto-generate migrations for this DB.
"""
