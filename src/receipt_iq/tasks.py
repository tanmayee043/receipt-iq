"""Background extraction: one Celery task per receipt, with Redis as the broker.

    submit(image) ──► data/uploads/<hash> ──► Redis ──► worker ──► provider (through the cache)
                                                                       │
                                     passed / needs_review ◄── validate ┤
                                                │                       └──► dead letter
                                                ▼                                 │
                                            Postgres ◄────────────────────────────┘

What each failure means:
- TemporaryError (quota, overload, network): retried with growing waits, since it says
  nothing about the receipt. If it never clears, the receipt is dead-lettered and not
  cached, so submitting it again works once the provider is back.
- ExtractionError (the provider can't read this receipt): dead-lettered right away.
  Retrying would spend money on the same answer.
- RuntimeError (wrong key, retired model): left to fail the task visibly, because every
  receipt would hit it and only a person can fix it.
- Postgres unreachable: retried, and the provider isn't paid again because its answer is
  cached. If it never comes back, the task fails visibly.

Provider calls go through the disk cache and results are saved under the image hash, so
running a task twice costs nothing and gives the same result. That matters because of
`acks_late`: a task is only removed from Redis once it has finished, so a crashed
worker's receipt is delivered again rather than lost.

Each submission gets its own task id, so `.get()` waits for this run instead of returning
a stored result from an earlier one. Screens read the outcome from Postgres (review.py).

The queue holds only the hash: a 10 MB photo doesn't belong in a message broker.

With provider "auto", the router (router.py) chooses for each receipt and records why.

Start Redis and Postgres with `docker compose up -d`, create the tables with
`alembic upgrade head`, and start a worker with:
    celery -A receipt_iq.tasks worker --pool=solo --loglevel=info

--pool=solo runs one task at a time, which suits the providers' rate limits and is what
works on Windows. It does not enforce Celery's time limits below (they need the prefork
pool on Linux); here, each provider's own request timeout is what bounds a task.
"""

import hashlib
import os
from collections.abc import Callable
from dataclasses import asdict
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, Protocol

from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded
from celery.result import AsyncResult
from sqlalchemy.exc import OperationalError

from receipt_iq.cache import CachedExtractor
from receipt_iq.db import session_scope
from receipt_iq.evaluation import ExtractionError, Extractor, TemporaryError
from receipt_iq.providers.azure_di import AzureDocumentIntelligence
from receipt_iq.providers.azure_openai import AzureOpenAI
from receipt_iq.providers.gemini import Gemini
from receipt_iq.providers.local import TesseractOllama
from receipt_iq.review import issue_json, save_result
from receipt_iq.router import Routed, Router, load_routes
from receipt_iq.validation import validate

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
TOLERANCE = Decimal(100)  # IDR, as in scripts/evaluate.py
AUTO = "auto"  # let the router choose the provider
LEADERBOARD = Path("leaderboard.json")
MAX_RETRIES = 5
RETRY_BASE_SECONDS = 60  # 1, 2, 4, 8, then 16 minutes
# How long Redis waits before handing a crashed worker's receipt to another worker. Has
# to outlast the longest task and retry wait, or a healthy task gets delivered twice.
VISIBILITY_TIMEOUT = int(os.environ.get("CELERY_VISIBILITY_TIMEOUT", "3600"))

app = Celery("receipt_iq", broker=f"{REDIS_URL}/0", backend=f"{REDIS_URL}/1")
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    # Take one task at a time, so a waiting receipt isn't held by a busy worker.
    worker_prefetch_multiplier=1,
    # Slower than the slowest provider's timeout and retries; see the note on --pool=solo.
    task_soft_time_limit=1500,
    task_time_limit=1800,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": VISIBILITY_TIMEOUT},
)


class Provider(Extractor, Protocol):
    version: str


