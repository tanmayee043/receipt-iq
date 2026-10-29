from decimal import Decimal

import pytest

from receipt_iq.evaluation import EvalReport, evaluate, match_items, name_similarity, score_receipt
from receipt_iq.schema import LineItem, Receipt

D = Decimal
TOL = D("0.01")


def answer() -> Receipt:
    return Receipt(
        items=[
            LineItem(name="ICED LATTE", total_price=D("4.50")),
            LineItem(name="CHICKEN WRAP", total_price=D("8.00")),
            LineItem(name="BROWNIE", total_price=D("3.00")),
        ],
        subtotal=D("15.50"),
        tax=D("1.24"),
        total=D("16.74"),
    )


def test_perfect_extraction() -> None:
    score = score_receipt(answer(), answer())
    assert score.fully_correct
    assert not score.flagged
    assert score.fields == {"subtotal": True, "tax": True, "total": True}


def test_fields_missing_from_both_are_not_scored() -> None:
    # The answer has no tip; a prediction without one isn't "right" about it,
    # there's just nothing to score.
    assert "tip" not in score_receipt(answer(), answer()).fields


def test_missing_or_invented_field_is_wrong() -> None:
    dropped_tax = answer().model_copy(update={"tax": None})
    assert score_receipt(dropped_tax, answer()).fields["tax"] is False
    invented_tip = answer().model_copy(update={"tip": D(2)})
    assert score_receipt(invented_tip, answer()).fields["tip"] is False


def test_ocr_mangled_name_still_matches() -> None:
    assert name_similarity("CHIKEN WRAP", "Chicken Wrap") > 0.8
    predicted = [LineItem(name="CHIKEN WRAP", total_price=D("8.00"))]
    assert match_items(predicted, answer().items, TOL) == 1


def test_right_name_wrong_price_does_not_match() -> None:
    predicted = [LineItem(name="CHICKEN WRAP", total_price=D("3.00"))]
    assert match_items(predicted, answer().items, TOL) == 0


def test_items_match_one_to_one() -> None:
    # Two predicted brownies can't both claim the single real one.
    predicted = [LineItem(name="BROWNIE", total_price=D("3.00"))] * 2
    assert match_items(predicted, answer().items, TOL) == 1


def test_dropped_item_is_caught_by_validator() -> None:
    dropped = answer().model_copy(update={"items": answer().items[:2]})
    score = score_receipt(dropped, answer())
    assert (score.items_matched, score.items_predicted, score.items_expected) == (2, 2, 3)
    assert not score.fully_correct
    assert score.flagged  # items now add up to 12.50, not the 15.50 subtotal


def test_consistent_mistake_slips_past_validator() -> None:
    # A misread that keeps the arithmetic intact: the validator can't see it.
    # This is why the catch rate is measured rather than assumed to be 100%.
    items = [*answer().items[:2], LineItem(name="BROWNIE", total_price=D("8.00"))]
    wrong = answer().model_copy(
        update={"items": items, "subtotal": D("20.50"), "total": D("21.74")}
    )
    score = score_receipt(wrong, answer())
    assert not score.fully_correct
    assert not score.flagged


class Replay:
    """Returns prepared receipts in order, standing in for a real provider."""

    name = "replay"

    def __init__(self, outputs: list[Receipt]) -> None:
        self.outputs = iter(outputs)

    def extract(self, image: bytes) -> Receipt:
        return next(self.outputs)


def test_report_aggregates() -> None:
    perfect = answer()
    dropped = answer().model_copy(update={"items": answer().items[:2]})
    wrong_tax = answer().model_copy(update={"tax": D("1.00"), "total": D("16.50")})

    report = evaluate(Replay([perfect, dropped, wrong_tax]), [(b"", answer())] * 3, tolerance=TOL)

    assert report.receipts == 3
    assert report.field_accuracy()["tax"] == pytest.approx(2 / 3)
    assert report.field_accuracy()["tip"] is None
    assert report.item_precision() == 1.0  # nothing invented
    assert report.item_recall() == pytest.approx(8 / 9)  # one item missed
    assert report.fully_correct_rate() == pytest.approx(1 / 3)
    # dropped item is flagged; wrong tax with a matching total is not
    assert report.catch_rate() == pytest.approx(1 / 2)
    assert report.false_alarm_rate() == 0.0


def test_empty_report_has_no_rates() -> None:
    report = EvalReport()
    assert report.item_f1() is None
    assert report.catch_rate() is None
