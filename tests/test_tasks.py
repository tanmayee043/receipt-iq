"""Queue tests: Celery runs tasks in-process with fake providers, so no Redis or network."""

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from receipt_iq import tasks
from receipt_iq.evaluation import ExtractionError, TemporaryError
from receipt_iq.schema import LineItem, Receipt

IMAGE = b"receipt image bytes"
GOOD = Receipt(items=[LineItem(name="TEA", total_price=Decimal(5000))], total=Decimal(5000))
# The total doesn't match the items, which the validator should flag.
BAD_MATH = Receipt(items=[LineItem(name="TEA", total_price=Decimal(5000))], total=Decimal(9000))


class FakeProvider:
    name = "fake"
    version = "v1"

    def __init__(self, *outcomes: Receipt | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def extract(self, image: bytes) -> Receipt:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def __call__(self) -> "FakeProvider":
        return self


@pytest.fixture(autouse=True)
def eager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RECEIPT_IQ_DATA", str(tmp_path))
    tasks.app.conf.update(task_always_eager=True, task_eager_propagates=False)
    tasks.get_extractor.cache_clear()
    yield
    tasks.app.conf.update(task_always_eager=False)
    tasks.get_extractor.cache_clear()


def run(monkeypatch: pytest.MonkeyPatch, provider: FakeProvider) -> Any:
    monkeypatch.setitem(tasks.PROVIDERS, "fake", provider)
    return tasks.submit(IMAGE, "fake").get()


def dead_letters(root: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text("utf-8")) for p in (root / "dead_letter").glob("*.json")]


def test_valid_receipt_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run(monkeypatch, FakeProvider(GOOD))
    assert result["status"] == "passed"
    assert result["issues"] == []
    assert Receipt.model_validate(result["receipt"]) == GOOD


def test_failed_arithmetic_goes_to_review(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run(monkeypatch, FakeProvider(BAD_MATH))
    assert result["status"] == "needs_review"
    assert result["issues"]


def test_image_is_stored_under_its_hash_and_only_the_hash_is_queued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = run(monkeypatch, FakeProvider(GOOD))
    assert (tmp_path / "uploads" / result["digest"]).read_bytes() == IMAGE
    assert IMAGE.decode() not in json.dumps(result)


def test_same_receipt_twice_calls_the_provider_once(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider(GOOD)
    first = run(monkeypatch, provider)
    second = run(monkeypatch, provider)
    assert provider.calls == 1
    assert first["receipt"] == second["receipt"]


def test_task_id_comes_from_provider_and_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(tasks.PROVIDERS, "fake", FakeProvider(GOOD, GOOD))
    assert tasks.submit(IMAGE, "fake").id == tasks.submit(IMAGE, "fake").id


def test_temporary_error_is_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider(TemporaryError("overloaded"), TemporaryError("overloaded"), GOOD)
    assert run(monkeypatch, provider)["status"] == "passed"
    assert provider.calls == 3


def test_temporary_error_that_never_clears_is_dead_lettered_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failures = [TemporaryError("down") for _ in range(tasks.MAX_RETRIES + 1)]
    provider = FakeProvider(*failures, GOOD)
    result = run(monkeypatch, provider)
    assert result["status"] == "dead_letter"
    assert result["kind"] == "retries_exhausted"
    assert provider.calls == tasks.MAX_RETRIES + 1

    # Not cached, so it works once the provider is back, and the dead letter clears.
    assert run(monkeypatch, provider)["status"] == "passed"
    assert dead_letters(tmp_path) == []


def test_unreadable_receipt_is_dead_lettered_without_retrying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = FakeProvider(ExtractionError("invalid output"))
    result = run(monkeypatch, provider)
    assert result["status"] == "dead_letter"
    assert result["kind"] == "extraction_failed"
    assert provider.calls == 1
    assert dead_letters(tmp_path)[0]["reason"] == "invalid output"


def test_cached_failure_is_dead_lettered_again_without_calling_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(ExtractionError("invalid output"))
    run(monkeypatch, provider)
    assert run(monkeypatch, provider)["status"] == "dead_letter"
    assert provider.calls == 1


def test_setup_problem_fails_the_task_visibly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(tasks.PROVIDERS, "fake", FakeProvider(RuntimeError("bad key")))
    with pytest.raises(RuntimeError, match="bad key"):
        tasks.submit(IMAGE, "fake").get()
    assert not (tmp_path / "dead_letter").exists()


def test_unknown_provider_is_refused_at_submit() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        tasks.submit(IMAGE, "nope")


def test_tasks_stay_queued_until_finished() -> None:
    # What makes a crashed worker's receipt come back instead of disappearing.
    assert tasks.extract_receipt.acks_late is True
    assert tasks.extract_receipt.reject_on_worker_lost is True
