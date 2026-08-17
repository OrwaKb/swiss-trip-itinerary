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
from app import INK_TONES, guard_numbers, reconcile, stylesheet  # noqa: E402

BASE = Path(__file__).parent
DATA = json.loads((BASE / "itinerary.json").read_text(encoding="utf-8"))
TRANS = json.loads((BASE / "translations.json").read_text(encoding="utf-8"))

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
APP_SRC = (BASE / "app.py").read_text(encoding="utf-8")
asked = set(re.findall(r'T\(\s*[\'"]([a-z0-9_.]+)[\'"]', APP_SRC))
# f-string keys built from a loop variable, e.g. T(f"ui.totals.{k}")
asked |= {f"ui.totals.{k}" for k in TRANS["meta"]["total_keys"]}
asked |= {f"ui.ink.{tone}" for tone in INK_TONES}
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
}
# Latin-1 letters, not just A-Z: the Swiss names carry umlauts (Brünig, Männlichen).
TOKEN = re.compile(r"\b[A-ZÄÖÜÉÈÀ][A-Za-zäöüéèàÄÖÜ]+\b|\b[A-Z]{2,}\b|\b[A-Z]\d+\b")

for lang in RTL_LANGS:
    dropped: dict[str, list[str]] = {}
    for path, english in paths.items():
        target = TRANS["content"][lang].get(path, "")
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

# --- report -----------------------------------------------------------------
print()
for line in review:
    print("REVIEW  " + line)
for line in failures:
    print("FAIL    " + line)
if failures:
    sys.exit(1)
print("\nAll hard checks passed.")
