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

**All 98 validation receipts are done.** The free daily quota ranged from 9 to 21 requests, so the
run took several days, resuming from the cache each time. Results are in the comparison below.

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

### All five on all 98 validation receipts

| Metric | Local, prompt v2 | Gemini 3.5 Flash | Gemma 4 31B | Azure | GPT-5 mini |
|---|---|---|---|---|---|
| Total correct | 35.7% | 99.0% | 89.8% | 91.8% | 92.9% |
| Line items: F1 | 29.0% | 90.7% | 92.7% | 89.2% | 84.4% |
| Fully correct receipts | 3 of 98 | 82 of 98 | 79 of 98 | 60 of 98 | 71 of 98 |
| Failed | 1 | 1 | 10 | 5 | 3 |
| Validator catch rate | 42.1% | 25.0% | 63.2% | 71.1% | 37.0% |
| Median latency | 23.0 s | 10.4 s | 110.5 s | 9.0 s | 8.3 s |

These are validation scores, used for tuning. The final scores are in the next section.

What the numbers show:

- **Reading the image beats OCR + a small model** by a wide margin: no prompt fixes text that OCR
  never read.
- **Gemini is the most accurate on whole receipts; GPT and Azure are the fastest.** Gemma is close
  to Gemini, with the best line-item F1, but about 10× slower, and most of its failures come from
  the free service, not from reading the receipt.
- **Partial runs can mislead.** On the first 73 receipts GPT scored 78.1%; on all 98 it scored 72.4%,
  which changed the router's best setting (see below).
- **The same mistake from Gemini, Gemma and GPT:** an item with an empty name. A next prompt version
  should require the printed name on every item (kept for later so these runs stay comparable).
- **A low catch rate isn't always a weak validator.** When checked on the first 57 receipts, none of
  the 6 wrong Gemini receipts the validator missed had a wrong amount: they differed in item names,
  a blank subtotal or extra 0-priced lines, which arithmetic can't see. Azure's "8.000" bug is the opposite warning: wrong amounts that are
  consistent with each other pass too.

### Final scores: test split

Run once, after the prompt and the router target were locked on validation. 95 of the 100 test
receipts can be scored; the other 5 labels have no printed total.

| Metric | Local, prompt v2 | Gemini 3.5 Flash | Gemma 4 31B | Azure | GPT-5 mini |
|---|---|---|---|---|---|
| Total correct | 30.5% | 100.0% | 90.5% | 88.4% | 93.7% |
| Line items: F1 | 26.8% | 96.1% | 88.5% | 90.9% | 94.2% |
| Fully correct receipts | 4 of 95 | **81 of 95** | 76 of 95 | 55 of 95 | 71 of 95 |
| Failed | 2 | 0 | 9 | 4 | 0 |
| Validator catch rate | 41.8% | 14.3% | 57.9% | 72.5% | 41.7% |
| Median latency | 21.8 s | 9.3 s | 70.6 s | 9.0 s | 9.9 s |

- **Same order as validation**, and each provider within a few points of its validation score
  (Gemini 83.7% → 85.3%, Gemma 80.6% → 80.0%, GPT 72.4% → 74.7%), so the prompt wasn't fitted to
  the validation receipts.
- **Gemini's low catch rate** is the blind spot above: its mistakes are mostly names and blank
  fields, which arithmetic can't see.
- **One caveat:** the validator's rules were first fixed against the test labels (see "Checking the
  checker"), so its catch rates here may read slightly better than on new receipts.

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
| Postgres unreachable when saving | Retries after 1 min, then fails visibly if it never returns | The answer is cached, so the provider isn't paid again |

- **Safe to run twice.** Provider calls go through the disk cache and results are saved under the
  image hash, so a repeat costs nothing. Each submission has its own task id, so `.get()` waits
  for that run instead of returning an earlier result.
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
docker compose up -d                                              # Redis and Postgres, this machine only
alembic upgrade head                                              # create or update the tables
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

- **Policy:** the cheapest provider that reaches 70% fully correct and answers within 60 s. Those
  that miss either are a last resort, most accurate first.
- **Circuit breaker:** after 3 failures in a row a provider is skipped for 5 minutes, then gets one
  trial call. A wrong key opens it immediately.
- **Sensitive documents:** `local_only=True` never uses anything that leaves the machine.
- **Routing log:** every result lists each provider tried, what happened and why.

