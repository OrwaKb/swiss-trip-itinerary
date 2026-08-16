"""Build step: gather freely-licensed photo candidates from Wikimedia Commons.

Run once when the photo set needs changing:

    python tools/fetch_candidates.py <scratch-dir>

Writes <scratch-dir>/candidates.json plus a 480px thumbnail per candidate, so the
shortlist can be looked at before anything is committed. Nothing here runs while the
app is serving — the app only ever reads the finished files in static/.

Only licences that permit reuse with attribution are kept. The licence, the author
and the file page all travel with the candidate so the credit line can be built from
real metadata rather than remembered.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://commons.wikimedia.org/w/api.php"
UA = ("SwissTripItineraryBuild/1.0 "
      "(https://github.com/OrwaKb/swiss-trip-itinerary; mckborwa@gmail.com)")

# Licences that allow reuse with credit. Anything else is dropped rather than judged.
OK_LICENCE = re.compile(
    r"^(cc0|cc[ -]by([ -]sa)?[ -][0-9.]+|public domain|pd[ -]|no restrictions)", re.I
)
MIN_WIDTH = 1600
MIN_RATIO, MAX_RATIO = 1.25, 2.4          # landscape, not a panorama sliver

# One entry per photo slot in the app. `queries` are tried in order and pooled;
# Commons search ranking is loose, so a couple of phrasings beats one.
SUBJECTS = [
    {"slot": "hero", "queries": [
        "Eiger Mönch Jungfrau panorama", "Bernese Alps Jungfrau region landscape"]},
    {"slot": "day0", "queries": [
        "Zurich Airport terminal", "Flughafen Zürich building"]},
    {"slot": "day1", "queries": [
        "Weggis Vierwaldstättersee", "Lake Lucerne paddle steamer", "Kapellbrücke Luzern"]},
    {"slot": "day2", "queries": [
        "Rigi Kulm view", "Rigi Bahn Vitznau railway", "Rigi summit panorama"]},
    {"slot": "day3", "queries": [
        "Grindelwald village Eiger", "Brünigpass road"]},
    {"slot": "day4", "queries": [
        "Männlichen view Eiger Mönch Jungfrau", "Männlichen Kleine Scheidegg trail"]},
    {"slot": "day5", "queries": [
        "Jungfraujoch Sphinx observatory", "Aletsch glacier Jungfraujoch", "Jungfraujoch"]},
    {"slot": "day6", "queries": [
        "Schilthorn Piz Gloria", "Mürren village Lauterbrunnen", "Schilthorn summit"]},
    {"slot": "day7", "queries": [
        "Aareschlucht Meiringen", "Aare Gorge walkway"]},
    {"slot": "day8", "queries": [
        "Swiss Alps aerial view from aircraft", "Alps from above clouds Switzerland"]},
]

PER_QUERY = 14
KEEP = 12


# Commons answers 429 quickly if asked in a tight loop. One request at a time, spaced,
# and back off rather than hammer — this is somebody else's free server.
PAUSE = 1.5
_last = [0.0]


def fetch(url: str) -> bytes:
    for attempt in range(6):
        wait = PAUSE - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 503) or attempt == 5:
                raise
            back = 5 * (2 ** attempt)
            print(f"  {exc.code}, waiting {back}s", flush=True)
            time.sleep(back)
    raise RuntimeError("unreachable")


def api(params: dict) -> dict:
    params = {**params, "format": "json", "formatversion": "2"}
    return json.loads(fetch(f"{API}?{urllib.parse.urlencode(params)}"))


def thumb_name(commons_title: str) -> str:
    """A thumbnail filename derived from the file it depicts.

    Naming these by position instead cost real time: a rate limit forced a re-run,
    Commons ranked the results differently the second time, and the already-downloaded
    slot_07.jpg was silently kept against a different file's metadata. Every picture
    reviewed after that point was the wrong one. Content-addressed names make the
    mismatch impossible rather than merely unlikely.
    """
    stem = re.sub(r"[^A-Za-z0-9]+", "-", commons_title.removeprefix("File:")).strip("-")
    digest = hashlib.sha1(commons_title.encode("utf-8")).hexdigest()[:8]
    return f"{stem[:60]}-{digest}.jpg"


def plain(value: str) -> str:
    """extmetadata ships HTML; the credit line wants the words."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", text).replace("&amp;", "&").strip()


def search(query: str) -> list[dict]:
    data = api({
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": "6",
        "gsrlimit": str(PER_QUERY),
        "prop": "imageinfo",
        "iiprop": "url|size|extmetadata",
        "iiurlwidth": "480",
    })
    out = []
    for page in data.get("query", {}).get("pages", []):
        info = (page.get("imageinfo") or [None])[0]
        if not info:
            continue
        meta = info.get("extmetadata", {})
        licence = plain(meta.get("LicenseShortName", {}).get("value", ""))
        width, height = info["width"], info["height"]
        if not OK_LICENCE.match(licence):
            continue
        if width < MIN_WIDTH or not (MIN_RATIO <= width / height <= MAX_RATIO):
            continue
        out.append({
            "title": page["title"],
            "width": width,
            "height": height,
            "licence": licence,
            "licence_url": plain(meta.get("LicenseUrl", {}).get("value", "")),
            "author": plain(meta.get("Artist", {}).get("value", "")) or "Unknown",
            "description": plain(meta.get("ImageDescription", {}).get("value", ""))[:220],
            "page": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page['title'])}",
            "file_url": info["url"],
            "thumb_url": info["thumburl"],
            "query": query,
        })
    return out


def main(scratch: Path) -> None:
    scratch.mkdir(parents=True, exist_ok=True)
    thumbs = scratch / "thumbs"
    thumbs.mkdir(exist_ok=True)

    out = scratch / "candidates.json"
    # Resume rather than restart: a rate limit part-way through should not cost the
    # subjects already gathered.
    catalogue = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}

    for subject in SUBJECTS:
        if subject["slot"] in catalogue:
            print(f"{subject['slot']:6} {len(catalogue[subject['slot']]):2} cached", flush=True)
            continue
        pool, seen = [], set()
        for query in subject["queries"]:
            for cand in search(query):
                if cand["title"] in seen:
                    continue
                seen.add(cand["title"])
                pool.append(cand)
        # stable order: same inputs, same pool, whatever Commons ranking does today
        pool.sort(key=lambda c: (-c["width"], c["title"]))
        pool = pool[:KEEP]

        for n, cand in enumerate(pool):
            name = thumb_name(cand["title"])
            if not (thumbs / name).exists():
                (thumbs / name).write_bytes(fetch(cand["thumb_url"]))
            cand["thumb_file"] = str(thumbs / name)
            cand["id"] = f"{subject['slot']}_{n:02d}"

        catalogue[subject["slot"]] = pool
        out.write_text(json.dumps(catalogue, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{subject['slot']:6} {len(pool):2} candidates", flush=True)

    print(f"\nwrote {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
