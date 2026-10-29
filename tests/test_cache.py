from decimal import Decimal
from pathlib import Path

import pytest

from receipt_iq.cache import CachedExtractor
from receipt_iq.evaluation import ExtractionError
from receipt_iq.schema import Receipt


class Counting:
    """A provider that counts its calls and fails on b"bad"."""

    name = "counting+model"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, image: bytes) -> Receipt:
        self.calls += 1
        if image == b"bad":
            raise ExtractionError("model returned garbage")
        return Receipt(total=Decimal(len(image)))


def test_second_call_is_served_from_disk(tmp_path: Path) -> None:
    inner = Counting()
    cached = CachedExtractor(inner, "v1", tmp_path)
    first = cached.extract(b"receipt")
    second = cached.extract(b"receipt")
    assert first == second
    assert inner.calls == 1
    assert (cached.hits, cached.misses) == (1, 1)


def test_cache_survives_a_new_process(tmp_path: Path) -> None:
    CachedExtractor(Counting(), "v1", tmp_path).extract(b"receipt")
    inner = Counting()
    CachedExtractor(inner, "v1", tmp_path).extract(b"receipt")
    assert inner.calls == 0


def test_new_prompt_version_misses(tmp_path: Path) -> None:
    CachedExtractor(Counting(), "v1", tmp_path).extract(b"receipt")
    inner = Counting()
    CachedExtractor(inner, "v2", tmp_path).extract(b"receipt")
    assert inner.calls == 1


def test_failures_are_cached_too(tmp_path: Path) -> None:
    inner = Counting()
    cached = CachedExtractor(inner, "v1", tmp_path)
    for _ in range(2):
        with pytest.raises(ExtractionError, match="garbage"):
            cached.extract(b"bad")
    assert inner.calls == 1


def test_replayed_latency_is_the_original(tmp_path: Path) -> None:
    cached = CachedExtractor(Counting(), "v1", tmp_path)
    cached.extract(b"receipt")
    original = cached.last_seconds
    cached.extract(b"receipt")
    assert cached.last_seconds == original
