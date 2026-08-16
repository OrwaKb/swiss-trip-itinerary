"""Where the trip notes live.

Streamlit Community Cloud gives an app no disk it can keep: the container is rebuilt
on every deploy and recycled when the app sleeps. A note written to a local file is
therefore private to one machine and temporary. So there are two backends:

  SqliteStore    a file on this machine. Zero setup, works offline, and is the right
                 answer while developing — but on Community Cloud it is wiped on
                 restart and is not shared with anyone else.
  SupabaseStore  a free hosted Postgres reached over its REST API. Set the secrets and
                 the app switches to it, at which point notes are genuinely shared and
                 genuinely kept.

open_store() picks between them. Nothing above this module knows which one it got;
`shared` says whether notes reach other people, and the app tells the reader plainly.

Only the standard library is used. Supabase's REST interface is plain HTTP, so it
needs no SDK and adds nothing to requirements.txt.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MAX_AUTHOR = 40
MAX_BODY = 700
MAX_PER_DAY = 300
HTTP_TIMEOUT = 10


class StoreError(RuntimeError):
    """Something went wrong talking to storage. Carries a translation key."""

    def __init__(self, key: str, detail: str = "") -> None:
        super().__init__(detail or key)
        self.key = key
        self.detail = detail


@dataclass(frozen=True)
class Note:
    id: str
    day: str
    author: str
    body: str
    created: str          # ISO-8601 UTC
    likes: tuple[str, ...] = field(default=())

    def liked_by(self, who: str) -> bool:
        return norm_author(who) in self.likes


# --------------------------------------------------------------------------- #
# Validation. Runs before anything is stored, in every backend.
# --------------------------------------------------------------------------- #

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANK_LINES = re.compile(r"\n{3,}")


def clean(text: str, limit: int) -> str:
    """Normalise, strip control characters, collapse runaway blank lines, cap length.

    Escaping is the renderer's job and happens there; this is about what gets stored.
    NFC matters for Hebrew and Arabic — the same word typed on two keyboards should
    not be stored as two different strings.
    """
    text = unicodedata.normalize("NFC", str(text))
    text = _CONTROL.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()[:limit]


def norm_author(name: str) -> str:
    """A name folded for comparison. Likes and deletes are keyed on this."""
    return clean(name, MAX_AUTHOR).casefold()


def validate(day: str, author: str, body: str) -> tuple[str, str]:
    author, body = clean(author, MAX_AUTHOR), clean(body, MAX_BODY)
    if not author:
        raise StoreError("ui.notes.err_name")
    if not body:
        raise StoreError("ui.notes.err_body")
    if not day:
        raise StoreError("ui.notes.err_day")
    return author, body


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# SQLite
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
  id      TEXT PRIMARY KEY,
  day     TEXT NOT NULL,
  author  TEXT NOT NULL,
  body    TEXT NOT NULL,
  created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS likes (
  note_id TEXT NOT NULL,
  who     TEXT NOT NULL,
  PRIMARY KEY (note_id, who)
);
CREATE INDEX IF NOT EXISTS notes_day ON notes(day, created);
"""


class SqliteStore:
    shared = False
    backend = "local"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Streamlit serves each session on its own thread off one cached store.
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def notes(self) -> dict[str, list[Note]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, day, author, body, created FROM notes ORDER BY created"
            ).fetchall()
            likes: dict[str, list[str]] = {}
            for note_id, who in self._db.execute("SELECT note_id, who FROM likes"):
                likes.setdefault(note_id, []).append(who)
        out: dict[str, list[Note]] = {}
        for note_id, day, author, body, created in rows:
            out.setdefault(day, []).append(
                Note(note_id, day, author, body, created, tuple(likes.get(note_id, ())))
            )
        return out

    def add(self, day: str, author: str, body: str) -> str:
        author, body = validate(day, author, body)
        with self._lock:
            (count,) = self._db.execute(
                "SELECT COUNT(*) FROM notes WHERE day = ?", (day,)
            ).fetchone()
            if count >= MAX_PER_DAY:
                raise StoreError("ui.notes.err_full")
            note_id = str(uuid.uuid4())
            self._db.execute(
                "INSERT INTO notes VALUES (?, ?, ?, ?, ?)",
                (note_id, day, author, body, now_iso()),
            )
            self._db.commit()
        return note_id

    def set_like(self, note_id: str, who: str, on: bool) -> None:
        who = norm_author(who)
        if not who:
            raise StoreError("ui.notes.err_name")
        with self._lock:
            if on:
                self._db.execute(
                    "INSERT OR IGNORE INTO likes VALUES (?, ?)", (note_id, who)
                )
            else:
                self._db.execute(
                    "DELETE FROM likes WHERE note_id = ? AND who = ?", (note_id, who)
                )
            self._db.commit()

    def delete(self, note_id: str, who: str) -> None:
        """Only the author, matched on the name they are posting under, may remove."""
        who = norm_author(who)
        with self._lock:
            row = self._db.execute(
                "SELECT author FROM notes WHERE id = ?", (note_id,)
            ).fetchone()
            if row is None:
                return
            if norm_author(row[0]) != who:
                raise StoreError("ui.notes.err_not_yours")
            self._db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            self._db.execute("DELETE FROM likes WHERE note_id = ?", (note_id,))
            self._db.commit()


