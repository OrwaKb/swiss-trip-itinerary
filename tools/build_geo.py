"""Build geo.json: every stop pinned, every drive, walk and lift drawn.

Run:  python tools/build_geo.py            (writes geo.json)
      python tools/build_geo.py --dry      (fetches and reports, writes nothing)

Nothing here runs while the app is running. Geometry is fetched once from
OpenStreetMap and OSRM, checked, simplified and committed, the same way
tools/build_images.py froze the photographs.

The seed coordinates below are authored by hand. Each one that carries an `osm`
query is then snapped to the real OpenStreetMap feature and the element id is
recorded in geo.json, so every pin can be traced back to something. A seed that
matches nothing, or that moves further than its tolerance, is reported loudly
rather than quietly kept.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "geo.json"
ITIN = json.loads((BASE / "itinerary.json").read_text(encoding="utf-8"))

UA = "swiss-trip-itinerary/1.0 (+https://github.com/OrwaKb/swiss-trip-itinerary)"
OSRM = "https://router.project-osrm.org"
VALHALLA = "https://valhalla1.openstreetmap.de/route"
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Douglas-Peucker tolerance. 10 m is below the width of the road on screen at
# every zoom the map offers, so the simplified line is visually identical.
PACE_S = 1.0  # be a good citizen on the public Overpass instance
SIMPLIFY_M = 10.0
COORD_DP = 5  # ~1.1 m at this latitude

# --------------------------------------------------------------------------- #
# The stops. Hand-authored, then verified against OpenStreetMap.
#
#   osm    Overpass filter used to find the real feature near the seed, and the
#          radius in metres to search. Snapping records the element id.
#   days   which itinerary days the pin belongs to; [] means a Plan B stop.
# --------------------------------------------------------------------------- #

STOPS = [
    # --- day 0, land and sleep at the airport ------------------------------
    dict(slug="zrh-terminal", name="Zurich Airport, Arrivals", kind="airport",
         lat=47.45045, lon=8.56198, days=[0, 8],
         what="Land here Friday evening. Leave from here Saturday morning.",
         osm=('nwr[aeroway=terminal]', 900)),
    dict(slug="zrh-radisson", name="Radisson Blu Hotel, Zurich Airport", kind="hotel",
         lat=47.44980, lon=8.56100, days=[0],
         what="Night 0, three rooms. Covered indoor walkway from the terminal, about 10 minutes.",
         osm=('nwr[tourism=hotel][name~"Radisson",i]', 700)),

    # --- day 1, the van, Weggis, and the boat ------------------------------
    dict(slug="zrh-rental", name="Rental Centre, Airport Center Level 1", kind="parking",
         lat=47.45010, lon=8.56420, days=[1],
         what="Collect the 7-seat van from 06:00. Desks close 23:30, so no Friday-night pickup.",
         osm=('nwr[amenity=car_rental]', 900)),
    dict(slug="weggis-village", name="Weggis", kind="town",
         lat=47.03330, lon=8.43330, days=[1, 2, 3],
         what="Base for nights 1 and 2, on the north shore of Lake Lucerne.",
         osm=('nwr[place~"village|town"][name="Weggis"]', 2500)),
    dict(slug="weggis-pier", name="Weggis boat pier", kind="pier",
         lat=47.03260, lon=8.43060, days=[1],
         what="SGV paddle steamers into Lucerne. The lake is the nicest way in.",
         osm=('nwr[amenity=ferry_terminal]', 700)),
    dict(slug="lucerne-pier", name="Lucerne, Bahnhofquai pier", kind="pier",
         lat=47.05030, lon=8.31010, days=[1],
         what="Boat arrives beside the station. The old town starts at the bridge.",
         osm=('nwr[amenity=ferry_terminal]', 500)),
    dict(slug="lucerne-kapellbrucke", name="Kapellbrücke, Lucerne", kind="sight",
         lat=47.05170, lon=8.30780, days=[1],
         what="The covered wooden bridge, 1365. Five minutes on foot from the pier.",
         osm=('way[man_made=bridge][name~"Kapell",i]', 400)),

    # --- day 2, Rigi -------------------------------------------------------
    dict(slug="weggis-cablecar", name="Weggis cable-car station", kind="lift",
         lat=47.03480, lon=8.42950, days=[2],
         what="Luftseilbahn Weggis-Rigi Kaltbad. First car up on a Sunday 08:10, then every half hour.",
         osm=('nwr[aerialway=station][name~"Weggis",i]', 2000)),
    dict(slug="rigi-kaltbad", name="Rigi Kaltbad", kind="station",
         lat=47.04660, lon=8.46920, days=[2],
         what="Change from the cable car to the cogwheel. Kanzeli viewpoint is an hour from here, gentle.",
         osm=('nwr[railway=station][name~"Kaltbad",i]', 900)),
    dict(slug="rigi-kanzeli", name="Kanzeli viewpoint", kind="sight",
         lat=47.04520, lon=8.46650, days=[2],
         what="The easy one for the parents. Paved, level, straight out over the lake.",
         osm=('nwr[tourism=viewpoint]', 700)),
    # The pin is the station, not the 1,798 m summit cairn 271 m north of it:
    # the station is where the cog railway stops and where you stand.
    dict(slug="rigi-kulm", name="Rigi Kulm", kind="station",
         lat=47.05516, lon=8.48539, days=[2],
         what="1,798 m. Queen of the Mountains. Summit paths are paved with a marked easier route.",
         osm=('nwr[name~"Rigi Kulm",i]', 2000)),

    # --- day 3, over the Brünig -------------------------------------------
    dict(slug="lungern-lakeside", name="Lungern, old Brünigstrasse", kind="sight",
         lat=46.78670, lon=8.15830, days=[3],
         what="Free, and the reason to leave the main road: the 2012 bypass tunnel hides the lake entirely.",
         osm=('nwr[place~"village|town"][name="Lungern"]', 2500)),
    dict(slug="meiringen", name="Meiringen", kind="town",
         lat=46.72690, lon=8.18360, days=[3, 7],
         what="Passed through on the way over, and the gorge stop on the way back.",
         osm=('nwr[place~"village|town"][name="Meiringen"]', 2500)),
    dict(slug="grindelwald-village", name="Grindelwald", kind="town",
         lat=46.62440, lon=8.04140, days=[3, 4, 5, 6, 7],
         what="Base for nights 3 to 6. Book this one first: the Jodelfest fills the village 11-13 September.",
         osm=('nwr[place~"village|town"][name="Grindelwald"]', 2500)),

    # --- day 4, Männlichen and the Panoramaweg ----------------------------
    dict(slug="grindelwald-terminal", name="Grindelwald Terminal", kind="lift",
         lat=46.61110, lon=8.05530, days=[4, 5],
         what="Both gondolas leave from here. Bus 121 is free on the guest card.",
         osm=('nwr[name~"Grindelwald Terminal",i]', 2000)),
    dict(slug="mannlichen-top", name="Männlichen gondola station", kind="lift",
         lat=46.61360, lon=7.94110, days=[4],
         what="Top of the 19-25 minute gondola. The Panoramaweg starts at the door.",
         osm=('nwr[aerialway=station][name~"nnlichen",i]', 1200)),
    dict(slug="mannlichen-summit", name="Männlichen summit", kind="summit",
         lat=46.61560, lon=7.93640, days=[4],
         what="2,343 m. The Royal Walk up is 20 minutes on a made path, and optional.",
         osm=('nwr[natural=peak][name~"nnlichen",i]', 1500)),
    dict(slug="kleine-scheidegg", name="Kleine Scheidegg", kind="station",
         lat=46.58530, lon=7.96140, days=[4, 5],
         what="End of the Panoramaweg. Cog train down to Grindelwald Grund from here.",
         osm=('nwr[railway=station][name~"Scheidegg",i]', 900)),
    dict(slug="grindelwald-grund", name="Grindelwald Grund", kind="station",
         lat=46.61160, lon=8.05430, days=[4],
         what="Where the cog train from Kleine Scheidegg puts you down, beside the Terminal.",
         osm=('nwr[name~"Grindelwald Grund",i]', 2000)),

    # --- day 5, Jungfraujoch ----------------------------------------------
    dict(slug="eigergletscher", name="Eigergletscher", kind="station",
         lat=46.57580, lon=7.97140, days=[5],
         what="Eiger Express drops you here in 15 minutes. Change to the cog railway into the mountain.",
         osm=('nwr[railway=station][name~"Eigergletscher",i]', 900)),
    dict(slug="jungfraujoch", name="Jungfraujoch, Top of Europe", kind="summit",
         lat=46.54750, lon=7.98060, days=[5],
         what="3,454 m, the highest railway station in Europe. Altitude sickness is common here: go slow.",
         osm=('nwr[name~"Jungfraujoch",i]', 3000)),

    # --- day 6, Schilthorn -------------------------------------------------
    dict(slug="stechelberg-parking", name="Schilthornbahn car park, Stechelberg", kind="parking",
         lat=46.55860, lon=7.90460, days=[6],
         what="CHF 11 for the day, open-air, no height limit. 22 km and about 30 minutes from Grindelwald.",
         osm=('nwr[amenity=parking]', 500)),
    dict(slug="gimmelwald", name="Gimmelwald", kind="lift",
         lat=46.54600, lon=7.89420, days=[6],
         what="First change on the way up. The cable car stops here whether you want it to or not.",
         osm=('nwr[aerialway=station][name~"Gimmelwald",i]', 2000)),
    dict(slug="murren-schilthornbahn", name="Mürren, Schilthornbahn station", kind="lift",
         lat=46.56060, lon=7.89250, days=[6],
         what="Second change. Mürren is car-free, and worth the walk through it.",
         osm=('nwr[aerialway=station]', 700)),
    dict(slug="birg", name="Birg", kind="lift",
         lat=46.55350, lon=7.85170, days=[6],
         what="2,677 m. Thrill Walk cliff gangway is included and takes 20 minutes.",
         osm=('nwr[name~"Birg",i]', 3000)),
    dict(slug="schilthorn", name="Schilthorn, Piz Gloria", kind="summit",
         lat=46.55560, lon=7.83520, days=[6],
         what="2,970 m. The revolving restaurant: brunch is CHF 38 alone, reservation mandatory.",
         osm=('nwr[natural=peak][name~"Schilthorn",i]', 1500)),
    dict(slug="murren-blm", name="Mürren BLM station", kind="station",
         lat=46.55890, lon=7.89620, days=[6],
         what="The other end of the village. Little mountain railway along the cliff to Grütschalp.",
         osm=('nwr[railway=station][name~"rren",i]', 900)),
    dict(slug="grutschalp", name="Grütschalp", kind="station",
         lat=46.59470, lon=7.90340, days=[6],
         what="Change to the cable car down. This is why the ticket to ask for is tariff D, the Rundreise.",
         osm=('nwr[name~"tschalp",i]', 3000)),
    dict(slug="lauterbrunnen", name="Lauterbrunnen", kind="station",
         lat=46.59360, lon=7.90880, days=[6],
         what="Bottom of the cable car. Bus 141 back down the valley to the van at Stechelberg.",
         osm=('nwr[railway=station][name~"Lauterbrunnen",i]', 900)),

    # --- day 7, the gorge and back ----------------------------------------
    dict(slug="aareschlucht-west", name="Aareschlucht West", kind="gorge",
         lat=46.72610, lon=8.19260, days=[7],
         what="Enter here. Walkways bolted to the wall of a limestone slot, 1.4 km through.",
         osm=('nwr[name~"Aareschlucht West",i]', 3000)),
    dict(slug="aareschlucht-ost", name="Aareschlucht Ost", kind="gorge",
         lat=46.72920, lon=8.21140, days=[7],
         what="Come out here, or turn round. There is a train back one stop if legs have had enough.",
         osm=('nwr[name~"Aareschlucht Ost",i]', 3000)),
    dict(slug="pr-matten", name="P+R Matten, Interlaken", kind="parking",
         lat=46.67720, lon=7.86690, days=[7],
         what="Where the Jodelfest notice sends cars: no parking in Grindelwald 11-13 September.",
         osm=('nwr[amenity=parking]', 900)),
    dict(slug="kloten-welcomeinn", name="Welcome Inn Hotel, Kloten", kind="hotel",
         lat=47.45170, lon=8.58540, days=[7, 8],
         what="Last night, free parking, close enough to the airport for an early flight.",
         osm=('nwr[tourism=hotel][name~"Welcome",i]', 1500)),

    # --- day 8, the van goes back -----------------------------------------
    dict(slug="zrh-parking3", name="Zurich Airport, Parking 3", kind="parking",
         lat=47.44630, lon=8.55780, days=[8],
         what="Rental returns, accepted 24/7. About 10 minutes on foot back to the terminal.",
         osm=('nwr[amenity=parking]', 700)),

    # --- Plan B, days deliberately empty ----------------------------------
    dict(slug="fluelen", name="Flüelen", kind="swap",
         lat=46.90220, lon=8.62500, days=[],
         what="Rigi in cloud: boat down the dramatic far end of the lake and back by train, about CHF 27.",
         osm=('nwr[place~"village|town"][name~"^Fl",i]', 2500)),
    dict(slug="grindelwald-first", name="Grindelwald First", kind="swap",
         lat=46.65900, lon=8.05400, days=[],
         what="Any washed-out day. Cliff Walk free with the gondola. Skip the Flyer, 2-3 hour queues.",
         osm=('nwr[name~"^First|Grindelwald First",i]', 3000)),
    dict(slug="blausee", name="Blausee", kind="swap",
         lat=46.53270, lon=7.66440, days=[],
         what="The real bad-weather day: 49 km and 50 minutes from Grindelwald, and better than First for the parents.",
         osm=('nwr[name="Blausee"]', 2000)),
]

# --------------------------------------------------------------------------- #
# The legs. Every movement in the plan, with where its geometry comes from.
#
#   mode   drive | walk | lift | boat
#   via    extra points a drive must pass through
#   osm    Overpass filter naming the real railway or cableway to trace
# --------------------------------------------------------------------------- #

LEGS = [
    dict(day=0, mode="walk", a="zrh-terminal", b="zrh-radisson",
         label="Covered indoor walkway"),

    dict(day=1, mode="drive", a="zrh-rental", b="weggis-village",
         label="A4 south past Zug, exit Küssnacht am Rigi"),
    dict(day=1, mode="walk", a="weggis-village", b="weggis-pier",
         label="Down to the pier"),
    dict(day=1, mode="boat", a="weggis-pier", b="lucerne-pier",
         label="SGV boat across the lake"),
    dict(day=1, mode="walk", a="lucerne-pier", b="lucerne-kapellbrucke",
         label="Into the old town"),

    dict(day=2, mode="walk", a="weggis-village", b="weggis-cablecar",
         label="Through the village to the cable car"),
    dict(day=2, mode="lift", a="weggis-cablecar", b="rigi-kaltbad",
         label="Cable car, Weggis to Rigi Kaltbad",
         osm='way[aerialway]'),
    dict(day=2, mode="lift", a="rigi-kaltbad", b="rigi-kulm",
         label="Cogwheel to the summit, hourly",
         osm='way[railway~"narrow_gauge|rail"][!service]'),
    dict(day=2, mode="walk", a="rigi-kulm", b="rigi-kaltbad",
         label="Rigi Panorama Trail, 6 km, wide gravel, mostly downhill"),
    dict(day=2, mode="walk", a="rigi-kaltbad", b="rigi-kanzeli",
         label="Kanzeli, about an hour return, gentle"),

    dict(day=3, mode="drive", a="weggis-village", b="grindelwald-village",
         via=["lungern-lakeside", "meiringen"],
         label="A2 to Hergiswil, A8 over the Brünig, the old lakeside road at Lungern"),

    dict(day=4, mode="walk", a="grindelwald-village", b="grindelwald-terminal",
         label="Walk or bus 121, free on the guest card"),
    dict(day=4, mode="lift", a="grindelwald-terminal", b="mannlichen-top",
         label="Männlichen gondola, 19-25 min",
         osm='way[aerialway]'),
    dict(day=4, mode="walk", a="mannlichen-top", b="mannlichen-summit",
         label="Royal Walk, 20 min up a made path"),
    dict(day=4, mode="walk", a="mannlichen-top", b="kleine-scheidegg",
         label="Panoramaweg, 4.9 km, 1h45, the whole Eiger wall on your left"),
    dict(day=4, mode="lift", a="kleine-scheidegg", b="grindelwald-grund",
         label="Cog railway down",
         osm='way[railway~"narrow_gauge|rail"][!service]'),

    dict(day=5, mode="lift", a="grindelwald-terminal", b="eigergletscher",
         label="Eiger Express gondola, 15 min",
         osm='way[aerialway]'),
    dict(day=5, mode="lift", a="eigergletscher", b="jungfraujoch",
         label="Cog railway through the Eiger, 26-30 min",
         osm='way[railway~"narrow_gauge|rail"][!service]'),

    dict(day=6, mode="drive", a="grindelwald-village", b="stechelberg-parking",
         label="22 km down the valley and up the other one, about 30 min"),
    dict(day=6, mode="lift", a="stechelberg-parking", b="gimmelwald",
         label="Cable car, first section",
         osm='way[aerialway]'),
    dict(day=6, mode="lift", a="gimmelwald", b="murren-schilthornbahn",
         label="Cable car, second section",
         osm='way[aerialway]'),
    dict(day=6, mode="lift", a="murren-schilthornbahn", b="birg",
         label="Cable car to Birg",
         osm='way[aerialway]'),
    dict(day=6, mode="lift", a="birg", b="schilthorn",
         label="Last section to Piz Gloria",
         osm='way[aerialway]'),
    dict(day=6, mode="walk", a="murren-schilthornbahn", b="murren-blm",
         label="Through car-free Mürren, end to end"),
    dict(day=6, mode="lift", a="murren-blm", b="grutschalp",
         label="Cliff railway to Grütschalp",
         osm='way[railway~"narrow_gauge|rail"][!service]'),
    dict(day=6, mode="lift", a="grutschalp", b="lauterbrunnen",
         label="Cable car down to the valley",
         osm='way[aerialway]'),
    # Bus 141 carries them back up the valley to the van; the road it takes is the
    # one drawn below, so it is not given a line of its own.
    dict(day=6, mode="drive", a="stechelberg-parking", b="grindelwald-village",
         label="Back over to Grindelwald, the same 22 km in reverse"),

    dict(day=7, mode="drive", a="grindelwald-village", b="aareschlucht-west",
         label="Down to Meiringen, about 30 min"),
    dict(day=7, mode="walk", a="aareschlucht-west", b="aareschlucht-ost",
         label="Through the gorge, 1.4 km of walkway"),
    dict(day=7, mode="drive", a="aareschlucht-west", b="kloten-welcomeinn",
         label="Back over the Brünig, past Lucerne, A4 to Kloten"),

    dict(day=8, mode="drive", a="kloten-welcomeinn", b="zrh-parking3",
         label="Rental return, then 10 minutes on foot to the terminal"),
    dict(day=8, mode="walk", a="zrh-parking3", b="zrh-terminal",
         label="On foot back to check-in"),
]

# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #

R_EARTH = 6371008.8


def haversine(a, b) -> float:
    """Metres between two (lat, lon) points."""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def _xy(pt, lat0):
    """Local flat projection in metres. Good to centimetres over one valley."""
    return (math.radians(pt[1]) * R_EARTH * math.cos(math.radians(lat0)),
            math.radians(pt[0]) * R_EARTH)


def simplify(points: list, tol: float = SIMPLIFY_M) -> list:
    """Douglas-Peucker, iterative so a 1,772-point drive cannot blow the stack."""
    if len(points) < 3:
        return list(points)
    lat0 = points[0][0]
    xy = [_xy(p, lat0) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        (x1, y1), (x2, y2) = xy[i], xy[j]
        dx, dy = x2 - x1, y2 - y1
        seg = math.hypot(dx, dy)
        worst, wi = -1.0, -1
        for k in range(i + 1, j):
            x0, y0 = xy[k]
            if seg == 0:
                d = math.hypot(x0 - x1, y0 - y1)
            else:
                d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / seg
            if d > worst:
                worst, wi = d, k
        if worst > tol:
            keep[wi] = True
            stack.append((i, wi))
            stack.append((wi, j))
    return [p for p, k in zip(points, keep) if k]


def round_geom(points: list) -> list:
    return [[round(p[0], COORD_DP), round(p[1], COORD_DP)] for p in points]


def length_m(points: list) -> float:
    return sum(haversine(points[i], points[i + 1]) for i in range(len(points) - 1))


# --------------------------------------------------------------------------- #
# The two services
# --------------------------------------------------------------------------- #

def _fetch(url: str, data=None, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_calls = {"overpass": 0, "osrm": 0, "valhalla": 0}
_live: list = []


def say(msg: str) -> None:
    """Progress, printed as it happens. A silent build looks like a hung one."""
    print(msg, flush=True)


def _probe() -> list:
    """Find the mirrors that answer, once.

    A dead mirror does not refuse, it hangs until the socket times out. Trying
    all three on every one of the fifty-odd queries below turns a two-minute
    build into a forty-minute one, so they are sorted out first and only the
    live ones are used after that.
    """
    if _live:
        return _live
    probe = urllib.parse.urlencode(
        {"data": "[out:json][timeout:10];node(46.620,8.040,46.625,8.045);out count;"}
    ).encode()
    for mirror in OVERPASS:
        # Twice, because the public instance answers a burst with 429 and one
        # unlucky moment should not write a working mirror off for the run.
        for attempt in range(2):
            start = time.time()
            try:
                _fetch(mirror, probe, timeout=25)
                _live.append(mirror)
                say(f"  overpass  live  {time.time() - start:5.1f}s  {mirror}")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 0:
                    time.sleep(5)
                    continue
                say(f"  overpass  down  {type(exc).__name__:18s}  {mirror}")
    if not _live:
        raise RuntimeError("no Overpass mirror answered the probe")
    return _live


def overpass(body: str) -> list:
    """Run an Overpass query against a mirror known to be alive.

    The public instance rate-limits a burst, so calls are paced and a refusal
    backs off rather than failing the build.
    """
    query = f"[out:json][timeout:60];{body}"
    payload = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(3):
        for mirror in _probe():
            try:
                raw = _fetch(mirror, payload)
                _calls["overpass"] += 1
                time.sleep(PACE_S)
                return json.loads(raw)["elements"]
            except Exception as exc:  # noqa: BLE001 - any failure means try the next
                last = f"{type(exc).__name__}: {exc}"
        wait = 3 + attempt * 5
        say(f"    overpass backing off {wait}s after {last}")
        time.sleep(wait)
    raise RuntimeError(f"every live Overpass mirror failed: {last}")


def osrm_route(profile: str, coords: list) -> dict:
    """Real road geometry between a list of (lat, lon) points.

    Driving only. The public demo server is built with the car profile alone and
    answers /foot/ and /walking/ with the identical car route rather than an
    error, which is how a 1.4 km walk through the Aare gorge came back as a
    17.5 km drive round the outside of it. Walking goes to Valhalla below.
    """
    assert profile == "driving", "the OSRM demo server only knows how to drive"
    pairs = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
    url = f"{OSRM}/route/v1/{profile}/{pairs}?overview=full&geometries=geojson"
    for attempt in range(4):
        try:
            d = json.loads(_fetch(url))
            if d.get("code") != "Ok":
                raise RuntimeError(d.get("code", "no code"))
            r = d["routes"][0]
            _calls["osrm"] += 1
            return {
                "geometry": [[lat, lon] for lon, lat in r["geometry"]["coordinates"]],
                "km": r["distance"] / 1000.0,
                "min": r["duration"] / 60.0,
            }
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                raise RuntimeError(f"OSRM {profile} failed: {exc}") from exc
            time.sleep(2 + attempt * 3)
    raise AssertionError("unreachable")


def _decode6(encoded: str) -> list:
    """Valhalla's encoded polyline, six decimal places rather than Google's five."""
    points, lat, lon, i = [], 0, 0, 0
    while i < len(encoded):
        for axis in range(2):
            shift, result = 0, 0
            while True:
                b = ord(encoded[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if axis == 0:
                lat += delta
            else:
                lon += delta
        points.append([lat / 1e6, lon / 1e6])
    return points


def valhalla_route(costing: str, coords: list) -> dict:
    """Pedestrian and car routing that respects the profile it is given.

    Used for every walk. On the Panoramaweg it returns 4.80 km against the
    signed 4.9 km, and on Kaltbad to Kanzeli 0.46 km, both of which are the
    walks people actually do.
    """
    body = json.dumps({
        "locations": [{"lat": la, "lon": lo, "type": "break"} for la, lo in coords],
        "costing": costing,
        "directions_options": {"units": "kilometers"},
    }).encode()
    for attempt in range(4):
        try:
            d = json.loads(_fetch(VALHALLA, body))
            trip = d["trip"]
            geom = []
            for leg in trip["legs"]:
                pts = _decode6(leg["shape"])
                geom.extend(pts[1:] if geom else pts)
            _calls["valhalla"] += 1
            time.sleep(PACE_S)
            return {"geometry": geom,
                    "km": trip["summary"]["length"],
                    "min": trip["summary"]["time"] / 60.0}
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                raise RuntimeError(f"Valhalla {costing} failed: {exc}") from exc
            time.sleep(3 + attempt * 4)
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------- #
# Stitching OSM ways into one line
# --------------------------------------------------------------------------- #

def stitch(ways: list, a, b):
    """Find the shortest chain of OSM ways running from a to b.

    An aerial cableway is straight and would survive almost any method, but a
    rack railway switches back on itself and its valley is full of other track:
    the Kleine Scheidegg query returns 93 ways, of which perhaps eight are the
    line you ride. Chaining greedily from whichever way starts nearest simply
    wanders off down a siding, which is why every railway in the first build
    came back as a straight line.

    So this is a shortest-path search instead. Way endpoints are the nodes,
    ways are the edges weighted by their own length, and Dijkstra runs from the
    node nearest a to the node nearest b. A branch that leads nowhere costs
    nothing, because the search never has to commit to it.
    """
    lines = [[(g["lat"], g["lon"]) for g in w.get("geometry", [])]
             for w in ways if len(w.get("geometry", [])) > 1]
    if not lines:
        return None

    # Endpoints within a few metres of each other are the same junction. OSM ways
    # that meet share a node exactly, so this only forgives rounding.
    def key(p):
        return (round(p[0], 6), round(p[1], 6))

    graph: dict = {}
    for line in lines:
        ka, kb = key(line[0]), key(line[-1])
        if ka == kb:
            continue
        w = length_m(line)
        graph.setdefault(ka, []).append((kb, w, line))
        graph.setdefault(kb, []).append((ka, w, line[::-1]))
    if not graph:
        return None

    start = min(graph, key=lambda k: haversine(k, a))
    goal = min(graph, key=lambda k: haversine(k, b))
    # Both ends have to be somewhere near the stations, or this is the wrong track.
    if haversine(start, a) > 500 or haversine(goal, b) > 500 or start == goal:
        return None

    import heapq
    dist = {start: 0.0}
    prev: dict = {}
    queue = [(0.0, start)]
    seen = set()
    while queue:
        d, node = heapq.heappop(queue)
        if node in seen:
            continue
        seen.add(node)
        if node == goal:
            break
        for nxt, w, line in graph.get(node, ()):
            nd = d + w
            if nd < dist.get(nxt, float("inf")):
                dist[nxt] = nd
                prev[nxt] = (node, line)
                heapq.heappush(queue, (nd, nxt))
    if goal not in dist:
        return None

    chain: list = []
    node = goal
    while node != start:
        node, line = prev[node]
        chain = list(line) + (chain[1:] if chain else [])
    if len(chain) < 2:
        return None
    if haversine(chain[0], a) > 500 or haversine(chain[-1], b) > 500:
        return None
    return [list(p) for p in chain]


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def bbox(points: list, pad_deg: float = 0.01) -> str:
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return (f"({min(lats) - pad_deg:.5f},{min(lons) - pad_deg:.5f},"
            f"{max(lats) + pad_deg:.5f},{max(lons) + pad_deg:.5f})")


def reuse_stops() -> dict:
    """Take the stop coordinates from the geo.json already on disk.

    Snapping is the expensive half of the build and the settled half: pins do not
    move once they are on the right feature. When only the routes are being
    rebuilt this skips thirty-odd Overpass calls and leaves the pins exactly where
    the last verified run put them. Prose and naming still come from the seed
    below, which is the copy under version control; only the coordinate and its
    provenance are reused.
    """
    old = json.loads(OUT.read_text(encoding="utf-8"))["stops"]
    stops = {}
    for s in STOPS:
        prev = old.get(s["slug"])
        if prev is None:
            raise SystemExit(f"--reuse-stops: {s['slug']} is not in the existing "
                             f"geo.json; run a full build first")
        keep = str(prev.get("source", "")).startswith("osm:")
        stops[s["slug"]] = {
            "name": s["name"], "kind": s["kind"],
            "lat": prev["lat"] if keep else round(s["lat"], COORD_DP),
            "lon": prev["lon"] if keep else round(s["lon"], COORD_DP),
            "days": s["days"], "what": s["what"],
            "source": prev["source"] if keep else "seed",
            "moved_m": prev["moved_m"] if keep else 0,
        }
        say(f"  {s['slug']:24s} {'reused  ' + prev['source'] if keep else 'from the seed'}")
    return stops


def snap_stops() -> dict:
    """Verify every seeded coordinate against OpenStreetMap.

    A seed that cannot be checked is kept and said so. The public Overpass
    instance is congested often enough that letting it fail the whole build would
    mean the map could not be rebuilt on a bad afternoon, and a hand-authored
    coordinate is a worse answer than a verified one but a far better answer than
    no geo.json at all.
    """
    # What a stop falls back to when it cannot be checked. The hand-typed seed is
    # the worst of the available answers: if a previous run verified this pin
    # against OpenStreetMap, that coordinate is better than the guess it replaced,
    # and losing it to a bad afternoon on Overpass is a regression.
    verified = {}
    if OUT.exists():
        try:
            for slug, prev in json.loads(OUT.read_text(encoding="utf-8"))["stops"].items():
                if str(prev.get("source", "")).startswith("osm:"):
                    verified[slug] = prev
        except (ValueError, KeyError):
            pass

    stops = {}
    for s in STOPS:
        prev = verified.get(s["slug"])
        if prev is not None:
            s["lat"], s["lon"] = prev["lat"], prev["lon"]
        seed = (s["lat"], s["lon"])
        source = prev["source"] if prev else "seed"
        moved = 0.0
        if s.get("osm"):
            filt, radius = s["osm"]
            try:
                els = overpass(
                    f"{filt}(around:{radius},{seed[0]:.5f},{seed[1]:.5f});out center 20;")
            except RuntimeError:
                els = None
                kept = "the verified pin" if prev else "the seed"
                say(f"  {s['slug']:24s} UNVERIFIED  overpass unreachable, kept {kept}")
                if not prev:
                    source = "seed (unverified)"
            best, bd = None, 1e9
            for e in els or ():
                c = e.get("center") or e
                if "lat" not in c:
                    continue
                d = haversine(seed, (c["lat"], c["lon"]))
                if d < bd:
                    best, bd = (e, (c["lat"], c["lon"])), d
            if els is None:
                pass  # already reported; the previous answer stands
            elif best is None:
                say(f"  {s['slug']:24s} NO MATCH within {radius} m, kept "
                    f"{'the verified pin' if prev else 'the seed'}")
            else:
                el, pt = best
                source = f"osm:{el['type']}/{el['id']}"
                moved = bd
                s["lat"], s["lon"] = round(pt[0], COORD_DP), round(pt[1], COORD_DP)
                flag = "   <-- check" if bd > radius * 0.7 else ""
                say(f"  {s['slug']:24s} {source:22s} moved {bd:6.0f} m{flag}")
        stops[s["slug"]] = {
            "name": s["name"], "kind": s["kind"],
            "lat": round(s["lat"], COORD_DP), "lon": round(s["lon"], COORD_DP),
            "days": s["days"], "what": s["what"], "source": source,
            "moved_m": round(moved),
        }
    return stops


def build_legs(stops: dict) -> list:
    out = []
    for leg in LEGS:
        a, b = stops[leg["a"]], stops[leg["b"]]
        pa, pb = (a["lat"], a["lon"]), (b["lat"], b["lon"])
        geom, km, mins, src = None, None, None, None

        if leg.get("osm"):
            try:
                els = overpass(f"{leg['osm']}{bbox([pa, pb], 0.035)};out geom;")
            except RuntimeError:
                els = []
                say(f"    day {leg['day']} {leg['a']} -> {leg['b']}: overpass "
                    f"unreachable, falling back")
            geom = stitch(els, pa, pb)
            if geom is None:
                say(
                    f"    day {leg['day']} {leg['a']} -> {leg['b']}: OSM trace failed "
                    f"({len(els)} ways), falling back")
            else:
                src = f"osm/{len(els)}ways"

        if geom is None and leg["mode"] in ("walk", "drive"):
            pts = [pa] + [(stops[v]["lat"], stops[v]["lon"]) for v in leg.get("via", [])] + [pb]
            if leg["mode"] == "walk":
                r = valhalla_route("pedestrian", pts)
                src = "valhalla/pedestrian"
            else:
                try:
                    r = osrm_route("driving", pts)
                    src = "osrm/driving"
                except RuntimeError:
                    r = valhalla_route("auto", pts)
                    src = "valhalla/auto"
            geom, km, mins = r["geometry"], r["km"], r["min"]

        if geom is None and leg.get("osm") and OUT.exists():
            try:
                for old in json.loads(OUT.read_text(encoding="utf-8"))["legs"]:
                    if (old["from"], old["to"], old["mode"]) != (leg["a"], leg["b"], leg["mode"]):
                        continue
                    if not str(old.get("source", "")).startswith("osm"):
                        continue
                    ends = old["geometry"][0], old["geometry"][-1]
                    if haversine(ends[0], pa) < 250 and haversine(ends[1], pb) < 250:
                        geom, src = old["geometry"], old["source"]
                        say(f"    day {leg['day']} {leg['a']} -> {leg['b']}: kept the "
                            f"trace from the last build")
                    break
            except (ValueError, KeyError):
                pass

        if geom is None:
            geom, src = [list(pa), list(pb)], "straight"

        simple = round_geom(simplify(geom))
        if km is None:
            km = length_m(geom) / 1000.0
        row = {
            "day": leg["day"], "mode": leg["mode"],
            "from": leg["a"], "to": leg["b"], "label": leg["label"],
            "km": round(km, 1), "source": src, "geometry": simple,
        }
        if mins is not None:
            row["min"] = round(mins)
        if leg.get("via"):
            row["via"] = leg["via"]
        out.append(row)
        say(
            f"  day {leg['day']} {leg['mode']:5s} {leg['a'] + ' -> ' + leg['b']:48s} "
            f"{km:6.1f} km {len(geom):5d}->{len(simple):4d} pts  {src}")
    return out


def cross_check(legs: list) -> list:
    """Hold the routed drives against the kilometres the itinerary claims."""
    problems = []
    for i, day in enumerate(ITIN["days"]):
        stated = day.get("drive_km") or 0
        routed = sum(l["km"] for l in legs if l["day"] == i and l["mode"] == "drive")
        if stated == 0 and routed == 0:
            continue
        if stated == 0 or routed == 0:
            problems.append(f"day {i}: itinerary says {stated} km, routes say {routed:.1f} km")
            continue
        drift = abs(routed - stated) / stated
        say(f"  day {i}: itinerary {stated:5.0f} km   routed {routed:6.1f} km   "
                      f"{drift * 100:4.0f}%   {'OK' if drift <= 0.15 else 'OFF'}")
        if drift > 0.15:
            problems.append(
                f"day {i}: itinerary says {stated} km, routing says {routed:.1f} km "
                f"({drift * 100:.0f}% apart)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="fetch and report, write nothing")
    ap.add_argument("--reuse-stops", action="store_true",
                    help="keep the pins from the existing geo.json and rebuild only "
                         "the routes (skips about 37 Overpass calls)")
    args = ap.parse_args()

    if args.reuse_stops:
        say("stops: reusing the pins from the existing geo.json")
        stops = reuse_stops()
    else:
        say("overpass: which mirrors are alive")
        _probe()
        say("\nstops: verifying seeds against OpenStreetMap")
        stops = snap_stops()

    say("\nlegs: tracing geometry")
    legs = build_legs(stops)

    say("\ncross-check: routed drives against itinerary.json")
    problems = cross_check(legs)
    for p in problems:
        say(f"  PROBLEM {p}")

    doc = {
        "_comment": "Built by tools/build_geo.py. Do not hand-edit geometry; "
                    "edit the seed in the tool and rebuild.",
        "attribution": {
            "basemap": "© swisstopo",
            "geometry": "© OpenStreetMap contributors (ODbL), routing by OSRM",
        },
        "tiles": {
            "light": "ch.swisstopo.pixelkarte-farbe",
            "dark": "ch.swisstopo.pixelkarte-grau",
            "url": "https://wmts.geo.admin.ch/1.0.0/{layer}/default/current/3857/{z}/{x}/{y}.jpeg",
            "max_zoom": 17,
        },
        "stops": stops,
        "legs": legs,
    }

    moved = [s for s in stops.values() if s["moved_m"] > 300]
    pts = sum(len(l["geometry"]) for l in legs)
    matched = sum(1 for s in stops.values() if str(s["source"]).startswith("osm:"))
    print(f"\n{len(stops)} stops ({matched} matched in OSM), {len(legs)} legs, "
          f"{pts:,} points after simplifying; "
          f"{_calls['overpass']} Overpass and {_calls['osrm']} OSRM calls")
    if moved:
        print("moved more than 300 m from the seed: "
              + ", ".join(f"{s['name']} ({s['moved_m']} m)" for s in moved))

    if args.dry:
        print("\n--dry: nothing written")
        return 1 if problems else 0

    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"\nwrote {OUT.name}, {OUT.stat().st_size / 1024:.0f} KB")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
