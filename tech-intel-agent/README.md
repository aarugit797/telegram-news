# tech-intel-agent

The application lives here. **See the [README at the repository root](../README.md)**
for what the product is, how it decides what to send, the architecture, and how to run
it.

This file used to carry its own copy of that description and drifted badly out of date —
it still described WhatsApp, Twilio and Claude long after the product moved to Telegram,
Gemini and Groq. One README, at the root, is what GitHub renders and the only one worth
keeping current.

## Layout

```
app/
  agents/      one per source: github, hackernews, arxiv, blogs, rss, plus the scheduler
  filters/     the two-stage rules + LLM gate, and the content fetcher with its SSRF guard
  processor/   dedup, source-capped selection, digest composition
  sender/      delivery queue worker
  responder/   the conversation side: guardrail, intent, four tools, composer
  prompts/     every prompt, separated from the code that calls it
  db/          models and repositories for the two databases
  core/        LLM client with credential rotation, embeddings, channels, config
  queues/      Redis: delivery queue, dead letter, rate limits, LLM budgets
alembic/       two independent migration chains, news and conversation
scripts/       reset_local.py
tests/
```

## Why prompts live in their own package

They are the product's voice, they change far more often than the code that calls them,
and their rationale is long. Keeping them beside the call site buried that reasoning in
functions that are otherwise short. Each prompt module's docstring records which real
output produced each rule — that history is the reason the rules survive editing.