**What routing is worth, replayed on all 98 validation receipts**
(`scripts/simulate_routing.py`, no provider calls):

| Policy | Fully correct | Line-item F1 | $ per 1,000 | Sent to review | Mean time |
|---|---|---|---|---|---|
| Always Gemini (most accurate) | 83.7% | 90.7% | 5.34 | 4.1% | 16.4 s |
| Always GPT | 72.4% | 84.4% | 1.08 | 10.2% | 8.4 s |
| Always Azure | 61.2% | 89.2% | 9.49 | 27.6% | 8.3 s |
| Router, 75% target, second opinion | 84.7% | 90.5% | 5.41 | 2.0% | 20.0 s |
| Router, 70% target, 1 answer | 73.5% | 87.1% | 1.28 | 8.2% | 9.0 s |
| **Router, 70% target, second opinion (default)** | 78.6% | 88.0% | 1.66 | 2.0% | 12.4 s |

The default router costs **69% less** than always using Gemini and sends half as many receipts to a
person. The price is real: 5 of the 98 receipts are no longer fully correct, 5.1 points.

**Why 70% and not 75%.** On the first 73 receipts GPT scored 78.1%, so a 75% target looked like the
best of both: 69% cheaper for only 2 receipts. The full run put GPT at 72.4%, below that target. The
router then tries Gemini first, which is about one receipt more accurate than always using Gemini
but saves nothing. So 75% was a setting tuned on a partial run, and it didn't survive the full one.

Read those numbers with care:

- **Tuned on these receipts.** The router's accuracy figures and this target come from these same
  receipts, which flatters it. The test split below is the honest number.
- **Costs are list prices from third-party trackers.** GPT's token counts are measured; Gemini's
  $5.40 assumes the same token counts as GPT, and a different figure changes the saving directly.
- **The speed limit matters.** Without it, the router sends everything to Gemma first at "$0" and
  reports 87.8% at $0.20. That's an artifact: Gemma takes about 110 s, has no paid tier we could
  price, and is often overloaded, so that saving wouldn't exist at real volume.
- **Differences are small.** One receipt is about 1 point here.

**Final check: the same router on the 95 test receipts.** Its accuracy figures still come from
validation (`leaderboard.json`), so the test receipts played no part in its choices:

| Policy | Fully correct | Line-item F1 | $ per 1,000 | Sent to review | Mean time |
|---|---|---|---|---|---|
| Always Gemini (most accurate) | 85.3% | 96.1% | 5.40 | 3.2% | 19.1 s |
| Always GPT | 74.7% | 94.2% | 1.11 | 12.6% | 10.7 s |
| Always Azure | 57.9% | 90.9% | 9.58 | 30.5% | 8.9 s |
| Router, 70% target, 1 answer | 74.7% | 94.2% | 1.11 | 12.6% | 10.7 s |
| **Router, 70% target, second opinion (default)** | 82.1% | 95.3% | 1.79 | 3.2% | 14.0 s |

The validation result holds on unseen receipts: the default router costs **67% less** than always
using Gemini, sends the same share to review, and loses 3 of 95 receipts (3.2 points), against 5 of
98 on validation. The second opinion is what earns that: without it, 1 receipt in 8 goes to review.

```bash
python scripts/simulate_routing.py data/cord/test.parquet   # no provider calls
```

**Checked for real through Redis and a worker:**

| Test | Result |
|---|---|
| 8 receipts, normal routing | GPT answered most; 2 flagged went to Gemini; one then passed validation, and the other was sent to review |
| Wrong GPT key, empty cache | HTTP 401 moved on to Gemini; on the next receipt the breaker skipped GPT, Gemini was out of quota, and Gemma answered |

```bash
python scripts/build_leaderboard.py      # no provider calls; re-run when results change
python scripts/simulate_routing.py       # no provider calls
python scripts/submit_receipts.py auto --limit 5
```

## Review queue (`review.py`, `db.py`)

Each finished receipt is saved to Postgres, one row per image hash. Anything flagged or
dead-lettered waits there for a person.

```
worker result ──► Postgres ──► passed
                          ├──► needs_review ─┐
                          └──► dead_letter ──┴──► correct ──► corrected ──► labeled data
                                             └──► dismiss ──► dismissed
```

- **The reason is stored with its location**, such as `items[2]` or `total`, so a screen can
  highlight the wrong line.
