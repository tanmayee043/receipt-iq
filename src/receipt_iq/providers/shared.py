"""Pieces every provider uses."""

import io
from typing import Any

from PIL import Image, UnidentifiedImageError

from receipt_iq.evaluation import ExtractionError

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

# Shared by the models that read the image directly, so their scores compare fairly.
VISION_PROMPT = """\
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


def mime_type(image: bytes) -> str:
    try:
        picture = Image.open(io.BytesIO(image))
    except UnidentifiedImageError as failure:
        raise ExtractionError("not a readable image") from failure
    return Image.MIME.get(picture.format or "", "image/png")
