"""Send CORD receipts through the queue and report what happened to each.

Usage: python scripts/submit_receipts.py {local,gemini,azure,gpt} [split] [--limit N]

Needs Redis (`docker compose up -d`). Receipts wait in Redis until a worker is running:
    celery -A receipt_iq.tasks worker --pool=solo --loglevel=info

With --no-wait it only queues them and exits, which shows that the queue holds the work
when no worker is up.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from kombu.exceptions import OperationalError

from receipt_iq.cord import CordExample, load_split
from receipt_iq.tasks import PROVIDERS, submit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("provider", choices=sorted(PROVIDERS))
    parser.add_argument("split", nargs="?", default="data/cord/validation.parquet")
    parser.add_argument("--limit", type=int, default=5, help="only the first N receipts")
    parser.add_argument("--no-wait", action="store_true", help="queue them and exit")
    parser.add_argument("--timeout", type=float, default=600, help="seconds to wait per receipt")
    args = parser.parse_args()

    examples = [e for e in load_split(Path(args.split)) if isinstance(e, CordExample)]
    examples = examples[: args.limit]
    try:
        queued = [(e.id, submit(e.image, args.provider)) for e in examples]
    except OperationalError as error:
        sys.exit(f"can't reach Redis ({error}); start it with: docker compose up -d")
    print(f"queued {len(queued)} receipts")
    if args.no_wait:
        return

    statuses: Counter[str] = Counter()
    for receipt_id, task in queued:
        result = task.get(timeout=args.timeout)
        statuses[result["status"]] += 1
        detail = result.get("reason") or "; ".join(result.get("issues", []))
        print(f"  {receipt_id}  {result['status']}  {detail[:100]}")
    print(dict(statuses))


if __name__ == "__main__":
    main()
