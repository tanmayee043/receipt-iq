"""Azure provider tests: a fake API, so no network and no free pages are used."""

import http.client
import io
import os
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from receipt_iq.cache import CachedExtractor
from receipt_iq.evaluation import ExtractionError, TemporaryError
from receipt_iq.providers.azure_di import (
    AzureDocumentIntelligence,
    Response,
    fit_size,
    http_request,
    to_receipt,
)
from receipt_iq.validation import validate

IMAGE = b"image bytes"
RESULT_URL = "https://azure.test/analyzeResults/1"
ACCEPTED: Response = (202, {"operation-location": RESULT_URL}, {})
RUNNING: Response = (200, {}, {"status": "running"})


def money(amount: float, content: str = "") -> dict[str, Any]:
    return {"type": "currency", "valueCurrency": {"amount": amount}, "content": content}


def item(name: str | None, **fields: Any) -> dict[str, Any]:
    if name is not None:
        fields["Description"] = {"valueString": name}
    return {"type": "object", "valueObject": fields}


def done(fields: dict[str, Any]) -> Response:
    return 200, {}, {"status": "succeeded", "analyzeResult": {"documents": [{"fields": fields}]}}


TEA = {"Items": {"valueArray": [item("TEA", TotalPrice=money(5000))]}, "Total": money(5000)}


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


def azure(api: FakeApi, fake_time: FakeTime | None = None) -> AzureDocumentIntelligence:
    t = fake_time or FakeTime()
    return AzureDocumentIntelligence(
        endpoint="https://azure.test/", api_key="test-key", send=api, sleep=t.sleep, clock=t.clock
    )


def test_analyze_then_poll_until_done() -> None:
    api = FakeApi(ACCEPTED, RUNNING, done(TEA))
    assert azure(api).extract(IMAGE).total == Decimal(5000)
    (post_url, post_body, headers), (poll_url, poll_body, _), _ = api.calls
    assert post_url.startswith("https://azure.test/documentintelligence/documentModels/")
    assert "prebuilt-receipt:analyze" in post_url
    assert post_body == {"base64Source": "aW1hZ2UgYnl0ZXM="}
    assert (poll_url, poll_body) == (RESULT_URL, None)


def test_locale_is_sent_and_part_of_the_cache_version() -> None:
    # Regression: without it, Azure read an Indonesian "8.000" as 8.0.
    api = FakeApi(ACCEPTED, done(TEA))
    provider = azure(api)
    provider.extract(IMAGE)
    assert "locale=id-ID" in api.calls[0][0]
    assert "id-ID" in provider.version


def test_small_image_is_sent_untouched() -> None:
    assert fit_size(IMAGE) == IMAGE


def test_oversized_image_is_shrunk_to_fit() -> None:
    # Regression: a 10 MB photo got HTTP 400 and was scored as a failed receipt.
    picture = Image.frombytes("RGB", (800, 800), os.urandom(800 * 800 * 3))
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    assert buffer.tell() > 200_000
    shrunk = fit_size(buffer.getvalue(), limit=200_000)
    assert len(shrunk) <= 200_000
    assert Image.open(io.BytesIO(shrunk)).format == "JPEG"


def test_oversized_garbage_is_an_extraction_error() -> None:
    with pytest.raises(ExtractionError, match="not a readable image"):
        fit_size(b"x" * 100, limit=10)


def test_error_shows_azures_specific_reason() -> None:
    error = {"message": "Invalid request.", "innererror": {"message": "image too large"}}
    with pytest.raises(ExtractionError, match="image too large"):
        azure(FakeApi((400, {}, {"error": error}))).extract(IMAGE)


def test_key_goes_in_a_header_never_the_url() -> None:
    api = FakeApi(ACCEPTED, done(TEA))
    azure(api).extract(IMAGE)
    for url, _, headers in api.calls:
        assert "test-key" not in url
        assert headers["Ocp-Apim-Subscription-Key"] == "test-key"


