"""Verification pass for the itinerary app.

Run:  python check.py

Hard failures (exit 1)
  - the cost figures do not reconcile
  - a string the app renders has no he/ar translation (it would fall back to English)
  - a UI key exists in one language but not another
  - a he/ar string contains a digit run that is not inside a U+2066…U+2069 isolate
  - a translations key points at nothing in itinerary.json

Review output (does not fail)
  - Latin proper nouns in the English source that do not reappear in he/ar
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import store as notes_store  # noqa: E402
import tripmap  # noqa: E402
from app import INK_TONES, guard_numbers, reconcile, stylesheet  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import build_geo  # noqa: E402  (its haversine, so the distance maths cannot drift)

BASE = Path(__file__).parent
DATA = json.loads((BASE / "itinerary.json").read_text(encoding="utf-8"))
TRANS = json.loads((BASE / "translations.json").read_text(encoding="utf-8"))
GEO = json.loads((BASE / "geo.json").read_text(encoding="utf-8"))

LRI, PDI = "⁦", "⁩"
RTL_LANGS = TRANS["meta"]["rtl"]

failures: list[str] = []
review: list[str] = []


def rendered_paths() -> dict[str, str]:
    """Every content path the app renders, mapped to its English source string."""
    paths: dict[str, str] = {"trip.title": DATA["trip"]["title"]}
    for i, s in enumerate(DATA["trip"]["assumptions"]):
        paths[f"trip.assumptions.{i}"] = s
    for i, item in enumerate(DATA["trip_wide_costs"]):
        paths[f"trip_wide_costs.{i}.item"] = item["item"]
        if item.get("note"):
            paths[f"trip_wide_costs.{i}.note"] = item["note"]
    for i, day in enumerate(DATA["days"]):
        for field in ("dow", "leg", "title", "sleep", "movement", "parents", "adults"):
            if day.get(field):
                paths[f"days.{i}.{field}"] = day[field]
        for j, item in enumerate(day["per_person"]):
            paths[f"days.{i}.per_person.{j}.item"] = item["item"]
        for j, item in enumerate(day["group"]):
            paths[f"days.{i}.group.{j}.item"] = item["item"]
        for k, tip in enumerate(day["tips"]):
            paths[f"days.{i}.tips.{k}"] = tip
    for i, swap in enumerate(DATA["weather_swaps"]):
        paths[f"weather_swaps.{i}.instead_of"] = swap["instead_of"]
        paths[f"weather_swaps.{i}.do"] = swap["do"]
    for i, step in enumerate(DATA["booking_order"]):
        paths[f"booking_order.{i}"] = step
    paths["totals.note_vs_six"] = DATA["totals"]["note_vs_six"]
    # The map's own prose. geo.json is a source of truth like itinerary.json, so
    # its strings answer to the same coverage rule.
    for slug, stop in GEO["stops"].items():
        paths[f"geo.stops.{slug}.name"] = stop["name"]
        paths[f"geo.stops.{slug}.what"] = stop["what"]
    for i, leg in enumerate(GEO["legs"]):
        paths[f"geo.legs.{i}.label"] = leg["label"]
    return paths


# --- 1. cost reconciliation -------------------------------------------------
try:
    result = reconcile(DATA)
    print(
        f"reconcile   OK   days {result['days_stated']:,.2f} "
        f"+ trip-wide {result['trip_wide']:,.2f} "
        f"= {result['days_stated'] + result['trip_wide']:,.2f} "
        f"vs grand {result['grand']:,.2f} (drift {result['drift']:+.2f}); "
        f"from components {result['days_computed'] + result['trip_wide']:,.2f} "
        f"(drift {result['days_computed'] + result['trip_wide'] - result['grand']:+.2f})"
    )
except AssertionError as exc:
    failures.append(f"reconcile: {exc}")

# --- 2. content coverage ----------------------------------------------------
paths = rendered_paths()
for lang in RTL_LANGS:
    table = TRANS["content"][lang]
    missing = [p for p in paths if p not in table]
    orphan = [k for k in table if k not in paths]
    if missing:
        failures.append(f"{lang}: {len(missing)} untranslated path(s): {missing}")
    if orphan:
        failures.append(f"{lang}: {len(orphan)} key(s) match nothing in itinerary.json: {orphan}")
    identical = [p for p in paths if table.get(p) == paths[p]]
    if identical:
        failures.append(f"{lang}: left in English: {identical}")
print(f"content     {len(paths)} paths rendered, "
      + ", ".join(f"{lang} {len(TRANS['content'][lang])}" for lang in RTL_LANGS))

# --- 3. UI key parity -------------------------------------------------------
ui = TRANS["ui"]
base_keys = set(ui[TRANS["meta"]["default"]])
for lang, table in ui.items():
    gap = base_keys - set(table)
    extra = set(table) - base_keys
    if gap:
        failures.append(f"ui/{lang}: missing {sorted(gap)}")
    if extra:
        failures.append(f"ui/{lang}: unknown {sorted(extra)}")
print(f"ui keys     {len(base_keys)} per language, {len(ui)} languages")

# --- 3b. every key app.py asks for actually exists ---------------------------
# Key parity alone cannot catch a key that is missing from every language at once,
# which is exactly what happens when a new widget is added.
APP_SRC = ((BASE / "app.py").read_text(encoding="utf-8")
           + (BASE / "tripmap.py").read_text(encoding="utf-8"))
asked = set(re.findall(r'T\(\s*[\'"]([a-z0-9_.]+)[\'"]', APP_SRC))
# f-string keys built from a loop variable, e.g. T(f"ui.totals.{k}")
asked |= {f"ui.totals.{k}" for k in TRANS["meta"]["total_keys"]}
asked |= {f"ui.ink.{tone}" for tone in INK_TONES}
# tripmap names these from the mode of each leg, so no literal appears next to a T(
asked |= {f"ui.map.mode.{mode}" for mode in tripmap.MODES}
# store.py names its own message keys and app.py renders them as T(exc.key), so the
# literal never appears next to a T( for the scan above to find.
for _src in ("store.py", "app.py"):
    asked |= set(re.findall(r'StoreError\(\s*[\'"]([a-z0-9_.]+)[\'"]',
                            (BASE / _src).read_text(encoding="utf-8")))
# note_count_key() picks one of these by count
asked |= {f"ui.notes.count_{suffix}" for suffix in ("zero", "one", "many", "many11")}
missing_keys = sorted(asked - set(TRANS["ui"][TRANS["meta"]["default"]]))
if missing_keys:
    failures.append(f"app.py asks for keys nothing defines: {missing_keys}")
unused = sorted(set(TRANS["ui"][TRANS["meta"]["default"]]) - asked
                - {f"lang.{code}" for code in TRANS["meta"]["languages"]})
if unused:
    review.append(f"ui keys defined but never rendered: {unused}")
print(f"key usage   {len(asked)} keys requested by app.py, all defined")

# --- 4. bidi isolation of numbers in RTL strings ----------------------------
DIGIT = re.compile(r"\d")
EASTERN = re.compile(r"[٠-٩۰-۹]")


def unisolated_digits(text: str) -> bool:
    depth, exposed = 0, False
    for ch in text:
        if ch == LRI:
            depth += 1
        elif ch == PDI:
            depth = max(0, depth - 1)
        elif depth == 0 and DIGIT.match(ch):
            exposed = True
    return exposed


bare = 0
for lang in RTL_LANGS:
    for key, value in list(TRANS["ui"][lang].items()) + list(TRANS["content"][lang].items()):
        if EASTERN.search(value):
            failures.append(f"{lang}/{key}: Eastern Arabic-Indic digits, must be 0-9")
        source = value.replace("{", "").replace("}", "")
        if unisolated_digits(source):
            bare += 1
            if unisolated_digits(guard_numbers(source)):
                failures.append(f"{lang}/{key}: digits still unisolated after guard: {value}")
print(f"bidi        all he/ar digit runs isolated "
      f"({bare} caught at runtime by guard_numbers, rest authored with LRI/PDI)")

# --- 5. proper nouns carried through ---------------------------------------
# Capitalised words that are not names: sentence openers, plain nouns, month names
# (which do get translated). Everything left over should be a place, an operator or a
# product, and must survive into he/ar. The list is only worth keeping if it stays
# exhaustive — a noisy review is one a dropped station name can hide in.
SENTENCE_START = {
    "Fares", "August", "September", "May", "November", "Parking", "After", "An",
    "Then", "Follow", "Bought", "Ask", "Ninety", "Plateau", "About", "Move",
    "Anyone", "Last", "Runs", "Fifty", "Tightest", "No", "Level", "Centre", "Town",
    "Old", "Sundays", "Fallback", "Train", "Check", "THIS", "DAY", "FLOATS", "NOT",
    "Most", "Leave", "Lakeside", "Summit", "Sphinx", "Ice", "Vignette",
    "Straight", "Boat", "Same", "Cable", "Walk", "Drive", "Summit", "Stop", "Get",
    "Full", "Every", "Van", "Rental", "Accommodation", "Flights", "At", "Buy",
    "Bring", "Rain", "Take", "There", "The", "Avoid", "Restaurants", "See", "Do",
    "Below", "Book", "Work", "Refuel", "Fill", "Terminal", "Land", "Collect",
    "Over", "Early", "Self", "Food", "Fuel", "Swiss", "Nothing", "Confirm", "Five",
    "Both", "Three", "Return", "Panorama", "Royal", "Sphinx", "Instead", "Any",
    "Swap", "Ten", "Vintage", "Dropping", "Hotel", "Rooms", "Big", "Picnic",
    "Visitor", "Second", "Mandatory", "Eiger", "Aare", "Boat", "Bus", "Jungfraujoch",
    "Schilthorn", "Mount", "Männlichen", "Pfingstegg", "Rigi", "Grindelwald",
    "Harder", "Schynige", "Trümmelbach", "Weggis", "Cliff", "Adventure", "Skip",
    "First", "Below", "Coats", "Paved", "Kanzeli", "It", "Their", "Allow", "Park",
    "Descend", "Grindelwald", "Wide", "Mürren", "Sunday", "Monday", "Tuesday",
    "Wednesday", "Thursday", "Friday", "Saturday", "Arrival", "Transfer",
    "Departure", "Mountain", "Landing", "Ice", "Northface", "Thrill", "Bond",
    "Skyline", "Piz", "Good", "Half", "Day", "Vienna", "Radisson", "Welcome",
    "Hyatt", "Zurich", "Lucerne", "Kloten", "Coop",
    # From the map's own prose. Common words that happen to start a sentence, plus
    # Europe, which is a proper noun but one that genuinely translates.
    "Arrivals", "Airport", "Night", "Covered", "Desks", "Base", "Change", "Queen",
    "Mountains", "Free", "Passed", "Top", "End", "Cog", "Cogwheel", "Where",
    "Little", "Bottom", "Enter", "Walkways", "Come", "Altitude", "Europe", "Down",
    "Into", "Through", "On", "Back", "This", "Both", "Rental", "Base",
    # From the revision that added the ninth night. Ordinary words that happen to
    # open a sentence or a cost line.
    "Only", "Collecting", "Collected", "Picking", "Returning", "That", "Four",
    "One", "Two", "Be", "Add", "Late", "Rail", "You", "Passport",
    # and from the new map prose on the spare Saturday
    "Direct", "Thirteen", "Flat", "Lifts", "Dropped", "Open", "Everything",
    # and from the reorder that put a rest day in Zurich at the front
    "All", "Worth", "If", "In", "Still", "Six", "Left", "Lake", "Along", "Optional",
    # Switzerland itself, for the same reason as Europe above: a proper noun
    # that genuinely translates, and is written in the reader's own script.
    "Switzerland",
    # Tel Aviv is the travellers' own city. Hebrew and Arabic write it in their own
    # script, and glossing it in Latin the way a Swiss place name is glossed would
    # be absurd — so it is exempt rather than expected to survive.
    "Tel", "Aviv",
}
# Latin-1 letters, not just A-Z: the Swiss names carry umlauts (Brünig, Männlichen).
TOKEN = re.compile(r"\b[A-ZÄÖÜÉÈÀ][A-Za-zäöüéèàÄÖÜ]+\b|\b[A-Z]{2,}\b|\b[A-Z]\d+\b")

for lang in RTL_LANGS:
    dropped: dict[str, list[str]] = {}
    for path, english in paths.items():
        # The isolates go in between the letter and the digit, so a motorway
        # number is stored as "A<LRI>4<PDI>" and a plain search for "A4" misses
        # it. Strip them before looking: the token survived, it is just wrapped.
        target = TRANS["content"][lang].get(path, "").replace(LRI, "").replace(PDI, "")
        for token in set(TOKEN.findall(english)):
            if token in SENTENCE_START:
                continue
            if token not in target:
                dropped.setdefault(path, []).append(token)
    if dropped:
        review.append(f"{lang}: Latin tokens to eyeball -> {json.dumps(dropped, ensure_ascii=False)}")
    else:
        print(f"proper noun {lang}: every Latin token from the source reappears")

# --- 6. every ink tone stays legible on its own surface and plane -----------
MIN_CONTRAST = {"ink": 7.0, "ink2": 4.5, "muted": 3.0}


def _channel(value: float) -> float:
    value /= 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(hexstr: str) -> float:
    h = hexstr.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


worst = ("", "", 99.0)
for tone, roles in INK_TONES.items():
    for role, floor in MIN_CONTRAST.items():
        for ground in ("surface", "plane"):
            got = contrast(roles[role], roles[ground])
            if got < floor:
                failures.append(
                    f"ink/{tone}.{role}: {roles[role]} is {got:.2f}:1 on the {tone} "
                    f"{ground} {roles[ground]}, needs {floor}:1"
                )
            if got / floor < worst[2]:
                worst = (f"{tone}.{role}", ground, got / floor)
# The accent and warn are not decoration: they carry the cost link, the today badge and
# the floating-day chip, so they answer to the same floor as body copy — on the card, on
# the page, and on their own wash. So does whatever is written on top of them, which is
# the pair that is easy to get wrong: a mid-tone gold takes white, not near-black.
for tone, roles in INK_TONES.items():
    if roles.get("scheme") not in ("light", "dark"):
        failures.append(f"ink/{tone}: no light/dark scheme declared")
    for role in ("accent", "warn"):
        for ground in ("surface", "plane", f"{role}_wash"):
            got = contrast(roles[role], roles[ground])
            if got < 4.5:
                failures.append(
                    f"ink/{tone}.{role}: {roles[role]} is {got:.2f}:1 on {ground} "
                    f"{roles[ground]}, needs 4.5:1"
                )
    for role, on in (("accent", "on_accent"), ("warn", "on_warn")):
        got = contrast(roles[on], roles[role])
        if got < 4.5:
            failures.append(
                f"ink/{tone}.{on}: {roles[on]} is {got:.2f}:1 on its own {role} "
                f"{roles[role]}, needs 4.5:1"
            )

# A colour written into the stylesheet cannot follow the theme. White over the photo
# scrim is the one legitimate exception, so it is named rather than counted.
STYLE = stylesheet("en", False, "night")
literal = [m for m in re.findall(r"(?<![-\w])(?:color|background(?:-color)?)\s*:\s*(#[0-9a-fA-F]{3,8})", STYLE)
           if m.lower() not in ("#fff", "#ffffff", "#0d1418")]
if literal:
    failures.append(f"theme: colours hardcoded in the stylesheet instead of a tone role: {sorted(set(literal))}")

print(f"ink tones   {len(INK_TONES)} tones ({sum(r['scheme'] == 'dark' for r in INK_TONES.values())} dark) "
      f"pass contrast on surface, plane and wash "
      f"(tightest {worst[0]} on {worst[1]}, {worst[2]:.2f}x its floor)")

ui_default_keys = TRANS["ui"][TRANS["meta"]["default"]]
for tone in INK_TONES:
    if f"ui.ink.{tone}" not in ui_default_keys:
        failures.append(f"ink/{tone}: no ui.ink.{tone} label in translations.json")

# --- 7. the today marker and expand-all actually render ---------------------
# The today marker is invisible until the trip starts, so it gets tested rather
# than eyeballed: render the day cards with the clock moved to a trip date.
import datetime  # noqa: E402

import app as _app  # noqa: E402


class _TripDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 9)


IMAGES = json.loads((BASE / "images.json").read_text(encoding="utf-8"))


def _translators(lang):
    ui_l, content_l = TRANS["ui"][lang], TRANS["content"].get(lang, {})
    rtl = lang in RTL_LANGS

    def T(key, **kw):
        s = ui_l.get(key, TRANS["ui"]["en"].get(key, key))
        return s.format(**kw) if kw else s

    def C(path, fallback):
        v = content_l.get(path)
        return fallback if v is None else v

    def tx(s):
        if s is None:
            return ""
        s = str(s)
        return _app.html.escape(_app.guard_numbers(s) if rtl else s, quote=True)

    return T, C, tx, f'dir="{"rtl" if rtl else "ltr"}" lang="{lang}"'


def _render_days(lang, fake_today, expand_all, notes_n=0):
    """The day cards as one string. day_card_html is pure, so no Streamlit is needed."""
    T, C, tx, dirattr = _translators(lang)
    today = (_TripDate if fake_today else datetime.date).today().isoformat()
    return "".join(
        _app.day_card_html(DATA, i, T, C, tx, dirattr, "CHF", DATA["trip"]["fx"],
                           expand_all, IMAGES, today, notes_n)
        for i in range(len(DATA["days"]))
    )


def _cards(html):
    found = re.findall(r'<details class="tp-day"[^>]*>', html)
    return found, [c for c in found if c.endswith(" open>")]


for lang in TRANS["meta"]["languages"]:
    html = _render_days(lang, fake_today=True, expand_all=False)
    _, opened = _cards(html)
    badge = TRANS["ui"][lang]["ui.day.today_badge"]
    if html.count('data-today="1"') != 1 or len(opened) != 1 or badge not in html:
        failures.append(
            f"today/{lang}: expected exactly one marked, opened, badged card; got "
            f"{html.count('data-today=\"1\"')} marked, {len(opened)} open, "
            f"badge present={badge in html}"
        )

html = _render_days("en", fake_today=False, expand_all=False)
if 'data-today="1"' in html or _cards(html)[1]:
    failures.append("today: a card is marked or auto-opened outside the trip dates")

html = _render_days("en", fake_today=False, expand_all=True)
found, opened = _cards(html)
if len(opened) != len(found) or len(found) != len(DATA["days"]):
    failures.append(f"expand-all: {len(opened)} of {len(found)} day cards open")
print(f"day cards   today marker fires on the right card in "
      f"{len(TRANS['meta']['languages'])} languages; expand-all opens all "
      f"{len(DATA['days'])}, nested cost tables untouched")

# --- 7b. the overview's hand-picked warnings still point at real tips --------
# critical_tips names days and tips by INDEX, so reordering the trip silently
# aims them at the wrong advice - or off the end of a shorter tip list, which is
# how this first showed up: an IndexError on page load, after every other check
# had passed. Resolve each one, then render the whole tab to be sure.
for _path in TRANS["meta"]["critical_tips"]:
    try:
        _, _di, _, _ti = _path.split(".")
        DATA["days"][int(_di)]["tips"][int(_ti)]
    except (IndexError, ValueError, KeyError):
        failures.append(f"critical_tips: {_path} points at no tip in itinerary.json")
_ovT, _ovC, _ovtx, _ovdir = _translators("en")
_overview_out: list[str] = []
_real_block, _app.block = _app.block, _overview_out.append
try:
    _app.render_overview(DATA, _ovT, _ovC, _ovtx, _ovdir,
                         TRANS["meta"]["critical_tips"])
except Exception as exc:  # noqa: BLE001 - any failure here is a broken page
    failures.append(f"overview: the Before you go tab raises {exc!r}")
finally:
    _app.block = _real_block
print(f"overview    {len(TRANS['meta']['critical_tips'])} critical tips resolve, "
      f"and the tab renders")

# --- 8. every photo is present, credited and small enough -------------------
# The photos are committed, so "it worked on my machine" is not evidence: this checks
# the files that are actually in the repo, and that each one still carries the author
# and licence it was taken under. Several are share-alike; dropping a credit would be
# a licence breach, not a cosmetic slip.
PHOTOS = BASE / "static" / "photos"
MAX_PHOTO_KB, MAX_TOTAL_KB = 260, 2600
wanted = ["hero"] + [f"day{i}" for i in range(len(DATA["days"]))]
total_kb = 0.0
for slot in wanted:
    meta = IMAGES.get(slot)
    if meta is None:
        failures.append(f"photo/{slot}: no entry in images.json")
        continue
    for name in (meta["file"], meta["thumb"]):
        path = PHOTOS / name
        if not path.exists():
            failures.append(f"photo/{slot}: {name} is in images.json but not on disk")
            continue
        kb = path.stat().st_size / 1024
        total_kb += kb
        if kb > MAX_PHOTO_KB:
            failures.append(f"photo/{slot}: {name} is {kb:.0f} KB, over {MAX_PHOTO_KB} KB")
    credit = meta.get("credit", {})
    for field_name in ("author", "licence", "page", "modified"):
        if not credit.get(field_name):
            failures.append(f"photo/{slot}: credit is missing {field_name}")
    if not str(credit.get("page", "")).startswith("https://commons.wikimedia.org/"):
        failures.append(f"photo/{slot}: credit page is not a Commons URL")
extra = set(IMAGES) - set(wanted)
if extra:
    failures.append(f"photo: images.json has slots for nothing that renders: {sorted(extra)}")
if total_kb > MAX_TOTAL_KB:
    failures.append(f"photo: {total_kb:.0f} KB of photos, over the {MAX_TOTAL_KB} KB budget")
# every credit must actually reach the reader
_T, _C, _tx, _dir = _translators("en")
credits_out = []
_real_block, _app.block = _app.block, credits_out.append
try:
    _app.render_credits(IMAGES, _T, _tx, _dir, len(DATA["days"]))
finally:
    _app.block = _real_block
for slot in wanted:
    author = IMAGES[slot]["credit"]["author"]
    if _app.html.escape(author, quote=True) not in credits_out[0]:
        failures.append(f"photo/{slot}: {author!r} never appears in the credits list")
print(f"photos      {len(wanted)} slots, {total_kb:.0f} KB committed, "
      f"every author and licence credited on the page")

# --- 9. a note is data, never markup ----------------------------------------
# The board is on a public URL, so anything a visitor types has to come back out as
# text. This renders the nastiest thing someone could type and insists it stays inert.
NASTY = '<script>alert(1)</script><img src=x onerror=alert(2)> & "quotes" \'and\''
probe = _app.note_html(
    notes_store.Note("n1", "2026-09-06", NASTY, NASTY, "2026-09-06T08:30:00+00:00",
                     ("someone",)),
    _T, _tx, _dir, "someone else",
)

# The invariant is not "the word script does not appear" — it is that nothing the
# visitor typed becomes an element. So parse the result and look at the tree.
class _Reader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.text = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


reader = _Reader()
reader.feed(probe)
ALLOWED = {"div", "span", "p"}
rogue = sorted({tag for tag, _ in reader.tags} - ALLOWED)
if rogue:
    failures.append(f"escaping: a note produced element(s) {rogue}; only {sorted(ALLOWED)} "
                    f"are ever written by note_html")
handlers = sorted({a for _, attrs in reader.tags for a in attrs if a.startswith("on")})
if handlers:
    failures.append(f"escaping: a note produced event handler attribute(s) {handlers}")
if NASTY not in "".join(reader.text):
    failures.append("escaping: the note body did not survive as literal text")
if 'dir="auto"' not in probe:
    failures.append('escaping: notes lost dir="auto", so a Hebrew note in an '
                    "English page would render backwards")
print(f"escaping    a note of script tags and event handlers parses to "
      f"{len(reader.tags)} elements, all of them mine, and comes back as text")

# --- 10. both storage backends behave identically ---------------------------
# Supabase cannot be reached from here without somebody's project, so it is exercised
# against a strict stub that speaks the same REST dialect. That proves the requests,
# not the service; the README says so plainly rather than implying more.
sys.path.insert(0, str(BASE / "tools"))
from stub_supabase import StubSupabase  # noqa: E402


def exercise(store, label):
    a = store.add("2026-09-05", "Orwa", "Can we take the early boat?")
    b = store.add("2026-09-05", "Dad", "Yes, I like that.")
    store.add("2026-09-09", "Mum", "Only if it is clear.")
    for who in ("Dad", "dad ", "Mum"):
        store.set_like(a, who, True)        # the middle one is the same person, folded
    store.set_like(b, "Orwa", True)
    store.set_like(b, "Orwa", False)

    got = store.notes()
    if sorted(got) != ["2026-09-05", "2026-09-09"]:
        failures.append(f"store/{label}: notes came back under {sorted(got)}")
    first = got["2026-09-05"][0]
    if [x.author for x in got["2026-09-05"]] != ["Orwa", "Dad"]:
        failures.append(f"store/{label}: notes are not in the order they were written")
    if len(first.likes) != 2 or not first.liked_by("DAD") or first.liked_by("Nobody"):
        failures.append(f"store/{label}: likes are wrong: {first.likes}")
    if got["2026-09-05"][1].likes:
        failures.append(f"store/{label}: unliking left the like behind")

    try:
        store.delete(a, "Dad")
        failures.append(f"store/{label}: let somebody delete another person's note")
    except notes_store.StoreError as exc:
        if exc.key != "ui.notes.err_not_yours":
            failures.append(f"store/{label}: wrong refusal {exc.key}")
    store.delete(a, "orwa")
    left = store.notes()
    if len(left["2026-09-05"]) != 1:
        failures.append(f"store/{label}: the author's own delete did not take")
    if any(x.id == a for v in left.values() for x in v):
        failures.append(f"store/{label}: deleted note is still readable")

    for args, key in ((("d", "", "x"), "ui.notes.err_name"),
                      (("d", "N", "   "), "ui.notes.err_body"),
                      (("", "N", "x"), "ui.notes.err_day")):
        try:
            store.add(*args)
            failures.append(f"store/{label}: accepted {args}")
        except notes_store.StoreError as exc:
            if exc.key != key:
                failures.append(f"store/{label}: {args} raised {exc.key}, wanted {key}")


with tempfile.TemporaryDirectory() as tmp:
    local = notes_store.SqliteStore(Path(tmp) / "sub" / "notes.db")
    exercise(local, "sqlite")
    if local.shared:
        failures.append("store/sqlite: claims notes are shared, and they are not")
    local.close()

stub = StubSupabase()
remote = notes_store.SupabaseStore("https://stub.supabase.co", "test-anon-key",
                                   opener=stub.opener)
exercise(remote, "supabase")
if not remote.shared:
    failures.append("store/supabase: does not claim to share notes")
stub.fail_next = 401
try:
    remote.notes()
    failures.append("store/supabase: a 401 did not raise")
except notes_store.StoreError as exc:
    if exc.key != "ui.notes.err_store":
        failures.append(f"store/supabase: a 401 surfaced as {exc.key}")


def _unreachable(req, timeout=None):
    raise OSError("no route to host")


try:
    notes_store.SupabaseStore("https://x", "k", opener=_unreachable).notes()
    failures.append("store/supabase: being offline did not raise")
except notes_store.StoreError as exc:
    if exc.key != "ui.notes.err_offline":
        failures.append(f"store/supabase: offline surfaced as {exc.key}")

if notes_store.clean("a\x00b\r\nc", 99) != "ab\nc":
    failures.append("store: clean() left control characters or CRLF behind")
if len(notes_store.clean("x" * 9999, notes_store.MAX_BODY)) != notes_store.MAX_BODY:
    failures.append("store: clean() did not cap the length")

if notes_store.open_store({}, Path(tempfile.mkdtemp()) / "n.db").backend != "local":
    failures.append("store: no secrets should mean the local backend")
if notes_store.open_store({"supabase_url": "https://a", "supabase_key": "b"},
                          Path("x")).backend != "supabase":
    failures.append("store: both secrets set should mean Supabase")
try:
    notes_store.open_store({"supabase_url": "https://a"}, Path("x"))
    failures.append("store: a half-filled config silently fell back to local storage")
except notes_store.StoreError as exc:
    if exc.key != "ui.notes.err_halfconfig":
        failures.append(f"store: half config raised {exc.key}")
print(f"notes store {len(stub.calls)} REST calls checked against the stub; sqlite and "
      f"supabase agree on ordering, likes, ownership and refusals")

# --- 9. the map -------------------------------------------------------------
# Two of these earn their place over all the others. The bounding box catches a
# swapped lat/lon, which puts a stop in the Indian Ocean and is otherwise invisible
# in a file of five-decimal numbers. The endpoint test catches a route fetched
# between the wrong two stops, which looks perfectly plausible on screen and is
# only discovered by driving it.

CH_BBOX = (45.80, 5.90, 47.85, 10.55)  # south, west, north, east
ENDPOINT_M = 250
DRIVE_TOLERANCE = 0.15

for slug, stop in GEO["stops"].items():
    lat, lon = stop["lat"], stop["lon"]
    if not (CH_BBOX[0] <= lat <= CH_BBOX[2] and CH_BBOX[1] <= lon <= CH_BBOX[3]):
        failures.append(f"geo/{slug}: {lat}, {lon} is not inside Switzerland")
    for d in stop["days"]:
        if not 0 <= d < len(DATA["days"]):
            failures.append(f"geo/{slug}: day {d} is not a day of this trip")

worst_end = (0.0, "nothing")
for i, leg in enumerate(GEO["legs"]):
    where = f"geo/leg {i} ({leg['from']} -> {leg['to']})"
    if not 0 <= leg["day"] < len(DATA["days"]):
        failures.append(f"{where}: day {leg['day']} is not a day of this trip")
    if leg["mode"] not in tripmap.MODES:
        failures.append(f"{where}: {leg['mode']} is not a way of getting anywhere")
    geom = leg["geometry"]
    if len(geom) < 2:
        failures.append(f"{where}: has no geometry")
        continue
    for end, slug in ((geom[0], leg["from"]), (geom[-1], leg["to"])):
        if slug not in GEO["stops"]:
            failures.append(f"{where}: {slug} is not a stop")
            continue
        stop = GEO["stops"][slug]
        gap = build_geo.haversine(end, (stop["lat"], stop["lon"]))
        if gap > worst_end[0]:
            worst_end = (gap, f"leg {i} at {slug}")
        if gap > ENDPOINT_M:
            failures.append(
                f"{where}: the line ends {gap:.0f} m away from {slug}, past the "
                f"{ENDPOINT_M} m tolerance — it is probably the wrong line")

# Every day of the trip has to be on the map, or the day switcher has a hole in it.
mapped_days = ({d for s in GEO["stops"].values() for d in s["days"]}
               | {l["day"] for l in GEO["legs"]})
blank = [i for i in range(len(DATA["days"])) if i not in mapped_days]
if blank:
    failures.append(f"geo: nothing is pinned on day(s) {blank}")

# The routed distance is the one honest check on the itinerary's own figures, and
# it found two places where those figures are wrong. itinerary.json is the source of
# truth and is not edited to make a check pass, so the two are named here with what
# the roads actually measure, and reported every run until somebody decides.
#
# Both were confirmed against two independent routers, OSRM and Valhalla, which
# agree with each other to within a kilometre.
KNOWN_DRIVE_DRIFT = {
    # Empty, and worth saying why. The long-standing drift was day 3's Weggis to
    # Grindelwald, costed at 105 km when the road is 130. Reordering the trip as a
    # loop deleted that drive outright - Grindelwald is now reached from Kloten and
    # Weggis is reached from the gorge - so the wrong figure went with it.
    # The old day-8 entry is gone on purpose. That drift was the departure day's
    # Kloten-to-Parking-3 hop, costed at 10 km when it is 2.5. The van now goes back
    # on the 11th, so the last two days have no driving at all and nothing to drift.
}
for i, day in enumerate(DATA["days"]):
    stated = day.get("drive_km") or 0
    routed = sum(l["km"] for l in GEO["legs"] if l["day"] == i and l["mode"] == "drive")
    if stated == 0 and routed == 0:
        continue
    if stated == 0 or routed == 0:
        failures.append(f"geo/day {i}: itinerary says {stated} km of driving, "
                        f"the routes say {routed:.1f} km")
        continue
    off = abs(routed - stated) / stated
    if off <= DRIVE_TOLERANCE:
        continue
    if i in KNOWN_DRIVE_DRIFT:
        review.append(f"day {i} drive_km: itinerary {stated} km vs {routed:.1f} km "
                      f"routed. {KNOWN_DRIVE_DRIFT[i]}")
    else:
        failures.append(
            f"geo/day {i}: itinerary says {stated} km, the routed line is "
            f"{routed:.1f} km ({off * 100:.0f}% apart)")
stale = [i for i in KNOWN_DRIVE_DRIFT
         if abs(sum(l["km"] for l in GEO["legs"] if l["day"] == i and l["mode"] == "drive")
                - (DATA["days"][i].get("drive_km") or 0))
         / max(DATA["days"][i].get("drive_km") or 1, 1) <= DRIVE_TOLERANCE]
if stale:
    failures.append(f"geo: day(s) {stale} are listed as known drive drift but now "
                    f"agree — delete them from KNOWN_DRIVE_DRIFT")

pts = sum(len(l["geometry"]) for l in GEO["legs"])
traced = sum(1 for l in GEO["legs"] if str(l.get("source", "")).startswith("osm"))
print(f"geo         {len(GEO['stops'])} stops all inside Switzerland, "
      f"{len(GEO['legs'])} legs ({traced} traced from OSM ways), {pts:,} points; "
      f"worst line end {worst_end[0]:.0f} m from its stop ({worst_end[1]})")

# The map document answers to the same rule as the stylesheet: no colour may be
# written down, because a literal cannot follow the theme into the dark tones.
_T, _C, _tx, _dir = _translators("en")
for _tone_name in INK_TONES:
    _tone = INK_TONES[_tone_name]
    _data = tripmap.payload(GEO, _T, _C, _tx, "en", False, _tone, -1)
    _doc = tripmap.document(_data, _tone, "en", "sans-serif", "1.5")
    _lit = [m for m in re.findall(
        r"(?<![-\w])(?:color|background(?:-color)?)\s*:\s*(#[0-9a-fA-F]{3,8})", _doc)
        if m.lower() not in ("#fff", "#ffffff", "#0d1418")]
    if _lit:
        failures.append(f"map/{_tone_name}: colours hardcoded in the map document "
                        f"instead of a tone role: {sorted(set(_lit))}")
    for _mode in tripmap.MODES:
        if f"--{_mode}:" not in _doc:
            failures.append(f"map/{_tone_name}: no line colour defined for {_mode}")

# The route lines are not text, so they answer to a different floor: legible on the
# paper they are drawn on, and far enough apart from each other that a drive is not
# mistaken for a walk. The dash patterns are the real safety net, so they are checked
# to be distinct too.
MAP_PAPER = {"light": "#F2EFE9", "dark": "#333333"}
ROUTE_ROLES = [f"route_{m}" for m in tripmap.MODES]


def _lab(hexstr: str):
    h = hexstr.lstrip("#")
    r, g, b = (_channel(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116  # noqa: E731
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


worst_paper, worst_apart = (99.0, ""), (999.0, "")
for tone, roles in INK_TONES.items():
    paper = MAP_PAPER[roles["scheme"]]
    for role in ROUTE_ROLES:
        if role not in roles:
            failures.append(f"ink/{tone}: no {role} for the map to draw with")
            continue
        got = contrast(roles[role], paper)
        if got < 3.0:
            failures.append(f"ink/{tone}.{role}: {roles[role]} is {got:.2f}:1 on the "
                            f"map paper {paper}, needs 3:1")
        if got < worst_paper[0]:
            worst_paper = (got, f"{tone}.{role}")
    for a, b in ((x, y) for i, x in enumerate(ROUTE_ROLES) for y in ROUTE_ROLES[i + 1:]):
        if a not in roles or b not in roles:
            continue
        la, lb = _lab(roles[a]), _lab(roles[b])
        apart = sum((p - q) ** 2 for p, q in zip(la, lb)) ** 0.5
        if apart < 22:
            failures.append(f"ink/{tone}: {a} and {b} are only {apart:.0f} apart in Lab, "
                            f"too close to tell one line from the other")
        if apart < worst_apart[0]:
            worst_apart = (apart, f"{tone} {a[6:]}/{b[6:]}")
dashes = [str(tripmap.MODE_STYLE[m]["dash"]) for m in tripmap.MODES]
if len(set(dashes)) != len(dashes):
    failures.append(f"map: two modes share a dash pattern, so colour is the only thing "
                    f"telling them apart: {dashes}")
print(f"map colour  {len(ROUTE_ROLES)} route roles x {len(INK_TONES)} tones legible on "
      f"the map and distinct (tightest {worst_paper[1]} {worst_paper[0]:.2f}:1, "
      f"closest pair {worst_apart[1]} dE {worst_apart[0]:.0f}); "
      f"{len(set(dashes))} distinct dash patterns")

# The two downloads are the whole offline story, so they get parsed rather than
# eyeballed: a file that does not open is worse than no file at all.
import xml.etree.ElementTree as ET  # noqa: E402

_gpx = tripmap.gpx(GEO, "Switzerland")
_kml = tripmap.kml(GEO, "Switzerland", INK_TONES["daylight"])
try:
    root = ET.fromstring(_gpx)
    ns = "{http://www.topografix.com/GPX/1/1}"
    n_wpt = len(root.findall(f"{ns}wpt"))
    n_trk = len(root.findall(f"{ns}trk"))
    n_pts = len(root.findall(f".//{ns}trkpt"))
    if n_wpt != len(GEO["stops"]):
        failures.append(f"gpx: {n_wpt} waypoints for {len(GEO['stops'])} stops")
    if n_trk != len(GEO["legs"]):
        failures.append(f"gpx: {n_trk} tracks for {len(GEO['legs'])} legs")
    if n_pts != pts:
        failures.append(f"gpx: {n_pts} track points, geo.json holds {pts}")
except ET.ParseError as exc:
    failures.append(f"gpx: does not parse: {exc}")
    n_wpt = n_trk = 0
try:
    kroot = ET.fromstring(_kml)
    kns = "{http://www.opengis.net/kml/2.2}"
    n_place = len(kroot.findall(f".//{kns}Placemark"))
    n_style = len(kroot.findall(f".//{kns}Style"))
    if n_place != len(GEO["stops"]) + len(GEO["legs"]):
        failures.append(f"kml: {n_place} placemarks for "
                        f"{len(GEO['stops'])} stops and {len(GEO['legs'])} legs")
    if n_style != len(tripmap.MODES):
        failures.append(f"kml: {n_style} line styles for {len(tripmap.MODES)} modes")
    if re.search(r"<color>(?!ff)", _kml):
        failures.append("kml: a line style is not fully opaque")
except ET.ParseError as exc:
    failures.append(f"kml: does not parse: {exc}")
print(f"exports     gpx {len(_gpx) / 1024:.0f} KB parses ({n_wpt} waypoints, "
      f"{n_trk} tracks), kml {len(_kml) / 1024:.0f} KB parses")

# Leaflet has to be on disk. If it is not, the map tab renders an empty box on a
# deploy and nothing says why.
for _asset in ("leaflet.js", "leaflet.css"):
    _path = BASE / "static" / "leaflet" / _asset
    if not _path.exists():
        failures.append(f"map: static/leaflet/{_asset} is missing, so the map cannot draw")
    elif _path.stat().st_size < 5000:
        failures.append(f"map: static/leaflet/{_asset} is only {_path.stat().st_size} bytes")

# --- report -----------------------------------------------------------------
print()
for line in review:
    print("REVIEW  " + line)
for line in failures:
    print("FAIL    " + line)
if failures:
    sys.exit(1)
print("\nAll hard checks passed.")