- **Corrections go through the same validator.** One that still doesn't add up is refused unless
  the person confirms it, because some real receipts misprint. Confirmed ones are marked.
- **The model's answer is kept beside the correction**, so you can see what it got wrong.
- **Passed receipts can be corrected too.** The validator can't see wrong amounts that add up.
- **A person's decision stands.** A later run never replaces a correction or dismissal.
- **A failure doesn't erase an answer**, while a success does replace an earlier dead letter.
- **Two people can't both correct one receipt.** The row is locked, and the second is refused.
- **Corrections stay out of the held-out split.** `export` leaves out any receipt in the test split,
  because a test receipt whose answer has been seen and fixed is no longer a test.

Tests run against a real Postgres, built by running the migrations, and one test fails if the
tables in `db.py` drift from them.

**Checked for real through Redis, Postgres and a worker, on cached receipts with a fake provider
address, so no provider could be called:**

| Test | Result |
|---|---|
| 20 receipts through GPT | 18 passed, 2 saved to the queue with their reason and location |
| Dead letter, then the same image through another provider | Queue entry cleared, status `passed`, failure details removed |
| A failing re-run of a receipt that already has an answer | Answer kept |
| Correct with the model's wrong answer | Refused; receipt stayed in the queue |
| Correct with the hand-made label, then re-run the receipt | `corrected`, and the re-run left it alone |
| Stop Postgres while a task finishes | Task retried, finished once Postgres was back, row saved |
| Export with the corrected receipt's split as the held-out one | 0 rows written |

Limits for now:

- **No record of who corrected a receipt.** That needs the login, which comes with the API.
- **Corrections are exported, not scored yet.** The leaderboard doesn't read them so far.
- **A newer run replaces the model's answer on a waiting receipt**, so the row holds the latest
  run, not every run.

```bash
python scripts/review_queue.py list
python scripts/review_queue.py show DIGEST
python scripts/review_queue.py correct DIGEST fixed.json
python scripts/review_queue.py dismiss DIGEST "not a receipt"
python scripts/review_queue.py export
```

## API (`api.py`, `security.py`, `settings.py`)

What the screens call. Everything is under `/api` except the health checks, so the screens can use
addresses like `/leaderboard`. Interactive docs are at `/api/docs`.

```
upload ──► size, login and rate checks ──► is it really an image? ──► Postgres: processing
                                                                               │
                     screen polls GET /documents/{digest} ◄── worker ◄── Redis ┘
```

| Endpoint | What it does | Access |
|---|---|---|
| `POST /api/documents` | Upload a receipt and return its hash straight away (202; 200 if already known) | Login |
| `GET /api/documents/{digest}` | Status, answer, issues and routing log | Login |
| `GET /api/documents/{digest}/image` | The receipt image | Login |
| `GET /api/review-queue` | Waiting receipts, oldest first, with cursor pagination | Login |
| `GET /api/routing-log` | Which provider handled each receipt and why, newest first | Login |
| `POST /api/documents/{digest}/correction` | Save a person's fix | Login |
| `POST /api/documents/{digest}/dismissal` | Take a receipt out of the queue | Login |
| `POST /api/login`, `POST /api/logout`, `GET /api/session` | Owner login | Public, stricter limit on login |
| `GET /api/leaderboard` | Accuracy, cost and speed per provider, and the router's order | Public |
| `GET /api/demo/samples`, `.../{id}/image`, `POST /api/demo/check` | Saved sample answers and the validator; no provider is called | Public |
| `GET /healthz`, `GET /readyz` | Running, and able to reach Postgres and Redis | Public, not rate limited |

**Protecting the quota:**

- **Login comes before the upload is read.** FastAPI reads a declared `File` before it runs
  dependencies, so the upload route reads the form itself, after the login and limits have passed.
- **Sessions live in Redis.** The cookie holds a random token and Redis only its hash, so logging
  out ends the session for real. The cookie is `HttpOnly`, `Secure` and `SameSite=Strict`.
- **Rate limits per IP**, also in Redis: 120 requests a minute in general, 10 uploads, 5 logins. If
  Redis is down, requests get a 503 rather than running with no limits.
- **Uploads are checked for what they really are.** Only JPEG and PNG, up to 10 MB and 100
  megapixels, and Pillow has to open them, so a renamed file is refused. The size limit also
  covers requests that don't declare a size.

