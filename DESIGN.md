# Design system: Receipt IQ

Shares the colors and type of the crypto surveillance dashboard and the portfolio site (adapted from the Kraken reference in `design-references/awesome-design-md/design-md/kraken`), so the projects read as one body of work. Follows the operating system's light or dark setting, with a manual toggle.

## Who it's for

- **An operations lead** deciding which provider to trust, checking why a receipt went where it did, and clearing the review queue. Usually at a laptop, mid-task.
- **Someone splitting a bill** on a phone, at a table, with a receipt in hand.
- **A hiring reviewer** with a minute to spare, who should be able to tell what the numbers mean and how far to trust them.

## Principles

- **Operate, don't decorate.** Every element answers a question the person has right now. Density is fine.
- **Purple means action or state.** It marks primary actions, the current page, focus and the providers the router will use. Never decoration.
- **Never color alone.** A status always carries an icon and a word. On the chart, providers meeting the policy are filled and the rest hollow.
- **Say how far to trust a number.** Validation scores are labeled provisional, and estimated costs say so next to the figure.
- **Text wears text colors.** Values and labels stay in `--text` and `--text-2`, even beside a colored mark.

## Color tokens

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#ffffff` | `#101114` | page |
| `--surface` | `#f6f6f9` | `#16171c` | second neutral layer: table header, notes, panels |
| `--raised` | `#ffffff` | `#1d1e25` | inputs, hovered rows |
| `--border` | `#dedee5` | `#2a2c36` | dividers |
| `--text` | `#101114` | `#ececf1` | primary text |
| `--text-2` | `#5c5f75` | `#9a9db0` | secondary text (at least 4.5:1 on `--bg` and `--surface`) |
| `--accent` | `#7132f5` | `#9b7bff` | actions, current page, focus, links |
| `--accent-strong` | `#5741d8` | `#b39cff` | hover and pressed |
| `--accent-subtle` | `rgba(133,91,251,0.14)` | `rgba(155,123,255,0.18)` | selected backgrounds |
| `--on-accent` | `#ffffff` | `#101114` | text on a filled accent button |
| `--up` | `#0f7a4a` | `#3ccf8e` | passed |
| `--down` | `#c4283d` | `#ff6b7d` | dead letter, failed, form errors |
| `--warn` | `#9a5a00` | `#f2b14a` | needs review, provisional |

## Type

- **IBM Plex Sans** for all UI text: 13 px labels, 14 px body, 16 px section titles, 20 px page title. Weights 400, 500 and 600.
- **IBM Plex Mono** only for values compared or scanned (scores, costs, seconds, hashes), with tabular numbers.

## Shape and depth

- Radius: 12 px buttons, inputs and panels (rounded, never pills), 8 px badges.
- Borders do most of the work. No shadows on panels.

## Layout

- Top bar: name, page links, theme toggle, login. At phone width the links move to their own row.
- Content is at most 1120 px wide, with a 16 px gutter on phones.
- Wide tables scroll inside their own container, with the first column pinned. The page itself never scrolls sideways.

## Charts

- Every chart has a table with the same numbers on the same page.
- One hue plus gray: filled accent for providers that meet the policy, hollow gray for the rest, labeled directly.
- Hairline solid gridlines. No dual axes.

## Motion

100 to 200 ms ease-out for state changes only (hover, row expanding). No page-load choreography, and nothing moves when `prefers-reduced-motion` is set.

## States

Every data region has a skeleton while loading, an empty state that says what would make data appear, and an error state that names the problem and offers a retry. A refetch keeps the previous data on screen.
