"""The trip map: every stop pinned, every drive, walk, lift and boat drawn.

Like app.py, this file contains no user-visible copy. Place names and prose come
from geo.json, their he/ar renderings from translations.json, and every colour is
a role out of the ink tone. Numbers are formatted here, in Python, and arrive in
the browser already wrapped in their bidi isolates, so the JavaScript below never
has to know which way the page runs.

The map is a hand-written Leaflet document inside an iframe. Leaflet itself is
served out of static/ beside the photographs; nothing but the map tiles is
fetched from another host while the app is running.

The module is called tripmap rather than map so that importing it cannot shadow
the built-in of that name.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

BASE = Path(__file__).parent
GEO_PATH = BASE / "geo.json"

# Served by Streamlit out of static/, same as the photographs. The CDN pair is a
# fallback that is not normally reached; check.py fails if the vendored copy is
# missing, so a deploy cannot quietly come to depend on it.
LEAFLET_VERSION = "1.9.4"
LEAFLET_JS = "app/static/leaflet/leaflet.js"
LEAFLET_CSS = "app/static/leaflet/leaflet.css"
CDN_JS = f"https://unpkg.com/leaflet@{LEAFLET_VERSION}/dist/leaflet.js"
CDN_CSS = f"https://unpkg.com/leaflet@{LEAFLET_VERSION}/dist/leaflet.css"

MODES = ("drive", "walk", "lift", "boat")

# How each kind of movement is drawn. The dash patterns matter more than the
# colours: on a phone in sunlight, and for the two people in five who cannot
# reliably separate red from green, the line has to be told apart by its shape.
MODE_STYLE = {
    "drive": {"weight": 5, "dash": None},
    "walk": {"weight": 4, "dash": "1 7"},
    "lift": {"weight": 4, "dash": "12 7"},
    "boat": {"weight": 3, "dash": "3 6"},
}

# Stops you sleep at get a ring; Plan B stops are drawn in the warn role.
SLEEPS = {"hotel", "town"}

MAP_HEIGHT = 880


@st.cache_data(show_spinner=False)
def load_geo(mtime: float) -> dict:
    """Keyed on mtime so an edited geo.json is actually noticed."""
    return json.loads(GEO_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Offline exports
# --------------------------------------------------------------------------- #

def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def gpx(geo: dict, name: str) -> str:
    """Waypoints and tracks, for Organic Maps, OsmAnd or a Garmin.

    This is the download that actually works in a valley with no signal: those
    apps hold the Switzerland map offline and will draw this on top of it.
    """
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="swiss-trip-itinerary" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <metadata><name>{_esc(name)}</name></metadata>",
    ]
    for slug, s in geo["stops"].items():
        days = ", ".join(f"day {d + 1}" for d in s["days"]) or "plan B"
        out.append(f'  <wpt lat="{s["lat"]}" lon="{s["lon"]}">')
        out.append(f"    <name>{_esc(s['name'])}</name>")
        out.append(f"    <desc>{_esc(days + '. ' + s['what'])}</desc>")
        out.append(f"    <type>{_esc(s['kind'])}</type>")
        out.append("  </wpt>")
    for leg in geo["legs"]:
        title = f"Day {leg['day'] + 1} {leg['mode']}: {leg['label']}"
        out.append(f"  <trk><name>{_esc(title)}</name><trkseg>")
        out.extend(f'    <trkpt lat="{lat}" lon="{lon}"></trkpt>'
                   for lat, lon in leg["geometry"])
        out.append("  </trkseg></trk>")
    out.append("</gpx>")
    return "\n".join(out) + "\n"


def _kml_colour(hexstr: str) -> str:
    """#RRGGBB out of a tone role, into KML's opaque AABBGGRR."""
    h = hexstr.lstrip("#")
    return f"ff{h[4:6]}{h[2:4]}{h[0:2]}".lower()


