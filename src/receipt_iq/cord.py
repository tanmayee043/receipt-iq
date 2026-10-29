"""Load the CORD receipt dataset and convert its labels into `Receipt`s.

CORD (https://github.com/clovaai/cord) is 1,000 Indonesian receipts, each with
a hand-labeled answer. Those labels are the ground truth that extraction
providers get scored against, so they go through the same schema the providers
must return.

The labels are raw strings copied off the receipt, so the same amount can
appear as "60.000", "60,000", "60000" or "Rp 60000.00". IDR is printed without
cents, so "." and "," are thousands separators, except for a trailing ".00"
on the few receipts that print cents anyway.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from receipt_iq.schema import LineItem, Receipt

CURRENCY = "IDR"


class CordLabelError(ValueError):
    """A label that can't be turned into a Receipt (e.g. no total printed)."""


@dataclass(frozen=True)
class CordExample:
    id: str
    image: bytes
    receipt: Receipt


def parse_amount(text: str) -> Decimal:
    """'Rp 24,000' -> 24000, '-60.000' -> -60000, '165000.00' -> 165000."""
    cleaned = re.sub(r"[^0-9.,\-]", "", text)
    negative = cleaned.startswith("-")
    # One or two digits after the last separator are cents; three are thousands.
    cents = re.search(r"[.,](\d{1,2})$", cleaned)
    whole = cleaned[: cents.start()] if cents else cleaned
    digits = re.sub(r"\D", "", whole)
    if not digits and not cents:
        raise CordLabelError(f"no amount in {text!r}")
    digits = digits or "0"
    value = Decimal(digits) + (Decimal(f"0.{cents.group(1)}") if cents else 0)
    return -value if negative else value


def parse_quantity(text: str) -> Decimal:
    """'2' -> 2, '1X' -> 1, 'x3' -> 3, '2.00' -> 2.

    Unlike amounts, a "." here is a decimal point: counts are printed as "2.00".
    """
    match = re.search(r"\d+(?:[.,]\d+)?", text)
    if not match:
        raise CordLabelError(f"no quantity in {text!r}")
    return Decimal(match.group().replace(",", "."))


def _as_list(value: Any) -> list[Any]:
    # CORD stores one entry as an object and several as a list.
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _amount_field(group: dict[str, Any], key: str, *, repeated: str) -> Decimal | None:
    """Read one amount field; "-" on the receipt means none.

    Some fields are printed more than once: `repeated="first"` keeps the first
    (a subtotal shown twice is one subtotal), `repeated="sum"` adds them up
    (two tax lines are two taxes).
    """
    values = [v for v in _as_list(group.get(key)) if re.search(r"\d", v)]
    if not values:
        return None
    if repeated == "first":
        return parse_amount(values[0])
    return sum((parse_amount(v) for v in values), Decimal(0))


def _line_item(entry: dict[str, Any]) -> LineItem | None:
    """One menu entry, or None for a modifier line that carries no price."""
    quantity = parse_quantity(entry["cnt"]) if entry.get("cnt") else Decimal(1)
    unit_price = parse_amount(entry["unitprice"]) if entry.get("unitprice") else None
    price = entry.get("price", entry.get("itemsubtotal"))
    if price is not None:
        total_price = parse_amount(_as_list(price)[0])
    elif unit_price is not None:
        total_price = quantity * unit_price
    else:
        # e.g. "=*LARGE*==" under a drink: a size note, not something bought.
        return None

    discount = None
    printed_discount = entry.get("discountprice")
    if isinstance(printed_discount, str) and printed_discount.endswith("%"):
        # A percentage: the printed line price may already be discounted, so
        # rebuild the gross price from the unit price when it's known.
        if unit_price is not None:
            total_price = quantity * unit_price
            discount = total_price * parse_quantity(printed_discount) / 100
    elif printed_discount:
        discount = abs(parse_amount(_as_list(printed_discount)[0]))

    if total_price < 0:
        # A discount printed as its own item ("Discount BCA 15%  -34,363"). It
        # costs nothing and takes the amount off, so the items still add up
        # to the printed subtotal.
        discount = -total_price
        total_price = Decimal(0)

    return LineItem(
        name=str(entry.get("nm") or entry.get("num") or "(unnamed)"),
        quantity=quantity,
        unit_price=unit_price,
        total_price=total_price,
        discount=discount or None,
    )


def to_receipt(gt_parse: dict[str, Any]) -> Receipt:
    """Convert one CORD `gt_parse` label into a Receipt."""
    items: list[LineItem] = []
    for entry in _as_list(gt_parse.get("menu")):
        # Add-ons ("+ extra cheese") are priced separately but belong to the
        # same subtotal, so they become their own lines.
        for line in [entry, *_as_list(entry.get("sub"))]:
            item = _line_item(line)
            if item is not None:
                items.append(item)

    sub_total = gt_parse.get("sub_total") or {}
    total_group = gt_parse.get("total") or {}
    total = _amount_field(total_group, "total_price", repeated="first")
    if total is None:
        # Some receipts only print cash paid and change. The label doesn't say
        # what the total is, so there's no ground truth to score against.
        raise CordLabelError("label has no total_price")

    discount = _amount_field(sub_total, "discount_price", repeated="sum")
    if discount:
        discount = abs(discount)
        line_discounts = sum((item.discount or 0 for item in items), Decimal(0))
        if line_discounts == discount:
            # The same discount printed twice: under the item and again under
            # the subtotal. Keep it once, at receipt level, or it's subtracted
            # twice.
            items = [item.model_copy(update={"discount": None}) for item in items]

    service = _amount_field(sub_total, "service_price", repeated="sum")
    other_service = _amount_field(sub_total, "othersvc_price", repeated="sum")
    if other_service is not None:
        service = (service or Decimal(0)) + other_service

    return Receipt(
        currency=CURRENCY,
        items=items,
        subtotal=_amount_field(sub_total, "subtotal_price", repeated="first"),
        # Printed with or without a minus sign; the schema stores it positive.
        discount=abs(discount) if discount is not None else None,
        service_charge=service,
        tax=_amount_field(sub_total, "tax_price", repeated="sum"),
        total=total,
    )


def load_split(parquet_path: Path) -> Iterator[CordExample | CordLabelError]:
    """Yield every example in a downloaded CORD parquet file.

    Labels that can't be parsed are yielded as errors rather than raised, so a
    caller can count them instead of losing the whole run to one bad label.
    """
    table = pq.read_table(parquet_path)
    for index, row in enumerate(table.to_pylist()):
        label = json.loads(row["ground_truth"])
        example_id = f"{parquet_path.stem}-{index:03d}"
        try:
            receipt = to_receipt(label["gt_parse"])
        except (CordLabelError, ValueError) as error:
            yield CordLabelError(f"{example_id}: {error}")
            continue
        yield CordExample(id=example_id, image=row["image"]["bytes"], receipt=receipt)
