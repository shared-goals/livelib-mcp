"""LiveLib trisolaris client: anonymous reads + llsid-cookie writes."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
import urllib.parse
from collections.abc import Callable, Mapping

import httpx

API_BASE = "https://beta.api.livelib.ru/trisolaris/api"
USER_AGENT = "livelib-mcp/0.1 (+https://github.com/shared-goals/livelib-mcp)"
SHELVES = ("reads", "unreads", "wants", "readings")
VISIBILITIES = ("public", "friends", "private")
BLOCK_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
BLOCK_ID_LEN = 10
LLSID_ENV = "LIVELIB_LLSID"
# Transient HTTP statuses worth another attempt (production behavior inherited
# from the old text-forge fetcher); everything else fails at once.
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3


def _retry_sleep(attempt: int) -> float:
    """Backoff before the next attempt: attempt is 0-based, so 2s, then 4s."""
    return 2 * (attempt + 1)


def _new_block_id() -> str:
    """Id for a text_json editor block: 10 chars of [A-Za-z0-9], per the API."""
    return "".join(secrets.choice(BLOCK_ID_ALPHABET) for _ in range(BLOCK_ID_LEN))


def client_from_env(env: Mapping[str, str] | None = None, **kwargs) -> TrisolarisClient:
    """Client from LIVELIB_LLSID; missing/empty env -> anonymous client.

    `env` keeps callers/tests network-free by letting them hand in the
    environment; **kwargs (a test transport) pass through to the client. The
    server's tools call this with no arguments, i.e. os.environ at call time.
    """
    env = os.environ if env is None else env
    return TrisolarisClient(llsid=env.get(LLSID_ENV) or None, **kwargs)


class LivelibApiError(Exception):
    """Error envelope from the trisolaris API."""

    def __init__(self, status, type, detail, error_descriptions=None):
        super().__init__(f"{status} {type}: {detail}")
        self.status = status
        self.type = type
        self.detail = detail
        self.error_descriptions = error_descriptions or []


class LivelibAuthError(LivelibApiError):
    """401 AuthenticationRequired — the call needs a valid llsid cookie."""


def _check(r: httpx.Response) -> dict | None:
    """Raise on any non-2xx or error envelope; 401 -> LivelibAuthError.

    Returns the parsed body, or None when the response carries no body at all
    (e.g. 204 No Content) — a bodyless 2xx is a success, it just has nothing
    to hand back.
    """
    status = r.status_code
    body = _body(r)
    if status // 100 != 2 or (body or {}).get("error"):
        err = (body or {}).get("error") or {}
        args = (
            (body or {}).get("status", status),
            err.get("type"),
            err.get("detail"),
            err.get("error_descriptions"),
        )
        raise (LivelibAuthError if status == 401 else LivelibApiError)(*args)
    return body


def _body(r: httpx.Response) -> dict | None:
    """Parse a JSON body, or None for a bodyless response (e.g. 204 No Content).

    Content-type is not trusted: an empty body with a JSON content-type header
    still has nothing to parse.
    """
    raw = r.content
    return json.loads(raw) if raw and raw.strip() else None


def _default_transport(
    method: str,
    url: str,
    *,
    json_body: dict | None = None,
    headers: dict | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict | None:
    """The default transport: one httpx request per attempt, retrying transient
    failures (RETRY_STATUSES / httpx.TransportError) up to MAX_ATTEMPTS total.
    `sleep` is injectable so tests keep the suite network-free and fast.
    Injected transports (any other `transport=`) are NOT retrying — they
    behave exactly as told; the retry belongs to this default only.
    """
    # trust_env=False is deliberate: LiveLib needs no proxy routing, and an
    # operator's ambient proxy env (esp. a no_proxy list with IDN entries like
    # `*.рф`) can crash httpx IN THE CONSTRUCTOR (InvalidURL: Invalid IDNA
    # hostname) before any request is made. The client must not depend on the
    # quality of the host's proxy bypass lists. If a proxy is ever genuinely
    # needed for LiveLib, add an explicit LIVELIB_PROXY env (YAGNI now).
    for attempt in range(MAX_ATTEMPTS):
        try:
            with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=25, trust_env=False) as c:
                r = c.request(method, url, json=json_body, headers=headers)
        except httpx.TransportError:
            # the last attempt's network failure propagates to the caller
            if attempt + 1 >= MAX_ATTEMPTS:
                raise
            sleep(_retry_sleep(attempt))
            continue
        if r.status_code in RETRY_STATUSES and attempt + 1 < MAX_ATTEMPTS:
            sleep(_retry_sleep(attempt))
            continue
        # non-retryable status, or the last attempt of a transient one: the
        # envelope check raises it (429/5xx -> LivelibApiError) or returns it
        return _check(r)


def _next_offset(payload: dict) -> int | None:
    """Offset of the next page, from payload.pagination.next_page.

    The API answers next_page as a path+query (e.g.
    /api/users/1/quotes?limit=2&ordering=created_desc&offset=2); the offset
    query param is authoritative. None when pagination ends.
    """
    next_page = (payload.get("pagination") or {}).get("next_page")
    if not next_page:
        return None
    offset = urllib.parse.parse_qs(urllib.parse.urlparse(next_page).query).get("offset")
    return int(offset[0]) if offset else None


WORK_URL_RE = re.compile(r"/work/(\d+)")


def parse_shelf_record(mark: dict) -> dict | None:
    """One shelf mark -> the consumer-shaped record, or None when the mark
    carries no usable art_edition.id. The single place where LiveLib shelf
    semantics are defined: rating is the API's 0-10 value, my_mark the 1-5
    star rendering (rating/2 rounded, None when unrated).
    """
    ae = mark.get("art_edition") or {}
    edition_id = ae.get("id")
    if edition_id is None:
        return None
    rating = mark.get("rating") or 0
    return {
        "collection_id": mark.get("id"),
        "edition_id": str(edition_id),
        "title": ae.get("title", ""),
        "author": ", ".join(
            a["full_name"] for a in ae.get("authors") or [] if a.get("full_name")
        ),
        "url": ae.get("url") or f"/book/{edition_id}",
        "rating": rating,
        "my_mark": round(rating / 2) if rating else None,
    }


class TrisolarisClient:
    # Acceptable quote visibilities, as the API documents them. A class
    # attribute so a subclass can widen it (tests use that to observe the API's
    # own 422 for a bad value instead of our pre-request ValueError).
    visibilities = VISIBILITIES

    def __init__(self, *, llsid: str | None = None, transport=None):
        self.llsid = llsid
        self.transport = transport or _default_transport

    def _headers(self) -> dict | None:
        """Auth header, or None so no Cookie key is sent at all when anonymous."""
        return {"Cookie": f"llsid={self.llsid}"} if self.llsid else None

    def _call(
        self, method: str, path: str, *, json_body: dict | None = None
    ) -> dict | None:
        """One API call; a rejected status is raised by the transport's check."""
        return self.transport(
            method, f"{API_BASE}/{path}", json_body=json_body, headers=self._headers()
        )

    def _payload(self, path: str) -> dict:
        return self._payload_of("GET", path)["payload"]

    def _payload_of(
        self, method: str, path: str, *, json_body: dict | None = None
    ) -> dict:
        """A call that MUST answer with an envelope: body -> payload envelope.

        A 2xx with no body (204) has nothing to hand back, so it is reported as
        LivelibApiError/EmptyResponse rather than crashing the caller with a
        TypeError on None["payload"]. Any rejected status is already raised by
        the transport's check.
        """
        body = self._call(method, path, json_body=json_body)
        if body is None:
            # The transport's check already passed, so this is a 2xx that came
            # back with no body; the transport hands back only the parsed body,
            # so the canonical bodyless-success 204 is what we can report.
            raise LivelibApiError(
                204,
                "EmptyResponse",
                f"{method} {path} returned no body — expected an envelope",
            )
        return body

    def add_quote(self, edition_id: int, text: str, visibility: str = "public") -> dict:
        """Add a quote to an edition; returns payload.data ({"id": user_quote_id}).

        `text` goes out byte-exact — no strip, no normalization. The editor block
        id is generated here; the API response carries only the new user-quote id.
        """
        if visibility not in self.visibilities:
            raise ValueError(
                f"unknown visibility {visibility!r}; expected one of {VISIBILITIES}"
            )
        body = {
            "text_json": {
                "editor_blocks": [
                    {"id": _new_block_id(), "type": "paragraph", "data": {"text": text}}
                ]
            },
            "visibility": visibility,
        }
        return self._payload_of(
            "POST", f"art-editions/{edition_id}/quotes", json_body=body
        )["payload"]["data"]

    def delete_quote(self, user_quote_id: int) -> bool:
        """Delete one of the caller's own quotes. True on 204; any other status
        (e.g. 404 — already gone) raises, so the caller decides."""
        self._call("DELETE", f"users/me/quotes/{user_quote_id}")
        return True

    def add_review(
        self,
        edition_id: int,
        title: str,
        text: str,
        rating: int = 10,
        *,
        has_spoilers: bool = False,
    ) -> dict:
        """Post a review of an edition; returns payload.data ({"id": review_id}.

        Contract (sniffed live 2026-10-29): title, text_json, rating, emotions,
        has_spoilers are ALL required at once. **Rating scale is 0-10** (it
        lands on the user's collection mark of the book — `reviewer_art_rating`
        in review records; a review itself never carries a rating; 10 = five
        stars on the site). `text` goes out byte-exact, split into one Editor.js
        paragraph block per blank-line-separated paragraph (each <p> renders
        separately; a single block flattens line breaks).
        """
        rating = int(rating)
        if not 0 <= rating <= 10:
            raise ValueError(f"rating must be 0..10, got {rating!r}")
        paragraphs = text.split("\n\n") if "\n\n" in text else [text]
        body = {
            "title": title,
            "text_json": {
                "editor_blocks": [
                    {"id": _new_block_id(), "type": "paragraph", "data": {"text": p}}
                    for p in paragraphs
                ]
            },
            "rating": rating,
            "emotions": [],
            "has_spoilers": bool(has_spoilers),
        }
        return self._payload_of(
            "POST", f"art-editions/{edition_id}/reviews", json_body=body
        )["payload"]["data"]

    def delete_review(self, review_id: int) -> bool:
        """Delete one of the caller's own reviews. True on 204. The path is the
        ROOT-level /api/reviews/{id} — the /users/me/reviews/... variant is
        404. A review edited in the legacy web editor answers 409
        NotEditableByNewEditor and can only be deleted there."""
        self._call("DELETE", f"reviews/{review_id}")
        return True

    def update_review(
        self,
        review_id: int,
        title: str,
        text: str,
        rating: int = 10,
        *,
        has_spoilers: bool = False,
    ) -> bool:
        """Update one of the caller's own reviews via PATCH; True on 204 (the
        response carries NO body — verified live). Same body rules as
        add_review plus the REQUIRED `visibility` ("public" for our posts). A
        review edited in the legacy web editor answers 409
        NotEditableByNewEditor — edit it there instead.
        """
        rating = int(rating)
        if not 0 <= rating <= 10:
            raise ValueError(f"rating must be 0..10, got {rating!r}")
        paragraphs = text.split("\n\n") if "\n\n" in text else [text]
        body = {
            "title": title,
            "text_json": {
                "editor_blocks": [
                    {"id": _new_block_id(), "type": "paragraph", "data": {"text": p}}
                    for p in paragraphs
                ]
            },
            "rating": rating,
            "emotions": [],
            "has_spoilers": bool(has_spoilers),
            "visibility": "public",
        }
        self._call("PATCH", f"reviews/{review_id}", json_body=body)
        return True

    def _walk(self, path: str, *, limit: int, extra_query: str = "") -> list[dict]:
        """Follow payload.pagination.next_page until absent; concat payload.data."""
        out, offset = [], 0
        while True:
            payload = self._payload(
                f"{path}?limit={limit}&offset={offset}{extra_query}"
            )
            data = payload["data"]
            out.extend(data)
            next_offset = _next_offset(payload)
            if next_offset is None or not data:
                return out
            offset = next_offset

    def _shelf_page(self, uid: int, shelf: str, offset: int) -> dict:
        return self._payload(f"users/{uid}/arts/{shelf}?limit=100&offset={offset}")

    def get_edition(self, edition_id: int) -> dict:
        return self._payload(f"art-editions/{edition_id}")["data"]

    def get_shelf(self, uid: int, shelf: str) -> dict:
        if shelf not in SHELVES:
            raise ValueError(f"unknown shelf {shelf!r}; expected one of {SHELVES}")
        out, offset = [], 0
        while True:
            payload = self._shelf_page(uid, shelf, offset)
            counters_all = (payload.get("counters") or {}).get("all", 0)
            data = payload["data"]
            out.extend(data)
            next_offset = _next_offset(payload)
            if next_offset is None or not data:
                break
            offset = next_offset
        editions, seen = [], set()
        duplicates = 0
        for mark in out:
            edition = mark.get("art_edition") or {}
            eid = edition.get("id")
            if eid in seen:
                duplicates += 1
                continue
            seen.add(eid)
            editions.append(edition)
        return {
            "editions": editions,
            "marks_seen": len(out),
            "duplicate_marks": duplicates,
            "counters_all": counters_all,
        }

    def get_edition_contents(self, edition_id: int) -> list[tuple[str, str]]:
        """Contents of a multi-work edition, page order: [(title, work_id)].
        Entries without a /work/<id> url carry no work relation -> skipped, as
        do entries with an empty title. Single page: the endpoint does not
        paginate (verified against tests/fixtures/contents_1001482955.json).
        """
        contents = []
        for entry in self._payload(f"art-editions/{edition_id}/contents")["data"]:
            match = WORK_URL_RE.search(entry.get("url") or "")
            if match and entry.get("title"):
                contents.append((entry["title"], match.group(1)))
        return contents

    def shelf_records(self, uid: int, shelf: str) -> list[dict]:
        """Shelf walk + edition dedupe (first record wins), each mark shaped by
        parse_shelf_record; marks without a usable edition id are skipped.
        """
        if shelf not in SHELVES:
            raise ValueError(f"unknown shelf {shelf!r}; expected one of {SHELVES}")
        out, offset, seen = [], 0, set()
        while True:
            payload = self._shelf_page(uid, shelf, offset)
            data = payload["data"]
            for mark in data:
                record = parse_shelf_record(mark)
                if record is None or record["edition_id"] in seen:
                    continue
                seen.add(record["edition_id"])
                out.append(record)
            next_offset = _next_offset(payload)
            if next_offset is None or not data:
                return out
            offset = next_offset

    def get_edition_quotes(
        self, edition_id: int, *, limit: int = 50, offset: int = 0
    ) -> dict:
        payload = self._payload(
            f"art-editions/{edition_id}/quotes?limit={limit}&offset={offset}"
        )
        return {
            "quotes": payload["data"],
            "counters_all": (payload.get("counters") or {}).get("all", 0),
            "next_offset": _next_offset(payload),
        }

    def get_quotes(self, uid: int) -> list[dict]:
        return self._walk(
            f"users/{uid}/quotes", limit=50, extra_query="&ordering=created_desc"
        )

    def get_reviews(self, uid: int) -> list[dict]:
        return self._walk(f"users/{uid}/reviews", limit=50)

    def search_editions(self, query: str, *, limit: int = 10) -> list[dict]:
        """Search; returns the art_edition records (mixed-type results carry
        their instance under `instance`, other types are not editions)."""
        data = self._payload(
            f"search?query={urllib.parse.quote(query, safe='')}&limit={limit}"
        )["data"]
        return [r["instance"] for r in data if r.get("type") == "art_editions"]
