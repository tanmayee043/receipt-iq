"""Runs migrations against DATABASE_URL (see receipt_iq.db)."""

from alembic import context
from sqlalchemy import create_engine

from receipt_iq.db import Base, database_url

engine = create_engine(database_url())
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