PROVIDERS: dict[str, Callable[[], Provider]] = {
    "local": TesseractOllama,
    "gemini": Gemini,
    "gemma": lambda: Gemini(model="gemma-4-31b-it"),
    "azure": AzureDocumentIntelligence,
    "gpt": AzureOpenAI,
}


def _data_root() -> Path:
    return Path(os.environ.get("RECEIPT_IQ_DATA", "data"))


def store_image(image: bytes) -> str:
    """Save the image under its hash and return the hash."""
    digest = hashlib.sha256(image).hexdigest()
    path = _data_root() / "uploads" / digest
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename, so a worker never reads half an image.
        temp = path.with_suffix(".tmp")
        temp.write_bytes(image)
        temp.replace(path)
    return digest


def submit(image: bytes, provider: str = AUTO, local_only: bool = False) -> AsyncResult:
    """Queue a receipt for extraction. Returns at once; `.get()` waits for the result.

    `provider` is "auto" to let the router choose, or a name from PROVIDERS. `local_only`
    is for sensitive documents: the router won't use anything that leaves this machine.
    """
    if provider != AUTO and provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; choose from {sorted(PROVIDERS)}")
    if local_only and provider != AUTO:
        raise ValueError("local_only only applies when the router chooses the provider")
    digest = store_image(image)
    return extract_receipt.apply_async((digest, provider, local_only))


@cache
def get_extractor(provider: str) -> CachedExtractor:
    # One per worker process: providers remember when they last called the API, and
    # that spacing only works if every task shares the same object.
    inner = PROVIDERS[provider]()
    return CachedExtractor(inner, inner.version, _data_root() / "cache")


@cache
def get_router() -> Router:
    # One per worker process, so circuit breakers remember failures across receipts.
    return Router(load_routes(LEADERBOARD), get_extractor)


def _dead_letter(digest: str, provider: str, kind: str, reason: str) -> dict[str, Any]:
    """A receipt that needs a person."""
    return {
        "status": "dead_letter",
        "digest": digest,
        "provider": provider,
        "kind": kind,
        "reason": reason,
    }


def _finish(task: Any, result: dict[str, Any]) -> dict[str, Any]:
    try:
        with session_scope() as session:
            save_result(session, result)
    except OperationalError as failure:
        raise task.retry(exc=failure, countdown=RETRY_BASE_SECONDS) from failure
    return result


@app.task(
    bind=True,
    name="extract_receipt",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=MAX_RETRIES,
)
def extract_receipt(
    self: Any, digest: str, provider: str, local_only: bool = False
) -> dict[str, Any]:
    image = (_data_root() / "uploads" / digest).read_bytes()
    routed: Routed | None = None
    try:
        if provider == AUTO:
            routed = get_router().extract(image, local_only)
            receipt = routed.receipt
        else:
            extractor = get_extractor(provider)
            receipt = extractor.extract(image)
    except (TemporaryError, SoftTimeLimitExceeded) as failure:
        retries = self.request.retries
        if retries >= MAX_RETRIES:
            reason = f"gave up after {retries} retries: {failure}"
            return _finish(self, _dead_letter(digest, provider, "retries_exhausted", reason))
        raise self.retry(exc=failure, countdown=RETRY_BASE_SECONDS * 2**retries) from failure
    except ExtractionError as failure:
        return _finish(self, _dead_letter(digest, provider, "extraction_failed", str(failure)))

    checked = validate(receipt, TOLERANCE)
    return _finish(
        self,
        {
            "status": "passed" if checked.passed else "needs_review",
            "digest": digest,
            "provider": routed.provider if routed else provider,
            "receipt": receipt.model_dump(mode="json"),
            "issues": [issue_json(issue) for issue in checked.issues],
            "seconds": (
                sum(attempt.seconds for attempt in routed.attempts)
                if routed
                else extractor.last_seconds
            ),
            # The routing log: which providers were tried, and why this one answered.
            "attempts": [asdict(attempt) for attempt in routed.attempts] if routed else [],
            "met_target": routed.met_target if routed else None,
        },
    )
