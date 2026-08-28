# -*- coding: utf-8 -*-
"""Prove a real Supabase project works, before trusting the family's notes to it.

check.py exercises SupabaseStore against a stub. That proves the requests the app
sends are well formed; it cannot prove Supabase accepts them, because reaching the
real service needs somebody's account. This does the other half: it runs the same
code against the real project and puts it back the way it found it.

    python tools/check_supabase.py

It reads the credentials from .streamlit/secrets.toml (git-ignored) or from the
SUPABASE_URL and SUPABASE_KEY environment variables. It never prints the key.

Everything it writes is on the sentinel day 1970-01-01 and is deleted again in a
finally block, so a failure halfway through does not leave litter in the table the
family can see. If it does leave something behind it says so, with the id.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import store as notes_store  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
SECRETS = BASE / ".streamlit" / "secrets.toml"
SENTINEL_DAY = "1970-01-01"          # not a day of this trip, so it cannot collide
ME = "supabase preflight"
NOT_ME = "somebody else"

ok, failed = [], []


def step(label: str, fn):
    try:
        fn()
    except AssertionError as exc:
        failed.append(f"{label}: {exc}")
        print(f"  FAIL  {label} — {exc}")
    except notes_store.StoreError as exc:
        failed.append(f"{label}: {exc.key} {exc.detail}")
        print(f"  FAIL  {label} — {exc.key} {exc.detail}".rstrip())
        print(f"        {diagnose(exc)}")
    else:
        ok.append(label)
        print(f"  ok    {label}")


def diagnose(exc: notes_store.StoreError) -> str:
    """Turn the HTTP code into the thing that is actually wrong."""
    detail = (exc.detail or "")
    if exc.key == "ui.notes.err_offline":
        return "Could not reach the host at all — check supabase_url, and that the project is not paused."
    if detail.startswith("401") or detail.startswith("403"):
        return ("Reached the project but was refused. Either the key is not the anon key, "
                "or RLS is on with no policy, or anon lacks the table grant. Re-run the "
                "grant and create policy statements from the README.")
    if detail.startswith("404"):
        return ("Reached the project but not the table. Either the SQL has not been run, "
                "or supabase_url has a path on the end of it — it should be just "
                "https://<ref>.supabase.co")
    if detail.startswith("42") or detail.startswith("400"):
        return ("The request was rejected. Most often a column type: id and note_id must "
                "be uuid, created must be timestamptz.")
    return "See the Supabase project's Logs for the matching request."


def load_secrets() -> tuple[str, str]:
    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")
    if SECRETS.exists():
        data = tomllib.loads(SECRETS.read_text(encoding="utf-8"))
        url = data.get("supabase_url", url)
        key = data.get("supabase_key", key)
        print(f"Credentials from {SECRETS.relative_to(BASE)}")
    elif url and key:
        print("Credentials from the environment")
    if not url or not key:
        sys.exit(
            f"No credentials. Either write {SECRETS.relative_to(BASE)} with\n"
            '  supabase_url = "https://xxxxxxxx.supabase.co"\n'
            '  supabase_key = "eyJ..."\n'
            "or set SUPABASE_URL and SUPABASE_KEY in the environment.\n"
            "That file is git-ignored; the key still belongs in Streamlit Cloud's own "
            "Settings -> Secrets for the deployed app."
        )
    # The key is never printed. The host is, because it is in every URL anyway and it
    # is the thing most likely to be wrong.
    print(f"Project {url}  (key {len(key)} characters, not shown)")
    return url, key


def main() -> int:
    url, key = load_secrets()
    store = notes_store.SupabaseStore(url, key)
    note_id = None
    print()
    try:
        before = {}

        def read():
            nonlocal before
            before = store.notes()
            assert isinstance(before, dict)
        step("reach the project and read every note", read)
        if failed:
            print("\nStopped: nothing else can be trusted until reading works.")
            return 1

        start = len(before.get(SENTINEL_DAY, []))

        def add():
            nonlocal note_id
            note_id = store.add(SENTINEL_DAY, ME, "preflight — safe to delete")
            assert note_id
        step("write a note", add)

        def readback():
            rows = store.notes().get(SENTINEL_DAY, [])
            assert len(rows) == start + 1, f"expected {start + 1} notes, found {len(rows)}"
            mine = [n for n in rows if n.id == note_id]
            assert mine, "the note was accepted but does not read back"
            note = mine[0]
            assert note.author == ME, f"author came back as {note.author!r}"
            assert note.body == "preflight — safe to delete", "body did not round-trip"
            from datetime import datetime
            datetime.fromisoformat(note.created)   # timestamptz must parse
        step("read it back with its text and timestamp intact", readback)

        def like():
            store.set_like(note_id, ME, True)
            rows = [n for n in store.notes().get(SENTINEL_DAY, []) if n.id == note_id]
            assert rows and rows[0].liked_by(ME), "the like did not stick"
        step("like it", like)

        def like_twice():
            store.set_like(note_id, ME, True)      # the composite primary key + Prefer:
            rows = [n for n in store.notes().get(SENTINEL_DAY, []) if n.id == note_id]
            assert len(rows[0].likes) == 1, f"liking twice stored {len(rows[0].likes)}"
        step("like it again without a duplicate (needs the (note_id, who) key)", like_twice)

        def unlike():
            store.set_like(note_id, ME, False)
            rows = [n for n in store.notes().get(SENTINEL_DAY, []) if n.id == note_id]
            assert not rows[0].likes, "the like did not come off"
        step("unlike it", unlike)

        def refuse():
            try:
                store.delete(note_id, NOT_ME)
            except notes_store.StoreError as exc:
                assert exc.key == "ui.notes.err_not_yours", f"refused with {exc.key}"
            else:
                raise AssertionError("somebody else was allowed to delete it")
        step("refuse a delete from the wrong author", refuse)

        def remove():
            store.delete(note_id, ME)
            rows = store.notes().get(SENTINEL_DAY, [])
            assert len(rows) == start, f"expected {start} notes after cleanup, found {len(rows)}"
        step("delete it as its author, leaving the table as it was", remove)
        if "delete it as its author, leaving the table as it was" in ok:
            note_id = None
    finally:
        if note_id:
            try:
                store.delete(note_id, ME)
                print(f"\nCleaned up the preflight note ({note_id}).")
            except notes_store.StoreError:
                print(f"\nCOULD NOT CLEAN UP: a note with id {note_id} is left on "
                      f"{SENTINEL_DAY}. Delete it from the Supabase table editor.")

    print()
    if failed:
        print(f"{len(failed)} of {len(ok) + len(failed)} checks failed. "
              f"The notes board is NOT ready.")
        return 1
    print(f"All {len(ok)} checks passed against the real project. Notes are shared and "
          f"kept — set the same two secrets in Streamlit Cloud and the "
          f"'kept on this server alone' notice goes away on the next run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
