"""Family trip itinerary, in English, Hebrew and Arabic.

Every user-visible string comes from itinerary.json or translations.json.
Nothing readable is written in this file.
"""

from __future__ import annotations

import html
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

import store as notes_store
import tripmap

BASE = Path(__file__).parent
DATA_PATH = BASE / "itinerary.json"
TRANS_PATH = BASE / "translations.json"
IMAGES_PATH = BASE / "images.json"

# Photos are served by Streamlit itself out of static/ (see .streamlit/config.toml).
# Nothing is fetched from another host while the app is running.
PHOTO_URL = "app/static/photos/{}"

# Where the local store keeps its file. Git ignores it; on a shared deploy the notes
# live in Supabase instead and this is never touched.
NOTES_DB = BASE / ".notes" / "notes.db"
# Note times are shown where the trip happens, not where the server happens to be.
TRIP_TZ = "Europe/Zurich"
POST_COOLDOWN_S = 6

# Bidi isolates. Wrapping a number, price, time or Latin run in LRI…PDI stops the
# bidi algorithm from reordering it inside a right-to-left sentence.
LRI, PDI = "⁦", "⁩"

CURRENCIES = ("CHF", "EUR", "ILS")
FX_KEY = {"EUR": "CHF_EUR", "ILS": "CHF_ILS"}

# Themes, taken off the trip's own photographs: glacier snow, the pink-gold of
# alpenglow on the summits, and the slate the mountain goes at dusk. Two light, two
# dark — the dark ones are the reason every colour is named as a role here rather than
# written into the CSS. A literal like `color: #fff` on a chip is invisible the moment
# the page behind it turns pale, so nothing below the tone table gets to hardcode one.
#
# The four route_* roles are the map's lines. They are not text, so they answer to a
# different floor: check.py holds them 3:1 against the map paper they are drawn on and
# pairwise apart from each other, because a drive and a walk that look alike on a phone
# in sunlight are worse than no map. The dash patterns carry the distinction anyway.
#
# check.py holds every tone to 7:1 on ink, 4.5:1 on ink2 and 3:1 on muted against BOTH
# its own surface and its own plane, so a tone cannot be added without passing. The
# accent and warn carry text too, so they are held to 4.5:1 on surface, plane and their
# own wash, and whatever sits on top of them to 4.5:1 as well.
INK_TONES = {
    "daylight": {  # glacier snow
        "scheme": "light",
        "ink": "#14222B", "ink2": "#425C6B", "muted": "#63808F",
        "rule": "#DCE5EA", "line": "#BDCDD7", "surface": "#FFFFFF", "plane": "#E9EFF3",
        "accent": "#B4432A", "accent_wash": "#FAE4DC", "on_accent": "#FFFFFF",
        "warn": "#8E5D00", "warn_wash": "#FAEFD6", "on_warn": "#FFFFFF",
        "route_drive": "#A8391F", "route_walk": "#12653A",
        "route_lift": "#134E92", "route_boat": "#653B94",
    },
    "alpenglow": {  # the same light, on warm paper
        "scheme": "light",
        "ink": "#231917", "ink2": "#5A4640", "muted": "#8A6D64",
        "rule": "#EADCD5", "line": "#D3BDB3", "surface": "#FDFAF8", "plane": "#F3ECE7",
        "accent": "#A03C27", "accent_wash": "#F7DFD6", "on_accent": "#FFFFFF",
        "warn": "#855400", "warn_wash": "#F7EAD2", "on_warn": "#FFFFFF",
        "route_drive": "#96331B", "route_walk": "#115C34",
        "route_lift": "#134887", "route_boat": "#5E3789",
    },
    "dusk": {  # slate blue, dark
        "scheme": "dark",
        "ink": "#E9F1F6", "ink2": "#AFC4D1", "muted": "#8399A8",
        "rule": "#28363F", "line": "#3A4C58", "surface": "#1B2832", "plane": "#121D25",
        "accent": "#F19479", "accent_wash": "#35231E", "on_accent": "#1A0E09",
        "warn": "#D9A44C", "warn_wash": "#2D2517", "on_warn": "#1F1804",
        "route_drive": "#FF9E80", "route_walk": "#54D89B",
        "route_lift": "#7CBEFF", "route_boat": "#CBA9FF",
    },
    "night": {  # near black, dark
        "scheme": "dark",
        "ink": "#EDF1F4", "ink2": "#A9B7C0", "muted": "#7D8C96",
        "rule": "#202830", "line": "#303B42", "surface": "#141A1F", "plane": "#0A0E11",
        "accent": "#EF8E74", "accent_wash": "#2B1A15", "on_accent": "#170C08",
        "warn": "#D6A14A", "warn_wash": "#251E12", "on_warn": "#1B1503",
        "route_drive": "#FF9B7C", "route_walk": "#4FD597",
        "route_lift": "#78BCFF", "route_boat": "#C7A4FF",
    },
}
DEFAULT_INK = "daylight"
RECONCILE_TOLERANCE_CHF = 2.0
DAY_TOLERANCE_CHF = 1.0

# --------------------------------------------------------------------------- #
# Data loading and the reconciliation assertion
# --------------------------------------------------------------------------- #

def reconcile(d: dict) -> dict:
    """Check the day costs add up to the stated totals. Raises on real drift."""
    n = d["trip"]["party"]["total"]

    days_computed = 0.0
    for i, day in enumerate(d["days"]):
        per_person = sum(x["chf"] for x in day["per_person"]) * n
        group = sum(x["chf"] for x in day["group"])
        computed = per_person + group
        stated = day["day_total_chf"]
        days_computed += computed
        assert abs(computed - stated) <= DAY_TOLERANCE_CHF, (
            f"days[{i}] ({day['date']}): components sum to {computed:.2f} "
            f"but day_total_chf is {stated}"
        )

    days_stated = sum(day["day_total_chf"] for day in d["days"])
    trip_wide = sum(x["total"] for x in d["trip_wide_costs"])
    grand = d["totals"]["grand_total_chf"]

    drift_stated = days_stated + trip_wide - grand
    drift_computed = days_computed + trip_wide - grand
    assert abs(drift_stated) <= RECONCILE_TOLERANCE_CHF, (
        f"stated day totals + trip-wide = {days_stated + trip_wide:.2f}, "
        f"grand_total_chf = {grand}, drift {drift_stated:.2f}"
    )
    assert abs(drift_computed) <= RECONCILE_TOLERANCE_CHF, (
        f"computed day totals + trip-wide = {days_computed + trip_wide:.2f}, "
        f"grand_total_chf = {grand}, drift {drift_computed:.2f}"
    )

    category_keys = [
        "accommodation_chf", "activities_chf", "food_chf", "half_fare_cards_chf",
        "van_chf", "fuel_chf", "parking_chf",
    ]
    categories = sum(d["totals"][k] for k in category_keys)
    assert abs(categories - grand) <= RECONCILE_TOLERANCE_CHF, (
        f"category totals sum to {categories}, grand_total_chf = {grand}"
    )

    return {
        "days_stated": days_stated,
        "days_computed": days_computed,
        "trip_wide": trip_wide,
        "grand": grand,
        "drift": drift_stated,
    }


# mtime is part of the cache key, not decoration: without it an edit to either JSON
# file is invisible until the process restarts.
@st.cache_data(show_spinner=False)
def load_data(mtime: float) -> tuple[dict, dict]:
    d = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return d, reconcile(d)


