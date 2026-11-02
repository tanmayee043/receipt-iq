"""Score every provider from its cached results and write leaderboard.json for the router.

Usage: python scripts/build_leaderboard.py [split]

Makes no provider calls. Providers are compared only on receipts all of them answered.
Re-run it when a provider's results change, and commit the file.
"""

import argparse
import json
from decimal import Decimal
from pathlib import Path

from receipt_iq.cord import CordExample, load_split
from receipt_iq.evaluation import evaluate
from receipt_iq.replay import RUNS, common_examples, replay_extractor

CACHE_ROOT = Path("data/cache")
OUTPUT = Path("leaderboard.json")
TOLERANCE = Decimal(100)  # IDR, as in evaluate.py


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("split", nargs="?", default="data/cord/validation.parquet")
    args = parser.parse_args()

    examples = [e for e in load_split(Path(args.split)) if isinstance(e, CordExample)]
    common = common_examples(examples, RUNS, CACHE_ROOT)
    pairs = [(e.image, e.receipt) for e in common]

    providers = []
    for run in RUNS:
        report = evaluate(replay_extractor(run, CACHE_ROOT), pairs, TOLERANCE)
        providers.append(
            {
                "provider": run.provider,
                "local": run.local,
                "fully_correct": report.fully_correct_rate(),
                "total_correct": report.field_accuracy()["total"],
                "item_f1": report.item_f1(),
                "catch_rate": report.catch_rate(),
                "false_alarm_rate": report.false_alarm_rate(),
                "failure_rate": report.failure_rate(),
                "median_seconds": report.median_seconds(),
                "cost_per_1000": run.cost_per_1000,
                "cost_note": run.cost_note,
            }
        )

    board = {"split": Path(args.split).stem, "receipts": len(common), "providers": providers}
    OUTPUT.write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")

    print(f"{len(common)} receipts answered by all {len(RUNS)} providers")
    for entry in sorted(providers, key=lambda p: -float(str(p["fully_correct"]))):
        print(
            f"  {entry['provider']:<8}"
            f"fully correct {float(str(entry['fully_correct'])):6.1%}   "
            f"F1 {float(str(entry['item_f1'])):6.1%}   "
            f"${entry['cost_per_1000']:>5.2f}/1000   "
            f"{float(str(entry['median_seconds'])):5.1f} s"
        )


if __name__ == "__main__":
    main()
