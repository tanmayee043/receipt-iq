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

To prove the harness, `scripts/evaluate.py` has two
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

**Partial result so far: 73 of 98 validation receipts** (the comparison tables below use the first 57). The free daily quota
has ranged from 9 to 21 requests, so the full run is being completed day by day from the cache.

```bash
python scripts/evaluate.py gemini        # needs GEMINI_API_KEY; re-run daily to resume
python scripts/evaluate.py azure         # needs AZURE_DI_ENDPOINT and AZURE_DI_KEY
python scripts/evaluate.py gpt           # needs AZURE_OPENAI_ENDPOINT, _KEY and _DEPLOYMENT
```

### Gemma 4 31B (same module, `--model gemma-4-31b-it`)

Google's open-weights model on the same API key, with a far larger free quota than Gemini, so
it covered all 98 receipts. Two differences from Gemini, both found by running it:

- **No enforced JSON.** Gemma answers a `responseSchema` with HTTP 500, so it gets the schema in
  the prompt instead, and the JSON is pulled out of its reply (it reasons first; the answer is the
  last part). The cost: 3 replies were invalid (2 items with an empty name, 1 broken JSON).
- **An overloaded free service.** Over two days it returned 503 "high demand", dropped
  connections and 500s. Only a 500 that survives every retry counts against the receipt, and 3 in
  a row count as an outage that stops the run (resumed later from the cache). The first run
  recorded 15 receipts as 500 failures. Those were retried the next day, together with one receipt
  that had never been reached: 7 of the 16 still failed and stay scored as failures, so most of
  the first-run failures had been overload, not bad images.

**CORD validation split, all 98 receipts:**

| Metric | Local, prompt v2 | Gemma 4 31B |
|---|---|---|
| Total correct | 35.7% | 89.8% |
| Subtotal / tax correct | 25.6% / 13.2% | 87.7% / 89.1% |
| Line items: precision / recall / F1 | 31.6% / 26.7% / 29.0% | 95.9% / 89.7% / 92.7% |
| Fully correct receipts | 3.1% | 80.6% |
| Failed (no usable output) | 1.0% | 10.2% (7 server errors, 3 invalid) |
| Validator catch rate / false alarms | 42.1% / 0.0% | 63.2% / 1.3% |
| Median latency | 23.0 s | 110.5 s |

Of the 91 receipts Gemma answered, 79 (87%) were fully correct.

### Azure Document Intelligence (`providers/azure_di.py`)

Microsoft's prebuilt receipt model: no prompt and no LLM. Free tier (500 pages a month, 4 MB per
file), so calls are spaced 3 s apart, larger images are re-encoded to fit, and used-up monthly
pages stop the run cleanly.

```
image ──► POST analyze ──► poll until done ──► map fields ──► Receipt
```

Running it on real receipts found problems in how its reply has to be read:

- **Locale.** By default a printed "8.000" came back as 8.0. The item and total were both wrong in
  the same way, so the arithmetic still added up and the validator passed it. `locale=id-ID` fixes
  amounts and totals.
- **Unit prices.** Even with the locale, a line's `Price` of "20.000" came back as 20.0 next to a
  correct `TotalPrice`. This caused all 14 false alarms in the first full run, so unit prices are
  now read from the printed text.
- **Service charge.** There's no such field; it reports them as `Tip`. CORD has no tips, so `Tip`
  maps to `service_charge`. With both fixes, fully correct went from 57.1% to 61.2% and false
  alarms from 25.0% to 0.0%.

**CORD validation split, all 98 receipts:**

| Metric | Local, prompt v2 | Gemma 4 31B | Azure |
|---|---|---|---|
| Total correct | 35.7% | 89.8% | 91.8% |
| Subtotal / tax correct | 25.6% / 13.2% | 87.7% / 89.1% | 89.6% / 60.9% |
| Line items: precision / recall / F1 | 31.6% / 26.7% / 29.0% | 95.9% / 89.7% / 92.7% | 93.0% / 85.8% / 89.2% |
| Fully correct receipts | 3.1% | 80.6% | 61.2% |
| Failed (no usable output) | 1.0% | 10.2% | 5.1% (no total found) |
| Validator catch rate / false alarms | 42.1% / 0.0% | 63.2% / 1.3% | 71.1% / 0.0% |
| Median latency | 23.0 s | 110.5 s | 9.0 s (includes the 3 s call spacing) |

