"""Arithmetic checks that catch extraction mistakes without knowing the right answer.

A model can be confident and still wrong. The numbers on a receipt have to agree
with each other, so if they don't, at least one was extracted wrongly and the
receipt goes to human review instead of the database.

Checks, from smallest to largest:
1. Each line:  quantity x unit_price == line total (when the unit price is printed)
2. Items:      sum of line totals (after line discounts) == subtotal (when printed)
3. Receipt:    subtotal - discount + service_charge + tax + tip == total

Tax-inclusive receipts (common where VAT is built into shelf prices) come in
two layouts, and both are accepted:
- the subtotal is printed net of tax, so items add up to subtotal + tax;
- the subtotal already includes tax, so total = subtotal with no tax added.
The cost: on those receipts the tax amount itself isn't cross-checked.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from receipt_iq.schema import Receipt

# Default allowed difference. Receipts round per line, so a few cents of drift is
# normal; currencies without cents (e.g. IDR in CORD) need a larger tolerance.
DEFAULT_TOLERANCE = Decimal("0.01")


class IssueCode(StrEnum):
    LINE_ARITHMETIC = "line_arithmetic"
    ITEMS_VS_SUBTOTAL = "items_vs_subtotal"
    TOTAL_MISMATCH = "total_mismatch"


@dataclass(frozen=True)
class Issue:
    code: IssueCode
    message: str
    expected: Decimal
    actual: Decimal
    # Which part of the receipt is wrong, e.g. "items[2]" or "total".
    location: str


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    # False when the receipt has neither items nor a subtotal, so the total
    # could not be checked against anything. Callers should not treat that as
    # a pass: it is "unknown", and belongs in review.
    total_verified: bool = False

    @property
    def passed(self) -> bool:
        return not self.issues and self.total_verified


def validate(receipt: Receipt, tolerance: Decimal = DEFAULT_TOLERANCE) -> ValidationResult:
    result = ValidationResult()

    def differs(a: Decimal, b: Decimal) -> bool:
        return abs(a - b) > tolerance

    for i, item in enumerate(receipt.items):
        if item.unit_price is None:
            continue
        expected = item.quantity * item.unit_price
        if differs(expected, item.total_price):
            result.issues.append(
                Issue(
                    code=IssueCode.LINE_ARITHMETIC,
                    message=f"{item.name!r}: {item.quantity} x {item.unit_price} "
                    f"should be {expected}, receipt says {item.total_price}",
                    expected=expected,
                    actual=item.total_price,
                    location=f"items[{i}]",
                )
            )

    items_sum = sum((item.net_price for item in receipt.items), Decimal(0))
    tax_inclusive = (
        receipt.subtotal is not None
        and receipt.tax is not None
        and not differs(items_sum, receipt.subtotal + receipt.tax)
    )

    # Runs even with no items: a printed subtotal with nothing behind it means
    # the items were missed, not that there weren't any.
    if receipt.subtotal is not None and differs(items_sum, receipt.subtotal) and not tax_inclusive:
        result.issues.append(
            Issue(
                code=IssueCode.ITEMS_VS_SUBTOTAL,
                message=f"line items add up to {items_sum}, subtotal says {receipt.subtotal}"
                + (f" (or {receipt.subtotal + receipt.tax} with tax)" if receipt.tax else ""),
                expected=items_sum,
                actual=receipt.subtotal,
                location="subtotal",
            )
        )

    # Prefer the printed subtotal: if it disagrees with the items, that is
    # already reported above, and this check then isolates the charges below it.
    if receipt.subtotal is not None:
        base = receipt.subtotal
    elif receipt.items:
        base = items_sum
    else:
        return result

    expected_total = (
        base
        - (receipt.discount or 0)
        + (receipt.service_charge or 0)
        + (receipt.tax or 0)
        + (receipt.tip or 0)
    )
    # Tax printed for information only, already inside the subtotal ("PPN
    # included"). Only allowed when there is a tax to leave out.
    tax_already_included = receipt.tax is not None and not differs(
        expected_total - receipt.tax, receipt.total
    )
    result.total_verified = True
    if differs(expected_total, receipt.total) and not tax_already_included:
        result.issues.append(
            Issue(
                code=IssueCode.TOTAL_MISMATCH,
                message=f"subtotal - discount + service + tax + tip = {expected_total}, "
                f"total says {receipt.total}",
                expected=expected_total,
                actual=receipt.total,
                location="total",
            )
        )

    return result
