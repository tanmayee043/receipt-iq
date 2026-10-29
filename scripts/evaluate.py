"""Score an extraction provider against a labeled CORD split.

Usage: python scripts/evaluate.py {oracle,noisy,local} [split.parquet] [--limit N]

- local:  Tesseract OCR + phi3 via Ollama, fully on this machine. Results are
  cached in data/cache/, so re-runs and interrupted runs don't redo work.
- oracle: returns the answer itself. Everything must score 100%; anything less
  is a bug in the scorer.
- noisy:  the answer with typical OCR mistakes injected (fixed seed). Simulated,
  not a provider result; it exercises the validator's catch rate.
"""

import argparse
import random
import sys
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

from receipt_iq.cache import CachedExtractor
from receipt_iq.cord import CordExample, load_split
from receipt_iq.evaluation import EvalReport, Extractor, evaluate
from receipt_iq.providers.local import TesseractOllama
from receipt_iq.schema import LineItem, Receipt

TOLERANCE = Decimal(100)  # IDR, as in check_cord_labels.py
SEED = 7
CACHE_ROOT = Path("data/cache")


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
    print(f"  failed            {percent(report.failure_rate())}   (no usable output)")
    seconds = report.median_seconds()
    print(f"  median latency    {'n/a' if seconds is None else f'{seconds:.1f} s'} per receipt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("provider", choices=["oracle", "noisy", "local"])
    parser.add_argument("split", nargs="?", default="data/cord/validation.parquet")
    parser.add_argument("--limit", type=int, help="only the first N receipts")
    parser.add_argument("--prompt", default="v2", help="local provider prompt version")
    args = parser.parse_args()

    examples = [e for e in load_split(Path(args.split)) if isinstance(e, CordExample)]
    examples = examples[: args.limit]
    answers = {e.image: e.receipt for e in examples}

    extractor: Extractor
    if args.provider == "oracle":
        extractor = Oracle(answers)
    elif args.provider == "noisy":
        extractor = Noisy(answers)
    else:
        local = TesseractOllama(prompt_version=args.prompt)
        cached = CachedExtractor(local, local.version, CACHE_ROOT)
        extractor = cached

    report = evaluate(extractor, progress(examples), TOLERANCE)
    print_report(extractor.name, report)
    if args.provider == "local":
        print(f"  version {cached.version}; cache: {cached.hits} reused, {cached.misses} new calls")


def progress(examples: list[CordExample]) -> Iterator[tuple[bytes, Receipt]]:
    for number, example in enumerate(examples, 1):
        print(f"\r  {number}/{len(examples)} {example.id}", end="", file=sys.stderr, flush=True)
        yield example.image, example.receipt
    print(file=sys.stderr)


if __name__ == "__main__":
    main()
