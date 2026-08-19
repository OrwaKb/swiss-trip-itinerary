# Trip map: every stop, every path

Design agreed 2026-08-19. Adds an interactive map of the whole trip to the
itinerary app: every stop pinned at navigation grade, every drive, walk and
lift drawn as real geometry.

## What it is for

Two jobs, and the second one is the demanding one.

1. Understand the shape of the trip from a laptop before flying.
2. Be usable standing in a car park at 08:00 with one hand on a phone.

Job 2 sets the accuracy bar. A pin on "Stechelberg" is useless; the pin has to
be the Schilthornbahn car park itself. It also sets the failure bar: when the
tiles do not load in a valley, the thing must still tell you where to walk.

## Decisions

**Interactive, not a rendered image.** Zoom is the whole point at the trail
level: which side of the ridge the Panoramaweg runs along cannot be shown in a
static overview.

**swisstopo as the basemap.** `wmts.geo.admin.ch` serves the Swiss national map
free, with no key and no registration, under the OGD terms; the only obligation
is the source credit. It draws the hiking paths and their difficulty grades,
which no generic basemap does.

    https://wmts.geo.admin.ch/1.0.0/{layer}/default/current/3857/{z}/{x}/{y}.jpeg

**Leaflet by hand inside `st.components.v1.html`.** Not streamlit-folium, not
pydeck. `requirements.txt` is two lines today and stays two lines. folium adds
two dependencies and someone else's release cadence on Community Cloud; pydeck
is WebGL data-viz that fights a hiking map and is heavy on a phone. Writing the
document by hand also gives the control the theme needs: four ink tones, RTL
popups, and check.py's rule that no colour is ever a literal.

**Leaflet is vendored into `static/leaflet/`, not loaded from a CDN.** The
config file already says nothing is fetched from another host at runtime, and a
map that needs a CDN to draw contradicts the offline half of this design. The
CDN remains as a fallback the ordinary path never reaches, and check.py fails if
the vendored copy goes missing so a deploy cannot come to depend on it.

**The day switcher lives inside the component**, in JavaScript, not as a
Streamlit widget. Changing day must not trigger a page rerun.

**Nothing is fetched at page load except tiles.** All geometry is fetched once
by `tools/build_geo.py` and committed, the same way `tools/build_images.py`
froze the photographs.

## Data: `geo.json`

A fourth source of truth beside itinerary.json, translations.json, images.json.

    stops: slug -> {name, lat, lon, kind, days:[...], what, source}
    legs:  [{day, mode: drive|walk|lift|boat, from, to, km, min, geometry:[[lat,lon]...]}]

Stops key by stable slug and carry the list of days they appear on, so
Grindelwald Terminal is one pin used by days 4 and 5 rather than three copies.
Legs join back to itinerary.json by day index, the same indexing
translations.json already uses in `days.5.tips.3`.

`source` records where the coordinate came from: an OSM element id where one was
matched, or `seed` where it was authored by hand. Provenance is what makes the
40 coordinates reviewable.

Geometry is simplified (Douglas-Peucker, 10 m) and rounded to 5 decimals.
Raw OSRM output for the Brunig drive alone is 1,772 points. It stays readable
JSON rather than an encoded polyline specifically so a bad line can be found and
fixed by eye.

## Where the geometry comes from

`tools/build_geo.py`, run by hand, output committed.

- **Stops** are seeded by hand, then verified against OpenStreetMap: for each
  seed the tool asks Overpass for the named feature nearby and snaps to it,
  recording the element id. A seed that matches nothing is reported, not
  silently kept.
- **Drives** from OSRM (`router.project-osrm.org`, free, no key), cross-checked
  against `drive_km` in itinerary.json.
- **Lifts** from OSM `aerialway=*` and rack/funicular railways. The Eiger
  Express, the Maennlichen gondola, the Rigi cog, the Weggis cable car and both
  Schilthornbahn sections all exist as real ways with true geometry.
- **Walks** from OSM path geometry where a signed route exists (the
  Panoramaweg), otherwise from foot routing, each checked by eye against
  swisstopo's own hiking layer.

## Theme and direction

Four ink tones, two map treatments: `pixelkarte-farbe` for daylight and
alpenglow, `pixelkarte-grau` inverted for dusk and night. Grey inverts cleanly,
colour does not.

Four new roles in `INK_TONES` -- `route_drive`, `route_walk`, `route_lift`,
`route_boat` -- so no colour is ever written as a literal. The pins reuse the
existing accent and warn roles rather than inventing more. check.py's colour scan
extends over the generated map document as well as `stylesheet()`, and holds the
route colours to 3:1 on the map paper and pairwise apart in Lab. The KML export
converts its line colours out of the tone for the same reason.

A boat turned out to be a fourth kind of movement, not a variant of a lift: the
SGV crossing on day 1 and the Fluelen weather swap are both boats. Colour alone
does not carry the distinction between four modes, so each also has its own dash
pattern and check.py fails if two of them collide.

The map never mirrors: north stays up and Switzerland does not flip. Popups,
legend, day switcher and stop list all take `dir="rtl"` in he/ar, the zoom
control moves to the left, and every number inside a popup goes through
`guard_numbers` for the bidi rule.

## Offline

A `tileerror` handler degrades: after repeated failures the basemap is dropped,
a warning strip appears, and a plain list of stops with coordinates and Maps
links is revealed. Only the imagery is lost, because all the geometry is already
in the document.

Plus GPX and KML downloads, honestly labelled. Google Maps does **not** import
either into the phone app for navigation; KML lands in Google My Maps, viewable
but not navigable. The download that actually works in a valley is the GPX,
loaded into Organic Maps or OsmAnd with Switzerland pre-downloaded.

## The gate: new checks in check.py

1. Every stop referenced by a leg exists.
2. Every day index is valid in itinerary.json.
3. Every day with `drive_km > 0` has a drive leg, agreeing within 15 %.
4. Every stop name has a he and ar translation.
5. Every coordinate lies inside Switzerland's bounding box. Catches a swapped
   lat/lon instantly.
6. No leg geometry starts or ends more than 250 m from its declared stop.
   Catches a route fetched between the wrong two points.
7. No hardcoded colour in the generated map document.
8. GPX and KML serialise, and round-trip the right number of waypoints.

5 and 6 are the two that actually catch navigation-grade errors.

## Files

New: `geo.json`, `tripmap.py`, `tools/build_geo.py`, `static/leaflet/`.
Touched: `app.py` (a fifth tab plus the colour roles), `translations.json`
(stop names and legend copy in he and ar), `check.py`, `README.md` for the
swisstopo credit.

`tripmap.py` is a separate module because `app.py` is already 1,344 lines.
It is named tripmap rather than map so that importing it cannot shadow the
built-in of that name in app.py.

## Not doing

Elevation profiles, live weather, GPS "you are here", bundled offline tiles
(50 MB in the repo), turn-by-turn, pin editing in the UI.
