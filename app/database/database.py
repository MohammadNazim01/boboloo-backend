from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

from app.core.config import settings


# =====================================================
# BASE MODEL
# =====================================================

Base = declarative_base()


# =====================================================
# DATABASE ENGINE
# =====================================================

if settings.ENVIRONMENT == "production":

    # Production containers (Cloud Run / AWS)
    # NullPool: no persistent pool — each request gets a fresh connection.
    # pool_pre_ping is meaningless with NullPool and is omitted.
    engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
        echo=False,
    )

else:

    # Local development
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
        echo=False,
    )


# =====================================================
# SESSION FACTORY
# =====================================================

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# =====================================================
# FASTAPI DB DEPENDENCY
# =====================================================

async def get_db():

    async with AsyncSessionLocal() as session:

        try:
            yield session

        except Exception:
            await session.rollback()
            raise

        finally:
            await session.close()