import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Neon's pooler (PgBouncer, transaction mode) can't keep prepared statements between queries.
engine = create_async_engine(
    os.environ["DATABASE_URL"],
    connect_args={"prepare_threshold": None},
    pool_pre_ping=True,
)
Session = async_sessionmaker(engine, expire_on_commit=False)
