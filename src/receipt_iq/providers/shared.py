"""Pieces every provider uses."""

from typing import Any

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