Azure reads totals and items about as well as Gemma, but fully correct is lower because of fields
it doesn't return: it has no receipt-level discount field (5 receipts) and left tax blank on 14,
often where a tax is printed. The scorer is strict here: 6 of the wrong receipts include a field
labeled 0 that Azure left blank. The 5 failures returned no total; 3 of them were among the 14
photos over 4 MB, which may be down to shrinking or to those photos being harder to read.

### GPT-5 mini on Azure OpenAI (`providers/azure_openai.py`)

Reads the image directly, with the same prompt as Gemini and strict JSON output. This is the one
provider that spends credit rather than a free tier: about $0.11 for all 98 receipts (roughly
155,000 tokens, at third-party-listed rates), so about $0.001 per receipt.

```
image ──► chat completions (strict JSON schema) ──► Receipt
```

Model choice, checked on an Azure for Students subscription (Nov 2026): `gpt-5.4-mini` has no
quota, and neither does `gpt-4o-mini` in the allowed region, so it runs `gpt-5-mini` version
2025-08-07, with automatic version upgrades turned off so runs stay comparable.

**CORD validation split, all 98 receipts:**

| Metric | GPT-5 mini |
|---|---|
| Total correct | 92.9% |
| Subtotal / tax correct | 86.2% / 82.6% |
| Line items: precision / recall / F1 | 83.8% / 84.9% / 84.4% |
| Fully correct receipts | 72.4% |
| Failed (no usable output) | 3.1% |
| Validator catch rate / false alarms | 37.0% / 0.0% |
| Median latency | 8.3 s |

- **Strict JSON isn't correct JSON.** All 3 failures matched the JSON schema but broke the
  `Receipt` rules: a negative price, an empty name and a quantity of 0. The schema can't express
  those limits; Pydantic caught them.
- **It can misread thousands too.** Two receipts came back as 172 for 172000 and 69 for 69000,
  despite the prompt's number rules. The amounts still added up, so the validator passed them,
  the same blind spot as Azure's "8.000".

### All five on the same 57 receipts

| Metric | Local, prompt v2 | Gemini 3.5 Flash | Gemma 4 31B | Azure | GPT-5 mini |
|---|---|---|---|---|---|
| Total correct | 40.4% | 98.2% | 91.2% | 89.5% | 94.7% |
| Line items: F1 | 31.7% | 95.0% | 92.0% | 88.6% | 86.3% |
| Fully correct receipts | 3 of 57 | 48 of 57 | 45 of 57 | 38 of 57 | 43 of 57 |
| Failed | 1 | 1 | 5 | 3 | 3 |
| Validator catch rate | 42.6% | 33.3% | 50.0% | 73.7% | 50.0% |
| Median latency | 24.7 s | 11.6 s | 102.8 s | 9.0 s | 8.6 s |

What the numbers show:

- **Reading the image beats OCR + a small model** by a wide margin: no prompt fixes text that OCR
  never read.
- **Gemini is the most accurate so far; GPT and Azure are the fastest.** Gemma is close to Gemini
  on accuracy but about 9× slower, and most of its failures come from the free service, not from
  reading the receipt.
- **The same mistake from Gemini, Gemma and GPT:** an item with an empty name. A next prompt version
  should require the printed name on every item (kept for later so these runs stay comparable).
- **A low catch rate isn't always a weak validator.** On Gemini, none of the 6 wrong receipts it
  missed has a wrong amount: they differ in item names, a blank subtotal or extra 0-priced lines,
  which arithmetic can't see. Azure's "8.000" bug is the opposite warning: wrong amounts that are
  consistent with each other pass too.

## Task queue (`tasks.py`)

Each receipt is one Celery task, with Redis as the broker, so an upload can return straight away
and the extraction runs in the background.

```
submit(image) ──► data/uploads/<hash> ──► Redis ──► worker ──► provider (through the cache)
                                                                   │
                                 passed / needs_review ◄── validate ┤
                                                                    └──► dead letter
```