# --------------------------------------------------------------------------- #
# Supabase, over PostgREST
# --------------------------------------------------------------------------- #

class SupabaseStore:
    shared = True
    backend = "supabase"

    def __init__(self, url: str, key: str, opener=None) -> None:
        self.base = url.rstrip("/") + "/rest/v1"
        self.key = key
        # Injected in tests so the request shape can be checked against a stub server
        # without needing anybody's real project.
        self._open = opener or urllib.request.urlopen

    def close(self) -> None:
        """Nothing is held open; the method exists so callers need not care which
        backend they were given."""

    def _call(self, path: str, method: str = "GET", body=None, prefer: str = ""):
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=headers, method=method
        )
        try:
            with self._open(req, timeout=HTTP_TIMEOUT) as r:
                raw = r.read()
        except urllib.error.HTTPError as exc:
            raise StoreError("ui.notes.err_store", f"{exc.code} {exc.reason}") from exc
        except OSError as exc:
            raise StoreError("ui.notes.err_offline", str(exc)) from exc
        return json.loads(raw) if raw else []

    def notes(self) -> dict[str, list[Note]]:
        rows = self._call("/notes?select=id,day,author,body,created&order=created.asc")
        likes_rows = self._call("/likes?select=note_id,who")
        likes: dict[str, list[str]] = {}
        for row in likes_rows:
            likes.setdefault(row["note_id"], []).append(row["who"])
        out: dict[str, list[Note]] = {}
        for row in rows:
            out.setdefault(row["day"], []).append(Note(
                row["id"], row["day"], row["author"], row["body"], row["created"],
                tuple(likes.get(row["id"], ())),
            ))
        return out

    def add(self, day: str, author: str, body: str) -> str:
        author, body = validate(day, author, body)
        note_id = str(uuid.uuid4())
        self._call("/notes", "POST", {
            "id": note_id, "day": day, "author": author,
            "body": body, "created": now_iso(),
        }, prefer="return=minimal")
        return note_id

    def set_like(self, note_id: str, who: str, on: bool) -> None:
        who = norm_author(who)
        if not who:
            raise StoreError("ui.notes.err_name")
        if on:
            self._call("/likes", "POST", {"note_id": note_id, "who": who},
                       prefer="resolution=ignore-duplicates,return=minimal")
        else:
            q = urllib.parse.quote(who, safe="")
            self._call(f"/likes?note_id=eq.{note_id}&who=eq.{q}", "DELETE",
                       prefer="return=minimal")

    def delete(self, note_id: str, who: str) -> None:
        who = norm_author(who)
        rows = self._call(f"/notes?id=eq.{note_id}&select=author")
        if not rows:
            return
        if norm_author(rows[0]["author"]) != who:
            raise StoreError("ui.notes.err_not_yours")
        self._call(f"/likes?note_id=eq.{note_id}", "DELETE", prefer="return=minimal")
        self._call(f"/notes?id=eq.{note_id}", "DELETE", prefer="return=minimal")


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #

def open_store(secrets, local_path: Path):
    """Supabase if it is configured, otherwise a local file.

    `secrets` is anything with a .get — st.secrets in the app, a plain dict in tests.
    A half-filled config is a mistake worth surfacing rather than silently downgrading
    to storage nobody else can see, so it raises.
    """
    url = (secrets.get("supabase_url") or "").strip()
    key = (secrets.get("supabase_key") or "").strip()
    if url and key:
        return SupabaseStore(url, key)
    if url or key:
        raise StoreError("ui.notes.err_halfconfig")
    return SqliteStore(local_path)
