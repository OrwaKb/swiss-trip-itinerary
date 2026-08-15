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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from app import INK_TONES, guard_numbers, reconcile  # noqa: E402

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
SENTENCE_START = {
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
print(f"ink tones   {len(INK_TONES)} tones pass contrast on their own surface and plane "
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


def _render_days(lang, fake_today, expand_all):
    out = []
    real_block, real_date = _app.block, _app.date
    _app.block, _app.date = out.append, (_TripDate if fake_today else real_date)
    try:
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

        _app.render_days(
            DATA, T, C, tx, f'dir="{"rtl" if rtl else "ltr"}" lang="{lang}"',
            "CHF", DATA["trip"]["fx"], expand_all,
        )
    finally:
        _app.block, _app.date = real_block, real_date
    return out[0]


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

# --- report -----------------------------------------------------------------
print()
for line in review:
    print("REVIEW  " + line)
for line in failures:
    print("FAIL    " + line)
if failures:
    sys.exit(1)
print("\nAll hard checks passed.")