| What happened | What the task does | Why |
|---|---|---|
| Valid receipt, arithmetic checks out | `passed` | |
| Valid receipt, arithmetic fails or can't be checked | `needs_review` | Feeds the review queue |
| `TemporaryError` (quota, overload, network) | Retries after 1, 2, 4, 8, then 16 min, then dead-letters without caching | Says nothing about the receipt, so it can be submitted again later |
| `ExtractionError` (provider can't read it) | Dead-letters immediately | Retrying spends money on the same answer |
| `RuntimeError` (wrong key, retired model) | Fails the task visibly | Every receipt would hit it, and only a person can fix it |

- **Safe to run twice.** The task id comes from the provider and image hash, and results go
  through the disk cache, so a repeat costs nothing.
- **Survives a crash.** A task only leaves Redis once it has finished (`acks_late`).
- **Only the hash is queued.** A 10 MB photo doesn't belong in a message broker.

**Checked for real with Redis and a worker, not only in tests:**

| Test | Result |
|---|---|
| Queue 3 receipts with no worker, then start one | They waited in Redis, then completed |
| Kill a worker with `Stop-Process -Force` mid-call | Redis still held the receipt; a second worker finished it, leaving 1 cache entry |
| Point a worker at a closed port | Real failures retried after 60 s, then 120 s; nothing cached or dead-lettered |

Two limits worth knowing:

- **A crashed receipt comes back slowly.** Redis re-delivers it only after the visibility timeout,
  1 hour by default (the crash test set `CELERY_VISIBILITY_TIMEOUT=30`). It has to outlast the
  longest task and retry wait, or a healthy task gets delivered twice.
- **`--pool=solo` doesn't enforce Celery's time limits.** It's what works on Windows, and running
  one task at a time suits the rate limits, but here each provider's own request timeout is what
  bounds a task. The time limits apply on Linux with the prefork pool. They weren't tested here.

```bash
docker compose up -d                                              # Redis, this machine only
celery -A receipt_iq.tasks worker --pool=solo --loglevel=info     # in a second terminal
python scripts/submit_receipts.py gpt --limit 3
```

## Router (`router.py`)

`submit(image)` now defaults to `provider="auto"`, and the router chooses for each receipt from
the numbers in `leaderboard.json`, which `scripts/build_leaderboard.py` generates from the cache.
Providers are only compared on receipts all of them answered.

```
ranked providers ──► circuit breaker open? skip ──► extract ──► validate ──► passed: done
                                                       │            │
                                 unavailable or failed: next    flagged: next, up to 2 answers
```

- **Policy:** the cheapest provider that reaches 75% fully correct and answers within 60 s. Those
  that miss either are a last resort, most accurate first.
- **Circuit breaker:** after 3 failures in a row a provider is skipped for 5 minutes, then gets one
  trial call. A wrong key opens it immediately.
- **Sensitive documents:** `local_only=True` never uses anything that leaves the machine.
- **Routing log:** every result lists each provider tried, what happened and why.

**What routing is worth, replayed on the 73 receipts all providers have answered**
(`scripts/simulate_routing.py`, no provider calls):

| Policy | Fully correct | Line-item F1 | $ per 1,000 | Sent to review | Mean time |
|---|---|---|---|---|---|
| Always Gemini (most accurate) | 86.3% | 94.6% | 5.33 | 4.1% | 16.8 s |
| Always GPT | 78.1% | 87.8% | 1.06 | 9.6% | 8.4 s |
| Always Azure | 65.8% | 89.3% | 9.32 | 27.4% | 8.3 s |
| **Router, 1 answer** | 79.5% | 91.5% | 1.33 | 6.8% | 9.1 s |
| **Router, second opinion (default)** | 83.6% | 92.6% | 1.63 | 1.4% | 12.9 s |

So far, the default router costs **69% less** than always using Gemini, at the price of 2 of the 73
receipts no longer being fully correct. It's also faster, and sends fewer receipts to a person.

Read those numbers with care:

- **Not final.** The router's accuracy figures come from these same receipts, so this flatters it.
  Gemini's run is also unfinished. The final number has to come from the test split.
- **Costs are list prices from third-party trackers.** GPT's token counts are measured; Gemini's
  $5.40 assumes the same token counts as GPT, and a different figure changes the saving directly.
- **The speed limit matters.** Without it, the router sends everything to Gemma first at "$0" and
  reports 87.7% at $0.20. That's an artifact: Gemma takes about 105 s, has no paid tier we could
  price, and is often overloaded, so that saving wouldn't exist at real volume.
- **Differences are small.** One receipt is 1.4 points here.

**Checked for real through Redis and a worker:**

| Test | Result |
|---|---|
| 8 receipts, normal routing | GPT answered most; 2 flagged went to Gemini; one then passed validation, and the other was sent to review |
| Wrong GPT key, empty cache | HTTP 401 moved on to Gemini; on the next receipt the breaker skipped GPT, Gemini was out of quota, and Gemma answered |

One limit: results are stored under the task id, so submitting a receipt again returns the stored
result until the new run finishes. That's fine for repeats, but after a dead letter from a provider
outage, the caller could read the old result first.

```bash
python scripts/build_leaderboard.py      # no provider calls; re-run when results change
python scripts/simulate_routing.py       # no provider calls
python scripts/submit_receipts.py auto --limit 5
```

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
```
