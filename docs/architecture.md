# Architecture

How a receipt moves through the system: the task queue, router, review queue, API and the hosted demo.

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

- **No record of who corrected a receipt.** There's one owner login (see API below).
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

- **The owner's pages aren't hosted.** Only the public demo is (see Hosted demo below), so
  uploading and reviewing run on a local copy.
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
- **Deploys on merge.** Every merge to `main` redeploys it from `render.yaml`.

```bash
docker build -t receipt-iq-demo .
docker run --rm -p 8000:8000 receipt-iq-demo   # http://localhost:8000
```
