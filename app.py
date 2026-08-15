"""Family trip itinerary, in English, Hebrew and Arabic.

Every user-visible string comes from itinerary.json or translations.json.
Nothing readable is written in this file.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import streamlit as st

BASE = Path(__file__).parent
DATA_PATH = BASE / "itinerary.json"
TRANS_PATH = BASE / "translations.json"

# Bidi isolates. Wrapping a number, price, time or Latin run in LRI…PDI stops the
# bidi algorithm from reordering it inside a right-to-left sentence.
LRI, PDI = "⁦", "⁩"

CURRENCIES = ("CHF", "EUR", "ILS")
FX_KEY = {"EUR": "CHF_EUR", "ILS": "CHF_ILS"}
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


@st.cache_data(show_spinner=False)
def load_data() -> tuple[dict, dict]:
    d = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return d, reconcile(d)


@st.cache_data(show_spinner=False)
def load_translations() -> dict:
    return json.loads(TRANS_PATH.read_text(encoding="utf-8"))


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


def stylesheet(lang: str, rtl: bool) -> str:
    direction = "rtl" if rtl else "ltr"
    align = "right" if rtl else "left"
    align_far = "left" if rtl else "right"
    marker = f"tp-dir-{direction}"

    return f"""
<span id="{marker}" hidden></span>
<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700&family=Noto+Sans+Arabic:wght@400;500;700&display=swap');

:root {{
  --surface: #fcfcfb;
  --plane: #f3f2ee;
  --ink: #0b0b0b;
  --ink2: #52514e;
  --muted: #898781;
  --rule: #e1e0d9;
  --line: #c3c2b7;
  --accent: #2a78d6;
  --accent-wash: #cde2fb;
  --warning: #fab219;
  --border: rgba(11, 11, 11, 0.10);
  --font: {FONT_STACK[lang]};
  --lh: {LINE_HEIGHT[lang]};
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
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 18px 18px 14px; margin-bottom: 14px;
}}
.tp-head h1 {{ font-size: 1.7rem; font-weight: 700; margin: 0 0 6px; letter-spacing: -0.01em; }}
.tp-head .sub {{ color: var(--ink2); font-size: 0.93rem; margin: 0; }}
.tp-head .sub .dot {{ color: var(--line); padding: 0 2px; }}
.tp-stats {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }}
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
.tp-list.warn li {{ border-inline-start: 3px solid var(--warning); }}
.tp-list li .when {{ display: block; color: var(--muted); font-size: 0.74rem; margin-bottom: 3px; }}
.tp-ol {{ list-style: none; counter-reset: step; padding: 0; margin: 0; }}
.tp-ol li {{
  counter-increment: step; background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 11px 13px 11px 13px; margin-bottom: 7px;
  font-size: 0.92rem; display: flex; gap: 11px; align-items: flex-start;
}}
.tp-ol li::before {{
  content: counter(step); flex: 0 0 22px; height: 22px; border-radius: 50%;
  background: var(--accent-wash); color: var(--ink); font-size: 0.76rem; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
  font-variant-numeric: tabular-nums; unicode-bidi: isolate;
}}

/* ---- day cards ---- */
.tp-day {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  margin-bottom: 9px; overflow: hidden;
}}
.tp-day[data-float="1"] {{ border-color: var(--warning); }}
.tp-day > summary {{
  list-style: none; cursor: pointer; padding: 13px 40px 13px 15px; position: relative;
}}
.tp-day[dir="rtl"] > summary {{ padding: 13px 15px 13px 40px; }}
.tp-day > summary::-webkit-details-marker {{ display: none; }}
.tp-day > summary::after {{
  content: ""; position: absolute; top: 20px; width: 8px; height: 8px;
  border-right: 2px solid var(--muted); border-bottom: 2px solid var(--muted);
  transform: rotate(-45deg); transition: transform .15s ease;
}}
.tp-day[dir="ltr"] > summary::after {{ right: 17px; }}
.tp-day[dir="rtl"] > summary::after {{ left: 17px; transform: rotate(135deg); }}
.tp-day[dir="ltr"][open] > summary::after {{ transform: rotate(45deg); }}
.tp-day[dir="rtl"][open] > summary::after {{ transform: rotate(45deg); }}
.tp-day > summary:hover {{ background: var(--plane); }}