@st.cache_data(show_spinner=False)
def load_translations(mtime: float) -> dict:
    return json.loads(TRANS_PATH.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def load_images(mtime: float) -> dict:
    return json.loads(IMAGES_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# The notes store
# --------------------------------------------------------------------------- #

@st.cache_resource(show_spinner=False)
def open_notes():
    """One store for the whole server, not one per visitor."""
    return notes_store.open_store(read_secrets(), NOTES_DB)


def read_secrets() -> dict:
    """st.secrets raises rather than returning empty when no secrets file exists,
    which is the normal case locally."""
    out = {}
    for key in ("supabase_url", "supabase_key"):
        try:
            out[key] = st.secrets[key]
        except Exception:
            pass
    return out


# Shared across sessions on purpose: everyone should see the same notes. The TTL is
# what makes another person's note appear without anybody reloading; a local write
# clears it immediately so your own note never lags.
#
# cache_resource, not cache_data, and the difference is not cosmetic: cache_data
# pickles what it stores, and Streamlit's file watcher reloads store.py on edit, which
# leaves cached Note objects pointing at a class that is no longer store.Note. Pickling
# then fails and takes the whole page down. cache_resource hands back the object itself.
# Nothing mutates the result, so sharing one copy between sessions is safe.
@st.cache_resource(ttl=15, show_spinner=False)
def read_notes(_store) -> dict:
    return _store.notes()


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #

_ISOLATED = re.compile(r"⁦[^⁩]*⁩")
_NUMBER_RUN = re.compile(r"\d+(?:[.,:]\d+)*(?:\s*[-–/]\s*\d+(?:[.,:]\d+)*)*")


def _wrap_numbers(fragment: str) -> str:
    return _NUMBER_RUN.sub(lambda m: LRI + m.group(0) + PDI, fragment)


def guard_numbers(s: str) -> str:
    """Isolate any numeric run not already isolated in the source string."""
    out, pos = [], 0
    for m in _ISOLATED.finditer(s):
        out.append(_wrap_numbers(s[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_wrap_numbers(s[pos:]))
    return "".join(out)


def money(chf: float, currency: str, fx: dict) -> str:
    if currency == "CHF":
        value = float(chf)
        digits = f"{value:,.0f}" if abs(value - round(value)) < 1e-9 else f"{value:,.2f}"
    else:
        value = float(chf) * fx[FX_KEY[currency]]
        digits = f"{value:,.0f}"
    return f"{currency} {digits}"


def num(text: str) -> str:
    """A programmatically built number/price: force it left-to-right."""
    return f'<span class="num" dir="ltr">{html.escape(str(text), quote=True)}</span>'


def photo(images: dict, slot: str, alt: str, cls: str, eager: bool = False) -> str:
    """An <img> for one slot, or nothing if that slot has no picture.

    width and height are stated so the page does not jump as each photo arrives, and
    everything below the first screen is left to the browser to load lazily.
    """
    meta = images.get(slot)
    if not meta:
        return ""
    loading = 'loading="eager" fetchpriority="high"' if eager else 'loading="lazy"'
    return (
        f'<img class="{cls}" src="{PHOTO_URL.format(meta["file"])}" '
        f'width="{meta["w"]}" height="{meta["h"]}" '
        f'alt="{html.escape(str(alt), quote=True)}" {loading} decoding="async">'
    )


def band(images: dict, slot: str, alt: str, inner: str) -> str:
    """The photo strip a day card is headed by, with `inner` laid over it.

    The thumbnail is painted underneath as the background. It is a twentieth of the
    weight and arrives first, so the band comes up as the right colours rather than as
    a grey hole that fills in — and if the full photo never arrives at all, the white
    type still has something dark under it instead of the page.
    """
    meta = images.get(slot)
    if not meta:
        return f'<div class="tp-band tp-band-bare">{inner}</div>'
    thumb_url = html.escape(PHOTO_URL.format(meta["thumb"]), quote=True)
    return (
        f'<div class="tp-band" style="background-image:url(&quot;{thumb_url}&quot;)">'
        f'{photo(images, slot, alt, "tp-band-img")}{inner}</div>'
    )


def note_count_key(n: int) -> str:
    """Arabic takes the singular noun from 11 up; Hebrew and English do not."""
    if n == 0:
        return "ui.notes.count_zero"
    if n == 1:
        return "ui.notes.count_one"
    return "ui.notes.count_many" if n < 11 else "ui.notes.count_many11"


def local_time(iso: str) -> tuple[str, str]:
    """A stored UTC timestamp as a Swiss-local date and time."""
    try:
        stamp = datetime.fromisoformat(iso)
    except ValueError:
        return iso, ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    try:
        stamp = stamp.astimezone(ZoneInfo(TRIP_TZ))
    except Exception:
        # No tz database on this machine: UTC is wrong by an hour or two, which is
        # better than failing to draw the note at all.
        stamp = stamp.astimezone(timezone.utc)
    return stamp.strftime("%Y-%m-%d"), stamp.strftime("%H:%M")


# --------------------------------------------------------------------------- #
# Stylesheet. Emitted in full on every run, with explicit values for both
# directions, so switching language cannot leave the previous one behind.
# --------------------------------------------------------------------------- #

FONT_STACK = {
    "en": "system-ui, -apple-system, 'Segoe UI', sans-serif",
    "he": "'Heebo', system-ui, -apple-system, 'Segoe UI', sans-serif",
    "ar": "'Noto Sans Arabic', system-ui, -apple-system, 'Segoe UI', sans-serif",
}
LINE_HEIGHT = {"en": "1.5", "he": "1.65", "ar": "1.9"}


def stylesheet(lang: str, rtl: bool, ink: str) -> str:
    direction = "rtl" if rtl else "ltr"
    align = "right" if rtl else "left"
    align_far = "left" if rtl else "right"
    marker = f"tp-dir-{direction}"
    tone = INK_TONES.get(ink, INK_TONES[DEFAULT_INK])

    return f"""
<span id="{marker}" hidden></span>
<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700&family=Noto+Sans+Arabic:wght@400;500;700&display=swap');

:root {{
  --surface: {tone["surface"]};
  --plane: {tone["plane"]};
  --ink: {tone["ink"]};
  --ink2: {tone["ink2"]};
  --muted: {tone["muted"]};
  --rule: {tone["rule"]};
  --line: {tone["line"]};
  --accent: {tone["accent"]};
  --accent-wash: {tone["accent_wash"]};
  --on-accent: {tone["on_accent"]};
  --warn: {tone["warn"]};
  --warn-wash: {tone["warn_wash"]};
  --on-warn: {tone["on_warn"]};
  --border: color-mix(in srgb, var(--ink) {14 if tone["scheme"] == "light" else 20}%, transparent);
  --lift: {"0 1px 2px rgba(16,32,44,.05), 0 10px 26px -20px rgba(16,32,44,.5)"
           if tone["scheme"] == "light" else
           "0 1px 2px rgba(0,0,0,.5), 0 12px 30px -22px rgba(0,0,0,1)"};
  --scrim: {".72" if tone["scheme"] == "light" else ".80"};
  --font: {FONT_STACK[lang]};
  --lh: {LINE_HEIGHT[lang]};
  /* tells the browser to paint form controls, scrollbars and the caret for this
     theme; without it a dark page keeps white dropdowns and a white scrollbar */
  color-scheme: {tone["scheme"]};
}}

/* ---- direction, set explicitly every run ---- */
html, body, .stApp,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stSidebar"] {{
  direction: {direction};
  text-align: {align};
}}
body:has(#tp-dir-rtl) .stApp,
body:has(#tp-dir-rtl) [data-testid="stMain"],
body:has(#tp-dir-rtl) [data-testid="stSidebar"] {{ direction: rtl; text-align: right; }}
body:has(#tp-dir-ltr) .stApp,
body:has(#tp-dir-ltr) [data-testid="stMain"],
body:has(#tp-dir-ltr) [data-testid="stSidebar"] {{ direction: ltr; text-align: left; }}

/* Sidebar swaps sides with the content. `direction` already mirrors a flex row's
   main axis, so the flow stays `row` in both languages — row-reverse would undo it. */
[data-testid="stAppViewContainer"] {{ flex-direction: row; }}
body:has(#tp-dir-rtl) [data-testid="stAppViewContainer"],
body:has(#tp-dir-ltr) [data-testid="stAppViewContainer"] {{ flex-direction: row; }}

/* Streamlit collapses the sidebar with translateX(-300px), which is only off-screen
   while the sidebar is on the left. With it on the right that slides the collapsed
   sidebar across the content — on a phone it showed as a strip of stray letters. Both
   rules below are guarded by the language marker, so neither can outlive its language. */
body:has(#tp-dir-rtl) [data-testid="stSidebar"][aria-expanded="false"] {{
  transform: translateX(300px);
  overflow: hidden;
}}

/* Streamlit's own sidebar chevrons point at the sidebar, so they flip with it */
[data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {{
  transform: scaleX({-1 if rtl else 1});
}}
body:has(#tp-dir-rtl) [data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
body:has(#tp-dir-rtl) [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {{
  transform: scaleX(-1);
}}
body:has(#tp-dir-ltr) [data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
body:has(#tp-dir-ltr) [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {{
  transform: scaleX(1);
}}

.stApp {{ background: var(--plane); }}
/* the sidebar is Streamlit's own chrome; keep it on the tone's surface so the cast
   is consistent rather than stopping at the edge of my markup */
[data-testid="stSidebar"] {{ background: var(--surface); }}

/* ---- Streamlit's own widgets ----
   config.toml can only name one theme, and it is baked in at deploy time. The two
   dark tones therefore have to take the widgets back by hand, or a dark page keeps
   white dropdowns and black-on-black labels. These rules are written against roles,
   so they are equally correct for the light tones and there is no dark-only branch
   that can rot unnoticed. */
html, body, .stApp, [data-testid="stSidebar"] {{ color: var(--ink); }}
[data-testid="stSidebar"] label, [data-testid="stSidebar"] p,
[data-testid="stWidgetLabel"] p {{ color: var(--ink2); }}

[data-baseweb="select"] > div, [data-baseweb="input"] > div,
[data-baseweb="base-input"], .stTextInput input, .stTextInput > div > div {{
  background: var(--surface); border-color: var(--rule); color: var(--ink);
}}
[data-baseweb="input"]:focus-within > div, .stTextInput > div > div:focus-within {{
  border-color: var(--accent);
}}
[data-baseweb="select"] div, [data-baseweb="select"] span,
[data-baseweb="select"] svg {{ color: var(--ink); fill: var(--ink); }}
.stTextInput input::placeholder {{ color: var(--muted); }}

/* the dropdown is portalled onto <body>, outside .tp, so it needs a global selector */
[data-baseweb="popover"] div[role="listbox"], [data-baseweb="menu"],
[data-baseweb="menu"] ul, [data-baseweb="popover"] > div > div {{
  background: var(--surface); color: var(--ink);
}}
[data-baseweb="menu"] li {{ color: var(--ink); }}
[data-baseweb="menu"] li[aria-selected="true"], [data-baseweb="menu"] li:hover {{
  background: var(--accent-wash); color: var(--ink);
}}

.stButton button {{
  background: var(--surface); color: var(--ink2); border: 1px solid var(--border);
}}
.stButton button:hover {{ border-color: var(--accent); color: var(--accent); }}

[data-baseweb="tab"] {{ color: var(--ink2); }}
[data-baseweb="tab"][aria-selected="true"],
[data-baseweb="tab"][aria-selected="true"] * {{ color: var(--accent); }}
[data-baseweb="tab-highlight"] {{ background: var(--accent); }}
[data-baseweb="tab-border"] {{ background: var(--rule); }}
[data-testid="stHeader"] svg,
[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stExpandSidebarButton"] svg {{ fill: var(--ink2); }}
[data-testid="stTooltipHoverTarget"] svg {{ fill: var(--muted); }}

/* keyboard focus has to stay visible once the accent is a warm red on a dark plane */
[data-testid="stSidebar"] :focus-visible, .stButton button:focus-visible,
.tp-day > summary:focus-visible, .tp-costs > summary:focus-visible {{
  outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 6px;
}}
html, body, .stApp, [data-testid="stSidebar"], .tp, .tp * {{
  font-family: var(--font);
  line-height: var(--lh);
}}
/* Streamlit ships its own heading and tab font at a specificity that beats the
   rule above; the Hebrew and Arabic faces have to win those too. */
.tp h1, .tp h2, .tp h3, .tp h4,
[data-baseweb="tab"], [data-baseweb="tab"] * {{ font-family: var(--font) !important; }}
[data-testid="stMain"] .block-container {{ padding: 1.1rem 1rem 3rem; max-width: 860px; }}
[data-testid="stHeader"] {{ background: transparent; }}

/* tab strip mirrors, same reasoning as the sidebar */
[data-baseweb="tab-list"] {{ flex-direction: row; gap: 4px; overflow-x: auto; }}
body:has(#tp-dir-rtl) [data-baseweb="tab-list"],
body:has(#tp-dir-ltr) [data-baseweb="tab-list"] {{ flex-direction: row; }}
[data-baseweb="tab"] {{
  font-family: var(--font); padding: 6px 14px; white-space: nowrap; flex: 0 0 auto;
}}
[data-baseweb="tab"] p {{ font-size: 0.92rem; }}
[data-testid="stSidebar"] label, [data-testid="stSidebar"] p {{ font-family: var(--font); }}

/* ---- app content ---- */
.tp {{ color: var(--ink); }}
.tp .num {{ font-variant-numeric: tabular-nums; unicode-bidi: isolate; }}

.tp-head {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
  padding: 0; margin-bottom: 14px; overflow: hidden; box-shadow: var(--lift);
}}
/* The opening photo runs to the card edge and carries the title. The scrim is what
   makes white type safe over an unknown picture, so it is not decoration — without it
   the title's contrast would depend on whichever photo happened to be chosen. */
.tp-hero {{ position: relative; }}
/* Streamlit ships `.st-emotion-cache-… img {{ object-fit: scale-down }}`, which beats a
   bare class and letterboxes the photo inside the banner with a pale gap down one side.
   Qualifying the selector with the element outranks it without reaching for !important. */
.tp-hero > img.tp-hero-img {{
  display: block; width: 100%; height: clamp(148px, 26vw, 220px); object-fit: cover;
}}
.tp-hero::after {{
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(to top,
    rgba(0,0,0,.74) 0%, rgba(0,0,0,.42) 34%, rgba(0,0,0,.06) 68%, rgba(0,0,0,0) 100%);
}}
.tp-hero-text {{ position: absolute; z-index: 1; bottom: 12px; inset-inline: 16px; }}
.tp-hero-text h1 {{
  color: #fff; font-size: 1.7rem; font-weight: 700; margin: 0 0 3px;
  letter-spacing: -0.01em; text-shadow: 0 1px 10px rgba(0,0,0,.5);
}}
.tp-hero-text .sub {{
  color: rgba(255,255,255,.94); font-size: 0.88rem; margin: 0;
  text-shadow: 0 1px 8px rgba(0,0,0,.55);
}}
.tp-hero-text .sub .dot {{ color: rgba(255,255,255,.5); padding: 0 2px; }}
.tp-stats {{ display: flex; flex-wrap: wrap; gap: 10px; padding: 13px; }}
.tp-stat {{
  flex: 1 1 150px; border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 12px; background: var(--plane);
}}
.tp-stat .lbl {{ display: block; color: var(--muted); font-size: 0.72rem;
  text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 3px; }}
.tp-stat .val {{ font-size: 1.35rem; font-weight: 600; }}
.tp-stat .alt {{ display: block; color: var(--ink2); font-size: 0.78rem; margin-top: 3px; }}

.tp h2 {{ font-size: 1.02rem; font-weight: 600; margin: 22px 0 4px; letter-spacing: 0.01em; }}
.tp .note {{ color: var(--ink2); font-size: 0.86rem; margin: 0 0 10px; }}

.tp-list {{ list-style: none; padding: 0; margin: 0; }}
.tp-list li {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 11px 13px; margin-bottom: 7px; font-size: 0.92rem;
}}
.tp-list.warn li {{ border-inline-start: 3px solid var(--warn); }}
.tp-list li .when {{ display: block; color: var(--muted); font-size: 0.74rem; margin-bottom: 3px; }}

/* ---- day cards ----
   The photograph is the card's header rather than a thumbnail beside it: the date,
   title and total sit on the picture. Closed, the band is a strip; open, it grows and
   gives the photo real room, which is the only animation on the page.

   Everything written on the band is white against the scrim rather than against the
   theme, and that is deliberate — the band's ground is a photograph in all four
   themes, so a role colour would be the wrong answer there. The scrim is what makes
   the white safe over a picture nobody has seen yet, so it is structure, not polish. */
.tp-day {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
  margin-bottom: 10px; overflow: hidden; box-shadow: var(--lift);
}}
.tp-day[data-float="1"] {{ border-color: var(--warn); }}
.tp-day > summary {{
  list-style: none; cursor: pointer; padding: 0; position: relative; display: block;
}}
.tp-day > summary::-webkit-details-marker {{ display: none; }}

.tp-band {{
  position: relative; display: flex; align-items: flex-end; overflow: hidden;
  min-height: 116px; padding: 14px 44px 13px 16px;
  background-color: #0d1418; background-size: cover; background-position: center;
  transition: min-height .24s ease;
}}
.tp-day[dir="rtl"] .tp-band {{ padding: 14px 16px 13px 44px; }}
.tp-day[open] .tp-band {{ min-height: 214px; }}
.tp-band-bare {{ background: var(--ink); }}
/* qualified for the same reason as the hero image: Streamlit ships an `img` rule at a
   specificity that beats a bare class and would letterbox the photo */
.tp-band > img.tp-band-img {{
  position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover;
  transition: transform .35s ease;
}}
.tp-band::after {{
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(to top,
    rgba(8,14,18,var(--scrim)) 0%, rgba(8,14,18,.64) 38%,
    rgba(8,14,18,.30) 68%, rgba(8,14,18,0) 100%);
}}
.tp-day > summary:hover .tp-band > img.tp-band-img {{ transform: scale(1.03); }}
.tp-sum {{ position: relative; z-index: 1; min-width: 0; width: 100%; }}

.tp-day > summary::after {{
  content: ""; position: absolute; top: 19px; z-index: 2; width: 9px; height: 9px;
  border-right: 2px solid rgba(255,255,255,.9);
  border-bottom: 2px solid rgba(255,255,255,.9);
  transform: rotate(-45deg); transition: transform .2s ease;
}}
.tp-day[dir="ltr"] > summary::after {{ right: 18px; }}
.tp-day[dir="rtl"] > summary::after {{ left: 18px; transform: rotate(135deg); }}
.tp-day[dir="ltr"][open] > summary::after {{ transform: rotate(45deg); }}
.tp-day[dir="rtl"][open] > summary::after {{ transform: rotate(45deg); }}

.tp-meta {{ display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin-bottom: 5px; }}
.tp-meta .date {{ color: var(--ink2); font-size: 0.79rem; font-variant-numeric: tabular-nums; }}
.tp-meta .dow {{ color: var(--muted); font-size: 0.79rem; }}
.chip {{
  font-size: 0.7rem; padding: 2px 8px; border-radius: 999px;
  background: var(--plane); border: 1px solid var(--border); color: var(--ink2);
}}
.chip.float {{ background: var(--warn); border-color: var(--warn); color: var(--on-warn); font-weight: 600; }}
.chip.today {{ background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }}
.tp-day[data-today="1"] {{ border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent); }}

/* on the band, over the photograph */
.tp-band .tp-meta .date {{ color: #fff; text-shadow: 0 1px 6px rgba(0,0,0,.8); }}
.tp-band .tp-meta .dow {{ color: rgba(255,255,255,.8); text-shadow: 0 1px 6px rgba(0,0,0,.8); }}
.tp-band .chip {{
  background: rgba(8,14,18,.5); border-color: rgba(255,255,255,.34); color: #fff;
}}
.tp-band .chip.today {{ background: var(--accent); border-color: var(--accent); color: var(--on-accent); }}
.tp-band .chip.float {{ background: var(--warn); border-color: var(--warn); color: var(--on-warn); }}
.tp-day .title {{
  font-size: 1.12rem; font-weight: 700; margin: 0 0 6px; letter-spacing: -0.012em;
  color: #fff; text-shadow: 0 1px 14px rgba(0,0,0,.5);
}}
.tp-day .headfoot {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: baseline; }}
.tp-day .headfoot .total {{
  font-size: 0.88rem; font-weight: 700; color: #fff; text-shadow: 0 1px 8px rgba(0,0,0,.75);
}}
.tp-day .headfoot .drive {{
  font-size: 0.79rem; color: rgba(255,255,255,.82); text-shadow: 0 1px 6px rgba(0,0,0,.8);
}}

@media (prefers-reduced-motion: reduce) {{
  .tp-band, .tp-band > img.tp-band-img, .tp-day > summary::after {{ transition: none; }}
  .tp-day > summary:hover .tp-band > img.tp-band-img {{ transform: none; }}
}}

.tp-body {{ padding: 2px 15px 15px; border-top: 1px solid var(--rule); }}
.tp-lbl {{
  display: block; color: var(--muted); font-size: 0.71rem; text-transform: uppercase;
  letter-spacing: 0.06em; margin: 13px 0 3px;
}}
.tp-body p {{ margin: 0; font-size: 0.91rem; }}
.tp-move {{
  background: var(--accent-wash); border-radius: 9px; padding: 11px 12px;
  margin-top: 4px; font-size: 0.91rem;
}}
.tp-float {{
  background: var(--warn-wash); border: 1px solid var(--warn); border-radius: 9px;
  padding: 11px 12px; margin-top: 12px; font-size: 0.89rem;
}}
/* Where the reader is standing relative to the trip. Quiet on purpose: it sits above
   nine day cards and is orientation, not an announcement. */
.tp-stage {{
  color: var(--ink2); font-size: 0.86rem; letter-spacing: 0.01em;
  padding: 2px 0 8px; border-bottom: 1px solid var(--rule); margin-bottom: 4px;
}}
.tp-tips {{ list-style: none; padding: 0; margin: 2px 0 0; }}
.tp-tips li {{
  font-size: 0.89rem; padding: 5px 0 5px 0; border-bottom: 1px solid var(--rule);
}}
.tp-tips li:last-child {{ border-bottom: none; }}

.tp-costs {{ margin-top: 14px; border-top: 1px solid var(--rule); padding-top: 10px; }}
.tp-costs > summary {{
  cursor: pointer; font-size: 0.8rem; color: var(--accent); list-style: none;
}}
.tp-costs > summary::-webkit-details-marker {{ display: none; }}

.tp-book {{ list-style: none; padding: 0; margin: 0 0 4px; }}
.tp-book li {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 11px 13px; margin-bottom: 7px; font-size: 0.92rem;
}}
.tp-book li[data-when="now"] {{ border-inline-start: 3px solid var(--warn); }}
.tp-book li[data-when="done"] {{ opacity: 0.72; }}
.tp-book .row {{ display: flex; gap: 10px; align-items: baseline; }}
.tp-book .what {{ flex: 1 1 auto; min-width: 0; font-weight: 600; }}
.tp-book .amt {{ flex: 0 0 auto; font-variant-numeric: tabular-nums; color: var(--ink2); }}
.tp-book .why {{ display: block; margin-top: 5px; font-size: 0.85rem; color: var(--ink2); }}
.tp-book .go {{
  display: inline-block; margin-top: 8px; padding: 4px 11px; border-radius: 999px;
  background: var(--accent-wash); color: var(--accent); font-size: 0.78rem;
  font-weight: 600; text-decoration: none; border: 1px solid var(--border);
}}
.tp-when {{ display: flex; gap: 10px; align-items: baseline; margin: 18px 0 2px; }}
.tp-when h3 {{ flex: 1 1 auto; margin: 0; font-size: 0.98rem; }}
.tp-when .amt {{ font-variant-numeric: tabular-nums; color: var(--muted); font-size: 0.84rem; }}
.tp-when + .note {{ margin-top: 2px; }}

/* ---- tables ---- */
.tp-scroll {{ overflow-x: auto; }}
table.tp-tbl {{ width: 100%; border-collapse: collapse; margin-top: 9px; font-size: 0.85rem; }}
table.tp-tbl th {{
  text-align: {align}; color: var(--muted); font-weight: 500; font-size: 0.72rem;
  text-transform: uppercase; letter-spacing: 0.05em;
  padding: 5px 8px; border-bottom: 1px solid var(--line);
}}
table.tp-tbl td {{ padding: 7px 8px; border-bottom: 1px solid var(--rule); vertical-align: top; }}
table.tp-tbl th.n, table.tp-tbl td.n {{ text-align: {align_far}; white-space: nowrap; }}
table.tp-tbl tr.sec td {{
  color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
  padding-top: 11px; border-bottom: none;
}}
table.tp-tbl tfoot td {{ font-weight: 600; border-bottom: none; border-top: 1px solid var(--line); }}
table.tp-tbl td.wrap {{ min-width: 150px; }}

/* ---- bars ---- */
.tp-bars {{ margin-top: 10px; }}
.tp-brow {{ margin-bottom: 11px; }}
.tp-blab {{ font-size: 0.78rem; color: var(--ink2); margin-bottom: 3px; }}
.tp-blab .d {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
/* Grid, not flex: the value label gets its own column, so the longest bar cannot
   squeeze its own label out of the track. Every bar scales inside the same 1fr,
   so reserving that space costs no accuracy. `justify-self: start` and the logical
   radii both follow `direction`, so the bar grows from the right in he/ar. */
.tp-btrack {{ display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 8px; }}
.tp-bfill {{
  height: 14px; background: var(--accent); justify-self: start; min-width: 0;
  border-start-start-radius: 0; border-end-start-radius: 0;
  border-start-end-radius: 4px; border-end-end-radius: 4px;
}}
.tp-bval {{ font-size: 0.79rem; color: var(--ink2); white-space: nowrap;
  font-variant-numeric: tabular-nums; }}

.tp-saving {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 11px 13px; margin-top: 10px; display: flex; justify-content: space-between;
  gap: 10px; font-size: 0.9rem; align-items: baseline;
}}
.tp-saving .v {{ font-weight: 600; }}
.tp-recon {{ color: var(--muted); font-size: 0.78rem; margin-top: 10px; }}

/* ---- notes ---- */
/* Each day is a card with its notes drawer welded to the bottom edge: the card loses
   its lower corners, the drawer loses its upper border, and the gap Streamlit puts
   between elements is closed so the two read as one object rather than two. */
/* The container IS the flex column that stacks card, drawer, card, drawer. Closing the
   gap on that element alone is the point — an earlier descendant selector also closed
   the gaps inside each drawer, which slid the buttons up into the note text. */
[data-testid="stVerticalBlock"].st-key-tp-days {{ gap: 0; }}
.st-key-tp-days .tp-day {{ border-radius: 14px 14px 0 0; margin-bottom: 0; }}
.st-key-tp-days [data-testid="stExpander"] {{ margin-bottom: 10px; }}
.st-key-tp-days [data-testid="stExpander"] details {{
  border: 1px solid var(--border); border-top: none; border-radius: 0 0 14px 14px;
  background: var(--surface); box-shadow: var(--lift);
}}
.st-key-tp-days [data-testid="stExpander"] summary {{ padding-block: 4px; }}
.st-key-tp-days [data-testid="stExpander"] summary p {{
  font-size: 0.8rem; color: var(--ink2);
}}
.st-key-tp-days .stButton button {{
  min-height: 26px; padding: 0 7px; font-size: 0.8rem;
}}
.tp-note {{ border-top: 1px solid var(--rule); padding: 9px 0 1px; }}
.tp-note:first-child {{ border-top: none; padding-top: 2px; }}
.tp-note .who {{ font-size: 0.86rem; font-weight: 600; }}
.tp-note .who.mine {{ color: var(--accent); }}
.tp-note .when {{ color: var(--muted); font-size: 0.73rem; margin-inline-start: 7px; }}
/* pre-wrap keeps the writer's line breaks; anywhere stops one long unbroken word
   from pushing the card wider than the phone */
.tp-note .body {{
  font-size: 0.9rem; margin: 3px 0 0; white-space: pre-wrap; overflow-wrap: anywhere;
}}
.tp-note .hearts {{ color: var(--muted); font-size: 0.76rem; }}
.tp-empty {{ color: var(--muted); font-size: 0.86rem; margin: 2px 0 6px; }}

/* ---- photo credits ---- */
.tp-credits {{ list-style: none; padding: 0; margin: 0; }}
.tp-credits li {{
  font-size: 0.8rem; color: var(--ink2); padding: 6px 0;
  border-bottom: 1px solid var(--rule);
}}
.tp-credits li:last-child {{ border-bottom: none; }}
.tp-credits .lbl {{ color: var(--muted); }}
.tp-credits a {{ color: var(--accent); }}

@media (max-width: 560px) {{
  .tp-hero-text h1 {{ font-size: 1.32rem; }}
  .tp-hero-text .sub {{ font-size: 0.8rem; }}
  .tp-band {{ min-height: 98px; padding: 12px 36px 11px 13px; }}
  .tp-day[dir="rtl"] .tp-band {{ padding: 12px 13px 11px 36px; }}
  .tp-day[open] .tp-band {{ min-height: 168px; }}
  .tp-day .title {{ font-size: 1.02rem; }}
  [data-testid="stMain"] .block-container {{ padding: 0.8rem 0.7rem 2.5rem; }}
  .tp-head {{ padding: 15px 14px 12px; }}
  .tp-head h1 {{ font-size: 1.4rem; }}
  .tp-stat {{ flex: 1 1 100%; }}
  .tp-body {{ padding: 2px 13px 13px; }}
  table.tp-tbl {{ font-size: 0.8rem; }}
  /* let the three-column tables fit a 390px phone rather than needing a swipe */
  table.tp-tbl td.wrap {{ min-width: 100px; }}
  table.tp-tbl th, table.tp-tbl td {{ padding-inline: 6px; }}
  /* the four tabs overflow a 390px phone by 4-14px depending on language, which
     crops the last label. Tightening the padding buys 36px, clearing all three. */
  [data-baseweb="tab"] {{ padding: 6px 10px; }}
  [data-baseweb="tab-list"] {{ gap: 2px; }}
}}
</style>
"""


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def render_header(d, T, C, tx, dirattr, cur, fx, images, show_prices=True):
    trip, totals = d["trip"], d["totals"]
    party = trip["party"]
    per_person = {
        "CHF": money(totals["per_person_chf"], "CHF", fx),
        "EUR": f"EUR {totals['per_person_eur']:,.0f}",
        "ILS": f"ILS {totals['per_person_ils']:,.0f}",
    }
    others = T("ui.sep").join(v for k, v in per_person.items() if k != cur)
    dates = T("ui.range", start=trip["start"], end=trip["end"])

    title = C("trip.title", trip["title"])
    # The two big figures are the thing people asked not to be met by every time, so
    # they come out of the header entirely rather than being greyed or blurred. The
    # Costs tab still has them: hiding them here makes looking at them a choice.
    stats = "" if not show_prices else (
        f'<div class="tp-stats">'
        f'<div class="tp-stat"><span class="lbl">{tx(T("ui.header.trip_total"))}</span>'
        f'<span class="val">{num(money(totals["grand_total_chf"], cur, fx))}</span></div>'
        f'<div class="tp-stat"><span class="lbl">{tx(T("ui.header.per_person"))}</span>'
        f'<span class="val">{num(per_person[cur])}</span>'
        f'<span class="alt">{num(others)}</span></div>'
        f'</div>'
    )
    block(
        f'<div class="tp" {dirattr}><div class="tp-head">'
        f'<div class="tp-hero">'
        f'{photo(images, "hero", title, "tp-hero-img", eager=True)}'
        f'<div class="tp-hero-text">'
        f'<h1>{tx(title)}</h1>'
        f'<p class="sub">{num(dates)}<span class="dot">{tx(T("ui.sep"))}</span>'
        f'{tx(T("ui.header.nights", n=trip["nights"]))}'
        f'<span class="dot">{tx(T("ui.sep"))}</span>'
        f'{tx(T("ui.header.party", total=party["total"]))}</p>'
        f'</div></div>'
        f'{stats}</div></div>'
    )


def render_credits(images, T, tx, dirattr, day_count):
    """Who took the photographs, and under what licence.

    Several of these are share-alike, so the credit is an obligation rather than a
    courtesy — and cropping them makes an adapted work, which is what "modified" says.
    """
    rows = []
    for slot, meta in images.items():
        if slot == "hero":
            label = T("ui.credits.hero")
        elif slot.startswith("day") and slot[3:].isdigit():
            index = int(slot[3:])
            if index >= day_count:
                continue
            label = T("ui.credits.day", n=index + 1)
        else:
            continue
        credit = meta["credit"]
        licence = html.escape(credit["licence"], quote=True)
        if credit.get("licence_url"):
            licence = (f'<a href="{html.escape(credit["licence_url"], quote=True)}"'
                       f' target="_blank" rel="noopener">{licence}</a>')
        rows.append(
            f'<li><span class="lbl">{tx(label)}</span>{tx(T("ui.sep"))}'
            f'<a href="{html.escape(credit["page"], quote=True)}" target="_blank"'
            f' rel="noopener">{html.escape(credit["author"], quote=True)}</a>'
            f'{tx(T("ui.sep"))}{licence}</li>'
        )
    block(
        f'<div class="tp" {dirattr}>'
        f'<h2>{tx(T("ui.credits.title"))}</h2>'
        f'<p class="note">{tx(T("ui.credits.note"))}</p>'
        f'<ul class="tp-credits">{"".join(rows)}</ul></div>'
    )


# The booking board's groups, in the order they are shown. Named here rather than
# inline because check.py builds the same "ui.booking.when.*" keys from this tuple:
# they are the one set of labels app.py asks for without a literal next to a T(.
BOOKING_WHEN = ("done", "now", "forecast", "soon", "walkup")

def render_overview(d, T, C, tx, dirattr, critical, cur, fx, show_prices=True):
    """Everything that happens before the trip: dangers, premises, then what to book."""
    trip = d["trip"]
    items = "".join(
        f"<li>{tx(C(f'trip.assumptions.{i}', s))}</li>"
        for i, s in enumerate(trip["assumptions"])
    )
    # The board is grouped by WHEN rather than numbered 1..n, because the thing that
    # was hard about this list was never the order - it was that eighteen items all
    # looked equally urgent when only three of them are.
    groups = []
    for when in BOOKING_WHEN:
        rows = [(i, b) for i, b in enumerate(d["booking_order"]) if b["when"] == when]
        if not rows:
            continue
        subtotal = ""
        if show_prices:
            subtotal = (
                f'<span class="amt">'
                f'{num(money(sum(b["chf"] for _, b in rows), cur, fx))}</span>'
            )
        cards = ""
        for i, b in rows:
            price = ""
            if show_prices:
                shown = (
                    tx(T("ui.booking.free")) if not b["chf"]
                    else num(money(b["chf"], cur, fx))
                )
                price = f'<span class="amt">{shown}</span>'
            link = ""
            if b.get("url"):
                link = (
                    f'<a class="go" href="{html.escape(b["url"], quote=True)}"'
                    f' target="_blank" rel="noopener">{tx(T("ui.booking.go"))}</a>'
                )
            cards += (
                f'<li data-when="{when}">'
                f'<div class="row"><span class="what">'
                f'{tx(C(f"booking_order.{i}.item", b["item"]))}</span>{price}</div>'
                f'<span class="why">{tx(C(f"booking_order.{i}.note", b["note"]))}</span>'
                f'{link}</li>'
            )
        groups.append(
            f'<div class="tp-when"><h3>{tx(T(f"ui.booking.when.{when}"))}</h3>'
            f'{subtotal}</div>'
            f'<p class="note">{tx(T(f"ui.booking.note.{when}"))}</p>'
            f'<ul class="tp-book">{cards}</ul>'
        )
    steps = "".join(groups)

    # A week out the question stopped being "what is on the list" and became "what is
    # still on it", and eighteen cards under four headings do not answer that at a
    # glance. Counted from the data rather than written down, so it cannot go stale the
    # next time an item moves out of "book today".
    outstanding = [b for b in d["booking_order"] if b["when"] in ("now", "forecast", "soon")]
    at_window = [b for b in d["booking_order"] if b["when"] == "walkup"]
    settled = [b for b in d["booking_order"] if b["when"] == "done"]
    tally = (
        f'<div class="tp-saving"><span>'
        f'{tx(T("ui.booking.tally", done=len(settled), left=len(outstanding), walkup=len(at_window)))}'
        f'</span>'
    )
    if show_prices:
        tally += (
            f'<span class="v">'
            f'{num(money(sum(b["chf"] for b in outstanding + at_window), cur, fx))} '
            f'{tx(T("ui.booking.tally_left"))}</span>'
        )
    tally += "</div>"

    # Sorted by the day each tip belongs to rather than by the order somebody appended
    # them to meta.critical_tips. Every line prints its own date, so one entry out of
    # sequence reads as a mistake in the trip rather than a mistake in the list — which
    # is exactly how 10 September came to sit above 9 September on the live page.
    warn = []
    for path in sorted(critical, key=lambda p: (int(p.split(".")[1]), int(p.split(".")[3]))):
        _, day_i, _, tip_i = path.split(".")
        day = d["days"][int(day_i)]
        text = C(path, day["tips"][int(tip_i)])
        when = f'{num(day["date"])} <span class="dot"></span> {tx(C(f"days.{day_i}.dow", day["dow"]))}'
        warn.append(f'<li><span class="when">{when}</span>{tx(text)}</li>')

    block(
        f'<div class="tp" {dirattr}>'
        f'<h2>{tx(T("ui.overview.warnings"))}</h2>'
        f'<p class="note">{tx(T("ui.overview.warnings_note"))}</p>'
        f'<ul class="tp-list warn">{"".join(warn)}</ul>'
        f'<h2>{tx(T("ui.overview.assumptions"))}</h2>'
        f'<ul class="tp-list">{items}</ul>'
        f'<h2>{tx(T("ui.booking.title"))}</h2>'
        f'<p class="note">{tx(T("ui.booking.note"))}</p>'
        f'{tally}'
        f'{steps}'
        f'</div>'
    )


def day_cost_table(day, i, n, T, C, tx, cur, fx):
    rows = []
    if day["per_person"]:
        rows.append(f'<tr class="sec"><td colspan="3">{tx(T("ui.cost.per_person_header", n=n))}</td></tr>')
        for j, item in enumerate(day["per_person"]):
            rows.append(
                f'<tr><td class="wrap">{tx(C(f"days.{i}.per_person.{j}.item", item["item"]))}</td>'
                f'<td class="n">{num(money(item["chf"], cur, fx))}</td>'
                f'<td class="n">{num(money(item["chf"] * n, cur, fx))}</td></tr>'
            )
    if day["group"]:
        rows.append(f'<tr class="sec"><td colspan="3">{tx(T("ui.cost.group_header"))}</td></tr>')
        for j, item in enumerate(day["group"]):
            rows.append(
                f'<tr><td class="wrap">{tx(C(f"days.{i}.group.{j}.item", item["item"]))}</td>'
                f'<td class="n"></td>'
                f'<td class="n">{num(money(item["chf"], cur, fx))}</td></tr>'
            )
    if not rows:
        return ""
    return (
        f'<details class="tp-costs"><summary>{tx(T("ui.day.show_costs"))}</summary>'
        f'<div class="tp-scroll"><table class="tp-tbl"><thead><tr>'
        f'<th scope="col">{tx(T("ui.cost.item"))}</th><th scope="col" class="n">{tx(T("ui.cost.each"))}</th>'
        f'<th scope="col" class="n">{tx(T("ui.cost.amount"))}</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        f'<tfoot><tr><td>{tx(T("ui.day.total"))}</td><td class="n"></td>'
        f'<td class="n">{num(money(day["day_total_chf"], cur, fx))}</td></tr></tfoot>'
        f'</table></div></details>'
    )


def day_card_html(d, i, T, C, tx, dirattr, cur, fx, expand_all, images, today,
                  notes_n=0, show_prices=True):
    """One day card, as markup. Pure — no Streamlit, so check.py can render and read it."""
    day = d["days"][i]
    n = d["trip"]["party"]["total"]
    floating = bool(day.get("floating"))
    is_today = day["date"] == today

    chips = f'<span class="chip">{tx(C(f"days.{i}.leg", day["leg"]))}</span>'
    if is_today:
        chips = f'<span class="chip today">{tx(T("ui.day.today_badge"))}</span>' + chips
    if floating:
        chips += f'<span class="chip float">{tx(T("ui.day.floating_badge"))}</span>'
    if notes_n:
        chips += (f'<span class="chip">'
                  f'{tx(T(note_count_key(notes_n), n=notes_n))}</span>')

    drive = (
        T("ui.day.drive", km=day["drive_km"], min=day["drive_min"])
        if day["drive_km"] else T("ui.day.no_drive")
    )
    title = C(f"days.{i}.title", day["title"])
    total = "" if not show_prices else (
        f'<span class="total">{tx(T("ui.day.total"))} '
        f'{num(money(day["day_total_chf"], cur, fx))}</span>'
    )
    head = (
        "<summary>" + band(images, f"day{i}", title,
            f'<div class="tp-sum">'
            f'<div class="tp-meta"><span class="date">{num(day["date"])}</span>'
            f'<span class="dow">{tx(C(f"days.{i}.dow", day["dow"]))}</span>{chips}</div>'
            f'<div class="title">{tx(title)}</div>'
            f'<div class="headfoot">{total}'
            f'<span class="drive">{tx(drive)}</span></div></div>'
        ) + "</summary>"
    )

    body = []
    if floating:
        body.append(f'<div class="tp-float">{tx(T("ui.day.floating_note"))}</div>')
    body.append(f'<span class="tp-lbl">{tx(T("ui.day.movement"))}</span>')
    body.append(f'<div class="tp-move">{tx(C(f"days.{i}.movement", day["movement"]))}</div>')
    if day.get("sleep"):
        body.append(
            f'<span class="tp-lbl">{tx(T("ui.day.sleep"))}</span>'
            f'<p>{tx(C(f"days.{i}.sleep", day["sleep"]))}</p>'
        )
    # One description, not two columns. The old split was by person - parents on the
    # left, adults on the right - and the premise turned out to be wrong: the two in
    # their sixties walk better than the three in their thirties. So each day now
    # runs the level version first and the harder add-ons after it, and nobody is
    # assigned to either half.
    if day.get("on_foot"):
        body.append(
            f'<span class="tp-lbl">{tx(T("ui.day.on_foot"))}</span>'
            f'<p>{tx(C(f"days.{i}.on_foot", day["on_foot"]))}</p>'
        )
    if day["tips"]:
        tips = "".join(
            f"<li>{tx(C(f'days.{i}.tips.{k}', t))}</li>" for k, t in enumerate(day["tips"])
        )
        body.append(
            f'<span class="tp-lbl">{tx(T("ui.day.tips"))}</span>'
            f'<ul class="tp-tips">{tips}</ul>'
        )
    if show_prices:
        body.append(day_cost_table(day, i, n, T, C, tx, cur, fx))

    return (
        f'<div class="tp" {dirattr}>'
        f'<details class="tp-day" {dirattr} data-float="{int(floating)}"'
        f' data-today="{int(is_today)}"{" open" if is_today or expand_all else ""}>'
        f'{head}'
        f'<div class="tp-body">{"".join(body)}</div></details></div>'
    )


def note_html(note, T, tx, dirattr, who):
    """One note. The body is escaped, and dir="auto" lets a Hebrew note read correctly
    inside an English page and the other way round."""
    day_str, time_str = local_time(note.created)
    when = T("ui.notes.when", date=day_str, time=time_str)
    mine = notes_store.norm_author(who) == notes_store.norm_author(note.author)
    # said in words, not only in colour — colour on its own is not a label
    yours = f' ({tx(T("ui.notes.you"))})' if mine else ""
    hearts = (f'<span class="hearts">{num("♥ " + str(len(note.likes)))}</span>'
              if note.likes else "")
    return (
        f'<div class="tp" {dirattr}><div class="tp-note">'
        f'<span class="who{" mine" if mine else ""}" dir="auto">'
        f'{html.escape(note.author, quote=True)}{yours}</span>'
        f'<span class="when">{tx(when)}</span> {hearts}'
        f'<p class="body" dir="auto">{html.escape(note.body, quote=True)}</p>'
        f'</div></div>'
    )


def notes_drawer(day_key, day_notes, store, T, tx, dirattr, who):
    """The notes attached to one day: read them, add one, agree with one, remove yours.

    Streamlit widgets cannot live inside the day card, which is a single block of
    markup — so the drawer is welded to the underside of the card with CSS instead.
    """
    count = len(day_notes)
    with st.expander(T(note_count_key(count), n=count),
                     icon=":material/chat_bubble_outline:"):
        if day_notes:
            for note in day_notes:
                block(note_html(note, T, tx, dirattr, who))
                mine = notes_store.norm_author(who) == notes_store.norm_author(note.author)
                with st.container(horizontal=True, gap="small"):
                    st.button(
                        T("ui.notes.like"), key=f"like-{note.id}", type="tertiary",
                        icon=":material/favorite:" if note.liked_by(who) else None,
                        on_click=act_like, args=(store, note, who, day_key),
                    )
                    if mine:
                        st.button(
                            T("ui.notes.delete"), key=f"del-{note.id}", type="tertiary",
                            on_click=act_delete, args=(store, note.id, who, day_key),
                        )
            st.caption(T("ui.notes.tz"))
        else:
            block(f'<div class="tp" {dirattr}>'
                  f'<p class="tp-empty">{tx(T("ui.notes.empty"))}</p></div>')

        box = f"note-box-{day_key}"
        st.text_area(
            T("ui.notes.title"), key=box, height=80,
            max_chars=notes_store.MAX_BODY, label_visibility="collapsed",
            placeholder=T("ui.notes.placeholder"),
        )
        st.button(T("ui.notes.post"), key=f"post-{day_key}", type="primary",
                  on_click=act_post, args=(store, day_key, who, box))

        problem = st.session_state.get("notes_problem")
        if problem and problem[0] == day_key:
            st.warning(T(problem[1]))
            st.session_state["notes_problem"] = None


# --- what the buttons do ----------------------------------------------------
# All three run as callbacks, before the next render. That is what lets act_post
# empty the text box: after the widget has drawn, its value is read-only.

def _fail(day_key: str, exc: notes_store.StoreError) -> None:
    st.session_state["notes_problem"] = (day_key, exc.key)


def act_post(store, day_key, who, box) -> None:
    now = time.monotonic()
    if now - st.session_state.get("last_post", -99.0) < POST_COOLDOWN_S:
        return _fail(day_key, notes_store.StoreError("ui.notes.err_toofast"))
    try:
        store.add(day_key, who, st.session_state.get(box, ""))
    except notes_store.StoreError as exc:
        return _fail(day_key, exc)
    st.session_state["last_post"] = now
    st.session_state[box] = ""
    read_notes.clear()


def act_like(store, note, who, day_key) -> None:
    try:
        store.set_like(note.id, who, not note.liked_by(who))
    except notes_store.StoreError as exc:
        return _fail(day_key, exc)
    read_notes.clear()


def act_delete(store, note_id, who, day_key) -> None:
    try:
        store.delete(note_id, who)
    except notes_store.StoreError as exc:
        return _fail(day_key, exc)
    read_notes.clear()


# Named here rather than only inside trip_stage() for the same reason as BOOKING_WHEN:
# the key is chosen at runtime, so no literal sits next to a T( for check.py to find.
STAGE_KEYS = ("ui.day.countdown", "ui.day.position")


def trip_stage(d, today_iso):
    """Where the reader is standing relative to the trip: a countdown before it, a
    position inside it, nothing once it is over. Returns (key, kwargs) for T(), or None.

    Nine cards all look alike on a phone. Before the trip the useful fact is how long
    is left; during it, which of the ten days this is — the today badge says a card is
    today but not that today is the fifth of ten.
    """
    today = date.fromisoformat(today_iso)
    start = date.fromisoformat(d["days"][0]["date"])
    end = date.fromisoformat(d["days"][-1]["date"])
    if today < start:
        return STAGE_KEYS[0], {"days": (start - today).days}
    if today <= end:
        return STAGE_KEYS[1], {"n": (today - start).days + 1, "of": len(d["days"])}
    return None


def render_days(d, T, C, tx, dirattr, cur, fx, expand_all, images, notes, store, who,
                show_prices=True):
    # During the trip the app is opened on a phone to answer "what are we doing now",
    # so today's card is marked and starts open. Outside the trip nothing matches.
    today = date.today().isoformat()
    stage = trip_stage(d, today)
    if stage:
        block(f'<div class="tp" {dirattr}>'
              f'<div class="tp-stage">{tx(T(stage[0], **stage[1]))}</div></div>')
    # Said once, above the days. Repeating it inside all nine drawers was louder than
    # the notes themselves.
    if store is not None and not getattr(store, "shared", False):
        st.caption(T("ui.notes.local"))
    # The welded-drawer styling hangs off this key. With no store there is no drawer to
    # weld to, so the plain key leaves the cards their own four corners.
    with st.container(key="tp-days" if store is not None else "tp-days-plain"):
        for i, day in enumerate(d["days"]):
            day_notes = notes.get(day["date"], [])
            block(day_card_html(d, i, T, C, tx, dirattr, cur, fx, expand_all,
                                images, today, len(day_notes), show_prices))
            # No store means no drawer at all, rather than buttons that fail on click.
            if store is not None:
                notes_drawer(day["date"], day_notes, store, T, tx, dirattr, who)


def bars(rows, cur, fx):
    """rows: list of (label_html, chf). Single hue, direct value labels, no axis."""
    peak = max((v for _, v in rows), default=0) or 1
    out = []
    for label, value in rows:
        width = max(0.0, value / peak * 100.0)
        out.append(
            f'<div class="tp-brow"><div class="tp-blab">{label}</div>'
            f'<div class="tp-btrack"><div class="tp-bfill" style="width:{width:.2f}%"></div>'
            f'<span class="tp-bval">{num(money(value, cur, fx))}</span></div></div>'
        )
    return f'<div class="tp-bars">{"".join(out)}</div>'


def render_costs(d, recon, T, C, tx, dirattr, cur, fx, sep, total_keys):
    n = d["trip"]["party"]["total"]
    totals = d["totals"]

    day_rows = [
        (
            f'<span class="d">{num(day["date"])}</span> {tx(sep)}'
            f'{tx(C(f"days.{i}.dow", day["dow"]))} {tx(sep)}'
            f'{tx(C(f"days.{i}.leg", day["leg"]))}',
            day["day_total_chf"],
        )
        for i, day in enumerate(d["days"])
    ]

    ticket_rows = []
    for i, day in enumerate(d["days"]):
        for j, item in enumerate(day["per_person"]):
            ticket_rows.append(
                f'<tr><td class="wrap">{tx(C(f"days.{i}.per_person.{j}.item", item["item"]))}</td>'
                f'<td class="n">{num(money(item.get("full", item["chf"]), cur, fx))}</td>'
                f'<td class="n">{num(money(item["chf"], cur, fx))}</td></tr>'
            )
    full_pp = totals["tickets_per_person_full_chf"]
    half_pp = totals["tickets_per_person_half_chf"]

    cat_rows = [(tx(T(f"ui.totals.{k}")), totals[k]) for k in total_keys]

    wide_rows = []
    for i, item in enumerate(d["trip_wide_costs"]):
        detail = C(f"trip_wide_costs.{i}.note", item.get("note", ""))
        if "unit" in item and "qty" in item:
            detail = (
                T("ui.costs.qty", qty=item["qty"], unit=money(item["unit"], cur, fx))
                + T("ui.sep") + detail
            )
        wide_rows.append(
            f'<tr><td class="wrap">{tx(C(f"trip_wide_costs.{i}.item", item["item"]))}<br>'
            f'<span class="tp-recon">{tx(detail)}</span></td>'
            f'<td class="n">{num(money(item["total"], cur, fx))}</td></tr>'
        )

    # The lean version: what can go, and the four lines that look like savings and
    # are the opposite. It sits in the Costs tab rather than Before you go because
    # it is arithmetic, not a task.
    sv = d["savings"]
    cut_rows = [
        f'<tr><td class="wrap">{tx(C(f"savings.cut.{j}.item", c["item"]))}<br>'
        f'<span class="tp-recon">{tx(C(f"savings.cut.{j}.instead", c["instead"]))}</span></td>'
        f'<td class="n">{num(money(c["chf"], cur, fx))}</td></tr>'
        for j, c in enumerate(sv["cut"])
    ]
    keep_rows = [
        f'<tr><td class="wrap">{tx(C(f"savings.keep.{j}.item", k["item"]))}<br>'
        f'<span class="tp-recon">{tx(C(f"savings.keep.{j}.why", k["why"]))}</span></td>'
        f'<td class="n">{num(money(k["chf"], cur, fx))}</td></tr>'
        for j, k in enumerate(sv["keep"])
    ]

    # The rounding drift is a fact about the CHF source figures, so this line stays
    # in CHF whatever the currency toggle says.
    base = d["trip"]["currency"]
    recon_line = T(
        "ui.costs.reconcile",
        sum=money(recon["days_stated"] + recon["trip_wide"], base, fx),
        grand=money(recon["grand"], base, fx),
        drift=money(abs(recon["drift"]), base, fx),
    )

    block(
        f'<div class="tp" {dirattr}>'
        f'<h2>{tx(T("ui.costs.by_day"))}</h2>'
        f'<p class="note">{tx(T("ui.costs.by_day_note"))}</p>'
        f'{bars(day_rows, cur, fx)}'

        f'<h2>{tx(T("ui.costs.tickets"))}</h2>'
        f'<p class="note">{tx(T("ui.costs.tickets_note"))}</p>'
        f'<div class="tp-scroll"><table class="tp-tbl"><thead><tr>'
        f'<th scope="col">{tx(T("ui.cost.item"))}</th><th scope="col" class="n">{tx(T("ui.costs.full"))}</th>'
        f'<th scope="col" class="n">{tx(T("ui.costs.half"))}</th></tr></thead>'
        f'<tbody>{"".join(ticket_rows)}</tbody>'
        f'<tfoot><tr><td>{tx(T("ui.costs.total"))}</td>'
        f'<td class="n">{num(money(full_pp, cur, fx))}</td>'
        f'<td class="n">{num(money(half_pp, cur, fx))}</td></tr></tfoot>'
        f'</table></div>'
        f'<div class="tp-saving"><span>{tx(T("ui.costs.saving"))}</span>'
        f'<span class="v">{num(money(full_pp - half_pp, cur, fx))}</span></div>'

        f'<h2>{tx(T("ui.costs.categories"))}</h2>'
        f'{bars(cat_rows, cur, fx)}'
        f'<div class="tp-saving"><span>{tx(T("ui.costs.grand_total"))}</span>'
        f'<span class="v">{num(money(totals["grand_total_chf"], cur, fx))}</span></div>'

        f'<h2>{tx(T("ui.costs.tripwide"))}</h2>'
        f'<div class="tp-scroll"><table class="tp-tbl"><thead><tr>'
        f'<th scope="col">{tx(T("ui.cost.item"))}</th><th scope="col" class="n">{tx(T("ui.cost.amount"))}</th>'
        f'</tr></thead><tbody>{"".join(wide_rows)}</tbody></table></div>'
        f'<p class="tp-recon">{tx(recon_line)}</p>'

        f'<h2>{tx(T("ui.savings.title"))}</h2>'
        f'<p class="note">{tx(C("savings.note", sv["note"]))}</p>'
        f'<div class="tp-scroll"><table class="tp-tbl"><thead><tr>'
        f'<th scope="col">{tx(T("ui.savings.cut"))}</th>'
        f'<th scope="col" class="n">{tx(T("ui.cost.amount"))}</th></tr></thead>'
        f'<tbody>{"".join(cut_rows)}</tbody>'
        f'<tfoot><tr><td>{tx(T("ui.savings.saved"))}</td>'
        f'<td class="n">{num(money(sv["saved_chf"], cur, fx))}</td></tr></tfoot>'
        f'</table></div>'
        f'<div class="tp-saving"><span>{tx(T("ui.savings.lean"))}</span>'
        f'<span class="v">{num(money(sv["lean_total_chf"], cur, fx))}'
        f'<span class="dot">{tx(T("ui.sep"))}</span>'
        f'{num(money(sv["lean_per_person_chf"], cur, fx))} {tx(T("ui.savings.each"))}'
        f'</span></div>'
        f'<div class="tp-scroll"><table class="tp-tbl"><thead><tr>'
        f'<th scope="col">{tx(T("ui.savings.keep"))}</th>'
        f'<th scope="col" class="n">{tx(T("ui.cost.amount"))}</th></tr></thead>'
        f'<tbody>{"".join(keep_rows)}</tbody></table></div>'

        f'<h2>{tx(T("ui.costs.five_vs_six"))}</h2>'
        f'<p class="note">{tx(C("totals.note_vs_six", totals["note_vs_six"]))}</p>'
        f'</div>'
    )


def render_options(d, T, C, tx, dirattr, cur, fx, show_prices=True):
    """The in-trip fallbacks, on their own so they are quick to find in bad weather."""
    # With prices off the column goes rather than emptying: an empty right-hand column
    # reads as missing data, and these are the pages you scan in bad weather.
    swaps = []
    for i, s in enumerate(d["weather_swaps"]):
        price = ""
        if show_prices:
            shown = (
                num(money(s["chf_pp"], cur, fx)) if s["chf_pp"] is not None
                else tx(T("ui.swaps.no_cost"))
            )
            price = f'<td class="n">{shown}</td>'
        swaps.append(
            f'<tr><td class="wrap">{tx(C(f"weather_swaps.{i}.instead_of", s["instead_of"]))}</td>'
            f'<td class="wrap">{tx(C(f"weather_swaps.{i}.do", s["do"]))}</td>'
            f'{price}</tr>'
        )
    cost_head = (
        f'<th scope="col" class="n">{tx(T("ui.swaps.cost"))}</th>' if show_prices else ""
    )
    block(
        f'<div class="tp" {dirattr}>'
        f'<h2>{tx(T("ui.swaps.title"))}</h2>'
        f'<div class="tp-scroll"><table class="tp-tbl"><thead><tr>'
        f'<th scope="col">{tx(T("ui.swaps.instead_of"))}</th><th scope="col">{tx(T("ui.swaps.do"))}</th>'
        f'{cost_head}</tr></thead>'
        f'<tbody>{"".join(swaps)}</tbody></table></div>'
        f'</div>'
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    # The page title is the trip title, straight from the data file.
    st.set_page_config(
        page_title=json.loads(DATA_PATH.read_text(encoding="utf-8"))["trip"]["title"],
        layout="centered",
        initial_sidebar_state="auto",
    )
    data, recon = load_data(DATA_PATH.stat().st_mtime)
    trans = load_translations(TRANS_PATH.stat().st_mtime)
    images = load_images(IMAGES_PATH.stat().st_mtime)
    geo = tripmap.load_geo(tripmap.GEO_PATH.stat().st_mtime)
    meta = trans["meta"]
    languages = meta["languages"]

    if "lang" not in st.session_state:
        q = st.query_params.get("lang")
        st.session_state.lang = q if q in languages else meta["default"]
    if "cur" not in st.session_state:
        q = st.query_params.get("cur")
        st.session_state.cur = q if q in CURRENCIES else data["trip"]["currency"]
    if "ink" not in st.session_state:
        q = st.query_params.get("ink")
        st.session_state.ink = q if q in INK_TONES else DEFAULT_INK
    # Off unless the link says otherwise. The plan is the thing people open this for;
    # the money is something they go and look at.
    if "prices" not in st.session_state:
        st.session_state.prices = st.query_params.get("prices") == "1"

    def sync_url() -> None:
        st.query_params["lang"] = st.session_state.lang
        st.query_params["cur"] = st.session_state.cur
        st.query_params["ink"] = st.session_state.ink
        st.query_params["prices"] = "1" if st.session_state.prices else "0"

    lang = st.session_state.lang
    rtl = lang in meta["rtl"]
    # dir drives the mirroring; lang lets a screen reader pick the right voice and
    # the browser pick the right font for the script.
    dirattr = f'dir="{"rtl" if rtl else "ltr"}" lang="{lang}"'
    ui = trans["ui"][lang]
    ui_default = trans["ui"][meta["default"]]
    content = trans["content"].get(lang, {})

    def T(key: str, **kw) -> str:
        s = ui.get(key, ui_default.get(key, key))
        return s.format(**kw) if kw else s

    def C(path: str, fallback):
        value = content.get(path)
        return fallback if value is None else value

    def tx(s) -> str:
        if s is None:
            return ""
        s = str(s)
        if rtl:
            s = guard_numbers(s)
        return html.escape(s, quote=True)

    block(stylesheet(lang, rtl, st.session_state.ink))

    with st.sidebar:
        st.selectbox(
            T("ui.language"), languages, key="lang",
            format_func=lambda code: ui.get(f"lang.{code}", code),
            on_change=sync_url,
        )
        st.selectbox(T("ui.currency"), CURRENCIES, key="cur", on_change=sync_url)
        st.selectbox(
            T("ui.ink"), list(INK_TONES), key="ink",
            format_func=lambda tone: T(f"ui.ink.{tone}"),
            on_change=sync_url,
        )
        st.toggle(
            T("ui.prices"), key="prices", on_change=sync_url, help=T("ui.prices.help"),
        )
        st.caption(T("ui.currency_note"))
        # Asked once, here, rather than above every note box. Deliberately not put in
        # the URL: the whole point of the link is that it gets forwarded, and a name
        # riding along in it would sign everyone else's notes with the sender's name.
        st.text_input(
            T("ui.name"), key="who", max_chars=notes_store.MAX_AUTHOR,
            help=T("ui.name.help"),
        )

    if (st.query_params.get("lang") != lang
            or st.query_params.get("cur") != st.session_state.cur
            or st.query_params.get("ink") != st.session_state.ink
            or st.query_params.get("prices") != ("1" if st.session_state.prices else "0")):
        sync_url()

    cur = st.session_state.cur
    fx = data["trip"]["fx"]

    show_prices = st.session_state.prices
    render_header(data, T, C, tx, dirattr, cur, fx, images, show_prices)

    # A store that cannot be opened must not take the itinerary down with it: the plan
    # is the thing people came for, and the notes are the extra.
    store, store_problem = None, None
    try:
        store = open_notes()
        notes = read_notes(store)
    except notes_store.StoreError as exc:
        notes, store_problem = {}, exc.key

    # Order matters: the plan itself comes first, then the map that draws it, and the
    # reference tabs sit behind them. Anyone opening this on the trip wants the day.
    days, themap, overview, options, costs = st.tabs([
        T("ui.tab.days"), T("ui.tab.map"), T("ui.tab.overview"),
        T("ui.tab.options"), T("ui.tab.costs"),
    ])
    with days:
        expand_all = st.checkbox(T("ui.day.expand_all"), key="expand_all")
        if store_problem:
            st.warning(T(store_problem))
        render_days(data, T, C, tx, dirattr, cur, fx, expand_all, images, notes,
                    store, st.session_state.get("who", ""), show_prices)
    with themap:
        # The map wants to know which pin is today's so it can beat, the same way
        # the day card marks itself. Outside the trip nothing matches and none do.
        todays = [i for i, day in enumerate(data["days"])
                  if day["date"] == date.today().isoformat()]
        tripmap.render_map(
            geo, T, C, tx, dirattr, lang, rtl, INK_TONES.get(st.session_state.ink,
                                                             INK_TONES[DEFAULT_INK]),
            FONT_STACK[lang], LINE_HEIGHT[lang],
            todays[0] if todays else -1, block,
        )
    with overview:
        render_overview(data, T, C, tx, dirattr, meta["critical_tips"], cur, fx,
                        show_prices)
        render_credits(images, T, tx, dirattr, len(data["days"]))
    with options:
        render_options(data, T, C, tx, dirattr, cur, fx, show_prices)
    with costs:
        render_costs(data, recon, T, C, tx, dirattr, cur, fx, T("ui.sep"), meta["total_keys"])


if __name__ == "__main__":
    main()
