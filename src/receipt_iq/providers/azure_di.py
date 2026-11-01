"""Azure Document Intelligence: Microsoft's prebuilt receipt model. No prompt, no LLM.

    image ──► POST analyze (202 + result URL) ──► poll until done ──► Receipt

The endpoint and key come from the AZURE_DI_ENDPOINT and AZURE_DI_KEY environment
variables, and the key is sent in a request header, never in the URL.

Runs on the free F0 tier (500 pages a month, 20 calls a minute), so every call,
polling included, is spaced out (`min_interval`). A used-up monthly allowance
stops the run with a TemporaryError; the cache keeps finished receipts.

The locale matters. Without one, the model reads an Indonesian "8.000" as 8.0, and
because the wrong numbers still add up, the validator can't catch it. CORD is
Indonesian, so the default is id-ID.

The model's parsed amounts need two corrections, both found on real receipts:
- Even with the locale, a line's unit `Price` of "20.000" comes back as 20.0, while
  `TotalPrice` on the same line is right. Unit prices are read from the printed text.
- It has no service charge field and reports Indonesian service charges as `Tip`,
  and CORD has no tips at all, so `Tip` maps to `service_charge`.

It has no receipt-level discount field either, so that stays empty.
"""

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from decimal import Decimal
from http.client import HTTPException
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from receipt_iq.cord import CordLabelError, parse_amount
from receipt_iq.evaluation import ExtractionError, TemporaryError
from receipt_iq.schema import LineItem, Receipt

MODEL = "prebuilt-receipt"
API_VERSION = "2024-11-30"
DEFAULT_LOCALE = "id-ID"
# The free tier rejects larger files with HTTP 400 (phone photos can be 10 MB).
MAX_IMAGE_BYTES = 4_000_000

Response = tuple[int, dict[str, str], dict[str, Any]]


def http_request(
    url: str, body: dict[str, Any] | None, headers: dict[str, str], timeout: float
) -> Response:
    """GET, or POST when there's a body. Returns (status, lowercase headers, parsed body)."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **headers}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _lower(response.headers), _parse(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _lower(error.headers), _parse(error.read())
    except (OSError, HTTPException) as failure:
        # Network trouble, not the receipt's fault.
        raise TemporaryError(f"azure unreachable: {failure}") from failure


def fit_size(image: bytes, limit: int = MAX_IMAGE_BYTES) -> bytes:
    """Re-encode an image over the size limit as JPEG, scaling down only if still too big."""
    if len(image) <= limit:
        return image
    try:
        picture = Image.open(io.BytesIO(image)).convert("RGB")
    except UnidentifiedImageError as failure:
        raise ExtractionError("not a readable image") from failure
    while True:
        buffer = io.BytesIO()
        picture.save(buffer, "JPEG", quality=90)
        if buffer.tell() <= limit:
            return buffer.getvalue()
        picture = picture.resize((picture.width * 3 // 4, picture.height * 3 // 4))


def _error_message(reply: dict[str, Any]) -> str | None:
    """Azure's specific reason when there is one ("Invalid request." alone says little)."""
    error = reply.get("error", {})
    message = error.get("innererror", {}).get("message") or error.get("message")
    return None if message is None else str(message)


