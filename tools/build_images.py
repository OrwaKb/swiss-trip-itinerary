"""Build step: turn the chosen Commons files into the images the app ships.

    python tools/build_images.py <scratch-dir>

Reads <scratch-dir>/picks.json (slot -> candidate id, plus an optional crop bias),
pulls a working-size copy of each original from Commons, crops it to the banner shape,
writes WebP into static/photos/, and records the credit for every one in images.json.

The app never fetches an image at runtime. Everything it serves is committed here,
which keeps it fast on a phone on hotel wifi and keeps the credits honest: the licence
and author come from the file's own Commons metadata, not from memory.

Cropping and resizing make an adapted work, so the credit records the modification and
the share-alike licences stay attached to the adaptation. That is the whole obligation.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.parse
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fetch_candidates import api, fetch, plain  # noqa: E402

BASE = Path(__file__).parent.parent
OUT = BASE / "static" / "photos"

# Wide banner shapes. The hero is a letterbox behind the trip title; a day photo is
# calmer at 2:1. Widths are ~1.6x the 860px content column, which is enough on a
# phone at 3x without paying for a file nobody looks at closely.
SHAPE = {"hero": (1400, 470), "day": (1200, 600)}
THUMB = (180, 120)
QUALITY = 78
SOURCE_WIDTH = 1800

# Where the crop window sits when the interesting part is not in the middle: 0 keeps
# the top of the frame, 1 the bottom. Several of these photos put the thing you came
# for near an edge — a spire, a summit, a dome — so this is per-photo, not a default.


def working_copy(commons_title: str) -> tuple[Image.Image, dict]:
    """A SOURCE_WIDTH-wide render plus the file's live metadata.

    Asking Commons to scale saves pulling 20MB originals over a domestic line, and the
    metadata is re-read here rather than trusted from the candidate sweep, so the credit
    matches the file as it stands today.
    """
    data = api({
        "action": "query", "titles": commons_title, "prop": "imageinfo",
        "iiprop": "url|size|extmetadata", "iiurlwidth": str(SOURCE_WIDTH),
    })
    page = data["query"]["pages"][0]
    info = page["imageinfo"][0]
    meta = info["extmetadata"]
    credit = {
        "title": page["title"].removeprefix("File:"),
        "author": plain(meta.get("Artist", {}).get("value", "")) or "Unknown",
        "licence": plain(meta.get("LicenseShortName", {}).get("value", "")),
        "licence_url": plain(meta.get("LicenseUrl", {}).get("value", "")),
        "page": "https://commons.wikimedia.org/wiki/"
                + urllib.parse.quote(page["title"].replace(" ", "_")),
        "modified": "cropped and resized",
    }
    img = Image.open(io.BytesIO(fetch(info.get("thumburl") or info["url"])))
    return img.convert("RGB"), credit


def crop_to(img: Image.Image, size: tuple[int, int], bias: float) -> Image.Image:
    """Cover-crop: fill the shape, keep the interesting band, never distort."""
    target_w, target_h = size
    want = target_w / target_h
    have = img.width / img.height
    if have > want:                      # too wide: trim the sides, centred
        keep = round(img.height * want)
        left = (img.width - keep) // 2
        img = img.crop((left, 0, left + keep, img.height))
    else:                                # too tall: trim top/bottom around the bias
        keep = round(img.width / want)
        top = round((img.height - keep) * bias)
        top = max(0, min(top, img.height - keep))
        img = img.crop((0, top, img.width, top + keep))
    return img.resize(size, Image.LANCZOS)


def main(scratch: Path) -> None:
    picks = json.loads((scratch / "picks.json").read_text(encoding="utf-8"))
    cands = json.loads((scratch / "candidates.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)

    manifest = {}
    for slot, pick in picks.items():
        chosen = next(c for c in cands[slot] if c["id"] == pick["id"])
        img, credit = working_copy(chosen["title"])
        bias = float(pick.get("bias", 0.5))
        credit["why"] = pick.get("why", "")

        wide = crop_to(img, SHAPE["hero" if slot == "hero" else "day"], bias)
        wide_name = f"{slot}.webp"
        wide.save(OUT / wide_name, "WEBP", quality=QUALITY, method=6)

        thumb_name = f"{slot}-t.webp"
        crop_to(img, THUMB, bias).save(
            OUT / thumb_name, "WEBP", quality=QUALITY, method=6
        )

        manifest[slot] = {
            "file": wide_name, "thumb": thumb_name,
            "w": wide.width, "h": wide.height, "credit": credit,
        }
        kb = (OUT / wide_name).stat().st_size / 1024
        tkb = (OUT / thumb_name).stat().st_size / 1024
        print(f"{slot:5} {wide.width}x{wide.height} {kb:6.1f} KB "
              f"+ thumb {tkb:5.1f} KB  {credit['licence']}", flush=True)

    path = BASE / "images.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(f.stat().st_size for f in OUT.iterdir()) / 1024
    print(f"\n{len(manifest)} slots, {total:.0f} KB in static/photos, wrote {path.name}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
