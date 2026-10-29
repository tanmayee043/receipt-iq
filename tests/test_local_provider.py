"""The provider logic, with OCR and the model faked (no Tesseract or Ollama needed)."""

import io
import json
from decimal import Decimal
from typing import Any

import pytest
from PIL import Image

from receipt_iq.evaluation import ExtractionError
from receipt_iq.providers.local import (
    OCR_VERSION,
    OCR_WIDTH,
    PROMPTS,
    RESPONSE_SCHEMA,
    TesseractOllama,
    prepare_for_ocr,
)
from receipt_iq.schema import Receipt
from receipt_iq.validation import validate

GOOD = json.dumps(
    {
        "items": [{"name": "NASI GORENG", "quantity": 1, "total_price": 25000}],
        "subtotal": 25000,
        "tax": 2500,
        "total": 27500,
    }
)


class FakeModel:
    """Replies with prepared strings, and records what it was sent."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, url: str, payload: dict[str, Any], timeout: float) -> str:
        self.payloads.append(payload)
        return self.replies.pop(0)


def provider(
    model: FakeModel, ocr_text: str = "NASI GORENG 25.000\nTOTAL 27.500"
) -> TesseractOllama:
    return TesseractOllama(ocr=lambda image: ocr_text, chat=model)


def test_valid_reply_becomes_receipt() -> None:
    model = FakeModel(GOOD)
    receipt = provider(model).extract(b"image")
    assert receipt.total == Decimal(27500)
    assert receipt.items[0].name == "NASI GORENG"


def test_request_is_deterministic_and_schema_constrained() -> None:
    model = FakeModel(GOOD)
    provider(model).extract(b"image")
    payload = model.payloads[0]
    assert payload["format"] == RESPONSE_SCHEMA
    assert payload["options"]["temperature"] == 0
    assert payload["messages"][-1]["content"].startswith("NASI GORENG")


def test_invalid_reply_is_retried_with_the_error() -> None:
    negative_total = GOOD.replace("27500", "-27500")
    model = FakeModel(negative_total, GOOD)
    assert provider(model).extract(b"image").total == Decimal(27500)
    retry = model.payloads[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": negative_total}
    assert "invalid" in retry[-1]["content"]


def test_gives_up_after_one_retry() -> None:
    model = FakeModel("not json", "still not json")
    with pytest.raises(ExtractionError, match="after retry"):
        provider(model).extract(b"image")
    assert len(model.payloads) == 2


def test_blank_ocr_skips_the_model() -> None:
    model = FakeModel()
    with pytest.raises(ExtractionError, match="no text"):
        provider(model, ocr_text="  \n").extract(b"image")
    assert model.payloads == []


def test_image_is_grayscaled_and_scaled_to_ocr_width() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (500, 800), "white").save(buffer, "PNG")
    prepared = Image.open(io.BytesIO(prepare_for_ocr(buffer.getvalue())))
    assert prepared.mode == "L"
    assert prepared.size == (OCR_WIDTH, 3200)  # aspect ratio kept


def test_unreadable_image_is_an_extraction_error() -> None:
    with pytest.raises(ExtractionError, match="not a readable image"):
        prepare_for_ocr(b"not an image")


def test_version_covers_ocr_and_prompt() -> None:
    assert TesseractOllama(prompt_version="v1").version == f"{OCR_VERSION}.prompt-v1"
    assert TesseractOllama().version == f"{OCR_VERSION}.prompt-v2"


@pytest.mark.parametrize("version", sorted(PROMPTS))
def test_worked_examples_are_correct(version: str) -> None:
    # A wrong example would teach the model wrong answers.
    for _, example_json in PROMPTS[version].examples:
        assert validate(Receipt.model_validate_json(example_json)).passed


def test_v2_sends_example_and_caps_output() -> None:
    model = FakeModel(GOOD)
    TesseractOllama(prompt_version="v2", ocr=lambda image: "TEXT", chat=model).extract(b"x")
    payload = model.payloads[0]
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert payload["messages"][-1]["content"] == "TEXT"
    assert payload["options"]["num_predict"] == 768


def test_v1_is_unchanged() -> None:
    model = FakeModel(GOOD)
    TesseractOllama(prompt_version="v1", ocr=lambda image: "TEXT", chat=model).extract(b"x")
    payload = model.payloads[0]
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert "num_predict" not in payload["options"]