def kml(geo: dict, name: str, tone: dict) -> str:
    """For Google My Maps and Google Earth.

    Worth being straight about what this one is: Google Maps on a phone will not
    navigate a KML. It imports into My Maps, where it is viewable under Your
    Places, and that is all. The GPX above is the one to take up a mountain.

    The line colours are converted out of the ink tone rather than written down,
    so an exported trip cannot end up drawn in colours the app stopped using.
    """
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        f"  <name>{_esc(name)}</name>",
    ]
    for mode in MODES:
        out.append(f'  <Style id="{mode}"><LineStyle>'
                   f"<color>{_kml_colour(tone['route_' + mode])}</color>"
                   f"<width>{MODE_STYLE[mode]['weight']}</width>"
                   "</LineStyle></Style>")
    out.append("  <Folder><name>Stops</name>")
    for slug, s in geo["stops"].items():
        days = ", ".join(f"day {d + 1}" for d in s["days"]) or "plan B"
        out.append("    <Placemark>")
        out.append(f"      <name>{_esc(s['name'])}</name>")
        out.append(f"      <description>{_esc(days + '. ' + s['what'])}</description>")
        out.append(f"      <Point><coordinates>{s['lon']},{s['lat']},0</coordinates></Point>")
        out.append("    </Placemark>")
    out.append("  </Folder>")
    out.append("  <Folder><name>Routes</name>")
    for leg in geo["legs"]:
        title = f"Day {leg['day'] + 1} {leg['mode']}: {leg['label']}"
        coords = " ".join(f"{lon},{lat},0" for lat, lon in leg["geometry"])
        out.append("    <Placemark>")
        out.append(f"      <name>{_esc(title)}</name>")
        out.append(f"      <styleUrl>#{leg['mode']}</styleUrl>")
        out.append(f"      <LineString><tessellate>1</tessellate>"
                   f"<coordinates>{coords}</coordinates></LineString>")
        out.append("    </Placemark>")
    out.append("  </Folder>")
    out.append("</Document></kml>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #

def payload(geo, T, C, tx, lang, rtl, tone, today_index) -> dict:
    """Everything the browser needs, with every string already rendered.

    Formatting the numbers here rather than in JavaScript is what keeps the
    Hebrew and Arabic popups from coming apart: each one leaves Python already
    inside its isolate.
    """
    stops = []
    for slug, s in geo["stops"].items():
        days = s["days"]
        name = C(f"geo.stops.{slug}.name", s["name"])
        what = C(f"geo.stops.{slug}.what", s["what"])
        badge = str(days[0] + 1) if days else T("ui.map.planb_badge")
        when = (T("ui.sep").join(T("ui.map.day", n=d + 1) for d in days)
                if days else T("ui.map.planb"))
        stops.append({
            "id": slug,
            "lat": s["lat"], "lon": s["lon"],
            "days": days,
            "kind": s["kind"],
            "swap": not days,
            "sleep": s["kind"] in SLEEPS and bool(days),
            "badge": tx(badge),
            "name": tx(name),
            "what": tx(what),
            "when": tx(when),
            "coord": tx(f"{s['lat']:.5f}, {s['lon']:.5f}"),
            "maps": f"https://www.google.com/maps/search/?api=1&query={s['lat']},{s['lon']}",
            "today": today_index in days,
        })

    legs = []
    for i, leg in enumerate(geo["legs"]):
        label = C(f"geo.legs.{i}.label", leg["label"])
        bits = [T("ui.map.km", km=f"{leg['km']:.1f}")]
        if leg.get("min"):
            bits.append(T("ui.map.min", n=leg["min"]))
        legs.append({
            "day": leg["day"],
            "mode": leg["mode"],
            "geom": leg["geometry"],
            "title": tx(T(f"ui.map.mode.{leg['mode']}")),
            "label": tx(label),
            "meta": tx(T("ui.sep").join(bits)),
        })

    days_present = sorted({d for s in stops for d in s["days"]}
                          | {l["day"] for l in legs})
    return {
        "rtl": rtl,
        "stops": stops,
        "legs": legs,
        "days": [{"i": d,
                  "badge": tx(str(d + 1)),
                  "label": tx(T("ui.map.day", n=d + 1))}
                 for d in days_present],
        "hasSwaps": any(s["swap"] for s in stops),
        "modes": {m: {"label": tx(T(f"ui.map.mode.{m}")), **MODE_STYLE[m]}
                  for m in MODES},
        "tiles": {
            "url": geo["tiles"]["url"],
            "layer": geo["tiles"]["dark" if tone["scheme"] == "dark" else "light"],
            "maxNative": geo["tiles"]["max_zoom"],
            "invert": tone["scheme"] == "dark",
        },
        "ui": {
            "all": tx(T("ui.map.all")),
            "allLabel": tx(T("ui.map.all_label")),
            "planb": tx(T("ui.map.planb")),
            "planbBadge": tx(T("ui.map.planb_badge")),
            "stops": tx(T("ui.map.stops")),
            "openMaps": tx(T("ui.map.open_maps")),
            "tilesFailed": tx(T("ui.map.tiles_failed")),
            "sleep": tx(T("ui.map.sleep")),
            "today": tx(T("ui.day.today_badge")),
            "attribution": tx(T("ui.map.attribution")),
        },
    }


def document(data: dict, tone: dict, lang: str, font: str, line_height: str) -> str:
    """The whole self-contained Leaflet page."""
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    direction = "rtl" if data["rtl"] else "ltr"
    align = "right" if data["rtl"] else "left"
    zoom_corner = "topleft" if data["rtl"] else "topright"

    return f"""<!doctype html>
<html lang="{lang}" dir="{direction}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="{LEAFLET_CSS}">
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
  --drive: {tone["route_drive"]};
  --walk: {tone["route_walk"]};
  --lift: {tone["route_lift"]};
  --boat: {tone["route_boat"]};
  color-scheme: {tone["scheme"]};
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: var(--surface); }}
body {{
  font-family: {font};
  line-height: {line_height};
  color: var(--ink);
  direction: {direction};
  text-align: {align};
}}
#wrap {{ display: flex; flex-direction: column; gap: .5rem; }}

/* ---- the day switcher ---- */
#bar {{ display: flex; flex-wrap: wrap; gap: .3rem; align-items: center; }}
#bar button {{
  font: inherit; font-size: .85rem; line-height: 1;
  padding: .45rem .6rem; min-width: 2.1rem;
  border: 1px solid var(--line); border-radius: .5rem;
  background: var(--surface); color: var(--ink2);
  cursor: pointer;
}}
#bar button:hover {{ border-color: var(--accent); color: var(--ink); }}
#bar button[aria-pressed="true"] {{
  background: var(--accent); color: var(--on-accent);
  border-color: var(--accent); font-weight: 700;
}}
#bar button.wide {{ padding-inline: .8rem; }}
#bar button.swap[aria-pressed="true"] {{ background: var(--warn); color: var(--on-warn); border-color: var(--warn); }}

/* ---- the map ---- */
#map {{
  height: 460px; width: 100%;
  border: 1px solid var(--line); border-radius: .6rem;
  background: var(--plane);
  /* The map is a picture of the ground. It never mirrors, whichever way the
     text around it runs. */
  direction: ltr;
}}
#map.dark .leaflet-tile-pane {{ filter: invert(1) brightness(.88) contrast(1.05); }}
#map.notiles .leaflet-tile-pane {{ display: none; }}
.leaflet-container {{ font-family: inherit; background: var(--plane); }}
.leaflet-control-attribution {{ display: none; }}

/* ---- pins ---- */
.pin {{
  display: grid; place-items: center;
  width: 24px; height: 24px; border-radius: 50%;
  background: var(--accent); color: var(--on-accent);
  font-size: 12px; font-weight: 700; line-height: 1;
  border: 2px solid var(--surface);
  box-shadow: 0 1px 4px rgba(0,0,0,.45);
  direction: ltr;
}}
.pin.swap {{ background: var(--warn); color: var(--on-warn); }}
.pin.sleep {{ outline: 2px solid var(--accent); outline-offset: 1px; }}
.pin.today {{ animation: beat 1.6s ease-in-out infinite; }}
@keyframes beat {{ 50% {{ transform: scale(1.18); }} }}
.dim {{ opacity: .2; }}

/* ---- popups ---- */
.leaflet-popup-content {{ margin: .7rem .8rem; min-width: 190px; }}
.leaflet-popup-content-wrapper {{
  background: var(--surface); color: var(--ink);
  border-radius: .55rem; box-shadow: 0 2px 18px rgba(0,0,0,.35);
}}
.leaflet-popup-tip {{ background: var(--surface); }}
.pop {{ direction: {direction}; text-align: {align}; }}
.pop h4 {{ margin: 0 0 .2rem; font-size: .98rem; }}
.pop .when {{ color: var(--accent); font-size: .76rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .04em; }}
.pop .what {{ margin: .35rem 0 .5rem; font-size: .85rem; color: var(--ink2); }}
.pop .coord {{ font-size: .74rem; color: var(--muted); direction: ltr;
  text-align: {align}; font-variant-numeric: tabular-nums; }}
.pop a {{
  display: inline-block; margin-top: .45rem; font-size: .8rem; font-weight: 700;
  color: var(--accent); text-decoration: none;
}}
.pop a:hover {{ text-decoration: underline; }}

/* ---- legend ---- */
#legend {{ display: flex; flex-wrap: wrap; gap: .1rem .9rem; align-items: center;
  font-size: .78rem; color: var(--ink2); }}
#legend .k {{ display: inline-flex; align-items: center; gap: .35rem; }}
#legend svg {{ flex: none; }}

/* ---- the fallback strip ---- */
#warn {{
  display: none; padding: .5rem .7rem; border-radius: .45rem;
  background: var(--warn-wash); color: var(--warn);
  font-size: .82rem; font-weight: 500; border: 1px solid var(--warn);
}}
#warn.on {{ display: block; }}

/* ---- the stop list, which is also the offline fallback ---- */
#listwrap h3 {{ margin: .2rem 0 .4rem; font-size: .8rem; letter-spacing: .05em;
  text-transform: uppercase; color: var(--muted); font-weight: 700; }}
#list {{ max-height: 210px; overflow-y: auto; border: 1px solid var(--rule);
  border-radius: .5rem; }}
#list button {{
  display: flex; gap: .55rem; align-items: baseline; width: 100%;
  font: inherit; text-align: {align}; cursor: pointer;
  padding: .45rem .6rem; border: 0; border-bottom: 1px solid var(--rule);
  background: var(--surface); color: var(--ink);
}}
#list button:last-child {{ border-bottom: 0; }}
#list button:hover {{ background: var(--plane); }}
#list button[hidden] {{ display: none; }}
#list .n {{
  flex: none; width: 1.35rem; height: 1.35rem; border-radius: 50%;
  display: grid; place-items: center; direction: ltr;
  background: var(--accent-wash); color: var(--accent);
  font-size: .7rem; font-weight: 700;
}}
#list .swap .n {{ background: var(--warn-wash); color: var(--warn); }}
#list .nm {{ flex: 1 1 auto; font-size: .85rem; }}
#list .co {{ flex: none; font-size: .7rem; color: var(--muted);
  direction: ltr; font-variant-numeric: tabular-nums; }}
#credit {{ font-size: .68rem; color: var(--muted); }}
@media (max-width: 520px) {{
  #map {{ height: 380px; }}
  #list .co {{ display: none; }}
}}
</style>
</head>
<body>
<div id="wrap">
  <div id="bar"></div>
  <div id="warn"></div>
  <div id="map"></div>
  <div id="legend"></div>
  <div id="listwrap">
    <h3 id="listtitle"></h3>
    <div id="list"></div>
  </div>
  <div id="credit"></div>
</div>
<script src="{LEAFLET_JS}"></script>
<script>
const D = {blob};

// Leaflet is served from static/ beside the photographs, so the ordinary case
// reaches no other host. If that path ever stops answering — a deploy served under
// a base path, say — there is one try at the CDN before giving up, because a stop
// list is a poor substitute for the thing this tab exists to show. If that fails
// too the list is still useful: every coordinate and Maps link is already here.
function boot() {{
  if (typeof L !== 'undefined') {{
    main();
    return;
  }}
  const css = document.createElement('link');
  css.rel = 'stylesheet';
  css.href = '{CDN_CSS}';
  document.head.appendChild(css);
  const js = document.createElement('script');
  js.src = '{CDN_JS}';
  js.onload = () => main();
  js.onerror = degrade;
  document.head.appendChild(js);
}}

function degrade() {{
  document.getElementById('map').style.display = 'none';
  document.getElementById('bar').style.display = 'none';
  document.getElementById('legend').style.display = 'none';
  showWarn();
  buildList(null);
  document.getElementById('credit').innerHTML = D.ui.attribution;
}}

// Strings arrive from Python already HTML-escaped, so they are assigned as
// markup. Anything going into an attribute has to be turned back into plain text.
function unesc(s) {{
  const t = document.createElement('textarea');
  t.innerHTML = s;
  return t.value;
}}

function showWarn() {{
  const w = document.getElementById('warn');
  w.innerHTML = D.ui.tilesFailed;
  w.classList.add('on');
}}

function popupHtml(s) {{
  return '<div class="pop">'
    + '<div class="when">' + s.when + (s.today ? ' \\u00b7 ' + D.ui.today : '') + '</div>'
    + '<h4>' + s.name + '</h4>'
    + '<div class="what">' + s.what + '</div>'
    + '<div class="coord">' + s.coord + '</div>'
    + '<a href="' + s.maps + '" target="_blank" rel="noopener">' + D.ui.openMaps + ' \\u2197</a>'
    + '</div>';
}}

let map, markers = {{}}, lines = [], listBtns = [], chosen = 'all';

function main() {{
  map = L.map('map', {{
    zoomControl: false,
    scrollWheelZoom: false,   // the page scrolls past it on a phone
    attributionControl: false,
  }});
  L.control.zoom({{position: '{zoom_corner}'}}).addTo(map);
  if (D.tiles.invert) document.getElementById('map').classList.add('dark');

  const tiles = L.tileLayer(D.tiles.url.replace('{{layer}}', D.tiles.layer), {{
    maxNativeZoom: D.tiles.maxNative,
    maxZoom: 19,
    minZoom: 7,
  }});
  // A handful of missing tiles at the edge of the country is normal. A wall of
  // them means the network is gone, and then the list below is the whole point.
  let misses = 0;
  tiles.on('tileerror', () => {{
    if (++misses >= 8) {{
      document.getElementById('map').classList.add('notiles');
      showWarn();
    }}
  }});
  tiles.addTo(map);

  for (const leg of D.legs) {{
    const st = D.modes[leg.mode];
    const casing = L.polyline(leg.geom, {{
      color: getVar('--surface'), weight: st.weight + 4, opacity: .55,
      lineCap: 'round', lineJoin: 'round', interactive: false,
    }}).addTo(map);
    const line = L.polyline(leg.geom, {{
      color: getVar('--' + leg.mode), weight: st.weight, opacity: .95,
      dashArray: st.dash, lineCap: st.dash ? 'butt' : 'round', lineJoin: 'round',
    }}).addTo(map);
    line.bindTooltip(leg.title + ' \\u00b7 ' + leg.label + '<br>' + leg.meta,
                     {{sticky: true}});
    lines.push({{leg, casing, line}});
  }}

  for (const s of D.stops) {{
    const cls = ['pin', s.swap ? 'swap' : '', s.sleep ? 'sleep' : '',
                 s.today ? 'today' : ''].filter(Boolean).join(' ');
    const m = L.marker([s.lat, s.lon], {{
      icon: L.divIcon({{
        className: '',
        html: '<span class="' + cls + '">' + s.badge + '</span>',
        iconSize: [24, 24], iconAnchor: [12, 12], popupAnchor: [0, -12],
      }}),
      title: unesc(s.name),
      riseOnHover: true,
    }}).addTo(map);
    m.bindPopup(popupHtml(s));
    markers[s.id] = {{s, m}};
  }}

  buildBar();
  buildLegend();
  buildList(map);
  document.getElementById('credit').innerHTML = D.ui.attribution;
  select('all');

  // Streamlit builds every tab up front and hides the ones you are not on, so
  // this document is laid out at zero height and Leaflet works out its zoom from
  // that. The whole trip then arrives fitted to a box with no size, which looks
  // like a map stuck somewhere over Pilatus. Refit whenever the box changes.
  new ResizeObserver(() => {{
    map.invalidateSize();
    select(chosen);
  }}).observe(document.getElementById('map'));
}}

function getVar(n) {{
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}}

function buildBar() {{
  const bar = document.getElementById('bar');
  const add = (key, text, title, extra) => {{
    const b = document.createElement('button');
    b.innerHTML = text;
    b.title = unesc(title);
    b.dataset.key = String(key);
    if (extra) b.className = extra;
    b.addEventListener('click', () => select(key));
    bar.appendChild(b);
    return b;
  }};
  add('all', D.ui.all, D.ui.allLabel, 'wide');
  for (const d of D.days) add(d.i, d.badge, d.label);
  if (D.hasSwaps) add('swap', D.ui.planb, D.ui.planb, 'wide swap');
}}

function buildLegend() {{
  const el = document.getElementById('legend');
  for (const [mode, m] of Object.entries(D.modes)) {{
    const k = document.createElement('span');
    k.className = 'k';
    k.innerHTML =
      '<svg width="30" height="8" aria-hidden="true">'
      + '<line x1="1" y1="4" x2="29" y2="4" stroke="var(--' + mode + ')" '
      + 'stroke-width="' + m.weight + '" stroke-linecap="'
      + (m.dash ? 'butt' : 'round') + '"'
      + (m.dash ? ' stroke-dasharray="' + m.dash + '"' : '') + '/></svg>'
      + '<span>' + m.label + '</span>';
    el.appendChild(k);
  }}
  const sleep = document.createElement('span');
  sleep.className = 'k';
  sleep.innerHTML = '<span class="pin sleep" style="width:16px;height:16px;'
    + 'font-size:9px">\\u2605</span><span>' + D.ui.sleep + '</span>';
  el.appendChild(sleep);
}}

function buildList(mapOrNull) {{
  const list = document.getElementById('list');
  document.getElementById('listtitle').innerHTML = D.ui.stops;
  for (const s of D.stops) {{
    const b = document.createElement('button');
    if (s.swap) b.className = 'swap';
    b.innerHTML = '<span class="n">' + s.badge + '</span>'
      + '<span class="nm">' + s.name + '</span>'
      + '<span class="co">' + s.coord + '</span>';
    if (mapOrNull) {{
      b.addEventListener('click', () => {{
        mapOrNull.flyTo([s.lat, s.lon], Math.max(mapOrNull.getZoom(), 14),
                        {{duration: .6}});
        markers[s.id].m.openPopup();
      }});
    }} else {{
      // No Leaflet: the row becomes the Maps link itself.
      b.addEventListener('click', () => window.open(s.maps, '_blank', 'noopener'));
    }}
    b.dataset.days = s.days.join(',');
    b.dataset.swap = s.swap ? '1' : '';
    list.appendChild(b);
    listBtns.push({{s, b}});
  }}
}}

function select(key) {{
  chosen = key;
  for (const b of document.querySelectorAll('#bar button')) {{
    b.setAttribute('aria-pressed', b.dataset.key === String(key) ? 'true' : 'false');
  }}
  const wantAll = key === 'all';
  const wantSwap = key === 'swap';
  const day = (!wantAll && !wantSwap) ? Number(key) : null;

  const inSet = s => wantAll ? true : wantSwap ? s.swap : s.days.includes(day);
  const legIn = l => wantAll ? true : wantSwap ? false : l.day === day;

  const bounds = [];
  for (const {{s, m}} of Object.values(markers)) {{
    const on = inSet(s);
    const el = m.getElement();
    if (el) el.classList.toggle('dim', !on);
    m.setZIndexOffset(on ? 400 : 0);
    if (on) bounds.push([s.lat, s.lon]);
  }}
  for (const {{leg, casing, line}} of lines) {{
    const on = legIn(leg);
    line.setStyle({{opacity: on ? .95 : .12}});
    casing.setStyle({{opacity: on ? .55 : 0}});
    if (on) for (const p of leg.geom) bounds.push(p);
  }}
  for (const {{s, b}} of listBtns) b.hidden = !inSet(s);
  if (bounds.length) {{
    map.fitBounds(bounds, {{padding: [28, 28], maxZoom: wantAll ? 12 : 15}});
  }}
}}

// Last, not first. Function declarations hoist but `let map` does not: calling
// main() above its declaration threw a temporal-dead-zone error and left the map
// container built but empty.
boot();
</script>
</body>
</html>
"""


def render_map(geo, T, C, tx, dirattr, lang, rtl, tone, font, line_height,
               today_index, block) -> None:
    """The map tab: an intro line, the map itself, and the two exports."""
    block(f'<div class="tp" {dirattr}><h2>{tx(T("ui.map.title"))}</h2>'
          f'<p>{tx(T("ui.map.lede"))}</p></div>')

    data = payload(geo, T, C, tx, lang, rtl, tone, today_index)
    components.html(document(data, tone, lang, font, line_height),
                    height=MAP_HEIGHT, scrolling=False)

    block(f'<div class="tp" {dirattr}><h2>{tx(T("ui.map.download"))}</h2>'
          f'<p>{tx(T("ui.map.download_note"))}</p></div>')
    name = C("trip.title", "Switzerland")
    left, right = st.columns(2)
    with left:
        st.download_button(T("ui.map.gpx"), gpx(geo, name),
                           file_name="swiss-trip.gpx",
                           mime="application/gpx+xml", use_container_width=True)
    with right:
        st.download_button(T("ui.map.kml"), kml(geo, name, tone),
                           file_name="swiss-trip.kml",
                           mime="application/vnd.google-earth.kml+xml",
                           use_container_width=True)
