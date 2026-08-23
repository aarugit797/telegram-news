from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from contextlib import asynccontextmanager

from app.core.config import settings

news_engine = create_async_engine(
    settings.database_url,
    pool_size=20,        
    pool_pre_ping=True,  
    echo=False,          
)

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