def test_every_call_is_spaced_out_for_the_free_tier() -> None:
    t = FakeTime()
    azure(FakeApi(ACCEPTED, RUNNING, done(TEA)), t).extract(IMAGE)
    assert t.sleeps == [3.0, 3.0]


def test_rate_limit_waits_as_told_then_retries() -> None:
    t = FakeTime()
    api = FakeApi((429, {"retry-after": "17"}, {}), ACCEPTED, done(TEA))
    assert azure(api, t).extract(IMAGE).total == Decimal(5000)
    assert 17.0 in t.sleeps


def test_used_up_monthly_pages_stop_the_run_without_retrying() -> None:
    api = FakeApi((403, {}, {"error": {"message": "Out of call volume quota."}}))
    with pytest.raises(TemporaryError, match="monthly"):
        azure(api).extract(IMAGE)
    assert len(api.calls) == 1


@pytest.mark.parametrize("status", [401, 403, 404])
def test_bad_key_or_endpoint_is_a_config_error_not_a_receipt_failure(status: int) -> None:
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        azure(FakeApi((status, {}, {"error": {"message": "denied"}}))).extract(IMAGE)


def test_overload_that_never_clears_is_temporary() -> None:
    with pytest.raises(TemporaryError, match="unavailable"):
        azure(FakeApi(*[(503, {}, {})] * 5)).extract(IMAGE)


def test_network_trouble_is_retried() -> None:
    responses: list[Response] = [ACCEPTED, done(TEA)]
    failures = [TemporaryError("timed out")]

    def flaky(url: str, body: Any, headers: dict[str, str], timeout: float) -> Response:
        if failures:
            raise failures.pop()
        return responses.pop(0)

    t = FakeTime()
    provider = AzureDocumentIntelligence(
        endpoint="https://azure.test", api_key="k", send=flaky, sleep=t.sleep, clock=t.clock
    )
    assert provider.extract(IMAGE).total == Decimal(5000)


def test_dropped_connection_is_temporary(monkeypatch: pytest.MonkeyPatch) -> None:
    def drop(*args: Any, **kwargs: Any) -> Any:
        raise http.client.RemoteDisconnected("closed")

    monkeypatch.setattr(urllib.request, "urlopen", drop)
    with pytest.raises(TemporaryError, match="unreachable"):
        http_request("https://example.invalid", None, {}, 1)


def test_rejected_image_fails_only_that_receipt() -> None:
    api = FakeApi((400, {}, {"error": {"message": "corrupt image"}}))
    with pytest.raises(ExtractionError, match="corrupt") as caught:
        azure(api).extract(IMAGE)
    assert not isinstance(caught.value, TemporaryError)


def test_failed_analysis_fails_only_that_receipt() -> None:
    failed: Response = (200, {}, {"status": "failed", "error": {"code": "InvalidContent"}})
    with pytest.raises(ExtractionError) as caught:
        azure(FakeApi(ACCEPTED, failed)).extract(IMAGE)
    assert not isinstance(caught.value, TemporaryError)


def test_internal_failure_is_temporary() -> None:
    failed: Response = (200, {}, {"status": "failed", "error": {"code": "InternalServerError"}})
    with pytest.raises(TemporaryError, match="internal"):
        azure(FakeApi(ACCEPTED, failed)).extract(IMAGE)


def test_result_that_never_arrives_is_temporary() -> None:
    api = FakeApi(ACCEPTED, *[RUNNING] * 40)
    with pytest.raises(TemporaryError, match="no result"):
        azure(api).extract(IMAGE)


def test_no_document_or_no_total_is_an_extraction_error() -> None:
    empty: Response = (200, {}, {"status": "succeeded", "analyzeResult": {"documents": []}})
    with pytest.raises(ExtractionError, match="no receipt"):
        azure(FakeApi(ACCEPTED, empty)).extract(IMAGE)
    with pytest.raises(ExtractionError, match="no total"):
        azure(FakeApi(ACCEPTED, done({"Items": {"valueArray": []}}))).extract(IMAGE)


