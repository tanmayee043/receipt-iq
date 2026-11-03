"""Postgres: where a receipt's result lives once the worker has finished with it.

One row per image, keyed by its hash. Redis only carries work; this is what the review
queue and the screens read, so it has to outlive Redis and the worker.

Tables change through migrations, not by hand:
    alembic upgrade head
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from functools import cache
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Engine,
    Index,
    Numeric,
    String,
    Text,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from receipt_iq.settings import get_settings

CONNECT_TIMEOUT_SECONDS = 5

PROCESSING = "processing"  # uploaded, and the worker hasn't finished
PASSED = "passed"
NEEDS_REVIEW = "needs_review"
DEAD_LETTER = "dead_letter"
FAILED = "failed"  # a setup problem or bug, so it's for whoever runs this, not a reviewer
CORRECTED = "corrected"  # a person fixed it
DISMISSED = "dismissed"  # a person decided there's nothing to extract


def database_url() -> str:
    return get_settings().database_url


@cache
def _engine(url: str) -> Engine:
    # pool_pre_ping: a worker can sit idle through a Postgres restart.
    return create_engine(
        url, pool_pre_ping=True, connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS}
    )


def get_engine() -> Engine:
    return _engine(database_url())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit if the block finishes, roll back if it raises."""
    with Session(get_engine(), expire_on_commit=False) as session, session.begin():
        yield session


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    # Reads updated_at back on every save, so it can still be used once the session closes.
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'passed', 'needs_review', 'dead_letter', 'failed', "
            "'corrected', 'dismissed')",
            name="ck_documents_status",
        ),
        # The review queue's query: everything waiting, oldest first.
        Index("ix_documents_status_created_at", "status", "created_at"),
    )

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str | None] = mapped_column(String(50))

    # The model's answer. Never changed by a correction, so the two can be compared.
    receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    met_target: Mapped[bool | None]
    seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))

    # Why it was dead-lettered or dismissed.
    failure_kind: Mapped[str | None] = mapped_column(String(50))
    failure_reason: Mapped[str | None] = mapped_column(Text)

    corrected_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # True when a person confirmed a correction that still doesn't add up.
    confirmed_mismatch: Mapped[bool] = mapped_column(server_default=text("false"))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
