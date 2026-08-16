"""A stand-in for Supabase's REST interface, used by check.py.

The Supabase backend cannot be exercised against a real project here — that needs
somebody's account. So it is exercised against this instead: an in-memory PostgREST
that speaks the subset the store actually uses (select, order, eq filters, insert,
delete, and the Prefer header). It is deliberately strict — a missing apikey, an
unknown table or an unparsed filter raises rather than quietly passing — so the store
cannot drift away from the shape of the requests it claims to send.

What this proves: URLs, methods, headers, payload shapes, filter encoding, and how
errors surface. What it cannot prove: that the real service accepts them. The README
records that distinction rather than glossing it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from io import BytesIO


class StubError(AssertionError):
    pass


class _Response(BytesIO):
    """Enough of an http.client.HTTPResponse for `with urlopen(...) as r: r.read()`."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class StubSupabase:
    """Call .opener and hand the result to SupabaseStore(opener=...)."""

    def __init__(self, key: str = "test-anon-key", base: str = "https://stub.supabase.co"):
        self.key = key
        self.base = base + "/rest/v1"
        self.tables: dict[str, list[dict]] = {"notes": [], "likes": []}
        self.calls: list[tuple[str, str]] = []
        self.fail_next: int | None = None      # set to an HTTP status to force an error

    # -- request handling ---------------------------------------------------
    def opener(self, req, timeout=None):
        if timeout is None:
            raise StubError("store must pass an explicit timeout")
        if req.headers.get("Apikey") != self.key:
            raise StubError(f"missing/incorrect apikey header: {dict(req.headers)}")
        if req.headers.get("Authorization") != f"Bearer {self.key}":
            raise StubError("missing bearer token")
        if not req.full_url.startswith(self.base):
            raise StubError(f"unexpected base url: {req.full_url}")

        if self.fail_next is not None:
            code, self.fail_next = self.fail_next, None
            raise urllib.error.HTTPError(
                req.full_url, code, "stub failure", {}, BytesIO(b"")
            )

        path = req.full_url[len(self.base):]
        route, _, query = path.partition("?")
        table = route.strip("/")
        if table not in self.tables:
            raise StubError(f"unknown table {table!r}")
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        self.calls.append((req.get_method(), path))

        body = json.loads(req.data.decode("utf-8")) if req.data else None
        method = req.get_method()
        if method == "GET":
            payload = self._select(table, params)
        elif method == "POST":
            payload = self._insert(table, body, req.headers.get("Prefer", ""))
        elif method == "DELETE":
            payload = self._delete(table, params)
        else:
            raise StubError(f"unsupported method {method}")
        return _Response(json.dumps(payload).encode("utf-8"))

    # -- the PostgREST subset ----------------------------------------------
    def _filters(self, params: dict) -> list[tuple[str, str]]:
        out = []
        for name, values in params.items():
            if name in ("select", "order"):
                continue
            value = values[0]
            if not value.startswith("eq."):
                raise StubError(f"unsupported filter {name}={value}")
            out.append((name, value[3:]))
        return out

    def _match(self, table: str, params: dict) -> list[dict]:
        rows = self.tables[table]
        for column, wanted in self._filters(params):
            if rows and column not in rows[0]:
                raise StubError(f"filter on unknown column {table}.{column}")
            rows = [r for r in rows if str(r.get(column)) == wanted]
        return rows

    def _select(self, table: str, params: dict) -> list[dict]:
        rows = self._match(table, params)
        if "order" in params:
            column, _, direction = params["order"][0].partition(".")
            rows = sorted(rows, key=lambda r: r[column], reverse=direction == "desc")
        if "select" in params:
            columns = params["select"][0].split(",")
            for column in columns:
                if rows and column not in rows[0]:
                    raise StubError(f"selected unknown column {table}.{column}")
            rows = [{c: r[c] for c in columns} for r in rows]
        return rows

    def _insert(self, table: str, body, prefer: str) -> list[dict]:
        rows = body if isinstance(body, list) else [body]
        for row in rows:
            keys = set(row)
            if table == "notes" and keys != {"id", "day", "author", "body", "created"}:
                raise StubError(f"notes insert has columns {sorted(keys)}")
            if table == "likes" and keys != {"note_id", "who"}:
                raise StubError(f"likes insert has columns {sorted(keys)}")
            clash = any(
                all(r[k] == row[k] for k in self._pk(table)) for r in self.tables[table]
            )
            if clash:
                if "ignore-duplicates" not in prefer:
                    raise urllib.error.HTTPError(
                        "", 409, "duplicate key", {}, BytesIO(b"")
                    )
                continue
            self.tables[table].append(dict(row))
        return [] if "return=minimal" in prefer else rows

    def _delete(self, table: str, params: dict) -> list[dict]:
        doomed = self._match(table, params)
        if not self._filters(params):
            raise StubError("unfiltered DELETE would empty the table")
        self.tables[table] = [r for r in self.tables[table] if r not in doomed]
        return []

    @staticmethod
    def _pk(table: str) -> tuple[str, ...]:
        return ("id",) if table == "notes" else ("note_id", "who")
