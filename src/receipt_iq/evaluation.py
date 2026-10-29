"""Score extracted receipts against labeled answers.

Three questions, per provider:
1. Amount fields: is each printed amount (total, tax, ...) right?
2. Line items: did it find the real items (recall) without inventing any
   (precision)? An item matches when its price is right and its name is close
   enough, since OCR routinely mangles names ("CHIKEN" for "CHICKEN").
3. Validator: of the receipts it got wrong, how many did the arithmetic check
   catch (catch rate), and of the ones it got right, how many were flagged
   anyway (false alarms)?
"""

import re
import statistics
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Protocol

from receipt_iq.schema import LineItem, Receipt
from receipt_iq.validation import DEFAULT_TOLERANCE, validate

AMOUNT_FIELDS = ("subtotal", "discount", "service_charge", "tax", "tip", "total")

# Names this similar (0 to 1, by matching characters) count as the same item.
NAME_SIMILARITY = 0.8


class ExtractionError(Exception):
    """A provider couldn't produce a valid Receipt (bad JSON, timeout, ...)."""


class TemporaryError(ExtractionError):
    """The provider is unavailable right now (quota used up, service down).

    Says nothing about the receipt, so it's never cached or scored as a
    failure: an evaluation run stops instead, and resumes on the next run.
    """


class Extractor(Protocol):
    """Anything that turns a receipt image into a Receipt.

    Raises ExtractionError when it can't. Extractors that replay stored results
    (see cache.py) also set `last_seconds` to the original call's duration, so
    latency isn't reported as near zero on a re-run.
    """

    name: str

    def extract(self, image: bytes) -> Receipt: ...


def _normalize(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", name.upper()).strip()


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


@dataclass
class ReceiptScore:
    # field -> correct?; fields absent from both answer and prediction are left out
    fields: dict[str, bool]
    items_matched: int
    items_predicted: int
    items_expected: int
    flagged: bool
    # True when the provider returned nothing usable; scored as all wrong and
    # counted as flagged, since it would go to human review.
    failed: bool = False
    seconds: float | None = None

    @property
    def fully_correct(self) -> bool:
        return (
            all(self.fields.values())
            and self.items_matched == self.items_predicted == self.items_expected
        )


def match_items(predicted: list[LineItem], expected: list[LineItem], tolerance: Decimal) -> int:
    """Count one-to-one matches: same net price and a similar name.

    Greedy on name similarity, best pairs first. It can miss the optimal
    pairing when two lines have near-identical names and prices, which only
    swaps which duplicate matched; an exact assignment (Hungarian algorithm)
    isn't worth the complexity for receipts of a few dozen lines.
    """
    candidates = sorted(
        (
            (name_similarity(p.name, e.name), i, j)
            for i, p in enumerate(predicted)
            for j, e in enumerate(expected)
            if abs(p.net_price - e.net_price) <= tolerance
        ),
        reverse=True,
    )
    used_p: set[int] = set()
    used_e: set[int] = set()
    for similarity, i, j in candidates:
        if similarity < NAME_SIMILARITY:
            break
        if i not in used_p and j not in used_e:
            used_p.add(i)
            used_e.add(j)
    return len(used_p)


def score_receipt(
    predicted: Receipt, expected: Receipt, tolerance: Decimal = DEFAULT_TOLERANCE
) -> ReceiptScore:
    fields: dict[str, bool] = {}
    for name in AMOUNT_FIELDS:
        got, want = getattr(predicted, name), getattr(expected, name)
        if got is None and want is None:
            continue
        fields[name] = got is not None and want is not None and abs(got - want) <= tolerance

    return ReceiptScore(
        fields=fields,
        items_matched=match_items(predicted.items, expected.items, tolerance),
        items_predicted=len(predicted.items),
        items_expected=len(expected.items),
        flagged=not validate(predicted, tolerance).passed,
    )


def failed_score(expected: Receipt) -> ReceiptScore:
    return ReceiptScore(
        fields={name: False for name in AMOUNT_FIELDS if getattr(expected, name) is not None},
        items_matched=0,
        items_predicted=0,
        items_expected=len(expected.items),
        flagged=True,
        failed=True,
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


@dataclass
class EvalReport:
    scores: list[ReceiptScore] = field(default_factory=list)

    @property
    def receipts(self) -> int:
        return len(self.scores)

    def field_accuracy(self) -> dict[str, float | None]:
        return {
            name: _ratio(
                sum(s.fields[name] for s in self.scores if name in s.fields),
                sum(name in s.fields for s in self.scores),
            )
            for name in AMOUNT_FIELDS
        }

    def item_precision(self) -> float | None:
        return _ratio(
            sum(s.items_matched for s in self.scores), sum(s.items_predicted for s in self.scores)
        )

    def item_recall(self) -> float | None:
        return _ratio(
            sum(s.items_matched for s in self.scores), sum(s.items_expected for s in self.scores)
        )

    def item_f1(self) -> float | None:
        p, r = self.item_precision(), self.item_recall()
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)

    def fully_correct_rate(self) -> float | None:
        return _ratio(sum(s.fully_correct for s in self.scores), self.receipts)

    def catch_rate(self) -> float | None:
        """Of receipts extracted wrongly, the share the validator flagged."""
        wrong = [s for s in self.scores if not s.fully_correct]
        return _ratio(sum(s.flagged for s in wrong), len(wrong))

    def false_alarm_rate(self) -> float | None:
        """Of receipts extracted correctly, the share flagged anyway."""
        right = [s for s in self.scores if s.fully_correct]
        return _ratio(sum(s.flagged for s in right), len(right))

    def failure_rate(self) -> float | None:
        return _ratio(sum(s.failed for s in self.scores), self.receipts)

    def median_seconds(self) -> float | None:
        times = [s.seconds for s in self.scores if s.seconds is not None]
        return statistics.median(times) if times else None


def evaluate(
    extractor: Extractor,
    examples: Iterable[tuple[bytes, Receipt]],
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> EvalReport:
    """Run `extractor` over (image, expected receipt) pairs and score each one."""
    report = EvalReport()
    for image, expected in examples:
        start = time.perf_counter()
        try:
            score = score_receipt(extractor.extract(image), expected, tolerance)
        except TemporaryError:
            raise
        except ExtractionError:
            score = failed_score(expected)
        elapsed = time.perf_counter() - start
        score.seconds = getattr(extractor, "last_seconds", None) or elapsed
        report.scores.append(score)
    return report
