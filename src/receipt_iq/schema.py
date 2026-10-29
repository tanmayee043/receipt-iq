"""The shape every extraction provider must return.

Money is always Decimal, never float: 0.1 + 0.2 != 0.3 in floating point, and a
validator that compares totals cannot afford that kind of drift.

Field names follow the CORD dataset's labels (menu items, sub_total, discount,
service, tax, total) so labeled examples map onto this model one-to-one.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

Money = Decimal


class LineItem(BaseModel):
    """One purchased item as printed on the receipt."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    quantity: Decimal = Field(default=Decimal(1), gt=0)
    # Many receipts print only the line total, so the unit price is optional.
    unit_price: Money | None = Field(default=None, ge=0)
    total_price: Money = Field(ge=0)


class Receipt(BaseModel):
    """A fully extracted receipt.

    Every amount except `total` is optional because receipts often leave lines
    out (no tip, no printed subtotal). Discounts are stored as positive numbers
    and subtracted during validation.
    """

    model_config = ConfigDict(extra="forbid")

    vendor: str | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    items: list[LineItem] = Field(default_factory=list)
    subtotal: Money | None = Field(default=None, ge=0)
    discount: Money | None = Field(default=None, ge=0)
    service_charge: Money | None = Field(default=None, ge=0)
    tax: Money | None = Field(default=None, ge=0)
    tip: Money | None = Field(default=None, ge=0)
    total: Money = Field(ge=0)
