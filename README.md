# Switzerland trip itinerary — EN / HE / AR

A Streamlit app that presents one family trip itinerary in English, Hebrew and Arabic,
with a CHF / EUR / ILS currency toggle. Mobile-first: it is meant to be opened on a
phone in a train station.

Five tabs. **Before you go** is the pre-trip briefing — what breaks the trip, what the
plan assumes, what to book and in what order. **Day by day** is one expandable card per
day, with the weather-dependent Jungfraujoch day flagged as floating. **Map** draws every
stop and every drive, walk, lift and boat between them. **Costs** breaks the money down
by day, by ticket and by category. **Plan B** is the weather swaps, kept on their own so
they are quick to find on a bad morning.

Prices are off by default. The plan is what people open this for, so the two big figures
stay out of the header, the day cards and Plan B until **Show prices** is switched on in
the sidebar — the Costs tab always has them, which makes looking at the money a choice
rather than the first thing you meet. The setting rides in the URL alongside language,
currency and ink tone, so a forwarded link arrives the way it was sent. Prices quoted
inside a tip stay put: a night surcharge is advice, not a running total.

During the trip the card for the current date is marked, badged **Today** and starts
open, so opening the app on a platform answers "what are we doing now" without a tap.
Outside 4–13 September nothing is marked.

`itinerary.json` is the single source of truth. Nothing is invented, re-costed or
re-planned in the app — every figure on screen traces back to that file.

## Files

| File | What it is |
|---|---|
| `app.py` | The whole app. Contains no user-visible copy. |
| `itinerary.json` | The trip. Source of truth for every fact and figure. |
| `translations.json` | Hebrew and Arabic renderings, plus UI chrome for all three languages. |
| `tripmap.py` | The map tab: the Leaflet document, and the GPX and KML exports. No copy here either. |
| `geo.json` | Where every stop is and how you get between them. Built, not hand-written. |
| `check.py` | Verification pass — run it after editing any of the JSON files. |
| `requirements.txt` | Pinned dependencies. |
| `.streamlit/config.toml` | Pins the light theme so the palette is the same on every device. |
| `static/leaflet/` | Leaflet 1.9.4, vendored. The map draws without reaching a CDN. |

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

Share links carry the language, currency and ink tone: `…/?lang=he&cur=ILS&ink=espresso`
opens in Hebrew with shekels in the warm tone.

## Ink tones

A sidebar picker for the text colour, with **black** as the default. The others are dark
tones with a cast rather than flat black — graphite (cool), espresso (warm), midnight
(blue). The page stays light in all four; the surface and page plane take only enough of
the same cast for the choice to read, because a cool ink on a warm page reads as a
mistake rather than a theme. Accent blue and the amber floating badge do not change.

`check.py` holds every tone to 7:1 on primary ink, 4.5:1 on secondary and 3:1 on muted,
against **both** its own surface and its own plane — so a new tone cannot be added
without passing. Add one by extending `INK_TONES` in `app.py` and adding a
`ui.ink.<name>` label in each language; the checks will tell you if either is missing.

## Verification

```bash
python check.py
```

Hard failures (exit 1):

- the cost figures do not reconcile
- a string the app renders has no Hebrew or Arabic translation
- a UI key exists in one language but not another
- `app.py` asks for a key nothing defines (parity alone misses a key absent from *every*
  language, which is what happens when a new widget is added)
- a Hebrew or Arabic string has a digit run outside a `U+2066…U+2069` isolate
- Eastern Arabic-Indic digits anywhere (prices must match Swiss tickets)
- a translations key points at nothing in `itinerary.json`
- an ink tone falls below its contrast floor on its own surface or plane
- the today marker lands on the wrong card, fires outside the trip dates, or
  expand-all misses a card
- a map coordinate falls outside Switzerland (which is what a swapped lat/lon looks
  like, and is otherwise invisible in a file of five-decimal numbers)
- a route's line starts or ends more than 250 m from the stop it claims to join —
  the signature of a route fetched between the wrong two places, which looks
  perfectly plausible until somebody drives it
- the routed kilometres disagree with `drive_km` in `itinerary.json` by more than 15 %
- a day of the trip has nothing pinned on it
- two route colours are too close to tell apart, or a route colour is illegible on
  the map paper, or two modes share a dash pattern
- the GPX or KML export does not parse, or has lost a waypoint
- `static/leaflet/` is missing, which would render the map tab as an empty box

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

## Notes on the days

Every day card has a drawer welded to its underside where the family can leave notes,
agree with each other's, and delete their own. You set your name once in the sidebar;
notes are signed with it, and that name is also what decides which notes you may remove.

Nothing is written into the itinerary. `itinerary.json` stays the single source of truth
for the plan; the notes live in their own store.

### Where the notes are kept

There are two backends and the app picks between them by itself.

**Local (the default).** A SQLite file at `.notes/notes.db`, which git ignores. Perfect
for running it on your own machine. On Streamlit Community Cloud it is close to useless
and the app says so on the page: the container has no disk it can keep, so the notes are
not shared with anybody else and are wiped whenever the app restarts or redeploys.

**Supabase (what you want once you share the link).** Free, and about five minutes:

