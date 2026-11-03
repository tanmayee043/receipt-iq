"""Work through the review queue from the terminal, until the screen exists.

Usage:
    python scripts/review_queue.py list [--limit N]
    python scripts/review_queue.py show DIGEST
    python scripts/review_queue.py correct DIGEST fixed.json [--confirm-mismatch]
    python scripts/review_queue.py dismiss DIGEST "not a receipt"
    python scripts/review_queue.py export [--out FILE] [--held-out SPLIT]

`export` writes corrections as labeled data, one JSON object per line, and leaves out any
receipt in the held-out split so its final score stays honest.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from receipt_iq.cord import CordExample, load_split
from receipt_iq.db import Document, session_scope
from receipt_iq.review import (
    DEFAULT_QUEUE_LIMIT,
    CorrectionRejected,
    DocumentNotFound,
    NotReviewable,
    correct,
    corrections,
    dismiss,
    get_document,
    review_queue,
)
from receipt_iq.schema import Receipt


def describe(document: Document) -> str:
    reasons = [issue["message"] for issue in document.issues]
    reason = document.failure_reason or "; ".join(reasons)
    return f"{document.digest}  {document.status:<12}  {document.provider}  {reason[:80]}"


def as_dict(document: Document) -> dict[str, object]:
    return {
        "digest": document.digest,
        "status": document.status,
        "provider": document.provider,
        "receipt": document.receipt,
        "issues": document.issues,
        "attempts": document.attempts,
        "failure_kind": document.failure_kind,
        "failure_reason": document.failure_reason,
        "corrected_receipt": document.corrected_receipt,
        "confirmed_mismatch": document.confirmed_mismatch,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list")
    listing.add_argument("--limit", type=int, default=DEFAULT_QUEUE_LIMIT)

    showing = commands.add_parser("show")
    showing.add_argument("digest")

    correcting = commands.add_parser("correct")
    correcting.add_argument("digest")
    correcting.add_argument("receipt", type=Path, help="JSON file with the corrected receipt")
    correcting.add_argument("--confirm-mismatch", action="store_true")

    dismissing = commands.add_parser("dismiss")
    dismissing.add_argument("digest")
    dismissing.add_argument("reason")

    exporting = commands.add_parser("export")
    exporting.add_argument("--out", type=Path, default=Path("data/corrections/labels.jsonl"))
    exporting.add_argument("--held-out", type=Path, default=Path("data/cord/test.parquet"))

    args = parser.parse_args()
    try:
        with session_scope() as session:
            if args.command == "list":
                documents = review_queue(session, args.limit)
                for document in documents:
                    print(describe(document))
                print(f"{len(documents)} waiting")
            elif args.command == "show":
                found = get_document(session, args.digest)
                if found is None:
                    raise DocumentNotFound(args.digest)
                print(json.dumps(as_dict(found), indent=1))
            elif args.command == "correct":
                receipt = Receipt.model_validate_json(args.receipt.read_text("utf-8"))
                print(describe(correct(session, args.digest, receipt, args.confirm_mismatch)))
            elif args.command == "dismiss":
                print(describe(dismiss(session, args.digest, args.reason)))
            else:
                held_out = {
                    hashlib.sha256(example.image).hexdigest()
                    for example in load_split(args.held_out)
                    if isinstance(example, CordExample)
                }
                labeled = corrections(session, exclude=held_out)
                args.out.parent.mkdir(parents=True, exist_ok=True)
                with args.out.open("w", encoding="utf-8") as output:
                    for document in labeled:
                        row = {
                            "digest": document.digest,
                            "label": document.corrected_receipt,
                            "model_answer": document.receipt,
                            "provider": document.provider,
                            "confirmed_mismatch": document.confirmed_mismatch,
                        }
                        output.write(json.dumps(row) + "\n")
                print(f"wrote {len(labeled)} corrections to {args.out}")
    except (DocumentNotFound, NotReviewable, CorrectionRejected) as failure:
        sys.exit(f"{type(failure).__name__}: {failure}")


if __name__ == "__main__":
    main()
