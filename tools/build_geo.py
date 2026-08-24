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
    # --- day 0, the landing, and days 7-9 back at the airport --------------
    dict(slug="zrh-terminal", name="Zurich Airport, Arrivals", kind="airport",
         lat=47.45045, lon=8.56198, days=[0, 1, 9],
         what="Four land here Friday at 20:30 and the fifth at 06:30 on Saturday. Back once more on the 13th, at the terminal by 08:45.",
         osm=('nwr[aeroway=terminal]', 900)),
    dict(slug="kloten-hotel", name="Kloten, the airport hotel", kind="hotel",
         lat=47.44896, lon=8.58595, days=[0, 7, 8, 9],
         what="The Zleep on the first night, booked. The 11th and 12th are still open, and five people will not fit in two Zleep rooms.",
         osm=('nwr[tourism=hotel][name~"Zleep",i]', 2500)),

    # --- day 1, the van, Zug and the move to Schwyz ------------------------
    dict(slug="zrh-rental", name="Rental Centre, Airport Center Level 1", kind="parking",
         lat=47.45010, lon=8.56420, days=[0],
         what="The 7-seat VW Caddy is collected here on landing, on the Friday evening, and is due back at noon on Friday the 11th.",
         osm=('nwr[amenity=car_rental]', 900)),
    dict(slug="zug-altstadt", name="Zug, the old town and the lakefront", kind="town",
         lat=47.16830, lon=8.51750, days=[1],
         what="Late morning on the way south. Flat, paved and full of benches, with the Zytturm to look at and the Kirschtorte to eat.",
         osm=('nwr[place~"town|city"][name="Zug"]', 2500)),
    dict(slug="schwyz-town", name="Schwyz, the Historical apartment", kind="hotel",
         lat=47.02070, lon=8.65300, days=[1, 2, 3],
         what="Booked, 5-7 September. Self check-in by key box at 16:00. Brunnen is five minutes, Weggis about thirty, Lucerne forty-five.",
         osm=('nwr[place~"village|town"][name="Schwyz"]', 2500)),
    dict(slug="brunnen-quay", name="Brunnen quay", kind="pier",
         lat=47.00330, lon=8.60530, days=[1],
         what="Five minutes from the apartment, where the lake turns the corner into the Urnersee. The view on all the old posters.",
         osm=('nwr[amenity=ferry_terminal]', 900)),

    # --- day 2, Rigi from Weggis and the boat into Lucerne -----------------
    dict(slug="weggis-village", name="Weggis", kind="town",
         lat=47.03330, lon=8.43330, days=[2],
         what="Half an hour round the lake from Schwyz. Park at the pier - CHF 13 for 24 hours, and it is not free.",
         osm=('nwr[place~"village|town"][name="Weggis"]', 2500)),
    dict(slug="weggis-cablecar", name="Weggis cable-car station", kind="lift",
         lat=47.03480, lon=8.42950, days=[2],
         what="Luftseilbahn Weggis-Rigi Kaltbad. Check the board: a Sunday timetable is not a weekday one.",
         osm=('nwr[aerialway=station][name~"Weggis",i]', 2000)),
    dict(slug="rigi-kaltbad", name="Rigi Kaltbad", kind="station",
         lat=47.04660, lon=8.46920, days=[2],
         what="Change from the cable car to the cogwheel. The 4 km walk down from Kulm ends here.",
         osm=('nwr[railway=station][name~"Kaltbad",i]', 900)),
    dict(slug="rigi-kanzeli", name="Kanzeli viewpoint", kind="sight",
         lat=47.04520, lon=8.46650, days=[2],
         what="The easy one. Paved, level, straight out over the lake.",
         osm=('nwr[tourism=viewpoint]', 700)),
    # The pin is the station, not the 1,798 m summit cairn 271 m north of it:
    # the station is where the cog railway stops and where you stand.
    dict(slug="rigi-kulm", name="Rigi Kulm", kind="station",
         lat=47.05516, lon=8.48539, days=[2],
         what="1,798 m. Queen of the Mountains, and exposed - take a layer even if Weggis is warm.",
         osm=('nwr[name~"Rigi Kulm",i]', 2000)),
    dict(slug="weggis-pier", name="Weggis boat pier", kind="pier",
         lat=47.03260, lon=8.43060, days=[2],
         what="SGV boats into Lucerne. The van sits here all day, which is what makes the evening crossing work.",
         osm=('nwr[amenity=ferry_terminal]', 700)),
    dict(slug="lucerne-pier", name="Lucerne, Bahnhofquai pier", kind="pier",
         lat=47.05030, lon=8.31010, days=[2],
         what="The boat arrives beside the station. Last one back leaves at 19:12 and reaches Weggis at 19:53.",
         osm=('nwr[amenity=ferry_terminal]', 500)),
    dict(slug="lucerne-kapellbrucke", name="Kapellbrücke, Lucerne", kind="sight",
         lat=47.05170, lon=8.30780, days=[2],
         what="The covered wooden bridge, 1365. Five minutes on foot from the pier, and best in the early evening.",
         osm=('way[man_made=bridge][name~"Kapell",i]', 400)),

    # --- day 3, over the Brünig to the Jungfrau ----------------------------
    dict(slug="lungern-lakeside", name="Lungern, Obsee", kind="sight",
         lat=46.78670, lon=8.15830, days=[3],
         what="Park in the Obsee car park - 222 spaces, CHF 5 for 24 hours - not the tight unmarked bend at Chälrütirank.",
         osm=('nwr[place~"village|town"][name="Lungern"]', 2500)),
    dict(slug="lungern-turren", name="Turren, above Lungern", kind="lift",
         lat=46.78330, lon=8.14170, days=[3],
         what="Ten minutes by cable car to a terrace facing the Bernese Alps across the lake. Every 20 minutes, and the last one DOWN on a weekday is 17:00.",
         osm=('nwr[aerialway=station][name~"Turren",i]', 2500)),
    dict(slug="meiringen", name="Meiringen", kind="town",
         lat=46.72690, lon=8.18360, days=[3],
         what="Fifteen minutes off the Brünig road, and the reason to come off it.",
         osm=('nwr[place~"village|town"][name="Meiringen"]', 2500)),
    dict(slug="aareschlucht-west", name="Aareschlucht West", kind="gorge",
         lat=46.72610, lon=8.19260, days=[3],
         what="Go in from this end. Free parking that takes a van, and the first half to mid-gorge is level, railed and wheelchair-passable.",
         osm=('nwr[name~"Aareschlucht West",i]', 3000)),
    dict(slug="aareschlucht-ost", name="Aareschlucht Ost", kind="gorge",
         lat=46.72920, lon=8.21140, days=[3],
         what="The far end, 1.4 km and about 45 minutes through. There is a train back one stop if legs have had enough.",
         osm=('nwr[name~"Aareschlucht Ost",i]', 3000)),
    dict(slug="grindelwald-village", name="Grindelwald", kind="town",
         lat=46.62440, lon=8.04140, days=[],
         what="Base for four nights, 7-11 September. Not booked yet: Chalet Adelheid leads at CHF 1,356, five minutes from the Terminal.",
         osm=('nwr[place~"village|town"][name="Grindelwald"]', 2500)),

    # --- day 4, Jungfraujoch ----------------------------------------------
    dict(slug="grindelwald-terminal", name="Grindelwald Terminal", kind="lift",
         lat=46.61482, lon=8.05020, days=[4],
         what="Five minutes from the apartment, or bus 121, free on the guest card. First Eiger Express at 08:00.",
         osm=('nwr[name~"Grindelwald Terminal",i]', 2000)),
    dict(slug="eigergletscher", name="Eigergletscher", kind="station",
         lat=46.57580, lon=7.97140, days=[4],
         what="Fifteen minutes up on the Eiger Express, then change to the cog railway into the mountain. The Eiger Trail starts here on the way down.",
         osm=('nwr[railway=station][name~"Eigergletscher",i]', 900)),
    dict(slug="jungfraujoch", name="Jungfraujoch, Top of Europe", kind="summit",
         lat=46.54750, lon=7.98060, days=[4],
         what="3,454 m, the highest railway station in Europe. The seat reservation is mandatory and altitude sickness is common: go slow.",
         osm=('nwr[name~"Jungfraujoch",i]', 3000)),

    # --- day 5, the Schilthorn round trip via Grütschalp ------------------
    dict(slug="lauterbrunnen", name="Lauterbrunnen", kind="station",
         lat=46.59360, lon=7.90880, days=[5],
         what="Park at the station, CHF 18 for the day. Neither the operator nor the garage publishes a height limit, so measure the van first.",
         osm=('nwr[railway=station][name~"Lauterbrunnen",i]', 900)),
    dict(slug="grutschalp", name="Grütschalp", kind="station",
         lat=46.59470, lon=7.90340, days=[5],
         what="Top of the funicular up the cliff. This is why the ticket to ask for is tariff D, the Rundreise.",
         osm=('nwr[name~"tschalp",i]', 3000)),
    dict(slug="murren-blm", name="Mürren BLM station", kind="station",
         lat=46.55890, lon=7.89620, days=[5],
         what="Twenty minutes along the cliff shelf with the Jungfrau across the valley. The loveliest flat ride in the Oberland.",
         osm=('nwr[railway=station][name~"rren",i]', 900)),
    dict(slug="murren-schilthornbahn", name="Mürren, Schilthornbahn station", kind="lift",
         lat=46.56060, lon=7.89250, days=[5],
         what="The other end of the village, which is car-free and worth the walk through. First car up at 07:40.",
         osm=('nwr[aerialway=station]', 700)),
    dict(slug="birg", name="Birg", kind="lift",
         lat=46.55350, lon=7.85170, days=[5],
         what="2,677 m. The Thrill Walk gangway is included and takes 20 minutes - and its floor is open metal grating.",
         osm=('nwr[name~"Birg",i]', 3000)),
    dict(slug="schilthorn", name="Schilthorn, Piz Gloria", kind="summit",
         lat=46.55560, lon=7.83520, days=[5],
         what="2,970 m. The rebuild finished in April 2026, the cars are new, and the revolving restaurant needs a reservation. Last car off the summit 17:50.",
         osm=('nwr[natural=peak][name~"Schilthorn",i]', 1500)),
    dict(slug="gimmelwald", name="Gimmelwald", kind="lift",
         lat=46.54600, lon=7.89420, days=[5],
         what="On the way back down the other side. The cable car stops here whether you want it to or not.",
         osm=('nwr[aerialway=station][name~"Gimmelwald",i]', 2000)),
    dict(slug="stechelberg-parking", name="Schilthornbahn valley station, Stechelberg", kind="parking",
         lat=46.55860, lon=7.90460, days=[5],
         what="The bottom of the other side, and the cheapest car park of the three: CHF 7 for eight hours, open-air, no height limit. Bus 141 back to Lauterbrunnen.",
         osm=('nwr[amenity=parking]', 500)),

    # --- day 6, First, Bachalpsee, and Harder Kulm in the evening ---------
    dict(slug="grindelwald-first", name="Grindelwald First", kind="lift",
         lat=46.65900, lon=8.05400, days=[],
         what="2,166 m, 25 minutes up in three stages from the middle of the village. The Cliff Walk at the top is free with the gondola.",
         osm=('nwr[name~"^First|Grindelwald First",i]', 3000)),
    dict(slug="bachalpsee", name="Bachalpsee", kind="sight",
         lat=46.66856, lon=8.03055, days=[],
         what="3 km each way on a wide, gently rising path, about fifty minutes out. On a still morning the Wetterhorn stands upside down in it.",
         osm=('nwr[natural=water][name~"Bachalpsee",i]', 2500)),
    dict(slug="interlaken-ost", name="Interlaken Ost", kind="station",
         lat=46.69030, lon=7.86900, days=[],
         what="Half an hour down the valley. The Harder Kulm funicular leaves from beside the station and nearby parking is about CHF 10.",
         osm=('nwr[railway=station][name~"Interlaken Ost",i]', 900)),
    dict(slug="harder-kulm", name="Harder Kulm", kind="summit",
         lat=46.70170, lon=7.86170, days=[],
         what="1,322 m, ten minutes up, flat at the top, the classic two-lakes view. Runs to 21:10 in September, so it is an evening rather than a day.",
         osm=('nwr[name~"Harder Kulm",i]', 2500)),

    # --- day 7, out before the Jodelfest ----------------------------------
    dict(slug="zrh-parking3", name="Zurich Airport, Parking 3", kind="parking",
         lat=47.44630, lon=8.55780, days=[7],
         what="Rental returns, around the clock. The van is due back at 12:00 on the 11th and an hour late can bill as a whole extra day.",
         osm=('nwr[amenity=parking]', 700)),

    # --- days 7 and 8, the two Zurich days --------------------------------
    dict(slug="zurich-hb", name="Zürich Hauptbahnhof", kind="station",
         lat=47.37790, lon=8.54030, days=[7, 8],
         what="Thirteen minutes from the airport. Its shops open on Sundays, which almost nothing else in Switzerland does.",
         # Not [railway=station]: the main station's own node carries
         # public_transport=station, and the railway=station tags sit on the
         # platforms. Anchoring on the name and widening the radius finds it.
         osm=('nwr[name~"Hauptbahnhof|^Z.rich HB$",i]', 1400)),
    dict(slug="zurich-altstadt", name="Zurich Old Town, Lindenhof", kind="town",
         lat=47.37300, lon=8.54100, days=[7, 8],
         what="Flat, paved and small. Lindenhof, the Fraumünster windows, then the lake promenade.",
         osm=('nwr[name="Lindenhof"]', 1200)),
    dict(slug="albisguetli", name="Albisgütli, the Knabenschiessen", kind="sight",
         lat=47.35420, lon=8.50930, days=[8],
         what="Zurich's own festival, 12-14 September, and the 12th is opening day. Free to walk into and it runs until 01:30. Tram 13 stops short, at Laubegg.",
         osm=('nwr[name~"Albisg",i]', 1800)),

    # --- days 3-7, the Kandertal base ------------------------------------
    dict(slug="reichenbach", name="Reichenbach im Kandertal, LaVida Peak", kind="hotel",
         lat=46.61670, lon=7.68330, days=[3, 4, 5, 6, 7],
         what="Booked, 7-11 September. Free parking on the premises. Frutigen is three minutes, Blausee sixteen, Oeschinensee twenty-three, and Grindelwald Terminal seventy-five.",
         osm=('nwr[place~"village|town"][name~"^Reichenbach",i]', 2500)),
    dict(slug="muelenen", name="Mülenen, Niesenbahn valley station", kind="station",
         lat=46.66029, lon=7.63706, days=[5],
         what="Ten minutes down the valley. The funicular climbs to 2,362 m in thirty minutes with a change at Schwandegg.",
         osm=('nwr[railway=station][name~"lenen",i]', 1500)),
    dict(slug="kandersteg-gondola", name="Kandersteg, Oeschinen gondola", kind="lift",
         lat=46.49500, lon=7.68100, days=[6],
         what="Twenty-three minutes up the valley. Parking can be gone by 11:00, and the ascent time slot is mandatory to 20 September.",
         osm=('nwr[aerialway=station][name~"Oeschinen",i]', 2500)),

    # --- Plan B, days deliberately empty ----------------------------------
    dict(slug="fluelen", name="Flüelen", kind="swap",
         lat=46.90220, lon=8.62500, days=[],
         what="Rigi in cloud: boat down the dramatic far end of the lake and back by train, about CHF 27, all of it seated.",
         osm=('nwr[place~"village|town"][name~"^Fl",i]', 2500)),
    dict(slug="blausee", name="Blausee", kind="swap",
         lat=46.53270, lon=7.66440, days=[6],
         what="The washed-out day. A 300 m flat asphalt path from the gate to the water, and the glass-bottomed boat is in the entry price.",
         osm=('nwr[name="Blausee"]', 2000)),
    dict(slug="oeschinensee", name="Oeschinensee", kind="swap",
         lat=46.49830, lon=7.72830, days=[6],
         what="Turquoise water under the Blüemlisalp walls. The ascent time slot is MANDATORY to 20 September and costs CHF 5 - without it you cannot board.",
         osm=('nwr[natural=water][name~"Oeschinensee",i]', 2500)),
    dict(slug="niesen-kulm", name="Niesen Kulm", kind="swap",
         lat=46.64500, lon=7.65170, days=[5],
         what="2,362 m by funicular from Mülenen, thirty minutes. On Wednesday and Friday evenings the last descents run to 23:25, so dinner at the top is possible.",
         osm=('nwr[name~"Niesen",i]', 3000)),
    dict(slug="trummelbach", name="Trümmelbach Falls", kind="swap",
         lat=46.57720, lon=7.90580, days=[],
         what="Ten glacier falls inside the mountain, reached by a lift built into the rock - but stairs past halfway and permanently wet, so turn back at the middle if the footing worries you.",
         osm=('nwr[name~"mmelbach",i]', 2000)),
    dict(slug="mannlichen-top", name="Männlichen", kind="swap",
         lat=46.61360, lon=7.94110, days=[],
         what="A spare day taken gently: gondola up from the Terminal, the almost level Panoramaweg across, cog railway down from Kleine Scheidegg.",
         osm=('nwr[aerialway=station][name~"nnlichen",i]', 1200)),
    dict(slug="kleine-scheidegg", name="Kleine Scheidegg", kind="swap",
         lat=46.58530, lon=7.96140, days=[],
         what="The far end of the Panoramaweg, and the junction every Jungfrau route passes through.",
         osm=('nwr[railway=station][name~"Scheidegg",i]', 900)),
    dict(slug="schynige-platte", name="Schynige Platte", kind="swap",
         lat=46.65530, lon=7.90800, days=[],
         what="A wet morning: a vintage cog railway from Wilderswil and a free alpine garden with over 750 species, with far fewer people than the famous peaks.",
         osm=('nwr[name~"Schynige Platte",i]', 2500)),
    dict(slug="engstligenalp", name="Engstligenalp, above Adelboden", kind="swap",
         lat=46.47500, lon=7.59170, days=[],
         what="The upper Engstligen fall passes directly beneath the cable car cabin, so a 600 m waterfall costs no walking at all. Runs to 18 October, 08:30-17:00.",
         osm=('nwr[aerialway=station][name~"Engstligen",i]', 3000)),
    dict(slug="thun", name="Thun", kind="swap",
         lat=46.75800, lon=7.62800, days=[],
         what="Twenty-five minutes down the valley: a medieval castle over a river town, arcaded streets with the shops up on the arcade roofs, and the lake at the bottom. No ticket, no altitude.",
         osm=('nwr[place~"town|city"][name="Thun"]', 2500)),

]

