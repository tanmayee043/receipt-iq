"""Disk cache for extraction results, so no model is ever called twice for the same input.

The key is (image hash, provider, version), where version covers everything
that changes the output (OCR settings, prompt): change either and every
receipt is extracted again; re-run the same experiment and it's free and
instant. That matters for slow local models (tens of seconds per receipt) and
for free cloud tiers with daily request limits. It also makes an interrupted
run resumable.

Failures are cached too, so a run doesn't retry a receipt the model can't
handle every time. Delete the provider's cache folder to retry them.
"""

import hashlib
import json
import time
from pathlib import Path

from receipt_iq.evaluation import ExtractionError, Extractor
from receipt_iq.schema import Receipt


class CachedExtractor:
    def __init__(self, inner: Extractor, version: str, root: Path) -> None:
        self.inner = inner
        self.name = inner.name
        self.version = version
        self.directory = root / _safe(inner.name) / _safe(version)
        self.last_seconds: float | None = None
        self.hits = 0
        self.misses = 0

    def extract(self, image: bytes) -> Receipt:
        path = self.directory / f"{hashlib.sha256(image).hexdigest()}.json"
        if path.exists():
            self.hits += 1
            entry = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.misses += 1
            entry = self._call(image)
            self.directory.mkdir(parents=True, exist_ok=True)
            # Write to a temp file then rename, so a crash mid-write never
            # leaves a half-written entry that looks valid.
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(entry), encoding="utf-8")
            temp.replace(path)

        self.last_seconds = entry["seconds"]
        if entry["error"] is not None:
            raise ExtractionError(entry["error"])
        return Receipt.model_validate_json(entry["receipt"])

    def _call(self, image: bytes) -> dict[str, object]:
        start = time.perf_counter()
        try:
            receipt: str | None = self.inner.extract(image).model_dump_json()
            error = None
        except ExtractionError as failure:
            receipt, error = None, str(failure)
        return {"receipt": receipt, "error": error, "seconds": time.perf_counter() - start}


def _safe(part: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in part)