**Behaviour worth knowing:**

- **Writes are safe to repeat.** The same image is one document and one provider call. A second
  correction or dismissal gets a 409 rather than being applied twice.
- **`processing` and `failed` are statuses.** `failed` means a setup problem or a bug, so it goes
  to whoever runs this and not the review queue. Uploading again retries it, as it does a dead
  letter or a `processing` row older than 2 hours, whose task must have been lost.
- **One error format:** `{"error": {"code", "message", "details"}}`, with no stack traces, and
  validation errors never echo what was submitted, so a password can't come back in one.
- **Settings are checked at startup.** There are no default addresses: `DATABASE_URL`, `REDIS_URL`
  and, for the API, `OWNER_PASSWORD_HASH` must be set or it stops with a clear error.

**Why tests use Compose and CI services and not Testcontainers:** both give a real Postgres and
Redis, which is what matters, and these are already running for local work. Testcontainers would
add a dependency without adding coverage.

**Checked for real over HTTP, with uvicorn, a worker, Redis and Postgres, on cached receipts with
a fake GPT address and fake keys:**

| Test | Result |
|---|---|
| Upload with no worker running | 202 and `processing`; once a worker started, it finished as `passed` |
| Same image again while it waited | 200, nothing queued |
| Upload or read the queue with no login | 401 |
| Text file named `.png` | 415 |
| 11 MB upload; 12 MB stream with no declared size | Both 413 |
| 2 flagged receipts | Second opinion from Gemini: one passed, one went to the queue with its location |
| Correct it: one that doesn't add up, one that does, then again | 422, 200, then 409 |
| Task whose image is missing | Row marked `failed` with the reason |
| Stop Postgres | Reads got 503, `/readyz` 503, `/healthz` 200; all fine once it was back |
| Stop Redis | Requests got 503, `/readyz` 503; the session survived the restart |
| Log out | Queue back to 401 |
| 7 wrong passwords in a row | 401 five times, then 429 |
| Start with no `OWNER_PASSWORD_HASH` | Stopped at startup naming the setting |

Limits for now:

- **Not public yet.** HTTPS comes with hosting, and `Secure` cookies need it.
- **One owner account**, so there's no record of who corrected what.
- **`/readyz` doesn't check for a worker**, so uploads can be accepted and wait with none running.
- **Limits are per IP in fixed windows.** A burst across a minute's edge can reach twice the limit.
- **Images only.** PDFs and multi-page documents aren't supported yet.
- **Graceful shutdown isn't tested.** Uvicorn finishes in-flight requests on SIGTERM on Linux, and
  this was only run on Windows.

```bash
python scripts/hash_password.py           # asks for a password, prints the hash for OWNER_PASSWORD_HASH
uvicorn receipt_iq.api:create_app --factory
```

For plain-http local testing, set `COOKIE_SECURE=false`.

## Screens (`frontend/`)

React, Vite, TypeScript, Tailwind and TanStack Query, with Recharts for the chart. The design
decisions are in [`DESIGN.md`](DESIGN.md), and share the crypto dashboard's colors and type.

| Screen | What it answers | Access |
|---|---|---|
| Demo (the front page) | What does this do? Try saved answers on sample receipts, fix one, split a bill | Public |
| Upload | Send a photo, and optionally keep it on this machine | Login |
| Receipt | What did the model read, beside the image, and what's wrong with it? Fix or dismiss it | Login |
| Review queue | What's waiting for a person, and why? | Login |
| Model leaderboard | Which provider should I trust, what does it cost, and which does the router try first? | Public |
| Routing log | Which provider handled this receipt, what did the others do, and why? | Login |

- **FastAPI serves the built screens.** One address means the `SameSite=Strict` login cookie works
  with no cross-origin setup. Page addresses get the app; a missing file or API address stays a 404.
- **The leaderboard says how far to trust it.** It's labeled provisional, an estimated cost says so
  beside the figure, and the chart's numbers are in the table too.
- **The chart library loads only on the page that uses it.** It's more than half the download.
- **A receipt updates by itself.** It polls every 2 s while processing, then stops.
- **The problem appears where it is.** A saved issue's location marks the wrong line or amount,
  with the reason beside it in words.
- **Money stays text in the form,** so an amount can't be rounded on its way to the server.

