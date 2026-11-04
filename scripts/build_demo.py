"""Build the sample receipts for the public demo, from stored validation results.

Usage: python scripts/build_demo.py

Makes no provider calls: the router replays over the cache, so each sample shows what
really happened to that receipt. Only the validation split is used. Publishing test
receipts would undermine the held-out score.

Writes demo/samples.json and resized images to demo/images/. Look at the images before
committing them.
"""

import json
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from receipt_iq.cord import CordExample, load_split
from receipt_iq.replay import RUNS, common_examples, replay_extractor
from receipt_iq.review import TOLERANCE, issue_json
from receipt_iq.router import Routed, Router, load_routes
from receipt_iq.validation import validate

CACHE_ROOT = Path("data/cache")
LEADERBOARD = Path("leaderboard.json")
OUTPUT = Path("demo")
MAX_SIDE = 1400
JPEG_QUALITY = 80


@dataclass(frozen=True)
class Kind:
    """A kind of sample a visitor should see, in the order they'll see them."""

    name: str
    count: int
    title: str
    note: str
    # A bill with one item is dull to split, but flagged receipts are rare enough to take any.
    min_items: int


KINDS = [
    Kind(
        "first_try",
        2,
        "An easy one",
        "The cheapest provider read it, and the numbers added up.",
        3,
    ),
    Kind(
        "second_opinion",
        1,
        "A second opinion",
        "The first answer didn't add up, so the router asked another provider.",
        3,
    ),
    Kind(
        "review",
        1,
        "One for a person",
        "The answers didn't add up, so it went to the review queue. Try fixing it.",
        1,
    ),
]


def kind_of(routed: Routed) -> str:
    if routed.status == "needs_review":
        return "review"
    return "first_try" if len(routed.attempts) == 1 else "second_opinion"


def resized(image: bytes) -> bytes:
    with Image.open(BytesIO(image)) as opened:
        picture = opened.convert("RGB")
    picture.thumbnail((MAX_SIDE, MAX_SIDE))
    output = BytesIO()
    picture.save(output, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return output.getvalue()


def main() -> None:
    examples = load_split(Path("data/cord/validation.parquet"))
    usable = [example for example in examples if isinstance(example, CordExample)]
    common = common_examples(usable, RUNS, CACHE_ROOT)
    extractors = {run.provider: replay_extractor(run, CACHE_ROOT) for run in RUNS}
    router = Router(load_routes(LEADERBOARD), extractors.__getitem__)

    found: dict[str, list[tuple[CordExample, Routed]]] = {kind.name: [] for kind in KINDS}
    for example in common:
        routed = router.extract(example.image)
        kind = next(k for k in KINDS if k.name == kind_of(routed))
        if len(found[kind.name]) < kind.count and len(routed.receipt.items) >= kind.min_items:
            found[kind.name].append((example, routed))

    missing = [kind.name for kind in KINDS if len(found[kind.name]) < kind.count]
    if missing:
        raise SystemExit(f"not enough receipts of these kinds: {missing}")

    (OUTPUT / "images").mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, object]] = []
    for kind in KINDS:
        for number, (example, routed) in enumerate(found[kind.name], 1):
            sample_id = f"sample-{len(samples) + 1}"
            image = resized(example.image)
            (OUTPUT / "images" / f"{sample_id}.jpg").write_bytes(image)
            print(f"{sample_id}  {example.id}  {routed.status}  {len(image) // 1024} KB")
            samples.append(
                {
                    "id": sample_id,
                    "source": example.id,
                    "title": f"{kind.title} ({number})" if kind.count > 1 else kind.title,
                    "note": kind.note,
                    "status": routed.status,
                    "provider": routed.provider,
                    "receipt": routed.receipt.model_dump(mode="json"),
                    "issues": [
                        issue_json(issue) for issue in validate(routed.receipt, TOLERANCE).issues
                    ],
                    "attempts": [asdict(attempt) for attempt in routed.attempts],
                    "seconds": sum(attempt.seconds for attempt in routed.attempts),
                }
            )
    (OUTPUT / "samples.json").write_text(json.dumps(samples, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
