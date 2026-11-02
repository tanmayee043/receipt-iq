"""Replay routing policies over cached results, to see what routing is actually worth.

Usage: python scripts/simulate_routing.py [split]

Makes no provider calls: each policy's choices are answered from the cache, then scored
against the labels. Compares always using one provider with the router at several
accuracy targets, with and without a second opinion on flagged receipts.

Validation numbers tune the target; they read better than they'd do on unseen receipts,
because the router's accuracy figures come from these same receipts.
"""

import argparse
import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from receipt_iq.cord import CordExample, load_split
from receipt_iq.evaluation import (
    EvalReport,
    ExtractionError,
    ReceiptScore,
    TemporaryError,
    failed_score,
    score_receipt,
)
from receipt_iq.replay import RUNS, common_examples, replay_extractor
from receipt_iq.router import DEFAULT_MAX_SECONDS, Router, load_routes

CACHE_ROOT = Path("data/cache")
LEADERBOARD = Path("leaderboard.json")
TOLERANCE = Decimal(100)  # IDR, as in evaluate.py
TARGETS = (0.6, 0.7, 0.75, 0.8)


@dataclass
class PolicyResult:
    name: str
    report: EvalReport
    cost_per_1000: float
    sent_to_review: float
    mean_seconds: float


def run_policy(name: str, router: Router, examples: list[CordExample]) -> PolicyResult:
    prices = {run.provider: run.cost_per_1000 for run in RUNS}
    report = EvalReport()
    cost = seconds = 0.0
    reviewed = 0
    for example in examples:
        attempts = []
        score: ReceiptScore
        try:
            routed = router.extract(example.image)
            attempts = routed.attempts
            score = score_receipt(routed.receipt, example.receipt, TOLERANCE)
            reviewed += routed.status == "needs_review"
        except (ExtractionError, TemporaryError):
            score = failed_score(example.receipt)
            reviewed += 1
        report.scores.append(score)
        # Every provider asked is paid for, including answers that were then thrown away.
        cost += sum(prices[a.provider] / 1000 for a in attempts if a.outcome != "skipped")
        seconds += sum(a.seconds for a in attempts)
    count = len(examples)
    return PolicyResult(name, report, cost / count * 1000, reviewed / count, seconds / count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("split", nargs="?", default="data/cord/validation.parquet")
    args = parser.parse_args()

    examples = [e for e in load_split(Path(args.split)) if isinstance(e, CordExample)]
    common = common_examples(examples, RUNS, CACHE_ROOT)
    routes = load_routes(LEADERBOARD)
    extractors = {run.provider: replay_extractor(run, CACHE_ROOT) for run in RUNS}

    results = []
    for route in routes:
        # One route and one answer is "always use this provider".
        router = Router([route], extractors.__getitem__, target=0.0, max_answers=1)
        results.append(run_policy(f"always {route.provider}", router, common))
    for limit, limit_label in ((math.inf, "no limit"), (DEFAULT_MAX_SECONDS, "60 s")):
        for target in TARGETS:
            for max_answers, label in ((1, "1 answer"), (2, "2 answers")):
                router = Router(
                    routes,
                    extractors.__getitem__,
                    target=target,
                    max_seconds=limit,
                    max_answers=max_answers,
                )
                name = f"router {target:.0%}, {limit_label}, {label}"
                results.append(run_policy(name, router, common))

    print(f"{len(common)} receipts\n")
    print(f"{'policy':<34}{'fully':>8}{'F1':>8}{'$/1000':>9}{'review':>9}{'mean s':>9}")
    for result in results:
        print(
            f"{result.name:<34}"
            f"{result.report.fully_correct_rate() or 0:>8.1%}"
            f"{result.report.item_f1() or 0:>8.1%}"
            f"{result.cost_per_1000:>9.2f}"
            f"{result.sent_to_review:>9.1%}"
            f"{result.mean_seconds:>9.1f}"
        )


if __name__ == "__main__":
    main()
