"""GPT provider tests: a fake API, so no network and no credit is used."""

import io
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from receipt_iq.cache import CachedExtractor
from receipt_iq.evaluation import ExtractionError, TemporaryError
from receipt_iq.providers.azure_di import Response
from receipt_iq.providers.azure_openai import AzureOpenAI, to_strict_schema
from receipt_iq.providers.shared import RESPONSE_SCHEMA, VISION_PROMPT


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, "PNG")
    return buffer.getvalue()


RECEIPT_JSON = json.dumps({"items": [{"name": "TEA", "total_price": 5000}], "total": 5000})


def answer(text: str | None, finish_reason: str = "stop") -> Response:
    choice = {"message": {"content": text}, "finish_reason": finish_reason}
    return 200, {}, {"choices": [choice]}


OK = answer(RECEIPT_JSON)


class FakeApi:
    def __init__(self, *responses: Response) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any] | None, dict[str, str]]] = []

    def __call__(
        self, url: str, body: dict[str, Any] | None, headers: dict[str, str], timeout: float
    ) -> Response:
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


def gpt(api: Any, fake_time: FakeTime | None = None) -> AzureOpenAI:
    t = fake_time or FakeTime()
    return AzureOpenAI(
        endpoint="https://gpt.test/",
        api_key="test-key",
        deployment="receipt-gpt",
        send=api,
        sleep=t.sleep,
        clock=t.clock,
    )


def test_valid_reply_becomes_receipt() -> None:
    assert gpt(FakeApi(OK)).extract(png()).total == Decimal(5000)


def test_request_shape() -> None:
    api = FakeApi(OK)
    gpt(api).extract(png())
    url, body, headers = api.calls[0]
    assert url == "https://gpt.test/openai/v1/chat/completions"
    assert "test-key" not in url
    assert headers == {"api-key": "test-key"}
    assert body is not None
    assert body["model"] == "receipt-gpt"
    assert body["max_completion_tokens"] == 4000
    assert body["response_format"]["json_schema"]["strict"] is True
    text, image = body["messages"][0]["content"]
    assert text["text"] == VISION_PROMPT
    assert image["image_url"]["url"].startswith("data:image/png;base64,")


def test_strict_schema_requires_every_property_and_nothing_extra() -> None:
    strict = to_strict_schema(RESPONSE_SCHEMA)
    assert set(strict["required"]) == set(strict["properties"])
    assert strict["additionalProperties"] is False
    line = strict["properties"]["items"]["items"]
    assert set(line["required"]) == {"name", "quantity", "unit_price", "total_price", "discount"}
    assert line["additionalProperties"] is False
    # Nullable fields stay nullable, so "required" doesn't force an invented value.
    assert line["properties"]["discount"]["type"] == ["number", "null"]


def test_requests_are_spaced_out() -> None:
    t = FakeTime()
    provider = gpt(FakeApi(OK, OK), t)
    provider.extract(png())
    provider.extract(png())
    assert t.sleeps == [3.0]


def test_rate_limit_waits_as_told_then_retries() -> None:
    t = FakeTime()
    api = FakeApi((429, {"retry-after": "17"}, {}), OK)
    assert gpt(api, t).extract(png()).total == Decimal(5000)
    assert 17.0 in t.sleeps


def test_overload_that_never_clears_is_temporary() -> None:
    with pytest.raises(TemporaryError, match="unavailable"):
        gpt(FakeApi(*[(503, {}, {})] * 5)).extract(png())


def test_network_trouble_is_retried() -> None:
    responses = [OK]
    failures = [TemporaryError("timed out")]

    def flaky(url: str, body: Any, headers: dict[str, str], timeout: float) -> Response:
        if failures:
            raise failures.pop()
        return responses.pop(0)

    assert gpt(flaky).extract(png()).total == Decimal(5000)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_setup_problem_is_a_config_error_not_a_receipt_failure(status: int) -> None:
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        gpt(FakeApi((status, {}, {"error": {"message": "denied"}}))).extract(png())


def test_rejected_request_fails_only_that_receipt() -> None:
    api = FakeApi((400, {}, {"error": {"message": "image too large"}}))
    with pytest.raises(ExtractionError, match="too large") as caught:
        gpt(api).extract(png())
    assert not isinstance(caught.value, TemporaryError)
    assert len(api.calls) == 1


def test_reply_cut_off_at_the_cap_is_an_extraction_error() -> None:
    api = FakeApi(answer("", "length"))
    with pytest.raises(ExtractionError, match="length"):
        gpt(api).extract(png())
    assert len(api.calls) == 1  # retrying would spend credit on the same result


def test_invalid_output_is_an_extraction_error() -> None:
    with pytest.raises(ExtractionError, match="invalid output"):
        gpt(FakeApi(answer('{"items": []}'))).extract(png())


def test_unreadable_image_is_an_extraction_error() -> None:
    with pytest.raises(ExtractionError, match="not a readable image"):
        gpt(FakeApi(OK)).extract(b"not an image")


def test_temporary_errors_are_not_cached(tmp_path: Path) -> None:
    provider = gpt(FakeApi(*[(503, {}, {})] * 5))
    cached = CachedExtractor(provider, provider.version, tmp_path)
    with pytest.raises(TemporaryError):
        cached.extract(png())
    assert not list(tmp_path.rglob("*.json"))


def test_missing_settings_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_DEPLOYMENT"):
        AzureOpenAI()
