from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from contextlib import asynccontextmanager

from app.core.config import settings

# The engine holds the pool of reusable connections to the News DB.
# create_async_engine (not create_engine) is what makes this an
# async-capable engine - it requires the URL to specify the asyncpg
# driver, which we already set up in config.py:
#   postgresql+asyncpg://...
news_engine = create_async_engine(
    settings.database_url,
    pool_size=20,        # matches our PgBouncer Pool 1 max connections decision
    pool_pre_ping=True,  # checks a connection is still alive before handing it out
    echo=False,          # set True temporarily if you ever need to see raw SQL being run
)

# A session FACTORY - not a session itself. Calling this creates a
# new session each time. expire_on_commit=False means objects you
# fetched stay usable after a commit, instead of SQLAlchemy forcing
# a fresh database read the next time you touch them.
NewsSessionLocal = async_sessionmaker(
    bind=news_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_news_session():
    """
    The actual thing other files import and use, like:

        async with get_news_session() as session:
            ...do queries with session...

    This creates a fresh session, hands it to whoever is using it,
    and guarantees it gets closed afterward even if an error happens
    partway through - the `try/finally` below is what makes that
    guarantee, regardless of how the `with` block exits.
    """
    session = NewsSessionLocal()
    try:
        yield session
    finally:
        await session.close()
