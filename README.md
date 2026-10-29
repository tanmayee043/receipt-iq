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
- **`evaluation.py`**: scores a provider's output against labeled answers (see below).
- **`cord.py`**: loads the [CORD](https://github.com/clovaai/cord) receipt dataset and turns its
  hand-made labels into the same schema, so they can be the answer key for scoring providers.

## Checking the checker

Before the validator judges model output, it has to agree with human-made labels:

| CORD split (100 receipts each) | Passed | Flagged | No printed total (excluded) |
|---|---|---|---|
| Test | 93 | 2 | 5 |
| Validation | 96 | 2 | 2 |

- **Test split:** the first run passed 82. Each failure traced back to a parser assumption or a
  missing rule (cents in "165000.00", per-line discounts, tax-inclusive prices, a subtotal
  printed twice), and each now has a regression test.
- **Validation split:** run *after* those fixes, as receipts the rules had never seen, it passed
  **93 of 97 (96%)**. That's the honest out-of-sample number. Two more discount layouts it
  exposed (a discount printed twice, a discount as a negative item) were then fixed too.
- **The 4 still flagged are real:** a label whose numbers don't add up, a total rounded to the
  nearest 500, an add-on that repeats its parent's price, and a tax labeled as "etc".

```bash
python scripts/download_cord.py test         # ~234 MB into data/cord/
python scripts/download_cord.py validation   # ~242 MB
python scripts/check_cord_labels.py data/cord/validation.parquet
```

## Evaluation harness

`evaluation.py` scores any provider's output against the CORD answers:

- **Amount fields:** accuracy per field (total, subtotal, tax, ...).
- **Line items:** precision, recall and F1. An item matches when its price is right and its name
  is at least 80% similar, since OCR mangles names.
- **Fully correct:** every field and every item right.
- **Validator catch rate:** of receipts extracted wrongly, the share the validator flagged; and
  **false alarms**, the correct ones it flagged anyway.

Split usage: prompts get tuned on the **validation** split; final scores are reported on **test**.

No real provider is wired in yet. To prove the harness, `scripts/evaluate.py` has two
simulated ones: `oracle` (returns the answer; scores 100%) and `noisy` (the answer with
injected OCR-style mistakes). On the noisy run the harness exposed a validator bug: a receipt
with **every item missed** passed, because the item check was skipped when there were no items.
Fixed, with a regression test; the simulated catch rate went from 90.3% to 96.8%.

```bash
python scripts/evaluate.py oracle
python scripts/evaluate.py noisy
```

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
```
