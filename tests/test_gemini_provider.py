"""Gemini provider logic with the API faked: no network, no key, no real waiting."""

import io
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from receipt_iq.cache import CachedExtractor
from receipt_iq.evaluation import ExtractionError, TemporaryError, evaluate
from receipt_iq.providers.gemini import DEFAULT_MODEL, Gemini, to_gemini_schema
from receipt_iq.schema import Receipt


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, "PNG")
    return buffer.getvalue()


RECEIPT_JSON = json.dumps({"items": [{"name": "TEA", "total_price": 5000}], "total": 5000})
OK = (200, {"candidates": [{"content": {"parts": [{"text": RECEIPT_JSON}]}}]})
RETRY_INFO = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}
DAILY_QUOTA = {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}
PER_MINUTE = (429, {"error": {"details": [RETRY_INFO]}})
PER_DAY = (429, {"error": {"details": [DAILY_QUOTA]}})


class FakeApi:
    def __init__(self, *responses: tuple[int, dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(
        self, url: str, body: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, body, headers))
        return self.responses.pop(0)


class FakeTime:
    """A clock that only moves when the code sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now


def gemini(api: FakeApi, fake_time: FakeTime | None = None) -> Gemini:
    t = fake_time or FakeTime()
    return Gemini(api_key="test-key", post=api, sleep=t.sleep, clock=t.clock)


def test_valid_reply_becomes_receipt() -> None:
    assert gemini(FakeApi(OK)).extract(png()).total == Decimal(5000)


def test_request_shape() -> None:
    api = FakeApi(OK)
    gemini(api).extract(png())
    url, body, headers = api.calls[0]
    assert url.endswith(f"/{DEFAULT_MODEL}:generateContent")
    # The key travels in a header, never in the URL.
    assert headers == {"x-goog-api-key": "test-key"}
    assert "test-key" not in url
    image_part = body["contents"][0]["parts"][0]["inline_data"]
    assert image_part["mime_type"] == "image/png"
    assert body["generationConfig"]["temperature"] == 0


def test_schema_conversion_uses_nullable() -> None:
    schema = to_gemini_schema(
        {"type": "object", "properties": {"tax": {"type": ["number", "null"]}}}
    )
    assert schema["properties"]["tax"] == {"type": "number", "nullable": True}


def test_requests_are_spaced_out() -> None:
    t = FakeTime()
    provider = gemini(FakeApi(OK, OK), t)
    provider.extract(png())
    provider.extract(png())
    assert t.sleeps == [6.0]


def test_per_minute_limit_waits_as_told_then_retries() -> None:
    t = FakeTime()
    api = FakeApi(PER_MINUTE, OK)
    assert gemini(api, t).extract(png()).total == Decimal(5000)
    assert 37.0 in t.sleeps
    assert len(api.calls) == 2


def test_daily_quota_stops_without_retrying() -> None:
    api = FakeApi(PER_DAY)
    with pytest.raises(TemporaryError, match="daily"):
        gemini(api).extract(png())
    assert len(api.calls) == 1


def test_server_errors_back_off_then_give_up() -> None:
    t = FakeTime()
    api = FakeApi(*[(503, {})] * 5)
    with pytest.raises(TemporaryError, match="retries"):
        gemini(api, t).extract(png())
    assert len(api.calls) == 5
    # Exponential backoff between attempts (the 1 s is request spacing topping
    # up the first 5 s wait), and no pointless wait after the last attempt.
    assert t.sleeps == [5, 1.0, 10, 20, 40]


def test_bad_key_is_a_config_error_not_a_receipt_failure() -> None:
    with pytest.raises(RuntimeError, match="API key"):
        gemini(FakeApi((403, {}))).extract(png())


def test_blocked_or_empty_reply_is_an_extraction_error() -> None:
    with pytest.raises(ExtractionError, match="no content"):
        gemini(FakeApi((200, {"candidates": [{"finishReason": "SAFETY"}]}))).extract(png())


def test_missing_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        Gemini()


def test_temporary_errors_are_not_cached_and_stop_the_run(tmp_path: Path) -> None:
    api = FakeApi(PER_DAY, OK)
    cached = CachedExtractor(gemini(api), "v1", tmp_path)
    with pytest.raises(TemporaryError):
        evaluate(cached, [(png(), Receipt(total=Decimal(5000)))])
    assert not list(tmp_path.rglob("*.json"))
    # The next run calls the API again instead of replaying the quota error.
    assert cached.extract(png()).total == Decimal(5000)


def test_retired_model_is_a_config_error_not_a_receipt_failure(tmp_path: Path) -> None:
    # Regression: a 404 for a retired model was cached as a failed receipt.
    retired = (404, {"error": {"message": "no longer available to new users"}})
    cached = CachedExtractor(gemini(FakeApi(retired)), "v1", tmp_path)
    with pytest.raises(RuntimeError, match="unavailable"):
        cached.extract(png())
    assert not list(tmp_path.rglob("*.json"))
