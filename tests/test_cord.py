"""Tests use real CORD test-split labels, copied inline so no download is needed."""

from decimal import Decimal

import pytest

from receipt_iq.cord import CordLabelError, parse_amount, parse_quantity, to_receipt
from receipt_iq.validation import validate

D = Decimal


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("17500", D(17500)),
        ("24,000", D(24000)),
        ("60.000", D(60000)),
        ("-60.000", D(-60000)),
        ("@11000", D(11000)),
        ("Rp 1.250.000", D(1250000)),
        # Regression (test-043): ".00" is cents, not thousands. Was read as 16,500,000.
        ("Rp 165000.00", D(165000)),
        ("-2 600", D(-2600)),
        # Regression (test-024): the cents rule left no digits and raised.
        ("Rp.0", D(0)),
    ],
)
def test_parse_amount(text: str, expected: Decimal) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2", D(2)), ("1X", D(1)), ("x3", D(3)), ("2.00", D(2)), ("1.00xITEMs", D(1))],
)
def test_parse_quantity(text: str, expected: Decimal) -> None:
    assert parse_quantity(text) == expected


def test_list_menu_with_discount() -> None:
    receipt = to_receipt(
        {
            "menu": [
                {"nm": "ICE BLACKCOFFE", "cnt": "2", "price": "82,000"},
                {"nm": "AVOCADO COFFEE", "cnt": "1", "price": "61,000"},
                {"nm": "CHIKEN KATSU FF", "cnt": "1", "price": "51,000"},
            ],
            "sub_total": {"subtotal_price": "194,000", "discount_price": "19,400"},
            "total": {"total_price": "174,600", "cashprice": "200,000", "changeprice": "25,400"},
        }
    )
    assert [i.total_price for i in receipt.items] == [D(82000), D(61000), D(51000)]
    assert receipt.items[0].quantity == D(2)
    assert receipt.discount == D(19400)
    assert receipt.total == D(174600)
    assert validate(receipt).passed


def test_single_menu_object_with_add_on() -> None:
    receipt = to_receipt(
        {
            "menu": {
                "nm": "JASMINE MT ( L )",
                "cnt": "1",
                "price": "24,000",
                "sub": {"nm": "COCONUT JELLY ( L )", "price": "4,000"},
            },
            "sub_total": {"subtotal_price": "28,000"},
            "total": {"total_price": "28,000", "cashprice": "100,000"},
        }
    )
    assert [i.name for i in receipt.items] == ["JASMINE MT ( L )", "COCONUT JELLY ( L )"]
    assert validate(receipt).passed


def test_unit_price_and_tax_on_top() -> None:
    receipt = to_receipt(
        {
            "menu": {"nm": "DONAT GULA", "unitprice": "@11000", "cnt": "1X", "price": "11,000"},
            "sub_total": {"subtotal_price": "11,000"},
            "total": {"total_price": "11,000"},
        }
    )
    assert receipt.items[0].unit_price == D(11000)
    assert validate(receipt).passed

    taxed = to_receipt(
        {
            "menu": {"nm": "TRAD KY TOAST CARTE", "price": "28.182"},
            "sub_total": {"subtotal_price": "28.182", "tax_price": "2.818"},
            "total": {"total_price": "31.000"},
        }
    )
    assert validate(taxed).passed


def test_negative_discount_is_stored_positive() -> None:
    receipt = to_receipt(
        {
            "menu": {"nm": "-TICKET CP", "cnt": "2", "price": "60.000"},
            "sub_total": {"subtotal_price": "60.000", "discount_price": "-60.000"},
            "total": {"total_price": "60.000"},
        }
    )
    assert receipt.discount == D(60000)


def test_label_without_total_is_rejected() -> None:
    with pytest.raises(CordLabelError):
        to_receipt({"menu": {"nm": "TEA", "price": "5,000"}})


# Regressions found by running the validator over the real CORD test split.


def test_line_discounts_reduce_the_items_sum() -> None:
    # test-061: every printed price is before its "-2 600"-style discount.
    receipt = to_receipt(
        {
            "menu": [
                {"nm": "TALAM UNGU", "unitprice": "6500", "cnt": "1x",
                 "discountprice": "-2 600", "price": "6,500"},
                {"nm": "AREM - AREM", "unitprice": "8000", "cnt": "1X",
                 "discountprice": "-3,200", "price": "8,000"},
                {"nm": "LEMPER", "unitprice": "9000", "cnt": "2X",
                 "discountprice": "-7.200", "price": "18,000"},
                {"nm": "TEH KOTAK", "unitprice": "5000", "cnt": "1X", "price": "5,000"},
            ],
            "sub_total": {"subtotal_price": "24,500"},
            "total": {"total_price": "24,500"},
        }
    )  # fmt: skip
    assert [i.discount for i in receipt.items] == [D(2600), D(3200), D(7200), None]
    assert validate(receipt).passed


