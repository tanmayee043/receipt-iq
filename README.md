# Receipt IQ

Turn receipt photos into validated line-item data, and measure how accurate,
fast and expensive each extraction provider is.

> Work in progress. This README grows with each milestone.

## What exists so far

- **`schema.py`**: the receipt shape every provider must return. Money is `Decimal`, never float.
- **`validation.py`**: arithmetic checks that catch extraction mistakes without knowing the
  right answer: line math, items vs. subtotal, and
  `subtotal - discount + service + tax + tip == total`. A receipt whose total can't be checked
  is reported as unverified, not as passing. Handles line discounts and both tax-inclusive layouts.
- **`cord.py`**: loads the [CORD](https://github.com/clovaai/cord) receipt dataset and turns its
  hand-made labels into the same schema, so they can be the answer key for scoring providers.

## Checking the checker

Before the validator judges model output, it has to agree with human-made labels. On CORD's
100-receipt test split:

| Result | Receipts |
|---|---|
| Passed | 93 |
| Label has no printed total (excluded from evaluation) | 5 |
| Flagged | 2 |

The first run passed 82. Each of the other failures traced back to a parser assumption or a
missing rule (cents in "165000.00", per-line discounts, tax-inclusive prices, a subtotal printed
twice), and each now has a regression test. The 2 still flagged are real: one label's numbers
don't add up, and one receipt rounds its total to the nearest 500.

```bash
python scripts/download_cord.py test     # ~234 MB into data/cord/
python scripts/check_cord_labels.py
```

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
```