def _lower(headers: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


def _parse(raw: bytes) -> dict[str, Any]:
    # The 202 from analyze has an empty body; error bodies aren't always JSON.
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _money(field: dict[str, Any] | None) -> Decimal | None:
    if not field:
        return None
    amount = field.get("valueCurrency", {}).get("amount", field.get("valueNumber"))
    # Through str: Decimal(0.1) would keep the float's rounding error.
    return None if amount is None else Decimal(str(amount))


def _printed_amount(field: dict[str, Any] | None) -> Decimal | None:
    """The amount in the field's printed text, with "." and "," as thousands separators."""
    try:
        return parse_amount(field["content"]) if field and field.get("content") else None
    except CordLabelError:
        return None


def _text(field: dict[str, Any] | None) -> str | None:
    if not field:
        return None
    text = field.get("valueString") or field.get("content")
    return str(text).strip() or None if text else None


def _line_item(item: dict[str, Any]) -> LineItem | None:
    """One entry of Azure's Items, or None when it has no name or no price to score."""
    fields = item.get("valueObject", {})
    name = _text(fields.get("Description"))
    unit_price = _printed_amount(fields.get("Price"))
    total_price = _money(fields.get("TotalPrice"))
    number = fields.get("Quantity", {}).get("valueNumber")
    quantity = Decimal(str(number)) if number and number > 0 else Decimal(1)
    if total_price is None and unit_price is not None:
        total_price = unit_price * quantity
    if name is None or total_price is None:
        return None
    # A discount printed as a negative line: stored the way the labels store it.
    if total_price < 0:
        return LineItem(name=name, quantity=quantity, total_price=Decimal(0), discount=-total_price)
    if unit_price is not None and unit_price < 0:
        unit_price = None
    return LineItem(name=name, quantity=quantity, unit_price=unit_price, total_price=total_price)


def _tax(fields: dict[str, Any]) -> Decimal | None:
    tax = _money(fields.get("TotalTax"))
    if tax is not None:
        return tax
    parts = [
        _money(detail.get("valueObject", {}).get("Amount"))
        for detail in fields.get("TaxDetails", {}).get("valueArray", [])
    ]
    known = [part for part in parts if part is not None]
    return sum(known, Decimal(0)) if known else None


def to_receipt(fields: dict[str, Any]) -> Receipt:
    """Map Azure's receipt fields onto our schema."""
    total = _money(fields.get("Total"))
    if total is None:
        raise ExtractionError("azure found no total")
    items = [_line_item(item) for item in fields.get("Items", {}).get("valueArray", [])]
    return Receipt(
        vendor=_text(fields.get("MerchantName")),
        items=[item for item in items if item is not None],
        subtotal=_money(fields.get("Subtotal")),
        tax=_tax(fields),
        service_charge=_money(fields.get("Tip")),
        total=total,
    )


class AzureDocumentIntelligence:
    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        locale: str = DEFAULT_LOCALE,
        # 3 s apart is the free tier's 20 calls a minute.
        min_interval: float = 3.0,
        max_retries: int = 4,
        max_polls: int = 40,
        timeout: float = 60,
        send: Callable[
            [str, dict[str, Any] | None, dict[str, str], float], Response
        ] = http_request,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        endpoint = endpoint or os.environ.get("AZURE_DI_ENDPOINT")
        key = api_key or os.environ.get("AZURE_DI_KEY")
        if not endpoint or not key:
            raise RuntimeError("set the AZURE_DI_ENDPOINT and AZURE_DI_KEY environment variables")
        self.name = "azure-document-intelligence"
        # "map": how fields become a Receipt. The cache stores the mapped result,
        # so a mapping change has to re-read the receipts.
        self.version = f"{MODEL}-{API_VERSION}.locale-{locale}.map-v2"
        self._url = (
            f"{endpoint.rstrip('/')}/documentintelligence/documentModels/{MODEL}:analyze"
            f"?api-version={API_VERSION}&locale={locale}"
        )
        self._headers = {"Ocp-Apim-Subscription-Key": key}
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.max_polls = max_polls
        self.timeout = timeout
        # Injected so tests run without network access or real waiting.
        self._send = send
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def extract(self, image: bytes) -> Receipt:
        body = {"base64Source": base64.b64encode(fit_size(image)).decode()}
        headers, _ = self._request(self._url, body)
        result_url = headers.get("operation-location")
        if not result_url:
            raise TemporaryError("azure accepted the receipt but gave no result URL")
        result = self._poll(result_url)
        documents = result.get("analyzeResult", {}).get("documents") or []
        if not documents:
            raise ExtractionError("azure found no receipt in the image")
        try:
            return to_receipt(documents[0].get("fields", {}))
        except ValidationError as error:
            raise ExtractionError(f"invalid output: {error}") from error

    def _poll(self, url: str) -> dict[str, Any]:
        for _ in range(self.max_polls):
            _, reply = self._request(url, None)
            status = reply.get("status")
            if status == "succeeded":
                return reply
            if status == "failed":
                error = reply.get("error", {})
                # Azure's own fault says nothing about the receipt.
                if "Internal" in str(error.get("code")):
                    raise TemporaryError(f"azure internal error: {error.get('message')}")
                raise ExtractionError(f"azure analysis failed: {error.get('message')}")
        raise TemporaryError(f"azure gave no result after {self.max_polls} checks")

    def _request(
        self, url: str, body: dict[str, Any] | None
    ) -> tuple[dict[str, str], dict[str, Any]]:
        for attempt in range(self.max_retries + 1):
            self._wait_for_slot()
            try:
                status, headers, reply = self._send(url, body, self._headers, self.timeout)
            except TemporaryError as failure:
                status, headers, reply = 0, {}, {"error": {"message": str(failure)}}
            if status in (200, 202):
                return headers, reply
            message = _error_message(reply)
            if status == 403 and "quota" in str(message).lower():
                # The free tier's monthly pages: not the key, and not the receipt.
                raise TemporaryError(f"azure free monthly pages used up: {message}")
            if status in (401, 403, 404):
                # A setup problem (wrong key or endpoint), not a bad receipt.
                raise RuntimeError(f"azure rejected the request (HTTP {status}): {message}")
            if status in (0, 429) or status >= 500:
                if attempt < self.max_retries:
                    delay = headers.get("retry-after", "")
                    self._sleep(float(delay) if delay.isdigit() else 5 * 2**attempt)
                continue
            raise ExtractionError(f"azure HTTP {status}: {message}")
        raise TemporaryError(f"azure unavailable: HTTP {status} after retries: {message}")

    def _wait_for_slot(self) -> None:
        """Space calls at least `min_interval` seconds apart."""
        now = self._clock()
        if self._last_request is not None:
            wait = self._last_request + self.min_interval - now
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()
