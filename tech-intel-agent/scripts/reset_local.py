"""
Wipes local state back to empty so a run can be tested end to end.

    python scripts/reset_local.py

Clears both databases and all three Redis logical databases, and KEEPS
the users table - deleting that would drop the whitelist and the bot
would answer its own owner with "Invite-only for now."

ONE STATEMENT PER TRANSACTION, deliberately. A first version batched
these and included a table name that did not exist. In Postgres a failed
statement aborts the whole transaction, so the error was caught in
Python, the commit that followed rolled back every delete with it, and
the wipe silently did nothing - the agents then ran against old data and
reported duplicates against a database that was supposed to be empty.
Committing each statement on its own means one wrong table name costs
that statement and nothing else.

WHAT THIS COSTS YOU. Wiping `signals` also wipes the memory that makes
duplicate detection work: signal_exists_by_url asks whether a url is
already a row, so anything currently trending can arrive again as new.
That is the point of a clean slate, but it does mean the first digest
after a reset may repeat items you have already seen.
"""
import asyncio
import sys

from sqlalchemy import text

from app.db.session_conversation import get_conversation_session
from app.db.session_news import get_news_session
from app.queues.redis_client import _client_for_db

# Order matters: signals reference batches, so the link is dropped before
# the parent rows go.
NEWS_STATEMENTS = [
    "UPDATE signals SET batch_id = NULL",
    "DELETE FROM batches",
    "DELETE FROM signals",
    "DELETE FROM rejected_signals",
    "DELETE FROM stats",
]

CONVERSATION_STATEMENTS = [
    "DELETE FROM summaries",
    "DELETE FROM messages",
    "DELETE FROM daily_costs",
    "UPDATE users SET daily_message_count = 0",
]


async def run(session_factory, statements, label):
    print(f"\n{label}")
    for statement in statements:
        async with session_factory() as session:
            try:
                result = await session.execute(text(statement))
                await session.commit()
                print(f"  {statement:<40} {result.rowcount:>5} rows")
            except Exception as e:
                print(f"  {statement:<40} FAILED: {type(e).__name__}")


async def main():
    await run(get_news_session, NEWS_STATEMENTS, "news database")
    await run(get_conversation_session, CONVERSATION_STATEMENTS, "conversation database")

    print("\nredis")
    for db, purpose in ((0, "delivery queue"), (1, "dead letter"), (2, "cache, rate limits, LLM budgets")):
        client = _client_for_db(db)
        keys = await client.keys("*")
        if keys:
            await client.delete(*keys)
        print(f"  db{db} {purpose:<34} {len(keys):>5} keys cleared")

    print("\nverifying")
    async with get_news_session() as session:
        for table in ("signals", "batches", "rejected_signals"):
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
            print(f"  news.{table:<20} {count}")
    async with get_conversation_session() as session:
        for table in ("messages", "summaries", "daily_costs"):
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
            print(f"  conversation.{table:<12} {count}")
        result = await session.execute(text(
            "SELECT channel, channel_user_id FROM users ORDER BY channel"))
        for channel, channel_user_id in result.all():
            print(f"  kept user: {channel} {channel_user_id}")


if __name__ == "__main__":
    if "--yes" not in sys.argv:
        print(__doc__)
        print("Re-run with --yes to actually wipe.")
        sys.exit(1)
    asyncio.run(main())
