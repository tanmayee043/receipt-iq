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
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from receipt_iq.evaluation import ExtractionError
from receipt_iq.schema import Receipt

# Bump when the OCR settings or the prompt change: the cache key includes both,
# so old results are never reused for a different pipeline.
OCR_VERSION = "gray2000-psm6"
PROMPT_VERSION = "v1"
OCR_WIDTH = 2000

SYSTEM_PROMPT = """\
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

# Kept simpler than the Receipt model's own JSON schema: small models follow a
# flat schema with plain numbers more reliably. Pydantic still enforces the
# full rules (non-negative amounts, required total) on what comes back.
_NUMBER_OR_NULL = {"type": ["number", "null"]}
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_price": _NUMBER_OR_NULL,
                    "total_price": {"type": "number"},
                    "discount": _NUMBER_OR_NULL,
                },
                "required": ["name", "total_price"],
            },
        },
        "subtotal": _NUMBER_OR_NULL,
        "discount": _NUMBER_OR_NULL,
        "service_charge": _NUMBER_OR_NULL,
        "tax": _NUMBER_OR_NULL,
        "tip": _NUMBER_OR_NULL,
        "total": {"type": "number"},
    },
    "required": ["items", "total"],
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
        url: str | None = None,
        timeout: float = 300,
        ocr: Callable[[bytes], str] = tesseract_ocr,
        chat: Callable[[str, dict[str, Any], float], str] = ollama_chat,
    ) -> None:
        self.model = model
        self.name = f"tesseract+{model}"
        self.version = f"{OCR_VERSION}.prompt-{PROMPT_VERSION}"
        self.url = url or os.environ.get("OLLAMA_URL", "http://localhost:11434")
        self.timeout = timeout
        # Injected so tests can run without Tesseract or a model.
        self.ocr = ocr
        self.chat = chat

    def extract(self, image: bytes) -> Receipt:
        text = self.ocr(image)
        if not text.strip():
            raise ExtractionError("OCR found no text")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
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
                    "options": {"temperature": 0, "num_ctx": 4096},
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