# --------------------------------------------------------------------------- #
# The legs. Every movement in the plan, with where its geometry comes from.
#
#   mode   drive | walk | lift | boat
#   via    extra points a drive must pass through
#   osm    Overpass filter naming the real railway or cableway to trace
# --------------------------------------------------------------------------- #

LEGS = [
    # --- day 0, the van is taken on landing -------------------------------
    dict(day=0, mode="walk", a="zrh-terminal", b="zrh-rental",
         label="Through to the Rental Centre on Level 1, straight off the flight"),
    dict(day=0, mode="drive", a="zrh-rental", b="kloten-hotel",
         label="Five minutes up the road to the hotel, about half past nine"),

    # --- day 1, back for the fifth traveller, then south -------------------
    dict(day=1, mode="drive", a="kloten-hotel", b="zrh-terminal",
         label="Back to the terminal for the 06:30 arrival - no rental desk to wait for"),
    dict(day=1, mode="drive", a="zrh-terminal", b="schwyz-town",
         via=["zug-altstadt"],
         label="A4 south past the Zugersee, Zug for late morning, Schwyz by about 12:30"),
    dict(day=1, mode="drive", a="schwyz-town", b="brunnen-quay",
         label="Five minutes to Brunnen while check-in waits for four o'clock"),

    # --- day 2, Rigi from Weggis, and Lucerne at dusk ---------------------
    dict(day=2, mode="drive", a="schwyz-town", b="weggis-village",
         label="Half an hour round the lake, and park at the pier"),
    dict(day=2, mode="walk", a="weggis-village", b="weggis-cablecar",
         label="Through the village to the cable-car station"),
    dict(day=2, mode="lift", a="weggis-cablecar", b="rigi-kaltbad",
         label="Luftseilbahn up to Rigi Kaltbad"),
    dict(day=2, mode="lift", a="rigi-kaltbad", b="rigi-kulm",
         label="Cogwheel on to the summit, 1,798 m"),
    dict(day=2, mode="walk", a="rigi-kulm", b="rigi-kanzeli",
         label="For whoever wants it: 4 km down to Kaltbad, about an hour and a quarter"),
    dict(day=2, mode="lift", a="rigi-kaltbad", b="weggis-cablecar",
         label="Back down to Weggis, where the van has been waiting"),
    dict(day=2, mode="boat", a="weggis-pier", b="lucerne-pier",
         label="SGV across the lake, 42 minutes, CHF 11.50 on a Half Fare Card"),
    dict(day=2, mode="walk", a="lucerne-pier", b="lucerne-kapellbrucke",
         label="Five minutes to the Kapellbrücke, best in the early evening"),
    dict(day=2, mode="boat", a="lucerne-pier", b="weggis-pier",
         label="Last boat back at 19:12, into Weggis at 19:53"),
    dict(day=2, mode="drive", a="weggis-village", b="schwyz-town",
         label="Back round the lake to Schwyz, about half an hour"),

    # --- day 3, over the Brünig and on into the Kandertal -----------------
    dict(day=3, mode="drive", a="schwyz-town", b="lungern-lakeside",
         label="A4 to Lucerne, then the A8 south through Sarnen and Giswil"),
    dict(day=3, mode="lift", a="lungern-lakeside", b="lungern-turren",
         label="If the morning is clear: ten minutes up to Turren, last one down 17:00"),
    dict(day=3, mode="drive", a="lungern-lakeside", b="aareschlucht-west",
         via=["meiringen"],
         label="Over the Brünig, then fifteen minutes off the road at Meiringen"),
    dict(day=3, mode="walk", a="aareschlucht-west", b="aareschlucht-ost",
         label="Through the gorge, 1.4 km, the first half level and railed"),
    dict(day=3, mode="drive", a="aareschlucht-west", b="reichenbach",
         label="Brienz, Interlaken, the Thunersee to Spiez, then south into the Kandertal"),

    # --- day 4, the long morning to Jungfraujoch --------------------------
    dict(day=4, mode="drive", a="reichenbach", b="grindelwald-terminal",
         label="Out at 06:40. 46 km and about 75 minutes for the 08:00 first gondola"),
    dict(day=4, mode="lift", a="grindelwald-terminal", b="eigergletscher",
         label="Eiger Express, 15 minutes"),
    dict(day=4, mode="lift", a="eigergletscher", b="jungfraujoch",
         label="Cog railway through the inside of the Eiger, about 25 minutes"),
    dict(day=4, mode="drive", a="grindelwald-terminal", b="reichenbach",
         label="And the same 75 minutes back, which is what this base costs"),

    # --- day 5, the Schilthorn round trip, then the Niesen at dusk --------
    dict(day=5, mode="drive", a="reichenbach", b="lauterbrunnen",
         label="Thirty-eight minutes over to Lauterbrunnen, and park at the station"),
    dict(day=5, mode="lift", a="lauterbrunnen", b="grutschalp",
         label="Funicular up the cliff to Grütschalp"),
    dict(day=5, mode="lift", a="grutschalp", b="murren-blm",
         label="The shelf train to Mürren, twenty flat minutes facing the Jungfrau"),
    dict(day=5, mode="walk", a="murren-blm", b="murren-schilthornbahn",
         label="Through Mürren, which is car-free and worth the walk"),
    dict(day=5, mode="lift", a="murren-schilthornbahn", b="birg",
         label="Up to Birg, 2,677 m, and the Thrill Walk"),
    dict(day=5, mode="lift", a="birg", b="schilthorn",
         label="On to Piz Gloria, 2,970 m. Last car off the summit 17:50"),
    dict(day=5, mode="lift", a="schilthorn", b="gimmelwald",
         label="Down the other side through Birg and Mürren to Gimmelwald"),
    dict(day=5, mode="lift", a="gimmelwald", b="stechelberg-parking",
         label="Last stage to Stechelberg, then bus 141 back up the valley"),
    dict(day=5, mode="drive", a="lauterbrunnen", b="reichenbach",
         label="Back to the Kandertal in the late afternoon"),
    dict(day=5, mode="drive", a="reichenbach", b="muelenen",
         label="Ten minutes down the valley for the evening funicular"),
    dict(day=5, mode="lift", a="muelenen", b="niesen-kulm",
         label="Thirty minutes to 2,362 m. Wednesday descents run to 23:25"),

    # --- day 6, Oeschinensee, and Blausee on the way home -----------------
    dict(day=6, mode="drive", a="reichenbach", b="kandersteg-gondola",
         label="Twenty-three minutes up the valley. Arrive before ten for a space"),
    dict(day=6, mode="lift", a="kandersteg-gondola", b="oeschinensee",
         label="Gondola up, then trail C down to the shore, twenty minutes"),
    dict(day=6, mode="drive", a="kandersteg-gondola", b="blausee",
         label="Blausee is on the way home, which is the whole reason it is today"),
    dict(day=6, mode="drive", a="blausee", b="reichenbach",
         label="Twelve minutes back down the Kandertal"),

    # --- day 7, down the valley and back to Zurich ------------------------
    dict(day=7, mode="drive", a="reichenbach", b="zrh-parking3",
         label="Spiez, Thun, Bern, then the A1 east. Van back by 12:00"),
    dict(day=7, mode="lift", a="zrh-terminal", b="zurich-hb",
         label="Train into the city, 13 minutes, and the afternoon is free"),
    dict(day=7, mode="walk", a="zurich-hb", b="zurich-altstadt",
         label="Over the Limmat into the old town"),

    # --- day 8, Zurich on foot and the Knabenschiessen --------------------
    dict(day=8, mode="lift", a="zrh-terminal", b="zurich-hb",
         label="Thirteen minutes in, and no van to think about"),
    dict(day=8, mode="walk", a="zurich-hb", b="zurich-altstadt",
         label="Lindenhof, the Fraumünster windows, then the lake promenade"),
    dict(day=8, mode="lift", a="zurich-hb", b="albisguetli",
         label="Tram 13 to the festival - it stops at Laubegg, four minutes short"),
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
