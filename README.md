# Tech Intelligence Agent

**Five tech sources, twice a day, five items, on Telegram — and you can ask it questions.**

Keeping up with tech means checking GitHub trending, HackerNews, arXiv, half a dozen
lab blogs and a pile of newsletters. Most of it is noise, and the good parts arrive
whenever they arrive. This reads all of it for you, throws away the ~95% that does not
matter, and sends what is left as two short digests a day.

Then — and this is the part that makes it different from an RSS reader — you can reply
and ask about anything it sent.

---

## What it actually looks like

A real digest, sent this morning:

```
1. Claude-Red offers structured SKILL.md covering SQLi, shellcode, EDR evasion, exploits
https://github.com/SnailSploit/Claude-Red

2. Octop Python self-hosted AI assistant supports multiple users and agents
https://github.com/TencentCloud/Octop

3. Obstacle-Aware Harness boosts task success to 71.9% and collision avoidance to 87.5%
http://arxiv.org/abs/2609.20822v1

4. Rustacean attacks campaign targets Rust developers using video calls, clipboard tricks
https://simonwillison.net/2026/Sep/17/targeted-attacks-on-rustaceans/

5. Qwen Flash performs like Gemini 3.8 Flash with $0.15 input, $0.47 output per 1M tokens
https://qwen.ai/blog?id=qwen3.8-omni-flash

Reply with a number to go deeper on any of these.
```

Bold name first, so your eye can scan it in five seconds without reading a word of the
rest. Then you reply. These are real replies to that exact digest:

> **you:** Can h elaborate Claude red
>
> **bot:** Claude-Red is a library of structured SKILL.md files that turn Claude into a
> domain-specialist red-team operator, covering 78 skills across 23 categories such as
> SQLi, shellcode, and EDR evasion. Drop the repo into `~/.claude/skills/claude-red` and
> Claude will auto-load the relevant skill when you mention the attack surface.

> **you:** Can u elaborate point 5
>
> **bot:** Qwen 3.8 Omni Flash is a large-language model matching Gemini 3.8 Flash on
> text and audio-visual tasks while costing less to run. Its inference price is $0.15
> per 1,000 input tokens and $0.47 per 1,000 output tokens, compared with Gemini's
> $1.50 and $9.00. **That's the only detail I have; the page couldn't be read.** See the
> original post on Qwen.ai.

Two things worth noticing. "Point 5" resolves, because the digest and the conversation
share one history and the item order is stored rather than re-derived.

And that last sentence is the interesting one: qwen.ai renders its article in
JavaScript, so there was nothing in the HTML to read. The bot says so instead of
padding out the summary it does have. It would rather be short than sound confident
about something it never saw.

---

## Who it is for

Someone technical who wants to *know what happened* without spending an hour a day
finding out. It assumes you can read "a Rust rewrite of SQLite with async IO" and know
whether you care.

It is deliberately **not** a feed. No infinite scroll, no engagement loop, five items
and a full stop.

---

## How it decides what is worth sending

Roughly 170 GitHub repos, 30 HN stories and 50 arXiv papers come in on a normal
morning. Five go out. Three things do the cutting:

**A two-stage filter, cheap first.** Rules run first and cost nothing — they drop most
candidates on stars, age, keywords. Only survivors reach an LLM, which scores novelty,
relevance and applicability from 1–5. The mean has to clear **3.5**.

**Per-source caps.** Selection used to be pure top-N by score, which is source-blind —
one real digest came out as five GitHub repos because GitHub had a good day. Caps are
`github:2, arxiv:1, blogs:1, rss:1, hackernews:1`, and they are maximums rather than
reservations: if no paper cleared the bar today, the slot fills from whatever else is
best. A full digest matters more than strict balance.

**Semantic dedup.** The same story arriving from HN and a newsletter is clustered into
one item, and only the highest-scoring member is sent.

Anything not sent stays in the pool and competes again in the evening.

### Breaking news bypasses all of it

AI lab blogs are polled every 30 minutes and checked for genuine urgency. A model
release does not wait for 9pm. Nothing else gets this treatment, because nothing else
is reliably worth interrupting someone for.

---

## Architecture

Two processes that never call each other. They share Postgres and Redis.

