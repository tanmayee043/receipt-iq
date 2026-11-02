"""Router tests: fake providers and a fake clock, so no network and no waiting."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from receipt_iq.evaluation import ExtractionError, TemporaryError
from receipt_iq.router import Attempt, CircuitBreaker, Route, Router, load_routes, rank
from receipt_iq.schema import LineItem, Receipt

IMAGE = b"receipt"
GOOD = Receipt(items=[LineItem(name="TEA", total_price=Decimal(5000))], total=Decimal(5000))
FLAGGED = Receipt(items=[LineItem(name="TEA", total_price=Decimal(5000))], total=Decimal(9000))

CHEAP = Route("cheap", accuracy=0.8, cost_per_1000=1.0, median_seconds=10)
PRICEY = Route("pricey", accuracy=0.9, cost_per_1000=5.0, median_seconds=10)
WEAK = Route("weak", accuracy=0.3, cost_per_1000=0.0, median_seconds=5, local=True)


class FakeProvider:
    def __init__(self, *outcomes: Receipt | Exception) -> None:
        self.name = "fake"
        self.outcomes = list(outcomes)
        self.calls = 0

    def extract(self, image: bytes) -> Receipt:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def router(fakes: dict[str, FakeProvider], routes: list[Route], **options: Any) -> Router:
    return Router(routes, fakes.__getitem__, target=0.75, **options)


def outcomes(attempts: list[Attempt]) -> list[tuple[str, str]]:
    return [(attempt.provider, attempt.outcome) for attempt in attempts]


def test_rank_tries_cheapest_above_target_then_best_below() -> None:
    other = Route("other", accuracy=0.5, cost_per_1000=9.0, median_seconds=1)
    ranked = rank([PRICEY, WEAK, other, CHEAP], target=0.75)
    assert [route.provider for route in ranked] == ["cheap", "pricey", "other", "weak"]


def test_rank_breaks_cost_ties_by_speed() -> None:
    slow = Route("slow", accuracy=0.8, cost_per_1000=0.0, median_seconds=100)
    fast = Route("fast", accuracy=0.8, cost_per_1000=0.0, median_seconds=5)
    assert [route.provider for route in rank([slow, fast], target=0.75)] == ["fast", "slow"]


def test_local_only_never_uses_a_provider_that_leaves_the_machine() -> None:
    ranked = rank([CHEAP, PRICEY, WEAK], 0.75, local_only=True)
    assert [route.provider for route in ranked] == ["weak"]


def test_too_slow_provider_is_only_a_last_resort() -> None:
    slow_free = Route("slow", accuracy=0.8, cost_per_1000=0.0, median_seconds=110)
    ranked = rank([slow_free, CHEAP], target=0.75, max_seconds=60)
    assert [route.provider for route in ranked] == ["cheap", "slow"]


def test_answer_from_a_too_slow_provider_is_reported_as_missing_the_target() -> None:
    slow = Route("slow", accuracy=0.8, cost_per_1000=0.0, median_seconds=110)
    fakes = {"cheap": FakeProvider(TemporaryError("down")), "slow": FakeProvider(GOOD)}
    routed = router(fakes, [slow, CHEAP], max_seconds=60).extract(IMAGE)
    assert (routed.provider, routed.met_target) == ("slow", False)


def test_passing_answer_stops_at_the_cheapest() -> None:
    fakes = {"cheap": FakeProvider(GOOD), "pricey": FakeProvider(GOOD)}
    routed = router(fakes, [CHEAP, PRICEY]).extract(IMAGE)
    assert (routed.provider, routed.status, routed.met_target) == ("cheap", "passed", True)
    assert fakes["pricey"].calls == 0


def test_rate_limit_falls_back_to_the_next_provider() -> None:
    fakes = {"cheap": FakeProvider(TemporaryError("429")), "pricey": FakeProvider(GOOD)}
    routed = router(fakes, [CHEAP, PRICEY]).extract(IMAGE)
    assert routed.provider == "pricey"
    assert outcomes(routed.attempts) == [("cheap", "unavailable"), ("pricey", "passed")]
    assert routed.attempts[0].reason == "429"


def test_unreadable_receipt_falls_back_to_the_next_provider() -> None:
    fakes = {"cheap": FakeProvider(ExtractionError("bad")), "pricey": FakeProvider(GOOD)}
    routed = router(fakes, [CHEAP, PRICEY]).extract(IMAGE)
    assert outcomes(routed.attempts) == [("cheap", "failed"), ("pricey", "passed")]


def test_flagged_answer_gets_a_second_opinion() -> None:
    fakes = {"cheap": FakeProvider(FLAGGED), "pricey": FakeProvider(GOOD)}
    routed = router(fakes, [CHEAP, PRICEY]).extract(IMAGE)
    assert (routed.provider, routed.status) == ("pricey", "passed")
    assert outcomes(routed.attempts) == [("cheap", "needs_review"), ("pricey", "passed")]


def test_no_second_opinion_when_limited_to_one_answer() -> None:
    fakes = {"cheap": FakeProvider(FLAGGED), "pricey": FakeProvider(GOOD)}
    routed = router(fakes, [CHEAP, PRICEY], max_answers=1).extract(IMAGE)
    assert (routed.provider, routed.status) == ("cheap", "needs_review")
    assert fakes["pricey"].calls == 0


def test_answer_cap_limits_spending_and_review_shows_the_most_accurate() -> None:
    fakes = {
        "cheap": FakeProvider(FLAGGED),
        "pricey": FakeProvider(FLAGGED),
        "weak": FakeProvider(GOOD),
    }
    routed = router(fakes, [WEAK, CHEAP, PRICEY]).extract(IMAGE)
    assert (routed.provider, routed.status) == ("pricey", "needs_review")
    assert fakes["weak"].calls == 0


def test_only_a_provider_below_target_answering_is_reported() -> None:
    fakes = {"cheap": FakeProvider(TemporaryError("down")), "weak": FakeProvider(GOOD)}
    routed = router(fakes, [CHEAP, WEAK]).extract(IMAGE)
    assert (routed.provider, routed.met_target) == ("weak", False)


def test_any_unavailable_with_no_answer_is_temporary_so_the_queue_retries() -> None:
    fakes = {
        "cheap": FakeProvider(TemporaryError("down")),
        "pricey": FakeProvider(ExtractionError("bad")),
    }
    with pytest.raises(TemporaryError, match="cheap: unavailable down"):
        router(fakes, [CHEAP, PRICEY]).extract(IMAGE)


def test_all_unreadable_is_a_receipt_failure() -> None:
    fakes = {
        "cheap": FakeProvider(ExtractionError("bad")),
        "pricey": FakeProvider(ExtractionError("bad")),
    }
    with pytest.raises(ExtractionError, match="no provider could read") as caught:
        router(fakes, [CHEAP, PRICEY]).extract(IMAGE)
    assert not isinstance(caught.value, TemporaryError)


def test_setup_problem_falls_back_and_stops_calling_that_provider() -> None:
    fakes = {"cheap": FakeProvider(RuntimeError("bad key")), "pricey": FakeProvider(GOOD, GOOD)}
    routing = router(fakes, [CHEAP, PRICEY])
    assert routing.extract(IMAGE).provider == "pricey"
    second = routing.extract(IMAGE)
    assert outcomes(second.attempts) == [("cheap", "skipped"), ("pricey", "passed")]
    assert fakes["cheap"].calls == 1


def test_provider_that_cant_be_built_falls_back() -> None:
    def build(name: str) -> FakeProvider:
        if name == "cheap":
            raise RuntimeError("set the API key")
        return FakeProvider(GOOD)

    assert Router([CHEAP, PRICEY], build, target=0.75).extract(IMAGE).provider == "pricey"


def test_repeated_failures_open_the_breaker_for_later_receipts() -> None:
    down = [TemporaryError("down") for _ in range(3)]
    fakes = {"cheap": FakeProvider(*down), "pricey": FakeProvider(GOOD, GOOD, GOOD, GOOD)}
    routing = router(fakes, [CHEAP, PRICEY])
    for _ in range(4):
        assert routing.extract(IMAGE).provider == "pricey"
    assert fakes["cheap"].calls == 3


class FakeClock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def test_breaker_opens_after_threshold_in_a_row() -> None:
    breaker = CircuitBreaker(threshold=3, cooldown=300, clock=FakeClock())
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()
    assert not breaker.allow()


def test_success_resets_the_count() -> None:
    breaker = CircuitBreaker(threshold=2, cooldown=300, clock=FakeClock())
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.allow()


def test_breaker_tries_again_after_the_cooldown_and_closes_on_success() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=1, cooldown=300, clock=clock)
    breaker.record_failure()
    clock.now = 299
    assert not breaker.allow()
    clock.now = 300
    assert breaker.allow()
    breaker.record_success()
    assert breaker.allow()


def test_failed_trial_after_cooldown_reopens_for_a_full_cooldown() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=2, cooldown=300, clock=clock)
    breaker.trip()
    clock.now = 300
    assert breaker.allow()
    breaker.record_failure()
    clock.now = 500
    assert not breaker.allow()


def test_load_routes_reads_the_leaderboard(tmp_path: Path) -> None:
    path = tmp_path / "leaderboard.json"
    entry = {
        "provider": "gpt",
        "local": False,
        "fully_correct": 0.7,
        "cost_per_1000": 1.1,
        "median_seconds": 8.0,
    }
    path.write_text(json.dumps({"providers": [entry]}), encoding="utf-8")
    assert load_routes(path) == [Route("gpt", 0.7, 1.1, 8.0, False)]