**Splitting a bill (`split.ts`):** an item is shared equally among the people who had it, after its
own discount. The whole receipt total is then shared in proportion to what each person's items cost.
That spreads tax, service, tip and discounts fairly, and also handles prices that already include
tax, without needing to know which kind of receipt it is. It uses whole-number arithmetic, with
leftover units going to the largest remainders, so the shares always add up to the printed total.
A test checks that across 300 combinations. Splits are saved in the browser only.

**Demo mode:** visitors can try everything without logging in, and it can't spend quota. The samples
in `demo/` are real saved results, replayed by `scripts/build_demo.py` with no provider calls, and
only from the validation split, because publishing test receipts would undermine the final score.
Fixing a flagged sample runs the real validator through `POST /api/demo/check`, which saves nothing.

The sample images are from [CORD](https://github.com/clovaai/cord) (Park et al., 2019) under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), resized and recompressed. See
[`demo/README.md`](demo/README.md).

**Tested:**

- 55 Playwright tests at phone and desktop widths with the API mocked: every empty, loading and
  error state, worst-case long text, sorting, uploading, correcting, dismissing, splitting, demo
  mode, login, logout, keyboard entry and both themes. Screenshots are kept as a CI artifact.
- A logged-out visitor in real Chromium at both widths, with no worker running: samples opened,
  shares added up to the printed total, the real validator refused a bad fix, the only POST was to
  the demo check, the owner's queue stayed locked, and there were no console errors.
- Real Chromium against the real server, Redis, Postgres and a worker, on cached receipts with fake
  provider addresses:

| Test | Result |
|---|---|
| Upload with no worker, then start one | Showed "Reading this receipt", then updated by itself to `needs_review` with no reload |
| Open it from the queue | The image loaded, with the reason beside the flagged amount |
| Correct with a total that doesn't add up | Refused by the real validator; "Save anyway" stored it as confirmed, with the model's answer kept |
| Upload the same image again | The correction stood |
| A text file renamed `.png` | Refused by the server |
| Phone width, real receipt | No sideways scrolling |
| The whole run | No console or Content-Security-Policy errors |

Limits for now:

- **Splits are per browser.** They can't be shared by link, and aren't kept on the server.
- **Sample receipts are from Indonesia,** so amounts are in rupiah with no currency symbol.
- **Hovering an item doesn't highlight it on the image.** Providers don't return positions, and
  asking would mean a new prompt version and re-spending quota. Zoom covers it for now.
- **Correcting is one person, one pass.** A saved correction can't be edited afterwards.
- **Fonts come from Google Fonts**, as on the crypto dashboard, rather than being self-hosted.
- **API types are written by hand** from the response models, so a change has to be made in both.
- **No Lighthouse score yet.** The plan's accessibility target of 90+ still needs measuring.

```bash
cd frontend
npm ci
npm run dev        # with the API running, proxies /api to it
npm test           # unit tests
npm run build      # FastAPI serves this from then on
npm run test:e2e   # needs the build
```

## Hosted demo (`web.py`, `Dockerfile`, `render.yaml`)

The public copy runs only the pages anyone may see: the demo, its validator check and the
leaderboard. It has no login, database, Redis or provider keys, so no visitor can spend a quota,
and there's nothing secret to configure.

```
browser ──HTTPS──► Render proxy ──► uvicorn receipt_iq.web:create_demo_app
                                      ├─ /api/leaderboard, /api/demo/*   (rate limited in memory)
                                      ├─ /healthz                         (Render's health check)
                                      └─ the built screens
```

- **One codebase, two apps.** `web.py` holds the public pages and `api.py` adds the owner's
  pages on top, so the demo can't drift from what runs locally. `web.py` never imports the task
  queue, and a test starts it with no settings at all to prove it.
- **Rate limit without Redis:** the same one-minute windows, counted in memory. Fine for one
  process; the counts reset on a restart, which costs nothing when there's no quota behind it.
- **No login on the public copy.** `/api/session` answers `demo_only`, so the screens explain
  that uploading and reviewing run on the owner's copy instead of showing a login that can't work.
- **Free plan:** it sleeps after 15 idle minutes, so the first visit after that takes about a
  minute. Every merge to `main` redeploys it.

```bash
docker build -t receipt-iq-demo .
docker run --rm -p 8000:8000 receipt-iq-demo   # http://localhost:8000
```

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env   # local addresses, no secrets
docker compose up -d     # tests need Postgres and Redis
pytest
```
