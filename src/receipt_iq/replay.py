"""Stored evaluation runs, read back from the cache without ever calling a provider.

The leaderboard and the routing simulation are both built from these, so working them
out costs nothing. Providers only count on receipts that every provider has answered,
otherwise a provider's score would depend on which receipts it happened to see.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from receipt_iq.cache import CachedExtractor, cache_path
from receipt_iq.cord import CordExample
from receipt_iq.evaluation import TemporaryError
from receipt_iq.schema import Receipt


@dataclass(frozen=True)
class Run:
    """Where one provider's validation results are cached, and what it costs."""

    provider: str
    cache_name: str
    version: str
    # List price per 1,000 receipts, ignoring free tiers: routing decisions should still
    # hold at volumes the free tiers can't cover. Prices are from third-party trackers
    # (Nov 2026), so check the providers' own pages before relying on them.
    cost_per_1000: float
    cost_note: str
    local: bool = False


RUNS = [
    Run(
        "local",
        "tesseract+phi3",
        "gray2000-psm6.prompt-v2",
        0.0,
        "runs on this machine; electricity not counted",
        local=True,
    ),
    Run(
        "gemini",
        "gemini-3.5-flash",
        "prompt-v1",
        5.40,
        "estimate: $1.50 / $9.00 per million tokens, assuming GPT's token counts per receipt",
    ),
    Run(
        "gemma",
        "gemma-4-31b-it",
        "prompt-v1",
        0.0,
        "free tier only: no paid price found, and it's slow and often overloaded",
    ),
    Run(
        "azure",
        "azure-document-intelligence",
        "prebuilt-receipt-2024-11-30.locale-id-ID.map-v2",
        10.0,
        "$10 per 1,000 pages; the free tier covers the first 500 a month",
    ),
    Run(
        "gpt",
        "receipt-gpt-5-mini",
        "prompt-v1.reasoning-low",
        1.11,
        "measured tokens per receipt at $0.25 / $2.00 per million",
    ),
]


class _NoCalls:
    """Stands in for the real provider, so a cache miss can never spend anything."""

    def __init__(self, name: str) -> None:
        self.name = name

    def extract(self, image: bytes) -> Receipt:
        raise TemporaryError(f"{self.name} has no stored answer for this receipt")


def replay_extractor(run: Run, root: Path) -> CachedExtractor:
    return CachedExtractor(_NoCalls(run.cache_name), run.version, root)


def common_examples(
    examples: Iterable[CordExample], runs: list[Run], root: Path
) -> list[CordExample]:
    """The receipts every run has answered."""
    extractors = [replay_extractor(run, root) for run in runs]
    return [
        example
        for example in examples
        if all(cache_path(extractor, example.image).exists() for extractor in extractors)
    ]
