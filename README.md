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

## Providers

### Local: Tesseract OCR + phi3 (`providers/local.py`)

Fully on the laptop: nothing leaves the machine and it costs nothing per page.

```
image ──► grayscale, 2000 px wide ──► Tesseract (--psm 6) ──► phi3 via Ollama ──► Receipt
                                                    JSON schema, temperature 0, 1 retry
```

Results are cached by image hash + provider + version (OCR settings and prompt), so re-runs
are free and an interrupted run resumes.

Prompts are versioned and kept side by side (`PROMPTS` in `local.py`), so every result can be
reproduced. Run one with `python scripts/evaluate.py local --prompt v1`.

**CORD validation split, 98 receipts, CPU only (i5-1135G7):**

| Metric | Prompt v1 | Prompt v2 |
|---|---|---|
| Total correct | 32.7% | 35.7% |
| Subtotal / tax correct | 0.0% / 8.7% | 25.6% / 13.2% |
| Line items: precision / recall / F1 | 18.2% / 24.6% / 20.9% | 31.6% / 26.7% / 29.0% |
| Fully correct receipts | 2.0% | 3.1% |
| Failed (no usable output) | 8.2% (timeouts) | 1.0% |
| Validator catch rate / false alarms | 45.8% / 0.0% | 42.1% / 0.0% |
| Median latency | 22.1 s | 23.0 s |

**v1 → v2:** v1's run showed phi3 never returned a subtotal, invented item names ("item1"),
computed totals instead of copying them, and ran until the 5-minute timeout on garbled text.
v2 names the labels to look for (SUBTOTAL, PB1/PPN, SERVICE, DISC), forbids placeholder names
and calculated values, adds one made-up worked example, and caps output at 768 tokens.

What the numbers show:

- **OCR is the bottleneck.** Tesseract's default layout mode recovered 10 of 34 amounts on a
  12-receipt sample; switching to `--psm 6` (one uniform text block) raised that to 24. No
  prompt can recover what OCR never read.
- **The output cap removed the timeouts** (8 → 0); the one v2 failure is invalid JSON.
- **Small samples mislead.** On the first 10 receipts v2's item F1 was 45%; on all 98 it's 29%.
- **The validator is weaker against an LLM than against OCR noise** (42–46% catch rate here
  vs. 97% on simulated OCR errors): phi3 invents items whose prices add up to its own total,
  so the arithmetic stays consistent while being wrong.

This is the baseline the cloud providers will be compared against.

### Gemini 3.5 Flash (`providers/gemini.py`)

Reads the image directly, so nothing is lost to OCR. Free tier, so only the public CORD
dataset is sent.

```
image ──► Gemini API (JSON schema, temperature 0) ──► Receipt
```

Built for free-tier limits: requests are spaced 6 s apart, a rate-limit error (429) waits for
the delay Google asks for, and a used-up daily quota stops the run cleanly. Quota and network
errors are never cached or scored, so the next run resumes exactly where this one stopped.

Model choice, checked on a new free-tier account (Oct 2026): `gemini-2.5-flash` is closed to new
accounts, and `gemini-3.8-flash` never answered (a one-word reply hung for 3 minutes). The model
is pinned rather than an alias like `gemini-flash-latest`, so runs stay comparable.

**Partial result: first 12 of 98 validation receipts.** The free daily quota ran out after
about 13 requests; the full run is being completed across several days from the cache.

| Metric (same 12 receipts) | Local, prompt v2 | Gemini 3.5 Flash |
|---|---|---|
| Total correct | 58.3% | 100.0% |
| Line items: F1 | 55.8% | 90.9% |
| Fully correct receipts | 0 of 12 | 9 of 12 |
| Median latency | 23.7 s | 13.6 s |

```bash
python scripts/evaluate.py gemini        # needs GEMINI_API_KEY; re-run daily to resume
```

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
```