```
PROCESS 1 — pipeline                          PROCESS 2 — responder
                                              
  github    08:00, 20:00                        Telegram long-poll
  hackernews 08:15, 20:15                             │
  arxiv      08:30                                    ▼
  rss        08:45                              whitelist → rate limit
  blogs      every 30 min                             │
      │                                               ▼
      ▼                                         guardrail  (injection / off-topic)
  rules filter  ──drop──▶                             │
      │                                               ▼
      ▼                                         intent classifier
  LLM filter    ──drop──▶ rejected cache              │
      │                                    ┌──────────┼──────────┬───────────┐
      ▼                                    ▼          ▼          ▼           ▼
  fetch content                        digest     RAG over   web search  smalltalk
      │                                history    pgvector
      ▼                                    └──────────┴──────────┴───────────┘
  embed (1024-d) ──▶ Postgres + pgvector              │
      │                                               ▼
      ▼                                       response composer
  dedup → select 5 → compose                          │
      │                                               ▼
      ▼                                            reply
  Redis queue ──▶ sender ──▶ Telegram
```

**Everything the reader sees is grounded.** The tools answer strictly from stored
source content, never from the model's own knowledge. If the source does not say it,
the bot says it does not know — including when a page failed to fetch, which it tracks
explicitly rather than quietly answering from a one-line summary.

**Links are assembled in Python, never written by the model.** Models reproduce URLs
unreliably, and a digest whose entire value is "here is the thing" cannot ship links
that 404.

---

## Stack

Python 3.11 · FastAPI · SQLAlchemy 2.0 async + asyncpg · PostgreSQL + **pgvector**
(HNSW) · Redis · APScheduler · **Gemini 3.5 Flash** with a Groq fallback ·
**Voyage AI** embeddings · LangGraph · Alembic (two independent migration chains) ·
Sentry

**LLM credentials rotate.** Three Gemini projects plus Groq, with per-credential
sliding-window rate limiting in Redis, daily budgets, and cooldown-driven rotation.
Pipeline and responder draw from separate lanes, so a heavy morning harvest cannot
starve someone mid-conversation.

---

## Running it locally

```bash
docker compose up -d                  # postgres + pgvector, redis
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                  # add your keys

alembic -c alembic_news.ini upgrade head
alembic -c alembic_conversation.ini upgrade head

python -m app.pipeline_main           # process 1: agents + scheduler
python -m app.responder.main          # process 2: conversation
```

Useful during development:

```bash
python scripts/reset_local.py --yes   # wipe both DBs + Redis, keep the user whitelist
pytest                                # 75 tests
```

Agents run on a schedule, so to see a digest immediately, trigger the stages by hand —
`run_github_agent()`, then `run_digest()`, then `run_sender_worker()`.

---

## Notes from building it

A few things that were not obvious and cost real debugging:

**`max_tokens` is not a brevity control.** Reasoning tokens are billed against the same
budget and never returned, so a tight ceiling does not shorten an answer — it truncates
it mid-clause. A reply that stopped at "The repository also" logged 41 output tokens
against a ceiling of 1024. The prompt controls length; the ceiling is a safety limit.

**An offset is an acknowledgement, not a bookmark.** The Telegram poller advanced its
offset after processing returned — and a failed send still returns. Replies that cost
five LLM calls were discarded while Telegram was told the message was handled. It now
advances only once the reply is confirmed sent.

**Prompt size is a rate limit.** The digest prompt grew to 18.7k characters across six
tuning passes. At that size Gemini's free tier returned 429 on the first call of every
run and the composer silently fell back to a weaker model — so three rounds of prompt
tuning were measured against the wrong thing. Rationale now lives in docstrings, which
are free; only rules go to the model.

**Fetching an attacker-supplied URL is a read primitive into your own network.** Links
come from HackerNews submissions, so on EC2 a redirect to `169.254.169.254` would put
IAM credentials into the database and then into a RAG answer. Redirects are followed
one hop at a time and every resolved address is checked before connecting.

---

## Status

Working end to end and used daily. Under active development — the LLM supply is the
current constraint, since free-tier limits make a heavy harvest and a live conversation
compete for the same quota.
