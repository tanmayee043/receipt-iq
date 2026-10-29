"""Run the arithmetic validator over CORD's own (correct) labels.

Usage: python scripts/check_cord_labels.py [data/cord/test.parquet]

The labels are human-checked, so they should pass. Every failure here is either
a gap in the validator's rules or a quirk of the dataset, and needs to be
understood before the validator is trusted to judge model output.
"""

import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from receipt_iq.cord import CordLabelError, load_split
from receipt_iq.validation import validate

# IDR receipts round to the nearest 100.
TOLERANCE = Decimal(100)


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/cord/test.parquet")
    outcomes: Counter[str] = Counter()
    failures: list[str] = []

    for example in load_split(path):
        if isinstance(example, CordLabelError):
            outcomes["unparseable label"] += 1
            failures.append(f"  unparseable  {example}")
            continue
        result = validate(example.receipt, tolerance=TOLERANCE)
        if result.passed:
            outcomes["passed"] += 1
        elif not result.issues:
            outcomes["unverified (total only)"] += 1
        else:
            for issue in result.issues:
                outcomes[f"failed: {issue.code}"] += 1
                failures.append(f"  {example.id}  {issue.message}")

    total = sum(1 for _ in load_split(path))
    print(f"{path}: {total} receipts")
    for outcome, count in outcomes.most_common():
        print(f"  {outcome:<30} {count}")
    if failures:
        print("\ndetails:")
        print("\n".join(failures))


if __name__ == "__main__":
    main()
