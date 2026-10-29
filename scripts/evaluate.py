"""Score an extraction provider against a labeled CORD split.

Usage: python scripts/evaluate.py [oracle|noisy] [data/cord/validation.parquet]

Real providers come later. Until then, two simulated ones exercise the harness
end to end on real CORD answers:
- oracle: returns the answer itself. Everything must score 100%; anything less
  is a bug in the scorer.
- noisy:  the answer with typical OCR mistakes injected (fixed seed). Shows the
  report format and estimates the validator's catch rate on those mistakes.
  These numbers are simulated, not a provider result.
"""

import random
import sys
from decimal import Decimal
from pathlib import Path

from receipt_iq.cord import CordExample, load_split
from receipt_iq.evaluation import AMOUNT_FIELDS, EvalReport, evaluate
from receipt_iq.schema import LineItem, Receipt

TOLERANCE = Decimal(100)  # IDR, as in check_cord_labels.py
SEED = 7


class Oracle:
    name = "oracle"

    def __init__(self, answers: dict[bytes, Receipt]) -> None:
        self.answers = answers

    def extract(self, image: bytes) -> Receipt:
        return self.answers[image]


def misread_digit(amount: Decimal, rng: random.Random) -> Decimal:
    """Change one digit, the way OCR reads 8 as 3 or 1 as 7."""
    digits = list(str(int(amount)))
    position = rng.randrange(len(digits))
    digits[position] = rng.choice([d for d in "0123456789" if d != digits[position]])
    return Decimal("".join(digits))


class Noisy(Oracle):
    name = "noisy (simulated)"

    def __init__(self, answers: dict[bytes, Receipt], error_rate: float = 0.5) -> None:
        super().__init__(answers)
        self.error_rate = error_rate
        self.rng = random.Random(SEED)

    def extract(self, image: bytes) -> Receipt:
        receipt = self.answers[image]
        if self.rng.random() > self.error_rate:
            return receipt
        items = list(receipt.items)
        mistake = self.rng.choice(["total", "drop_item", "item_price", "item_name"])
        if mistake == "total":
            return receipt.model_copy(update={"total": misread_digit(receipt.total, self.rng)})
        if not items:
            return receipt
        i = self.rng.randrange(len(items))
        if mistake == "drop_item":
            del items[i]
        elif mistake == "item_price" and items[i].total_price > 0:
            new_price = misread_digit(items[i].total_price, self.rng)
            items[i] = items[i].model_copy(update={"total_price": new_price, "unit_price": None})
        elif mistake == "item_name":
            # A small typo; the scorer should still count this as a match.
            name = items[i].name
            items[i] = LineItem(**{**items[i].model_dump(), "name": name[:-1] or name})
        return receipt.model_copy(update={"items": items})


def percent(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:6.1%}"


def print_report(provider: str, report: EvalReport) -> None:
    print(f"\n{provider}: {report.receipts} receipts")
    print("  amount fields")
    for name, accuracy in report.field_accuracy().items():
        print(f"    {name:<16}{percent(accuracy)}")
    print("  line items")
    print(f"    precision       {percent(report.item_precision())}")
    print(f"    recall          {percent(report.item_recall())}")
    print(f"    F1              {percent(report.item_f1())}")
    print(f"  fully correct     {percent(report.fully_correct_rate())}")
    print("  validator")
    print(f"    catch rate      {percent(report.catch_rate())}   (wrong receipts flagged)")
    print(f"    false alarms    {percent(report.false_alarm_rate())}   (right receipts flagged)")


def main() -> None:
    provider = sys.argv[1] if len(sys.argv) > 1 else "oracle"
    path = Path(sys.argv[2] if len(sys.argv) > 2 else "data/cord/validation.parquet")

    examples = [e for e in load_split(path) if isinstance(e, CordExample)]
    answers = {e.image: e.receipt for e in examples}
    extractors = {"oracle": Oracle(answers), "noisy": Noisy(answers)}
    if provider not in extractors:
        sys.exit(f"provider must be one of {sorted(extractors)}")

    extractor = extractors[provider]
    report = evaluate(extractor, ((e.image, e.receipt) for e in examples), TOLERANCE)
    print_report(extractor.name, report)
    assert set(report.field_accuracy()) == set(AMOUNT_FIELDS)


if __name__ == "__main__":
    main()