def test_temporary_errors_are_not_cached(tmp_path: Path) -> None:
    provider = azure(FakeApi((403, {}, {"error": {"message": "quota"}})))
    cached = CachedExtractor(provider, provider.version, tmp_path)
    with pytest.raises(TemporaryError):
        cached.extract(IMAGE)
    assert not list(tmp_path.rglob("*.json"))


def test_fields_map_onto_the_schema() -> None:
    receipt = to_receipt(
        {
            "MerchantName": {"valueString": "KOPI KITA"},
            "Items": {
                "valueArray": [
                    item(
                        "TEA",
                        Quantity={"valueNumber": 2},
                        Price=money(2.5, "2.500"),
                        TotalPrice=money(5000),
                    )
                ]
            },
            "Subtotal": money(5000),
            "TotalTax": money(500),
            "Tip": money(100),  # how Azure reports a service charge
            "Total": money(5600),
        }
    )
    assert receipt.vendor == "KOPI KITA"
    assert receipt.items[0].quantity == 2
    assert receipt.items[0].unit_price == 2500
    assert receipt.items[0].total_price == 5000
    amounts = (receipt.subtotal, receipt.tax, receipt.service_charge, receipt.total)
    assert amounts == (Decimal(5000), Decimal(500), Decimal(100), Decimal(5600))
    # Azure has no discount field, and CORD has no tips.
    assert receipt.discount is None and receipt.tip is None


def test_missing_line_total_comes_from_unit_price_times_quantity() -> None:
    fields = {
        "Items": {
            "valueArray": [item("TEA", Quantity={"valueNumber": 3}, Price=money(2.0, "2.000"))]
        },
        "Total": money(6000),
    }
    assert to_receipt(fields).items[0].total_price == 6000


def test_unit_price_comes_from_the_printed_text() -> None:
    # Regression: Azure parsed a printed "20.000" as 20.0 while the line total was
    # right, so the validator flagged correct receipts.
    fields = {
        "Items": {
            "valueArray": [item("TEA", Price=money(20.0, "20.000"), TotalPrice=money(20000))]
        },
        "Total": money(20000),
    }
    line = to_receipt(fields).items[0]
    assert line.unit_price == 20000
    assert validate(to_receipt(fields)).passed


def test_items_without_a_name_or_price_are_skipped() -> None:
    fields = {
        "Items": {"valueArray": [item(None, TotalPrice=money(5000)), item("TEA")]},
        "Total": money(5000),
    }
    assert to_receipt(fields).items == []


def test_zero_quantity_falls_back_to_one() -> None:
    fields = {
        "Items": {"valueArray": [item("TEA", Quantity={"valueNumber": 0}, TotalPrice=money(5000))]},
        "Total": money(5000),
    }
    assert to_receipt(fields).items[0].quantity == 1


def test_negative_line_becomes_a_discount() -> None:
    fields = {
        "Items": {"valueArray": [item("PROMO", TotalPrice=money(-3000))]},
        "Total": money(0),
    }
    line = to_receipt(fields).items[0]
    assert line.total_price == 0
    assert line.discount == 3000
    assert line.net_price == -3000


def test_tax_falls_back_to_tax_details() -> None:
    fields = {
        "TaxDetails": {
            "valueArray": [
                {"valueObject": {"Amount": money(100)}},
                {"valueObject": {"Amount": money(200)}},
            ]
        },
        "Total": money(5000),
    }
    assert to_receipt(fields).tax == 300


def test_missing_settings_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_DI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_DI_KEY", raising=False)
    with pytest.raises(RuntimeError, match="AZURE_DI_ENDPOINT"):
        AzureDocumentIntelligence()
