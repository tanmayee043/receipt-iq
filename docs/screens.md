# Screens

The React app: what each screen answers, how bills are split, and how it was tested.

## Screens (`frontend/`)

React, Vite, TypeScript, Tailwind and TanStack Query, with Recharts for the chart. The design
decisions are in [`DESIGN.md`](../DESIGN.md), and share the crypto dashboard's colors and type.

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
[`demo/README.md`](../demo/README.md).

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