def test_percentage_discount_on_a_free_item() -> None:
    # test-012: a shopping bag at @880 with 100% off, printed as itemsubtotal "0".
    receipt = to_receipt(
        {
            "menu": [
                {"nm": "BLUS WANITA", "unitprice": "@120,000", "cnt": "1",
                 "discountprice": "0%", "itemsubtotal": "120,000"},
                {"nm": "SHOPPING BAG", "unitprice": "@880", "cnt": "1",
                 "discountprice": "100%", "itemsubtotal": "0"},
            ],
            "total": {"total_price": "120,000"},
        }
    )  # fmt: skip
    bag = receipt.items[1]
    assert (bag.total_price, bag.discount, bag.net_price) == (D(880), D(880), D(0))
    assert validate(receipt).passed


def test_subtotal_printed_twice_is_not_doubled() -> None:
    # test-013: the same subtotal appears twice on the receipt.
    receipt = to_receipt(
        {
            "menu": [
                {"nm": "BASO KUAH", "unitprice": "43.636", "cnt": "1", "price": "43.636"},
                {"nm": "A.MINERAL GELAS", "unitprice": "3,000", "cnt": "1", "price": "3,000"},
            ],
            "sub_total": {"subtotal_price": ["46.636", "46.636"], "tax_price": "4.664"},
            "total": {"total_price": "51.300"},
        }
    )
    assert receipt.subtotal == D(46636)
    assert validate(receipt).passed


def test_dash_means_no_tax() -> None:
    # test-060: tax printed as "-".
    receipt = to_receipt(
        {
            "menu": {"nm": "Bola Ubi Mini", "unitprice": "12.500", "cnt": "2x", "price": "25.000"},
            "sub_total": {"subtotal_price": "25.000", "tax_price": "-"},
            "total": {"total_price": "25.000"},
        }
    )
    assert receipt.tax is None
    assert validate(receipt).passed


def test_items_without_a_price() -> None:
    # test-026: price missing but unit price and count printed -> derive it.
    # test-070: a size note with no price at all is not an item.
    receipt = to_receipt(
        {
            "menu": [
                {"nm": "Teh Tawar", "unitprice": "4.000", "cnt": "2"},
                {"nm": "=*LARGE*==", "cnt": "2"},
            ],
            "total": {"total_price": "8.000"},
        }
    )
    assert [(i.name, i.total_price) for i in receipt.items] == [("Teh Tawar", D(8000))]


def test_discount_printed_on_line_and_subtotal_counts_once() -> None:
    # validation-007: -9,545 appears under the item and again under the subtotal.
    receipt = to_receipt(
        {
            "menu": {"nm": "Choco Devil", "cnt": "4", "discountprice": "-9,545", "price": "63,636"},
            "sub_total": {"subtotal_price": "63,636", "discount_price": "-9,545",
                          "tax_price": "5,409"},
            "total": {"total_price": "59,500"},
        }
    )  # fmt: skip
    assert receipt.items[0].discount is None
    assert receipt.discount == D(9545)
    assert validate(receipt).passed


def test_discount_printed_as_a_negative_item() -> None:
    # validation-021: "Discount BCA 15%" is its own line at -34,363.
    receipt = to_receipt(
        {
            "menu": [
                {"nm": "Mango Lemon Tea", "cnt": "1", "price": "Rp 29,090"},
                {"nm": "Sliders Set", "cnt": "1", "price": "Rp 113,636"},
                {"nm": "Chicken Vege Rice Bowl", "cnt": "1", "price": "Rp 86,363"},
                {"nm": "Discount BCA 15%", "cnt": "1", "price": "-Rp 34,363"},
            ],
            "sub_total": {"subtotal_price": "Rp 194,726", "service_price": "Rp 9,736",
                          "tax_price": "Rp 20,446"},
            "total": {"total_price": "Rp 224,908"},
        }
    )  # fmt: skip
    line = receipt.items[3]
    assert (line.total_price, line.discount, line.net_price) == (D(0), D(34363), D(-34363))
    assert validate(receipt).passed


def test_tax_inclusive_receipt() -> None:
    # test-048: item prices include tax; subtotal is printed net of it.
    receipt = to_receipt(
        {
            "menu": {"nm": "S-Hazelnut KOI Cafe", "unitprice": "@25,000", "cnt": "1X",
                     "price": "25,000", "sub": [{"nm": "25%"}, {"nm": "Less Ice"}]},
            "sub_total": {"subtotal_price": "22,728", "tax_price": "2,272"},
            "total": {"total_price": "25,000"},
        }
    )  # fmt: skip
    assert len(receipt.items) == 1
    assert validate(receipt).passed
