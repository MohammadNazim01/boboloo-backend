from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from alembic import context

from app.core.config import settings
from app.database.database import Base
from app.database import models  # IMPORTANT → load models

from sqlalchemy.ext.asyncio import async_engine_from_config
import asyncio


# =====================================================
# ALEMBIC CONFIG
# =====================================================
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ✅ Tell Alembic about your models
target_metadata = Base.metadata


# =====================================================
# DATABASE URL FROM .env
# =====================================================
config.set_main_option(
    "sqlalchemy.url",
    settings.DATABASE_URL
)


# =====================================================
# OFFLINE MIGRATION
# =====================================================
def run_migrations_offline() -> None:

    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# =====================================================
# ONLINE MIGRATION (ASYNC ✅)
# =====================================================
def do_run_migrations(connection: Connection):

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# =====================================================
# ENTRYPOINT
# =====================================================
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()