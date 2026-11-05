"""Choose a provider for each receipt, using what the evaluation measured.

Policy: try the cheapest provider that met the accuracy target and speed limit (ties go
to the fastest). Providers that missed either are a last resort, most accurate first.

    ranked providers ──► circuit breaker open? skip ──► extract ──► validate ──► passed: done
                                                           │            │
                                     unavailable or failed: next    flagged: next, up to
                                                                    `max_answers`

Every decision is recorded in `attempts`, so the routing log can show why a provider
handled a receipt.

A flagged answer is worth a second opinion, but a passing one isn't proof: the validator
can't see wrong amounts that still add up. So routing can't be more accurate than the
validator lets it be, which scripts/simulate_routing.py measures.
"""

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from receipt_iq.evaluation import ExtractionError, Extractor, TemporaryError
from receipt_iq.schema import Receipt
from receipt_iq.validation import validate

# Chosen on the validation split: at 75% GPT doesn't qualify, so routing saves almost nothing.
DEFAULT_TARGET = 0.70
# Someone is waiting on an upload, so a free model taking minutes doesn't qualify.
DEFAULT_MAX_SECONDS = 60.0
TOLERANCE = Decimal(100)  # IDR, as in scripts/evaluate.py


@dataclass(frozen=True)
class Route:
    """One provider's measured numbers, from leaderboard.json."""

    provider: str
    accuracy: float
    cost_per_1000: float
    median_seconds: float
    # True when nothing leaves this machine, the only option for sensitive documents.
    local: bool = False


def load_routes(path: Path) -> list[Route]:
    entries = json.loads(path.read_text(encoding="utf-8"))["providers"]
    return [
        Route(
            provider=entry["provider"],
            accuracy=entry["fully_correct"],
            cost_per_1000=entry["cost_per_1000"],
            median_seconds=entry["median_seconds"],
            local=entry["local"],
        )
        for entry in entries
    ]


def rank(
    routes: list[Route],
    target: float,
    local_only: bool = False,
    max_seconds: float = math.inf,
) -> list[Route]:
    """The order to try providers in."""
    usable = [route for route in routes if route.local or not local_only]
    good_enough = [r for r in usable if qualifies(r, target, max_seconds)]
    others = [r for r in usable if not qualifies(r, target, max_seconds)]
    return sorted(good_enough, key=lambda r: (r.cost_per_1000, r.median_seconds)) + sorted(
        others, key=lambda r: -r.accuracy
    )


def qualifies(route: Route, target: float, max_seconds: float) -> bool:
    return route.accuracy >= target and route.median_seconds <= max_seconds


class CircuitBreaker:
    """Stop calling a provider that keeps failing, and try it again after a cool-down.

    Without this, every receipt would wait through a dead provider's full retries before
    moving on. After `threshold` failures in a row the breaker opens; once `cooldown`
    seconds pass, one call is let through, and its outcome closes or reopens the breaker.
    """

    def __init__(
        self,
        threshold: int = 3,
        cooldown: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        return self._clock() - self._opened_at >= self.cooldown

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = self._clock()

    def trip(self) -> None:
        """Open right away, for failures that repeating can't fix (a wrong key)."""
        self._failures = self.threshold
        self._opened_at = self._clock()


@dataclass(frozen=True)
class Attempt:
    provider: str
    # passed, needs_review, failed, unavailable or skipped (breaker open)
    outcome: str
    reason: str = ""
    seconds: float = 0.0


@dataclass
class Routed:
    receipt: Receipt
    provider: str
    status: str
    attempts: list[Attempt]
    # False when only a provider missing the accuracy target or speed limit could answer.
    met_target: bool = True


@dataclass
class Router:
    routes: list[Route]
    get_extractor: Callable[[str], Extractor]
    target: float = DEFAULT_TARGET
    max_seconds: float = DEFAULT_MAX_SECONDS
    # Answers to pay for on one receipt: 1 means never ask for a second opinion.
    max_answers: int = 2
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    def extract(self, image: bytes, local_only: bool = False) -> Routed:
        attempts: list[Attempt] = []
        flagged: list[tuple[Route, Receipt]] = []
        anything_unavailable = False

        for route in rank(self.routes, self.target, local_only, self.max_seconds):
            breaker = self.breakers.setdefault(route.provider, CircuitBreaker())
            if not breaker.allow():
                attempts.append(Attempt(route.provider, "skipped", "circuit breaker open"))
                anything_unavailable = True
                continue
            start = time.perf_counter()
            try:
                extractor = self.get_extractor(route.provider)
                receipt = extractor.extract(image)
            except TemporaryError as failure:
                # Checked before ExtractionError, which it extends.
                breaker.record_failure()
                anything_unavailable = True
                attempts.append(self._attempt(route, "unavailable", start, str(failure)))
                continue
            except ExtractionError as failure:
                # The provider is working; it just can't read this receipt.
                breaker.record_success()
                attempts.append(self._attempt(route, "failed", start, str(failure)))
                continue
            except RuntimeError as failure:
                # Wrong key or retired model: the other providers can still answer.
                breaker.trip()
                anything_unavailable = True
                attempts.append(self._attempt(route, "unavailable", start, str(failure)))
                continue

            breaker.record_success()
            seconds = getattr(extractor, "last_seconds", None)
            if validate(receipt, TOLERANCE).passed:
                attempts.append(self._attempt(route, "passed", start, seconds=seconds))
                return Routed(receipt, route.provider, "passed", attempts, self._met(route))
            attempts.append(self._attempt(route, "needs_review", start, seconds=seconds))
            flagged.append((route, receipt))
            if len(flagged) >= self.max_answers:
                break

        if flagged:
            # All flagged, so a person decides; show them the most accurate provider's answer.
            route, receipt = max(flagged, key=lambda pair: pair[0].accuracy)
            return Routed(receipt, route.provider, "needs_review", attempts, self._met(route))
        summary = "; ".join(f"{a.provider}: {a.outcome} {a.reason}".strip() for a in attempts)
        if anything_unavailable:
            # Could still work once a provider recovers, so the queue should retry.
            raise TemporaryError(f"no provider available ({summary})")
        raise ExtractionError(f"no provider could read this receipt ({summary})")

    def _met(self, route: Route) -> bool:
        return qualifies(route, self.target, self.max_seconds)

    def _attempt(
        self,
        route: Route,
        outcome: str,
        start: float,
        reason: str = "",
        seconds: float | None = None,
    ) -> Attempt:
        elapsed = time.perf_counter() - start
        return Attempt(route.provider, outcome, reason, elapsed if seconds is None else seconds)