.tp-meta {{ display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin-bottom: 4px; }}
.tp-meta .date {{ color: var(--ink2); font-size: 0.79rem; font-variant-numeric: tabular-nums; }}
.tp-meta .dow {{ color: var(--muted); font-size: 0.79rem; }}
.chip {{
  font-size: 0.7rem; padding: 2px 7px; border-radius: 999px;
  background: var(--plane); border: 1px solid var(--border); color: var(--ink2);
}}
.chip.float {{ background: var(--warning); border-color: var(--warning); color: #2a1f00; font-weight: 600; }}
.tp-day .title {{ font-size: 1.03rem; font-weight: 600; margin: 0 0 6px; }}
.tp-day .headfoot {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: baseline; }}
.tp-day .headfoot .total {{ font-size: 0.85rem; font-weight: 600; }}
.tp-day .headfoot .drive {{ font-size: 0.79rem; color: var(--muted); }}

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
  background: #fff6e0; border: 1px solid var(--warning); border-radius: 9px;
  padding: 11px 12px; margin-top: 12px; font-size: 0.89rem;
}}
.tp-split {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 2px; }}
.tp-split .col {{ min-width: 0; }}
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

@media (max-width: 560px) {{
  [data-testid="stMain"] .block-container {{ padding: 0.8rem 0.7rem 2.5rem; }}
  .tp-head {{ padding: 15px 14px 12px; }}
  .tp-head h1 {{ font-size: 1.4rem; }}
  .tp-stat {{ flex: 1 1 100%; }}
  .tp-split {{ grid-template-columns: 1fr; gap: 2px; }}
  .tp-day > summary {{ padding: 12px 34px 12px 13px; }}
  .tp-day[dir="rtl"] > summary {{ padding: 12px 13px 12px 34px; }}
  .tp-body {{ padding: 2px 13px 13px; }}
  table.tp-tbl {{ font-size: 0.8rem; }}
  /* let the three-column tables fit a 390px phone rather than needing a swipe */
  table.tp-tbl td.wrap {{ min-width: 100px; }}
  table.tp-tbl th, table.tp-tbl td {{ padding-inline: 6px; }}
}}
</style>
"""


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def render_header(d, T, C, tx, dirattr, cur, fx):
    trip, totals = d["trip"], d["totals"]
    party = trip["party"]
    per_person = {
        "CHF": money(totals["per_person_chf"], "CHF", fx),
        "EUR": f"EUR {totals['per_person_eur']:,.0f}",
        "ILS": f"ILS {totals['per_person_ils']:,.0f}",
    }
    others = T("ui.sep").join(v for k, v in per_person.items() if k != cur)
    dates = T("ui.range", start=trip["start"], end=trip["end"])

    block(
        f'<div class="tp" dir="{dirattr}"><div class="tp-head">'
        f'<h1>{tx(C("trip.title", trip["title"]))}</h1>'
        f'<p class="sub">{num(dates)}<span class="dot">{tx(T("ui.sep"))}</span>'
        f'{tx(T("ui.header.nights", n=trip["nights"]))}'
        f'<span class="dot">{tx(T("ui.sep"))}</span>'
        f'{tx(T("ui.header.party", total=party["total"], parents=party["parents"], adults=party["adults"]))}</p>'
        f'<div class="tp-stats">'
        f'<div class="tp-stat"><span class="lbl">{tx(T("ui.header.trip_total"))}</span>'
        f'<span class="val">{num(money(totals["grand_total_chf"], cur, fx))}</span></div>'
        f'<div class="tp-stat"><span class="lbl">{tx(T("ui.header.per_person"))}</span>'
        f'<span class="val">{num(per_person[cur])}</span>'
        f'<span class="alt">{num(others)}</span></div>'
        f'</div></div></div>'
    )


def render_overview(d, T, C, tx, dirattr, critical):
    """Everything that happens before the trip: dangers, premises, then what to book."""
    trip = d["trip"]
    items = "".join(
        f"<li>{tx(C(f'trip.assumptions.{i}', s))}</li>"
        for i, s in enumerate(trip["assumptions"])
    )
    steps = "".join(
        f"<li><span>{tx(C(f'booking_order.{i}', s))}</span></li>"
        for i, s in enumerate(d["booking_order"])
    )

    warn = []
    for path in critical:
        _, day_i, _, tip_i = path.split(".")
        day = d["days"][int(day_i)]
        text = C(path, day["tips"][int(tip_i)])
        when = f'{num(day["date"])} <span class="dot"></span> {tx(C(f"days.{day_i}.dow", day["dow"]))}'
        warn.append(f'<li><span class="when">{when}</span>{tx(text)}</li>')

    block(
        f'<div class="tp" dir="{dirattr}">'
        f'<h2>{tx(T("ui.overview.warnings"))}</h2>'
        f'<p class="note">{tx(T("ui.overview.warnings_note"))}</p>'
        f'<ul class="tp-list warn">{"".join(warn)}</ul>'
        f'<h2>{tx(T("ui.overview.assumptions"))}</h2>'
        f'<ul class="tp-list">{items}</ul>'
        f'<h2>{tx(T("ui.booking.title"))}</h2>'
        f'<ol class="tp-ol">{steps}</ol>'
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
        f'<th>{tx(T("ui.cost.item"))}</th><th class="n">{tx(T("ui.cost.each"))}</th>'
        f'<th class="n">{tx(T("ui.cost.amount"))}</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        f'<tfoot><tr><td>{tx(T("ui.day.total"))}</td><td class="n"></td>'
        f'<td class="n">{num(money(day["day_total_chf"], cur, fx))}</td></tr></tfoot>'
        f'</table></div></details>'
    )


def render_days(d, T, C, tx, dirattr, cur, fx):
    n = d["trip"]["party"]["total"]
    cards = []
    for i, day in enumerate(d["days"]):
        floating = bool(day.get("floating"))
        chips = f'<span class="chip">{tx(C(f"days.{i}.leg", day["leg"]))}</span>'
        if floating:
            chips += f'<span class="chip float">{tx(T("ui.day.floating_badge"))}</span>'

        drive = (
            T("ui.day.drive", km=day["drive_km"], min=day["drive_min"])
            if day["drive_km"] else T("ui.day.no_drive")
        )
        head = (
            f'<summary><div class="tp-meta"><span class="date">{num(day["date"])}</span>'
            f'<span class="dow">{tx(C(f"days.{i}.dow", day["dow"]))}</span>{chips}</div>'
            f'<div class="title">{tx(C(f"days.{i}.title", day["title"]))}</div>'
            f'<div class="headfoot"><span class="total">{tx(T("ui.day.total"))} '
            f'{num(money(day["day_total_chf"], cur, fx))}</span>'
            f'<span class="drive">{tx(drive)}</span></div></summary>'
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
        if day.get("parents") or day.get("adults"):
            cols = ""
            if day.get("parents"):
                cols += (
                    f'<div class="col"><span class="tp-lbl">{tx(T("ui.day.parents"))}</span>'
                    f'<p>{tx(C(f"days.{i}.parents", day["parents"]))}</p></div>'
                )
            if day.get("adults"):
                cols += (
                    f'<div class="col"><span class="tp-lbl">{tx(T("ui.day.adults"))}</span>'
                    f'<p>{tx(C(f"days.{i}.adults", day["adults"]))}</p></div>'
                )
            body.append(f'<div class="tp-split">{cols}</div>')
        if day["tips"]:
            tips = "".join(
                f"<li>{tx(C(f'days.{i}.tips.{k}', t))}</li>" for k, t in enumerate(day["tips"])
            )
            body.append(
                f'<span class="tp-lbl">{tx(T("ui.day.tips"))}</span>'
                f'<ul class="tp-tips">{tips}</ul>'
            )
        body.append(day_cost_table(day, i, n, T, C, tx, cur, fx))

        cards.append(
            f'<details class="tp-day" dir="{dirattr}" data-float="{int(floating)}">'
            f'{head}<div class="tp-body">{"".join(body)}</div></details>'
        )
    block(f'<div class="tp" dir="{dirattr}">{"".join(cards)}</div>')


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


def render_costs(d, recon, T, C, tx, dirattr, cur, fx, sep):
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

    cat_rows = [
        (tx(T(f"ui.totals.{k}")), totals[k])
        for k in load_translations()["meta"]["total_keys"]
    ]

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
        f'<div class="tp" dir="{dirattr}">'
        f'<h2>{tx(T("ui.costs.by_day"))}</h2>'
        f'<p class="note">{tx(T("ui.costs.by_day_note"))}</p>'
        f'{bars(day_rows, cur, fx)}'

        f'<h2>{tx(T("ui.costs.tickets"))}</h2>'
        f'<p class="note">{tx(T("ui.costs.tickets_note"))}</p>'
        f'<div class="tp-scroll"><table class="tp-tbl"><thead><tr>'
        f'<th>{tx(T("ui.cost.item"))}</th><th class="n">{tx(T("ui.costs.full"))}</th>'
        f'<th class="n">{tx(T("ui.costs.half"))}</th></tr></thead>'
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
        f'<th>{tx(T("ui.cost.item"))}</th><th class="n">{tx(T("ui.cost.amount"))}</th>'
        f'</tr></thead><tbody>{"".join(wide_rows)}</tbody></table></div>'
        f'<p class="tp-recon">{tx(recon_line)}</p>'

        f'<h2>{tx(T("ui.costs.five_vs_six"))}</h2>'
        f'<p class="note">{tx(C("totals.note_vs_six", totals["note_vs_six"]))}</p>'
        f'</div>'
    )


def render_options(d, T, C, tx, dirattr, cur, fx):
    """The in-trip fallbacks, on their own so they are quick to find in bad weather."""
    swaps = []
    for i, s in enumerate(d["weather_swaps"]):
        price = (
            num(money(s["chf_pp"], cur, fx)) if s["chf_pp"] is not None
            else tx(T("ui.swaps.no_cost"))
        )
        swaps.append(
            f'<tr><td class="wrap">{tx(C(f"weather_swaps.{i}.instead_of", s["instead_of"]))}</td>'
            f'<td class="wrap">{tx(C(f"weather_swaps.{i}.do", s["do"]))}</td>'
            f'<td class="n">{price}</td></tr>'
        )
    block(
        f'<div class="tp" dir="{dirattr}">'
        f'<h2>{tx(T("ui.swaps.title"))}</h2>'
        f'<div class="tp-scroll"><table class="tp-tbl"><thead><tr>'
        f'<th>{tx(T("ui.swaps.instead_of"))}</th><th>{tx(T("ui.swaps.do"))}</th>'
        f'<th class="n">{tx(T("ui.swaps.cost"))}</th></tr></thead>'
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
    data, recon = load_data()
    trans = load_translations()
    meta = trans["meta"]
    languages = meta["languages"]

    if "lang" not in st.session_state:
        q = st.query_params.get("lang")
        st.session_state.lang = q if q in languages else meta["default"]
    if "cur" not in st.session_state:
        q = st.query_params.get("cur")
        st.session_state.cur = q if q in CURRENCIES else data["trip"]["currency"]

    def sync_url() -> None:
        st.query_params["lang"] = st.session_state.lang
        st.query_params["cur"] = st.session_state.cur

    lang = st.session_state.lang
    rtl = lang in meta["rtl"]
    dirattr = "rtl" if rtl else "ltr"
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

    block(stylesheet(lang, rtl))

    with st.sidebar:
        st.selectbox(
            T("ui.language"), languages, key="lang",
            format_func=lambda code: ui.get(f"lang.{code}", code),
            on_change=sync_url,
        )
        st.selectbox(T("ui.currency"), CURRENCIES, key="cur", on_change=sync_url)
        st.caption(T("ui.currency_note"))

    if st.query_params.get("lang") != lang or st.query_params.get("cur") != st.session_state.cur:
        sync_url()

    cur = st.session_state.cur
    fx = data["trip"]["fx"]

    render_header(data, T, C, tx, dirattr, cur, fx)

    overview, days, costs, options = st.tabs([
        T("ui.tab.overview"), T("ui.tab.days"), T("ui.tab.costs"), T("ui.tab.options"),
    ])
    with overview:
        render_overview(data, T, C, tx, dirattr, meta["critical_tips"])
    with days:
        render_days(data, T, C, tx, dirattr, cur, fx)
    with costs:
        render_costs(data, recon, T, C, tx, dirattr, cur, fx, T("ui.sep"))
    with options:
        render_options(data, T, C, tx, dirattr, cur, fx)


if __name__ == "__main__":
    main()
