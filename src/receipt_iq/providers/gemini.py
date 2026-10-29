"""Gemini: a vision model reads the receipt image directly, with no OCR step.

    image ──► Gemini API (JSON schema, temperature 0) ──► Receipt

Runs on the free tier, which has two consequences built into this module:
- Rate limits. Requests are spaced out (`min_interval`), and a 429 waits for
  the delay Google asks for before retrying. A used-up daily quota stops the
  run with a TemporaryError; the cache keeps finished receipts, so the next
  run resumes where this one stopped.
- Privacy. Google may use free-tier requests to improve its models, so only
  public data (the CORD dataset) is ever sent.

The API key comes from the GEMINI_API_KEY environment variable and is sent in
a request header, never in the URL, so it can't end up in logs.
"""

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from receipt_iq.evaluation import ExtractionError, TemporaryError
from receipt_iq.providers.shared import RESPONSE_SCHEMA
from receipt_iq.schema import Receipt

PROMPT_VERSION = "v1"
# Pinned, not an alias like "gemini-flash-latest": an alias can switch models
# under you and make runs incomparable. Checked on a new free-tier account
# (Oct 2026): gemini-2.5-flash is closed to new accounts, and gemini-3.8-flash
# never answered (even a one-word reply hung for 3 minutes).
DEFAULT_MODEL = "gemini-3.5-flash"
API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

PROMPT = """\
Copy the data printed on this shop receipt into JSON. Copy what is printed;
never calculate or invent anything.

Items:
- One entry per purchased line, with the name copied as printed.
- "total_price" is the amount printed on that line. "quantity" is the count
  printed for it, default 1. A discount printed under an item goes in that
  item's "discount".

Receipt-level amounts, found by the label printed next to them:
- "subtotal": SUBTOTAL, SUB TOTAL, JUMLAH
- "tax": TAX, PB1, PPN, VAT
- "service_charge": SERVICE, SVC, SC
- "discount": DISC, DISKON, DISCOUNT (below the subtotal)
- "total": the amount on the TOTAL line, copied exactly
Use null for an amount whose label isn't printed.

Number format:
- "." and "," are thousands separators: "24.000" and "24,000" are 24000.
- Discounts are positive numbers, even when printed as "-3.000".

Ignore cash (TUNAI, CASH), change (KEMBALI, CHANGE) and card payment lines.
"""


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert our JSON Schema to the OpenAPI subset Gemini's responseSchema takes.

    The one difference that matters here: a nullable field is written
    `{"type": "number", "nullable": true}` instead of `{"type": ["number", "null"]}`.
    """
    converted: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, list):
            converted["type"] = next(t for t in value if t != "null")
            converted["nullable"] = "null" in value
        elif key == "properties":
            converted[key] = {name: to_gemini_schema(sub) for name, sub in value.items()}
        elif key == "items":
            converted[key] = to_gemini_schema(value)
        else:
            converted[key] = value
    return converted


def _mime_type(image: bytes) -> str:
    try:
        picture = Image.open(io.BytesIO(image))
    except UnidentifiedImageError as failure:
        raise ExtractionError("not a readable image") from failure
    return Image.MIME.get(picture.format or "", "image/png")


Response = tuple[int, dict[str, Any]]


def http_post(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> Response:
    """POST JSON and return (status code, parsed body), including for error statuses."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.load(error)
        except json.JSONDecodeError:
            return error.code, {}
    except (urllib.error.URLError, TimeoutError) as failure:
        # Network trouble, not the receipt's fault.
        raise TemporaryError(f"gemini unreachable: {failure}") from failure


def _retry_delay(body: dict[str, Any]) -> float | None:
    """Google's suggested wait from a 429 body ("RetryInfo": {"retryDelay": "37s"})."""
    for detail in body.get("error", {}).get("details", []):
        delay = detail.get("retryDelay")
        if isinstance(delay, str) and (match := re.fullmatch(r"([\d.]+)s", delay)):
            return float(match.group(1))
    return None


def _daily_quota_exhausted(body: dict[str, Any]) -> bool:
    # QuotaFailure details name the quota, e.g. "...PerDayPerProjectPerModel".
    return "PerDay" in json.dumps(body.get("error", {}).get("details", []))


class Gemini:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        min_interval: float = 6.0,
        max_retries: int = 4,
        timeout: float = 120,
        post: Callable[[str, dict[str, Any], dict[str, str], float], Response] = http_post,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("set the GEMINI_API_KEY environment variable")
        self.model = model
        self.name = model
        self.version = f"prompt-{PROMPT_VERSION}"
        self._headers = {"x-goog-api-key": key}
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        # Injected so tests run without network access or real waiting.
        self._post = post
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def extract(self, image: bytes) -> Receipt:
        body = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": _mime_type(image),
                                "data": base64.b64encode(image).decode(),
                            }
                        },
                        {"text": PROMPT},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(RESPONSE_SCHEMA),
            },
        }
        reply = self._request(body)
        try:
            text = reply["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as failure:
            candidates = reply.get("candidates") or [{}]
            reason = reply.get("promptFeedback") or candidates[0].get("finishReason")
            raise ExtractionError(f"no content in reply ({reason})") from failure
        try:
            return Receipt.model_validate_json(text)
        except ValidationError as error:
            raise ExtractionError(f"invalid output: {error}") from error

    def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{API_URL}/{self.model}:generateContent"
        for attempt in range(self.max_retries + 1):
            self._wait_for_slot()
            status, reply = self._post(url, body, self._headers, self.timeout)
            if status == 200:
                return reply
            if status in (401, 403):
                raise RuntimeError(f"gemini rejected the API key (HTTP {status})")
            if status == 404:
                # A setup problem (model retired or misspelled), not a bad
                # receipt: stop instead of caching it as one.
                message = reply.get("error", {}).get("message")
                raise RuntimeError(f"gemini model {self.model!r} unavailable: {message}")
            if status == 429 and _daily_quota_exhausted(reply):
                raise TemporaryError("gemini daily free quota used up; resume tomorrow")
            if status == 429 or status >= 500:
                # Per-minute limit or overloaded service: wait and try again,
                # unless that was the last attempt.
                if attempt < self.max_retries:
                    self._sleep(_retry_delay(reply) or 5 * 2**attempt)
                continue
            raise ExtractionError(f"gemini HTTP {status}: {reply.get('error', {}).get('message')}")
        raise TemporaryError(f"gemini still failing after {self.max_retries} retries")

    def _wait_for_slot(self) -> None:
        """Space requests at least `min_interval` seconds apart."""
        now = self._clock()
        if self._last_request is not None:
            wait = self._last_request + self.min_interval - now
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()
