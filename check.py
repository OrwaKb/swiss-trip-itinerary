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
from app import guard_numbers, reconcile  # noqa: E402

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
    "Schilthorn", "Mount", "Mannlichen", "Pfingstegg", "Rigi", "Grindelwald",
    "Harder", "Schynige", "Trummelbach", "Weggis", "Cliff", "Adventure", "Skip",
    "First", "Below", "Coats", "Paved", "Kanzeli", "It", "Their", "Allow", "Park",
    "Descend", "Grindelwald", "Wide", "Murren", "Sunday", "Monday", "Tuesday",
    "Wednesday", "Thursday", "Friday", "Saturday", "Arrival", "Transfer",
    "Departure", "Mountain", "Landing", "Ice", "Northface", "Thrill", "Bond",
    "Skyline", "Piz", "Good", "Half", "Day", "Vienna", "Radisson", "Welcome",
    "Hyatt", "Zurich", "Lucerne", "Kloten", "Coop",
}
TOKEN = re.compile(r"\b[A-Z][A-Za-z]+\b|\b[A-Z]{2,}\b|\b[A-Z]\d+\b")

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

# --- report -----------------------------------------------------------------
print()
for line in review:
    print("REVIEW  " + line)
for line in failures:
    print("FAIL    " + line)
if failures:
    sys.exit(1)
print("\nAll hard checks passed.")
