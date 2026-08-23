# Tech Intelligence Agent

A real-time tech intelligence agent that monitors GitHub, HackerNews,
arXiv, AI lab blogs, and tech newsletters, filters for genuinely
important signals using a hybrid rules+LLM filter, and delivers
natural-sounding WhatsApp notifications. Users can reply and ask
follow-up questions, answered strictly from verified source content
via RAG - never hallucinated.

## Status
Under active development. See project structure below for what's
built vs stubbed.

## Architecture
Two independent async processes sharing a Postgres (News DB +
Conversation DB) and Redis data layer:

- **Process One - Intelligence Pipeline** (`app/pipeline_main.py`):
  5 source agents -> hybrid filter -> News DB -> batching agent ->
  WhatsApp notification.
- **Process Two - WhatsApp Responder** (`app/responder/main.py`):
  Twilio webhook -> guardrail -> intent classifier -> LangGraph
  agent (4 tools) -> response composer -> reply.

## Local development
```
docker compose up -d
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in real values
```

## Tech stack
FastAPI, SQLAlchemy (async) + PostgreSQL + pgvector, Redis,
APScheduler, Claude (Anthropic), Voyage AI embeddings, LangGraph +
LangSmith, Twilio WhatsApp API, Sentry, Alembic. Deployed on AWS
(EC2 + RDS + ElastiCache).
