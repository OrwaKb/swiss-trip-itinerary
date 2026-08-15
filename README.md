# Switzerland trip itinerary — EN / HE / AR

A Streamlit app that presents one family trip itinerary in English, Hebrew and Arabic,
with a CHF / EUR / ILS currency toggle. Mobile-first: it is meant to be opened on a
phone in a train station.

Four tabs. **Before you go** is the pre-trip briefing — what breaks the trip, what the
plan assumes, what to book and in what order. **Day by day** is one expandable card per
day, with the weather-dependent Jungfraujoch day flagged as floating. **Costs** breaks
the money down by day, by ticket and by category. **Plan B** is the weather swaps, kept
on their own so they are quick to find on a bad morning.

`itinerary.json` is the single source of truth. Nothing is invented, re-costed or
re-planned in the app — every figure on screen traces back to that file.

## Files

| File | What it is |
|---|---|
| `app.py` | The whole app. Contains no user-visible copy. |
| `itinerary.json` | The trip. Source of truth for every fact and figure. |
| `translations.json` | Hebrew and Arabic renderings, plus UI chrome for all three languages. |
| `check.py` | Verification pass — run it after editing either JSON file. |
| `requirements.txt` | Pinned dependencies. |
| `.streamlit/config.toml` | Pins the light theme so the palette is the same on every device. |

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
streamlit run app.py
```

Opens on <http://localhost:8501>. Python 3.11+.

## Deploy to Streamlit Community Cloud

1. Push this folder to a GitHub repo, including `.streamlit/`.
2. At <https://share.streamlit.io> choose **New app**, pick the repo and branch, set
   **Main file path** to `app.py`, and deploy.
3. Nothing else to configure — no secrets, no API keys, no environment variables. The
   app makes no network calls at runtime; the only external request is the browser
   fetching the Hebrew and Arabic webfonts from Google Fonts, and there is a system
   fallback stack if that is blocked.

Share links carry the language and currency: `…/?lang=he&cur=ILS` opens in Hebrew with
shekels.

## Verification

```bash
python check.py
```

Hard failures (exit 1):

- the cost figures do not reconcile
- a string the app renders has no Hebrew or Arabic translation
- a UI key exists in one language but not another
- a Hebrew or Arabic string has a digit run outside a `U+2066…U+2069` isolate
- Eastern Arabic-Indic digits anywhere (prices must match Swiss tickets)
- a translations key points at nothing in `itinerary.json`

It also prints a review list of Latin tokens in the English source that do not reappear
in a translation, so a dropped proper noun is easy to spot. The current residue is all
sentence-initial common words (`No`, `Then`, `About`, `Old Town`, `THIS DAY FLOATS`).

The same reconciliation runs inside `app.py` on load, so the app refuses to start on
bad data rather than showing wrong totals. Tolerances: CHF 1 per day, CHF 2 on the
grand total.

## How the languages work

`app.py` contains no copy at all. Two lookups supply every string:

- `T("ui.day.total")` — app chrome, from `translations.json` → `ui` → *language*.
- `C("days.5.title", fallback)` — trip content. For `en` the fallback is used, which is
  the raw `itinerary.json` value; for `he`/`ar` it is the keyed override in
  `translations.json` → `content`. Keys are dotted paths into `itinerary.json`, so
  `check.py` can prove coverage.

To add a language: add its code to `meta.languages`, a full `ui` block, a `content`
block, a font stack in `FONT_STACK` and a line height in `LINE_HEIGHT`. Add it to
`meta.rtl` too if it is right-to-left. `check.py` then enforces completeness.

### Translation rules applied

- Proper nouns keep their Latin spelling in brackets after a transliteration —
  `יונגפראויוך (Jungfraujoch)`, `يونغفراويوخ (Jungfraujoch)`. Places, mountains, hotels,
  stations, roads (`A4`, `Brunig`), bus numbers (`141`) and shops (`Coop`) all follow
  this, so the app can be matched against a station sign or a ticket.
- Prices, currency codes, dates, times, distances, durations are never translated or
  reformatted.
- Western digits (0–9) in all three languages.

## RTL

Streamlit does not do right-to-left, so the app handles it:

- The stylesheet is re-emitted in full on every run with the direction written out
  explicitly, and each rule that differs by direction is also guarded on a marker
  element (`body:has(#tp-dir-rtl) …`) that only exists for the current language. A
  stale rule therefore cannot outlive a language switch.
- Layout mirrors, not just text: the sidebar moves to the right, tabs run
  right-to-left, table columns reverse, the parents/adults grid swaps, card chevrons
  and Streamlit's own sidebar chevrons flip, and the bars fill from the right.
- `direction: rtl` already reverses a flex row's main axis, so the flow stays
  `row` — adding `row-reverse` would cancel it out.
- Streamlit collapses the sidebar with `translateX(-300px)`, which is only off-screen
  when the sidebar is on the left. In RTL that is mirrored, or the collapsed sidebar
  slides across the content.
- Numbers, prices, times and Latin runs are wrapped in LRI/PDI isolates in
  `translations.json`. `guard_numbers()` in `app.py` isolates any digit run that was
  missed, and every figure the app formats itself is emitted inside
  `<span dir="ltr">`. `CHF 1,416` never renders reversed.
- Hebrew loads Heebo, Arabic loads Noto Sans Arabic, and Arabic gets a taller line
  height (1.9 against 1.5 for Latin).

## Chart

The cost-by-day and category charts are hand-built HTML, not a plotting library: one
hue, direct value labels, no legend for a single series, no axis (every value is
labelled), 14px bars with a 4px rounded data end. The track is a two-column grid so the
longest bar cannot squeeze out its own label, and both `justify-self` and the logical
border radii follow `direction`, so the bars mirror for free.
