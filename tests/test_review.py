"""Review queue tests, against a real Postgres (see conftest.py)."""

from decimal import Decimal
from threading import Event, Thread
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.orm import Session

from receipt_iq.db import (
    CORRECTED,
    DEAD_LETTER,
    DISMISSED,
    NEEDS_REVIEW,
    PASSED,
    Base,
    get_engine,
    session_scope,
)
from receipt_iq.review import (
    CorrectionRejected,
    DocumentNotFound,
    NotReviewable,
    correct,
    corrections,
    dismiss,
    get_document,
    issue_json,
    review_queue,
    save_result,
)
from receipt_iq.schema import LineItem, Receipt
from receipt_iq.validation import validate

FIXED = Receipt(items=[LineItem(name="TEA", total_price=Decimal(5000))], total=Decimal(5000))
STILL_WRONG = Receipt(items=[LineItem(name="TEA", total_price=Decimal(5000))], total=Decimal(9000))


def answer(digest: str = "a", status: str = NEEDS_REVIEW, **fields: Any) -> dict[str, Any]:
    return {
        "digest": digest,
        "status": status,
        "provider": "gpt",
        "receipt": STILL_WRONG.model_dump(mode="json"),
        **fields,
    }


def failure(digest: str = "a") -> dict[str, Any]:
    return {
        "digest": digest,
        "status": DEAD_LETTER,
        "provider": "auto",
        "kind": "retries_exhausted",
        "reason": "down",
    }


def status_of(session: Session, digest: str = "a") -> str:
    document = get_document(session, digest)
    assert document is not None
    session.refresh(document)
    return document.status


def test_tables_match_the_migrations(database: None) -> None:
    # Fails if a column changes in db.py without a migration.
    with get_engine().connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def test_issue_keeps_where_the_problem_is_so_a_screen_can_highlight_it() -> None:
    issue = validate(STILL_WRONG, Decimal(100)).issues[0]
    assert issue_json(issue) == {
        "code": "total_mismatch",
        "message": issue.message,
        "expected": "5000",
        "actual": "9000",
        "location": "total",
    }


def test_queue_holds_only_what_needs_a_person_oldest_first(database: None) -> None:
    # Separate transactions, because Postgres's now() is the time a transaction began.
    # "z-first" sorts last by hash, so this fails if the queue isn't ordered by age.
    for result in (answer("z-first"), failure("a-second"), answer("fine", PASSED)):
        with session_scope() as session:
            save_result(session, result)
    with session_scope() as session:
        assert [document.digest for document in review_queue(session)] == ["z-first", "a-second"]


def test_queue_limit(session: Session) -> None:
    for digest in "abc":
        save_result(session, answer(digest))
    assert len(review_queue(session, limit=2)) == 2


def test_saving_twice_is_safe(session: Session) -> None:
    save_result(session, answer())
    save_result(session, answer())
    assert len(review_queue(session)) == 1


def test_success_replaces_an_earlier_dead_letter(session: Session) -> None:
    save_result(session, failure())
    save_result(session, answer(status=PASSED))
    document = get_document(session, "a")
    assert document is not None
    assert (document.status, document.failure_kind, document.failure_reason) == (
        PASSED,
        None,
        None,
    )


def test_failure_does_not_erase_an_answer(session: Session) -> None:
    save_result(session, answer(status=PASSED))
    save_result(session, failure())
    assert status_of(session) == PASSED


@pytest.mark.parametrize("result", [answer(status=PASSED), failure()])
def test_a_later_run_never_replaces_a_correction(session: Session, result: dict[str, Any]) -> None:
    save_result(session, answer())
    correct(session, "a", FIXED)
    save_result(session, result)
    document = get_document(session, "a")
    assert document is not None
    assert document.status == CORRECTED
    assert document.corrected_receipt == FIXED.model_dump(mode="json")


def test_a_later_run_never_replaces_a_dismissal(session: Session) -> None:
    save_result(session, failure())
    dismiss(session, "a", "not a receipt")
    save_result(session, answer(status=PASSED))
    assert status_of(session) == DISMISSED


def test_correction_keeps_the_model_answer_beside_it(session: Session) -> None:
    save_result(session, answer())
    document = correct(session, "a", FIXED)
    assert document.status == CORRECTED
    assert document.receipt == STILL_WRONG.model_dump(mode="json")
    assert document.corrected_receipt == FIXED.model_dump(mode="json")
    assert document.corrected_at is not None
    assert document.confirmed_mismatch is False
    assert review_queue(session) == []


def test_correction_that_does_not_add_up_is_refused(session: Session) -> None:
    save_result(session, answer())
    with pytest.raises(CorrectionRejected, match="total says 9000"):
        correct(session, "a", STILL_WRONG)
    assert status_of(session) == NEEDS_REVIEW


def test_correction_with_nothing_to_check_the_total_against_is_refused(session: Session) -> None:
    save_result(session, answer())
    with pytest.raises(CorrectionRejected, match="can't be checked"):
        correct(session, "a", Receipt(total=Decimal(5000)))


def test_confirmed_mismatch_is_saved_and_marked(session: Session) -> None:
    save_result(session, answer())
    document = correct(session, "a", STILL_WRONG, confirm_mismatch=True)
    assert (document.status, document.confirmed_mismatch) == (CORRECTED, True)


def test_dead_letter_can_be_corrected_with_no_model_answer(session: Session) -> None:
    save_result(session, failure())
    document = correct(session, "a", FIXED)
    assert document.status == CORRECTED
    assert document.receipt is None


def test_passed_receipt_can_be_corrected_because_passing_is_not_proof(session: Session) -> None:
    save_result(session, answer(status=PASSED))
    assert correct(session, "a", FIXED).status == CORRECTED


def test_correcting_twice_is_refused(session: Session) -> None:
    save_result(session, answer())
    correct(session, "a", FIXED)
    with pytest.raises(NotReviewable, match="already corrected"):
        correct(session, "a", FIXED)


def test_unknown_receipt_is_reported(session: Session) -> None:
    with pytest.raises(DocumentNotFound):
        correct(session, "missing", FIXED)
    with pytest.raises(DocumentNotFound):
        dismiss(session, "missing", "gone")


def test_dismiss_takes_it_out_of_the_queue_and_keeps_the_reason(session: Session) -> None:
    save_result(session, failure())
    document = dismiss(session, "a", "not a receipt")
    assert (document.status, document.failure_reason) == (DISMISSED, "not a receipt")
    assert review_queue(session) == []


def test_passed_receipt_cannot_be_dismissed(session: Session) -> None:
    save_result(session, answer(status=PASSED))
    with pytest.raises(NotReviewable, match="not waiting"):
        dismiss(session, "a", "changed my mind")


def test_corrections_leave_out_the_held_out_split(session: Session) -> None:
    for digest in ("train", "held-out"):
        save_result(session, answer(digest))
        correct(session, digest, FIXED)
    save_result(session, answer("waiting"))
    labeled = corrections(session, exclude={"held-out"})
    assert [document.digest for document in labeled] == ["train"]


def test_two_people_correcting_one_receipt_cannot_both_succeed(database: None) -> None:
    with session_scope() as session:
        save_result(session, answer())

    first_has_lock = Event()
    outcome: list[Exception] = []

    def second_person() -> None:
        first_has_lock.wait(timeout=5)
        try:
            with session_scope() as session:
                correct(session, "a", FIXED)
        except NotReviewable as refused:
            outcome.append(refused)

    thread = Thread(target=second_person)
    thread.start()
    with session_scope() as session:
        correct(session, "a", FIXED)
        first_has_lock.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert len(outcome) == 1
