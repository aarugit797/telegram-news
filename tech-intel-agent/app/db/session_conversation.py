from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from contextlib import asynccontextmanager

from app.core.config import settings

# Separate engine, separate pool, pointed at the Conversation DB's
# own connection string - genuinely independent from news_engine.
conversation_engine = create_async_engine(
    settings.conversation_database_url,
    pool_size=10,        # matches our PgBouncer Pool 2 max connections decision
    pool_pre_ping=True,
    echo=False,
)

ConversationSessionLocal = async_sessionmaker(
    bind=conversation_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_conversation_session():
    """
    Same pattern as get_news_session() above, but for the
    Conversation DB. Used like:

        async with get_conversation_session() as session:
            ...do queries with session...
    """
    session = ConversationSessionLocal()
    try:
        yield session
    finally:
        await session.close()