1. Create a project at supabase.com.
2. Open the SQL editor and run:

   ```sql
   create table notes (
     id      uuid primary key,
     day     text not null,
     author  text not null,
     body    text not null,
     created timestamptz not null
   );
   create table likes (
     note_id uuid not null references notes(id) on delete cascade,
     who     text not null,
     primary key (note_id, who)
   );
   -- The app reads every note in one call and orders by created; it never
   -- filters by day, so this is the index that serves the query it makes.
   create index notes_created on notes (created);

   alter table notes enable row level security;
   alter table likes enable row level security;
   create policy "family reads"  on notes for select using (true);
   create policy "family writes" on notes for insert with check (true);
   create policy "family edits"  on notes for delete using (true);
   create policy "likes read"    on likes for select using (true);
   create policy "likes write"   on likes for insert with check (true);
   create policy "likes remove"  on likes for delete using (true);

   -- Supabase grants these by default on a new project, so this is usually a no-op.
   -- It is here because a missing grant and a missing policy both come back as a
   -- flat 401, and an hour spent rewriting policies that were already right is an
   -- hour wasted. Only the three verbs the app uses: it never issues an UPDATE.
   grant select, insert, delete on notes to anon;
   grant select, insert, delete on likes to anon;
   ```

3. In the Streamlit Community Cloud app menu, choose **Settings -> Secrets** and paste
   your project URL and its **anon** key (Project Settings -> API), never the service key:

   ```toml
   supabase_url = "https://xxxxxxxx.supabase.co"
   supabase_key = "eyJ..."
   ```

4. Prove it before trusting it to anybody:

   ```
   python tools/check_supabase.py
   ```

   It reads the same two values from `.streamlit/secrets.toml` (git-ignored, and it
   must stay that way — this repo is public) or from `SUPABASE_URL` and `SUPABASE_KEY`
   in the environment, then runs the real `SupabaseStore` against the real project:
   write a note, read it back with its timestamp intact, like it, like it twice
   without duplicating, unlike it, refuse a delete from the wrong author, delete it.
   Everything it writes is on the sentinel day `1970-01-01` and is removed again in a
   `finally` block. It never prints the key, and when something fails it says which of
   the URL, the key, the policies, the grants or the column types is the likely cause.

The app switches over on the next run and the "kept on this server alone" notice
disappears. Setting only one of the two secrets is treated as a mistake and reported
rather than silently falling back to storage nobody else can see.

Two things worth knowing. The policies above let anyone with the link read and write, so
the link is the only lock on the board — which is the right trade for five people, and
the wrong one if you post the URL publicly. And Supabase pauses a free project after a
week with no traffic; a single page view counts as traffic, and restoring it from the
dashboard does not lose data.

### What is actually tested

`python check.py` exercises both backends through the same set of assertions, so they
have to agree on ordering, likes, ownership and every refusal. The Supabase one runs
against a strict stub in `tools/stub_supabase.py` that speaks the same REST dialect,
because reaching the real service needs somebody's account. That proves the requests the
app sends; it does not prove Supabase accepts them. The first note you post after wiring
the secrets is the real test.

## The map

A fifth tab draws the whole trip: every stop, and every stretch you drive, walk, ride
or sail. It is a hand-written Leaflet document inside a Streamlit HTML component —
no `folium`, no `pydeck`, and no new Python dependency. Leaflet is vendored into
`static/leaflet/`, so apart from the map tiles nothing is fetched from another host.

The basemap is swisstopo's own national map (`wmts.geo.admin.ch`), which is free,
needs no key, and draws the hiking paths and their difficulty grades. The two dark
ink tones swap it for the grey edition and invert it; colour does not invert cleanly.
Route colours are tone roles like every other colour, and the four modes are told
apart by dash pattern as well as hue.

The map never mirrors — north stays up — but the popups, legend, day switcher and
stop list all take `dir="rtl"`, and every number is formatted in Python so it arrives
already inside its bidi isolate.

### Rebuilding geo.json

```bash
python tools/build_geo.py          # writes geo.json
python tools/build_geo.py --dry    # fetches and reports, writes nothing
```

Stop coordinates are seeded by hand in the tool, then snapped to the real
OpenStreetMap feature; the element id lands in `geo.json` as `source`, so every pin
can be traced back to something. Drives come from OSRM, lifts and mountain railways
are traced from OSM ways, and the whole lot is simplified to 10 m and rounded to five
decimals. Nothing is fetched while the app is running.

The public Overpass instance is often congested. The tool probes the mirrors once,
paces its calls and backs off on a refusal, so a slow run is normal and a failed one
is rare.

### Taking it offline

Alpine valleys lose signal, and the tiles need a connection. Two escape hatches:

- If the tiles fail, the map drops the background, says so, and falls back to the
  stop list, which still carries every coordinate and Maps link.
- GPX and KML downloads. **GPX is the one that matters** — Organic Maps and OsmAnd
  hold Switzerland offline and will draw every pin and path on it. KML is for Google
  My Maps on a laptop: Google Maps on a phone shows it under Your Places but will
  not navigate it.

## Photographs

Ten photographs, one for the header and one per day, all from Wikimedia Commons under
licences that allow reuse. They are cropped, resized and committed to `static/photos`,
so the app still makes no network call of its own while it is running. Author, licence
and a link to the file page are listed under **Before you go**, which is what the
share-alike ones require.

To change the set:

```bash
python tools/fetch_candidates.py <scratch-dir>   # gather candidates + thumbnails
# look at the thumbnails, write <scratch-dir>/picks.json
python tools/build_images.py <scratch-dir>       # crop, resize, rewrite images.json
```

`picks.json` maps each slot to a candidate id and a `bias` between 0 and 1 saying where
the crop window sits vertically. That is not a detail: several of these photos put the
thing you came for near an edge, and a centred crop decapitates the Lucerne water tower
and grazes the dome on the Sphinx observatory.
