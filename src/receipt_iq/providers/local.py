"""The fully local provider: Tesseract OCR reads the text, a small LLM structures it.

Nothing leaves the laptop, so this is the path for sensitive documents, and it
costs nothing per page. The tradeoff is accuracy and speed: OCR loses the
receipt's layout, and a 3.8B model on a CPU takes tens of seconds per receipt.

    image ──► Tesseract (text) ──► phi3 via Ollama (JSON) ──► Receipt
"""

import io
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from receipt_iq.evaluation import ExtractionError
from receipt_iq.providers.shared import RESPONSE_SCHEMA
from receipt_iq.schema import Receipt

# Bump when the OCR settings change: the cache key includes this and the prompt
# version, so old results are never reused for a different pipeline.
OCR_VERSION = "gray2000-psm6"
OCR_WIDTH = 2000


@dataclass(frozen=True)
class Prompt:
    system: str
    # Worked examples as (OCR text, expected JSON) pairs, shown before the real
    # receipt. Made up, never taken from the evaluation splits.
    examples: list[tuple[str, str]] = field(default_factory=list)
    # Cap on generated tokens; stops runaway output on garbled OCR text.
    max_tokens: int | None = None


_V1_SYSTEM = """\
You extract data from the OCR text of a shop receipt. The text may contain OCR
mistakes. Return only JSON matching the schema.

Rules:
- One entry in "items" per purchased line. "total_price" is the line amount as
  printed (before any discount on that line). "quantity" defaults to 1.
- Amounts are plain numbers. Receipts may use "." or "," as thousands
  separators: "24.000" and "24,000" both mean 24000.
- A discount is a positive number, even when printed as "-2,000".
- Use null for anything not printed on the receipt ("-" means not printed).
  Never compute or guess a value.
- Ignore cash paid, change, and card payment lines.
"""

# v2 targets what v1's validation run showed: phi3 never filled in the
# subtotal, invented item names ("item1"), computed totals instead of copying
# them, and ran until the timeout on garbled text.
_V2_SYSTEM = """\
You copy data from the OCR text of a shop receipt into JSON. The text may
contain OCR mistakes. Copy what is printed; never calculate or invent anything.

Items:
- One entry per purchased line, with the name copied as printed. Never use
  placeholder names like "item1".
- "total_price" is the amount printed on that line. "quantity" is the count
  printed before the name, default 1.
- A discount printed under an item goes in that item's "discount".

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

_V2_EXAMPLE_OCR = """\
KOPI KITA
JL. MERDEKA 12
2 ES KOPI SUSU 36.000
1 ROTI BAKAR 18,000
  DISC -3.000
SUBTOTAL 51.000
PB1 10% 5.100
TOTAL 56.100
TUNAI 100.000
KEMBALI 43.900"""

_V2_EXAMPLE_JSON = json.dumps(
    {
        "items": [
            {"name": "ES KOPI SUSU", "quantity": 2, "unit_price": None, "total_price": 36000,
             "discount": None},
            {"name": "ROTI BAKAR", "quantity": 1, "unit_price": None, "total_price": 18000,
             "discount": 3000},
        ],
        "subtotal": 51000, "discount": None, "service_charge": None, "tax": 5100,
        "tip": None, "total": 56100,
    }
)  # fmt: skip

PROMPTS = {
    "v1": Prompt(system=_V1_SYSTEM),
    "v2": Prompt(
        system=_V2_SYSTEM,
        examples=[(_V2_EXAMPLE_OCR, _V2_EXAMPLE_JSON)],
        max_tokens=768,
    ),
}

# The UB Mannheim installer doesn't add Tesseract to PATH.
_WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def find_tesseract() -> str:
    command = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if command:
        return command
    if os.path.exists(_WINDOWS_TESSERACT):
        return _WINDOWS_TESSERACT
    raise RuntimeError("Tesseract not found: install it or set TESSERACT_CMD")


def prepare_for_ocr(image: bytes, width: int = OCR_WIDTH) -> bytes:
    """Grayscale and scale to a fixed width.

    Receipt text is small relative to a phone photo, and Tesseract reads best
    when characters are a few dozen pixels tall.
    """
    try:
        picture = Image.open(io.BytesIO(image)).convert("L")
    except UnidentifiedImageError as failure:
        raise ExtractionError("not a readable image") from failure
    scale = width / picture.width
    picture = picture.resize((width, round(picture.height * scale)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    return buffer.getvalue()


def tesseract_ocr(image: bytes, command: str | None = None, timeout: float = 60) -> str:
    """Image bytes in, text out.

    --psm 6 treats the image as one uniform block of text. On CORD's
    validation split it found about 2.5x more amounts than --psm 4 (single
    column), which splits receipts' two-column item/price lines apart.
    """
    try:
        result = subprocess.run(
            [command or find_tesseract(), "stdin", "stdout", "--psm", "6"],
            input=prepare_for_ocr(image),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as failure:
        raise ExtractionError(f"tesseract timed out after {timeout}s") from failure
    if result.returncode != 0:
        raise ExtractionError(f"tesseract failed: {result.stderr.decode(errors='replace')}")
    return result.stdout.decode("utf-8", errors="replace")


def ollama_chat(url: str, payload: dict[str, Any], timeout: float) -> str:
    """POST to Ollama's /api/chat and return the reply text."""
    request = urllib.request.Request(
        f"{url}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return str(json.load(response)["message"]["content"])
    except (urllib.error.URLError, TimeoutError) as failure:
        raise ExtractionError(f"ollama request failed: {failure}") from failure


class TesseractOllama:
    def __init__(
        self,
        model: str = "phi3",
        prompt_version: str = "v2",
        url: str | None = None,
        timeout: float = 300,
        ocr: Callable[[bytes], str] = tesseract_ocr,
        chat: Callable[[str, dict[str, Any], float], str] = ollama_chat,
    ) -> None:
        self.model = model
        self.prompt = PROMPTS[prompt_version]
        self.name = f"tesseract+{model}"
        self.version = f"{OCR_VERSION}.prompt-{prompt_version}"
        self.url = url or os.environ.get("OLLAMA_URL", "http://localhost:11434")
        self.timeout = timeout
        # Injected so tests can run without Tesseract or a model.
        self.ocr = ocr
        self.chat = chat

    def extract(self, image: bytes) -> Receipt:
        text = self.ocr(image)
        if not text.strip():
            raise ExtractionError("OCR found no text")

        messages = [{"role": "system", "content": self.prompt.system}]
        for example_text, example_json in self.prompt.examples:
            messages += [
                {"role": "user", "content": example_text},
                {"role": "assistant", "content": example_json},
            ]
        messages.append({"role": "user", "content": text})
        options: dict[str, Any] = {"temperature": 0, "num_ctx": 4096}
        if self.prompt.max_tokens is not None:
            options["num_predict"] = self.prompt.max_tokens
        # One retry: show the model its own invalid output and the error.
        for attempt in range(2):
            reply = self.chat(
                self.url,
                {
                    "model": self.model,
                    "messages": messages,
                    "format": RESPONSE_SCHEMA,
                    "stream": False,
                    # temperature 0: same input, same output, so runs are comparable
                    "options": options,
                },
                self.timeout,
            )
            try:
                return Receipt.model_validate_json(reply)
            except ValidationError as error:
                if attempt == 1:
                    raise ExtractionError(f"invalid output after retry: {error}") from error
                messages += [
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": f"That JSON is invalid:\n{error}\nReturn corrected JSON only.",
                    },
                ]
        raise AssertionError("unreachable")
