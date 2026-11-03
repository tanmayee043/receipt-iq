"""The review queue: receipts a person has to look at, and what they decide.

    worker result ──► save_result ──► passed
                                 ├──► needs_review ─┐
                                 └──► dead_letter ──┴──► correct ──► corrected
                                                    └──► dismiss ──► dismissed

Rules that decide what a re-run may overwrite:
- A person's decision stands. A later run never replaces a correction or a dismissal.
- A failure doesn't erase an answer. If a re-run dead-letters a receipt that already has
  one, the answer stays. A success does replace an earlier dead letter.

A correction goes through the same validator as a model's answer. If it still doesn't add
up it's refused unless the person confirms it, because some real receipts misprint.
"""

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from receipt_iq.db import (
    CORRECTED,
    DEAD_LETTER,
    DISMISSED,
    NEEDS_REVIEW,
    Document,
)
from receipt_iq.schema import Receipt
from receipt_iq.validation import Issue, validate

TOLERANCE = Decimal(100)  # IDR, as in scripts/evaluate.py
DEFAULT_QUEUE_LIMIT = 50


class DocumentNotFound(LookupError):
    pass


class NotReviewable(ValueError):
    """A person already decided this one, or there is nothing to decide."""


class CorrectionRejected(ValueError):
    """The correction fails validation and wasn't confirmed."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("; ".join(issues) or "the total can't be checked against anything")
        self.issues = issues


def issue_json(issue: Issue) -> dict[str, str]:
    """An issue as stored, with `location` so a screen can highlight the wrong line."""
    return {key: str(value) for key, value in asdict(issue).items()}


def save_result(session: Session, result: dict[str, Any]) -> None:
    """Record what a worker found. Safe to call twice for the same result."""
    digest, status = result["digest"], result["status"]
    # Insert-then-lock so two workers finishing the same image can't both insert.
    session.execute(insert(Document).values(digest=digest, status=status).on_conflict_do_nothing())
    document = _locked(session, digest)

    if document.status in (CORRECTED, DISMISSED):
        return
    if status == DEAD_LETTER and document.receipt is not None:
        return

    document.status = status
    document.provider = result["provider"]
    document.receipt = result.get("receipt")
    document.issues = result.get("issues", [])
    document.attempts = result.get("attempts", [])
    document.met_target = result.get("met_target")
    document.seconds = result.get("seconds")
    document.failure_kind = result.get("kind")
    document.failure_reason = result.get("reason")


def get_document(session: Session, digest: str) -> Document | None:
    return session.get(Document, digest)


def review_queue(session: Session, limit: int = DEFAULT_QUEUE_LIMIT) -> list[Document]:
    """Everything waiting for a person, oldest first."""
    query = (
        select(Document)
        .where(Document.status.in_((NEEDS_REVIEW, DEAD_LETTER)))
        .order_by(Document.created_at, Document.digest)
        .limit(limit)
    )
    return list(session.scalars(query))


def correct(
    session: Session, digest: str, receipt: Receipt, confirm_mismatch: bool = False
) -> Document:
    """Save a person's fix. Passed receipts can be fixed too: passing isn't proof."""
    document = _locked(session, digest)
    if document.status in (CORRECTED, DISMISSED):
        raise NotReviewable(f"{digest} is already {document.status}")
    checked = validate(receipt, TOLERANCE)
    if not checked.passed and not confirm_mismatch:
        raise CorrectionRejected([issue.message for issue in checked.issues])

    document.status = CORRECTED
    document.corrected_receipt = receipt.model_dump(mode="json")
    document.confirmed_mismatch = not checked.passed
    document.corrected_at = datetime.now(UTC)
    return document


def dismiss(session: Session, digest: str, reason: str) -> Document:
    """Take a receipt out of the queue with no answer, such as a photo that isn't one."""
    document = _locked(session, digest)
    if document.status not in (NEEDS_REVIEW, DEAD_LETTER):
        raise NotReviewable(f"{digest} is {document.status}, not waiting for review")
    document.status = DISMISSED
    document.failure_kind = "dismissed"
    document.failure_reason = reason
    return document


def corrections(session: Session, exclude: set[str]) -> list[Document]:
    """Corrected receipts to use as labeled data, leaving out `exclude` (image hashes).

    Excluding the held-out split keeps its score honest: a test receipt whose answer has
    been seen and fixed would no longer be a test.
    """
    query = select(Document).where(Document.status == CORRECTED).order_by(Document.corrected_at)
    return [document for document in session.scalars(query) if document.digest not in exclude]


def _locked(session: Session, digest: str) -> Document:
    # Held until commit, so two people correcting one receipt can't both succeed.
    document = session.get(Document, digest, with_for_update=True, populate_existing=True)
    if document is None:
        raise DocumentNotFound(digest)
    return document
