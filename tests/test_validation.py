from decimal import Decimal

import pytest
from pydantic import ValidationError

from receipt_iq.schema import LineItem, Receipt
from receipt_iq.validation import DEFAULT_TOLERANCE, IssueCode, validate

D = Decimal


def dinner(**overrides: object) -> Receipt:
    """A correct receipt: 2 x 4.50 + 6.00 = 15.00, -1.00 + 1.20 tax + 3.00 tip = 18.20."""
    fields: dict[str, object] = {
        "vendor": "Corner Cafe",
        "currency": "USD",
        "items": [
            LineItem(name="Latte", quantity=D(2), unit_price=D("4.50"), total_price=D("9.00")),
            LineItem(name="Bagel", total_price=D("6.00")),
        ],
        "subtotal": D("15.00"),
        "discount": D("1.00"),
        "tax": D("1.20"),
        "tip": D("3.00"),
        "total": D("18.20"),
    }
    fields.update(overrides)
    return Receipt.model_validate(fields)


def codes(receipt: Receipt, tolerance: Decimal = DEFAULT_TOLERANCE) -> list[IssueCode]:
    return [issue.code for issue in validate(receipt, tolerance).issues]


def test_correct_receipt_passes() -> None:
    result = validate(dinner())
    assert result.issues == []
    assert result.passed


def test_misread_total_is_caught() -> None:
    # A classic OCR error: 18.20 read as 13.20.
    result = validate(dinner(total=D("13.20")))
    assert [i.code for i in result.issues] == [IssueCode.TOTAL_MISMATCH]
    assert result.issues[0].expected == D("18.20")
    assert not result.passed


def test_wrong_line_price_is_caught_and_located() -> None:
    items = [
        LineItem(name="Latte", quantity=D(2), unit_price=D("4.50"), total_price=D("4.50")),
        LineItem(name="Bagel", total_price=D("6.00")),
    ]
    result = validate(dinner(items=items, subtotal=D("10.50"), total=D("13.70")))
    assert [(i.code, i.location) for i in result.issues] == [
        (IssueCode.LINE_ARITHMETIC, "items[0]")
    ]


def test_missed_line_item_shows_up_against_subtotal() -> None:
    # The model dropped the bagel; everything else is right.
    items = [LineItem(name="Latte", quantity=D(2), unit_price=D("4.50"), total_price=D("9.00"))]
    assert codes(dinner(items=items)) == [IssueCode.ITEMS_VS_SUBTOTAL]


def test_all_items_missed_is_caught() -> None:
    # Regression: found by the evaluation harness. With no items, the items
    # check was skipped, so subtotal and total agreeing was enough to pass.
    assert codes(dinner(items=[])) == [IssueCode.ITEMS_VS_SUBTOTAL]


def test_without_printed_subtotal_items_are_the_base() -> None:
    assert codes(dinner(subtotal=None)) == []
    assert codes(dinner(subtotal=None, total=D("20.00"))) == [IssueCode.TOTAL_MISMATCH]


def test_line_discount_counts_against_subtotal() -> None:
    items = [
        LineItem(name="Latte", quantity=D(2), unit_price=D("4.50"), total_price=D("9.00")),
        LineItem(name="Bagel", total_price=D("6.00"), discount=D("2.00")),
    ]
    assert codes(dinner(items=items, subtotal=D("13.00"), total=D("16.20"))) == []
    # Ignoring the discount would make the items look like 15.00.
    assert codes(dinner(items=items)) == [IssueCode.ITEMS_VS_SUBTOTAL]


def test_tax_inclusive_prices_pass() -> None:
    # Items 15.00 include 1.36 VAT; the subtotal is printed net of it.
    receipt = dinner(subtotal=D("13.64"), discount=None, tax=D("1.36"), tip=None, total=D("15.00"))
    assert codes(receipt) == []
    # Still caught when neither reading of the subtotal fits.
    assert codes(receipt.model_copy(update={"subtotal": D("12.00")})) == [
        IssueCode.ITEMS_VS_SUBTOTAL,
        IssueCode.TOTAL_MISMATCH,
    ]


def test_tax_already_inside_subtotal_passes() -> None:
    # CORD test-043: subtotal 165,000 includes 15,000 tax; total is 165,000.
    receipt = Receipt(
        items=[LineItem(name="Cheese Tart", quantity=D(6), total_price=D(165000))],
        subtotal=D(165000),
        tax=D(15000),
        total=D(165000),
    )
    assert codes(receipt) == []
    # Without a tax line there is nothing to leave out, so a gap is still a gap.
    assert codes(receipt.model_copy(update={"tax": None, "total": D(150000)})) == [
        IssueCode.TOTAL_MISMATCH
    ]


def test_service_charge_counts_toward_total() -> None:
    assert codes(dinner(service_charge=D("2.00"), total=D("20.20"))) == []


def test_rounding_within_tolerance_passes() -> None:
    assert codes(dinner(total=D("18.21"))) == []
    assert codes(dinner(total=D("18.22"))) == [IssueCode.TOTAL_MISMATCH]


def test_currency_without_cents_needs_larger_tolerance() -> None:
    # CORD receipts are in IDR and round to the nearest 100.
    receipt = Receipt(
        currency="IDR",
        items=[LineItem(name="Nasi Goreng", total_price=D(25000))],
        tax=D(2500),
        total=D(27500),
    )
    assert codes(receipt, tolerance=D(100)) == []
    assert codes(receipt.model_copy(update={"total": D(27450)}), tolerance=D(100)) == []
    assert codes(receipt.model_copy(update={"total": D(28000)}), tolerance=D(100)) == [
        IssueCode.TOTAL_MISMATCH
    ]


def test_total_only_receipt_is_not_a_pass() -> None:
    # Nothing to check the total against: unknown, not correct.
    result = validate(Receipt(total=D("42.00")))
    assert result.issues == []
    assert not result.total_verified
    assert not result.passed


@pytest.mark.parametrize(
    "fields",
    [
        {"total": D("-1")},
        {"total": D(1), "currency": "usd"},
        {"total": D(1), "items": [{"name": "", "total_price": D(1)}]},
        {"total": D(1), "items": [{"name": "x", "quantity": D(0), "total_price": D(1)}]},
        {"total": D(1), "made_up_field": "x"},
    ],
)
def test_schema_rejects_malformed_output(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Receipt.model_validate(fields)
