"""Tests for TrisolarisClient — fixture-backed transport, no network.

The transport maps EXACT recorded URLs to fixture bodies; an unmapped URL
raises KeyError naming the URL, which pins client URL construction. Every
call goes through a real httpx.MockTransport, so method, body and headers
(the auth Cookie) are exercised by actual httpx request building.

Transport contract: transport(method, url, *, json_body=None, headers=None)
-> parsed body dict. Records `calls` (method, url, json_body) and `requests`
(httpx.Request). `status` sets the HTTP status the handler answers with.
"""

import json
import pathlib
import re

import httpx
import pytest

from livelib_mcp import LivelibApiError, LivelibAuthError, TrisolarisClient
from livelib_mcp import client as client_mod
from livelib_mcp.client import client_from_env

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
BASE = "https://beta.api.livelib.ru/trisolaris/api"


def load(name) -> tuple[dict | None, str]:
    """Fixture body as (parsed-or-None, raw text) — mirrors a real response."""
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return (None if not text.strip() else json.loads(text)), text


class FixtureTransport:
    """Exact-URL -> fixture body, served through httpx.MockTransport."""

    def __init__(self, mapping, *, status=200):
        # Accept either {url: fixture} (all GET) or {(method, url): fixture}.
        self.mapping = {
            (k if isinstance(k, tuple) else ("GET", k)): v for k, v in mapping.items()
        }
        self.status = status
        self.calls = []
        self.requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            key = self._key(request.method, str(request.url))
            if key not in self.mapping:
                raise KeyError(f"unrecorded URL: {request.method} {request.url}")
            fixture, status = self._entry(key)
            _parsed, raw = load(fixture)
            return httpx.Response(
                status,
                content=raw.encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
            )

        self.http = httpx.MockTransport(handler)

    @staticmethod
    def _key(method, url):
        """Normalize a mapping key: a plain URL string means GET, a (METHOD, url)
        tuple an explicit method. Requests look up by (method, url) either way."""
        return ("GET", url) if method == "GET" else (method, url)

    def _entry(self, key):
        """Mapping value -> (fixture name, HTTP status to answer with).

        A fixture is a real recorded response, so its own "status" field is
        authoritative: error envelopes are served as errors, success bodies as
        2xx. `status` (constructor) overrides for the rare bodyless record.
        """
        value = self.mapping[key]
        if isinstance(value, Exception):  # pre-raised envelope
            raise value
        parsed, _ = load(value)
        recorded = (parsed or {}).get("status")
        return value, recorded if isinstance(recorded, int) else self.status

    def __call__(self, method, url, *, json_body=None, headers=None):
        self.calls.append((method, url, json_body))
        # Contract: method and full URL must be given; keys are (method, url).
        head = {"User-Agent": client_mod.USER_AGENT}
        head.update(headers or {})
        with httpx.Client(transport=self.http) as c:
            r = c.request(method, url, json=json_body, headers=head)
        return self._check(r)

    def _check(self, r: httpx.Response) -> dict | None:
        """Same status/error handling as the real default transport."""
        body = r.json() if r.content else None
        if r.status_code // 100 != 2 or (body or {}).get("error"):
            err = (body or {}).get("error") or {}
            args = (
                (body or {}).get("status", r.status_code),
                err.get("type"),
                err.get("detail"),
                err.get("error_descriptions"),
            )
            raise (LivelibAuthError if r.status_code == 401 else LivelibApiError)(*args)
        return body


def fixture_transport(**pairs):
    """FixtureTransport from {path: spec}; spec "fixture.json" (GET) or
    ("METHOD", "fixture") for a write."""
    mapping = {}
    for path, spec in pairs.items():
        method, fixture = spec if isinstance(spec, tuple) else ("GET", spec)
        mapping[(method, path if "://" in path else f"{BASE}/{path}")] = fixture
    return FixtureTransport(mapping)


def env_transport(pairs):
    """(factory, transport) pair for a tool call whose client is built by the
    REAL client_from_env: only the transport is injected, so a monkeypatched
    os.environ still decides whether a Cookie is sent. The tool builds its
    client internally, so the instance is returned alongside the factory."""
    t = FixtureTransport(
        {k if isinstance(k, tuple) else ("GET", k): v for k, v in pairs.items()}
    )

    def factory():
        return client_from_env(transport=t)

    return factory, t


def sent_json(t, index: int = 0) -> dict:
    """The JSON body a recorded call sent out (index into t.calls).

    A write always carries a body, so the assert keeps the type checker honest
    without hiding anything: a None here is a real bug (no body was sent).
    """
    body = t.calls[index][2]
    assert body is not None, f"call {index} ({t.calls[index][0]}) sent no body"
    return body


def fixture_payload(name) -> dict:
    """Envelope payload of a fixture; every fixture used this way carries one."""
    body = load(name)[0]
    assert body is not None, f"fixture {name} is bodyless"
    payload = body["payload"]
    assert isinstance(payload, dict)
    return payload


def read_body(raw_result) -> dict:
    """Read a dict-valued tools/call result back from its text block."""
    return json.loads(raw_result.content[0].text)


def urls(t):
    return [(m, u) for m, u, _ in t.calls]


EDITION_URL = "art-editions/1004124394"


def test_get_edition_returns_payload_data():
    t = fixture_transport(**{EDITION_URL: "edition_1004124394.json"})
    ed = TrisolarisClient(transport=t).get_edition(1004124394)
    assert urls(t) == [("GET", f"{BASE}/art-editions/1004124394")]
    assert ed["id"] == 1004124394
    assert ed["title"] == "Экономика всего. Как институты определяют нашу жизнь"


def test_get_edition_missing_raises_api_error():
    # 404 body recorded from /quotes/25803826; asked via an unresolvable id so
    # the transport serves that recorded error envelope for the requested URL.
    t = fixture_transport(**{"art-editions/25803826": "error_404.json"})
    with pytest.raises(LivelibApiError) as ei:
        TrisolarisClient(transport=t).get_edition(25803826)
    assert not isinstance(ei.value, LivelibAuthError)
    assert ei.value.status == 404
    assert ei.value.type == "ResourceNotFoundByID"
    assert "25803826" in ei.value.detail


def test_error_422_envelope_fields():
    t = fixture_transport(
        **{"users/345497/arts/readings?limit=100&offset=0": "error_422.json"}
    )
    with pytest.raises(LivelibApiError) as ei:
        TrisolarisClient(transport=t).get_shelf(345497, "readings")
    err = ei.value
    assert err.status == 422
    assert err.type == "ParamValidationError"
    assert err.error_descriptions[0]["loc"] == ["query", "limit"]
    assert (
        err.error_descriptions[0]["msg"] == "Input should be less than or equal to 100"
    )


SHELF_URL = "users/345497/arts/readings?limit=100&offset=0"


def test_get_shelf_dedupes_duplicate_marks():
    t = fixture_transport(**{SHELF_URL: "shelf_readings_dupes.json"})
    out = TrisolarisClient(transport=t).get_shelf(345497, "readings")
    assert urls(t) == [("GET", f"{BASE}/{SHELF_URL}")]
    assert out["marks_seen"] == 3
    assert out["duplicate_marks"] == 1
    assert out["counters_all"] == 3
    assert [e["id"] for e in out["editions"]] == [1004124394, 1003047920]


def test_get_shelf_rejects_bad_shelf():
    t = fixture_transport()
    with pytest.raises(ValueError):
        TrisolarisClient(transport=t).get_shelf(345497, "read ")
    assert t.calls == []


EDQ_URL = "art-editions/1004124394/quotes?limit=5&offset=0"


def test_get_edition_quotes_one_page():
    t = fixture_transport(**{EDQ_URL: "edition_quotes_page1.json"})
    out = TrisolarisClient(transport=t).get_edition_quotes(1004124394, limit=5)
    assert urls(t) == [("GET", f"{BASE}/{EDQ_URL}")]
    assert len(out["quotes"]) == 5
    assert out["counters_all"] == 67
    # recorded next_page: /api/art-editions/1004124394/quotes?limit=5&offset=5
    assert out["next_offset"] == 5


def test_get_edition_quotes_last_page_next_offset_none():
    t = fixture_transport(
        **{"art-editions/1004124394/quotes?limit=5&offset=65": "quotes_last_page.json"}
    )
    out = TrisolarisClient(transport=t).get_edition_quotes(
        1004124394, limit=5, offset=65
    )
    assert out["next_offset"] is None
    assert out["counters_all"] == 67


def test_get_quotes_walks_pages_in_order():
    t = fixture_transport(
        **{
            "users/345497/quotes?limit=50&offset=0&ordering=created_desc": "quotes_walk_p1.json",
            "users/345497/quotes?limit=50&offset=50&ordering=created_desc": "quotes_walk_p2.json",
        }
    )
    ids = [q["id"] for q in TrisolarisClient(transport=t).get_quotes(345497)]
    assert urls(t) == [
        ("GET", f"{BASE}/users/345497/quotes?limit=50&offset=0&ordering=created_desc"),
        ("GET", f"{BASE}/users/345497/quotes?limit=50&offset=50&ordering=created_desc"),
    ]  # no third call
    p1 = fixture_payload("quotes_walk_p1.json")["data"]
    p2 = fixture_payload("quotes_walk_p2.json")["data"]
    assert ids == [q["id"] for q in p1 + p2]


def test_get_quotes_empty_first_page():
    t = fixture_transport(
        **{
            "users/345497/quotes?limit=50&offset=0&ordering=created_desc": "quotes_walk_empty.json"
        }
    )
    assert TrisolarisClient(transport=t).get_quotes(345497) == []
    assert len(t.calls) == 1


def test_get_reviews_walks_single_page():
    t = fixture_transport(
        **{"users/345497/reviews?limit=50&offset=0": "reviews_walk_last.json"}
    )
    out = TrisolarisClient(transport=t).get_reviews(345497)
    assert urls(t) == [("GET", f"{BASE}/users/345497/reviews?limit=50&offset=0")]
    data = fixture_payload("reviews_walk_last.json")["data"]
    assert len(out) == len(data) == 2
    assert [r["id"] for r in out] == [r["id"] for r in data]


SEARCH_URL = "search?query=%D0%90%D1%83%D0%B7%D0%B0%D0%BD&limit=3"


def test_search_editions_returns_records():
    t = fixture_transport(**{SEARCH_URL: "search_auzan.json"})
    out = TrisolarisClient(transport=t).search_editions("Аузан", limit=3)
    assert urls(t) == [("GET", f"{BASE}/{SEARCH_URL}")]
    data = fixture_payload("search_auzan.json")["data"]
    editions = [r for r in data if r["type"] == "art_editions"]
    assert len(data) == 3 and len(editions) == 2  # mixed types: + 1 "authors"
    assert len(out) == len(editions)
    assert [e["id"] for e in out] == [r["instance"]["id"] for r in editions]
    assert out[0]["title"] == editions[0]["instance"]["title"]


# ---------------------------------------------------------------------------
# Auth plumbing: llsid cookie, client_from_env, 401 -> LivelibAuthError.
# ---------------------------------------------------------------------------


def test_anonymous_client_sends_no_cookie_header():
    t = fixture_transport(**{EDITION_URL: "edition_1004124394.json"})
    TrisolarisClient(transport=t).get_edition(1004124394)
    req = t.requests[0]
    assert "cookie" not in req.headers  # no Cookie key at all when anonymous
    assert req.headers["user-agent"] == client_mod.USER_AGENT


def test_llsid_client_sends_cookie_header():
    t = fixture_transport(**{EDITION_URL: "edition_1004124394.json"})
    TrisolarisClient(llsid="abc123", transport=t).get_edition(1004124394)
    assert t.requests[0].headers["cookie"] == "llsid=abc123"


def test_client_from_env_with_llsid():
    c = client_from_env({"LIVELIB_LLSID": "s3cr3t"})
    assert c.llsid == "s3cr3t"


def test_client_from_env_missing_or_empty_is_anonymous():
    assert client_from_env({}).llsid is None
    assert client_from_env({"LIVELIB_LLSID": ""}).llsid is None


def test_client_default_llsid_is_none():
    assert TrisolarisClient().llsid is None


def test_client_from_env_forwards_transport_and_keeps_no_cookie():
    # env-argument + kwargs pass-through, the hook the server tests rely on.
    t = fixture_transport(**{EDITION_URL: "edition_1004124394.json"})
    assert client_from_env({"OTHER": "x"}, transport=t).llsid is None
    assert client_from_env({"LIVELIB_LLSID": "z"}, transport=t).llsid == "z"


def test_401_raises_auth_error_with_envelope_fields():
    t = fixture_transport(
        **{"users/me/quotes?limit=50&offset=0&ordering=created_desc": "error_401.json"}
    )
    with pytest.raises(LivelibAuthError) as ei:
        # "me" is not an int: the API rejects this call with 401 before the uid
        # is ever used, which is what this pin is about (the fixture is recorded
        # from /users/me/quotes).
        TrisolarisClient(transport=t).get_quotes("me")  # type: ignore[arg-type]  # deliberate non-int uid, 401 is the point
    err = ei.value
    assert isinstance(err, LivelibApiError)
    assert err.status == 401
    assert err.type == "AuthenticationRequired"
    assert err.detail == "You must be logged in to access this resource."


# ---------------------------------------------------------------------------
# Writes: add_quote / delete_quote.
# ---------------------------------------------------------------------------


ADD_URL = "art-editions/1004124394/quotes"
DELETE_PATH = "users/me/quotes/25804270"
CREATED_URL = f"{BASE}/{ADD_URL}"
DELETE_URL = f"{BASE}/{DELETE_PATH}"


def test_add_quote_creates_and_returns_payload_data():
    t = fixture_transport(**{ADD_URL: ("POST", "quote_created.json")})
    out = TrisolarisClient(llsid="tok", transport=t).add_quote(
        1004124394, "Текст цитаты"
    )
    assert out == {"id": 25804270}
    assert urls(t) == [("POST", CREATED_URL)]
    assert t.requests[0].headers["cookie"] == "llsid=tok"
    body = t.calls[0][2]
    assert body is not None
    assert body["visibility"] == "public"
    blocks = body["text_json"]["editor_blocks"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "paragraph"
    assert blocks[0]["data"]["text"] == "Текст цитаты"
    bid = blocks[0]["id"]
    assert len(bid) == 10
    assert bid.isascii() and all(ch.isalnum() for ch in bid)


def test_add_quote_text_is_byte_exact():
    raw = "  Отличаются  только  пробелы\t\n"
    t = fixture_transport(**{ADD_URL: ("POST", "quote_created.json")})
    TrisolarisClient(llsid="tok", transport=t).add_quote(
        1004124394, raw, visibility="friends"
    )
    sent = json.loads(t.requests[0].content.decode("utf-8"))
    assert sent["text_json"]["editor_blocks"][0]["data"]["text"] == raw
    assert sent["visibility"] == "friends"


def test_add_quote_block_id_differs_per_call():
    ids = []
    for _ in range(2):
        t = fixture_transport(**{ADD_URL: ("POST", "quote_created.json")})
        TrisolarisClient(llsid="tok", transport=t).add_quote(1004124394, "раз")
        ids.append(sent_json(t, 0)["text_json"]["editor_blocks"][0]["id"])
    assert ids[0] != ids[1]


def test_add_quote_rejects_bad_visibility_without_http_call():
    t = fixture_transport()
    with pytest.raises(ValueError):
        TrisolarisClient(llsid="tok", transport=t).add_quote(
            1004124394, "x", visibility="publick"
        )
    assert t.calls == []
    assert t.requests == []


def test_add_quote_422_from_api_raises_api_error():
    # Bypasses the client-side visibility guard (which would refuse before any
    # request) to pin what the API itself says about a bad visibility, learned
    # live: the client surfaces that envelope unchanged.
    class NoGuard(TrisolarisClient):
        visibilities = TrisolarisClient.visibilities + ("publick",)

    t = fixture_transport(**{ADD_URL: ("POST", "error_422_visibility.json")})
    with pytest.raises(LivelibApiError) as ei:
        NoGuard(llsid="tok", transport=t).add_quote(
            1004124394, "x", visibility="publick"
        )
    assert not isinstance(ei.value, LivelibAuthError)
    assert ei.value.status == 422
    d = ei.value.error_descriptions[0]
    assert d["loc"] == ["body", "visibility"]
    assert d["msg"] == "Input should be 'public', 'friends' or 'private'"


def test_add_quote_401_raises_auth_error():
    t = fixture_transport(**{ADD_URL: ("POST", "error_401.json")})
    with pytest.raises(LivelibAuthError):
        TrisolarisClient(transport=t).add_quote(1004124394, "x")


# A 2xx response with NO body cannot carry the envelope an envelope-expecting
# method must return: the client says so as LivelibApiError/EmptyResponse
# instead of dying with an opaque TypeError on None["payload"].


def test_add_quote_204_empty_body_raises_empty_response():
    t = fixture_transport(**{ADD_URL: ("POST", "empty.json")}, status=204)
    with pytest.raises(LivelibApiError) as ei:
        TrisolarisClient(llsid="tok", transport=t).add_quote(1004124394, "x")
    assert not isinstance(ei.value, LivelibAuthError)
    assert ei.value.status == 204
    assert ei.value.type == "EmptyResponse"
    assert "POST" in ei.value.detail and ADD_URL in ei.value.detail
    assert len(t.calls) == 1  # the empty body is not retried


def test_add_review_204_empty_body_raises_empty_response():
    t = fixture_transport(**{"art-editions/1/reviews": ("POST", "empty.json")}, status=204)
    with pytest.raises(LivelibApiError) as ei:
        TrisolarisClient(transport=t).add_review(1, "T", "x")
    assert ei.value.type == "EmptyResponse"
    assert "POST" in ei.value.detail


def test_get_edition_204_empty_body_raises_empty_response():
    t = fixture_transport(**{EDITION_URL: "empty.json"}, status=204)
    with pytest.raises(LivelibApiError) as ei:
        TrisolarisClient(transport=t).get_edition(1004124394)
    assert ei.value.status == 204
    assert ei.value.type == "EmptyResponse"
    assert "GET" in ei.value.detail


def test_empty_response_is_not_an_api_error_envelope_shape():
    # detail is the client's own sentence; error_descriptions stays empty
    # because the API described nothing.
    t = fixture_transport(**{EDITION_URL: "empty.json"}, status=204)
    with pytest.raises(LivelibApiError) as ei:
        TrisolarisClient(transport=t).get_edition(1004124394)
    assert ei.value.error_descriptions == []
    assert str(ei.value) == (
        "204 EmptyResponse: GET art-editions/1004124394 returned no body "
        "— expected an envelope"
    )


def test_delete_quote_204_returns_true():
    t = fixture_transport(**{DELETE_PATH: ("DELETE", "empty.json")})
    assert TrisolarisClient(llsid="tok", transport=t).delete_quote(25804270) is True
    assert urls(t) == [("DELETE", DELETE_URL)]
    assert t.requests[0].headers["cookie"] == "llsid=tok"


def test_delete_quote_404_raises_api_error():
    t = fixture_transport(**{DELETE_PATH: ("DELETE", "error_404.json")})
    with pytest.raises(LivelibApiError) as ei:
        TrisolarisClient(llsid="tok", transport=t).delete_quote(25804270)
    assert ei.value.status == 404
    assert not isinstance(ei.value, LivelibAuthError)


def test_unmapped_url_raises_key_error():
    t = fixture_transport()
    with pytest.raises(KeyError):
        TrisolarisClient(transport=t).get_edition(1)


def test_default_transport_raises_auth_error_on_401():
    # Default transport (no injection) maps a 401 body to LivelibAuthError.
    # 401 is not retryable, so the retrying default answers it after one call.
    t = fixture_transport(**{"art-editions/999999999": "error_404.json"})
    c = TrisolarisClient(transport=t)
    assert c.transport is t
    with pytest.raises(LivelibApiError):
        c.get_edition(999999999)
    assert len(t.calls) == 1


# ---------------------------------------------------------------------------
# Default transport retries (task P2-A 1): transient HTTP statuses and
# network-level httpx.TransportError are retried up to 3 attempts total, with
# sleep 2*(attempt+1) between attempts. sleep is injected so the suite stays
# network-free and fast. FixtureTransport is NOT retrying: an injected
# transport behaves exactly as told (its single call per request is pinned).
# ---------------------------------------------------------------------------


class ScriptedTransport:
    """Stands in for httpx.Client in _default_transport: answers a list of
    outcomes (httpx.Response or exception) and records the requests."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def request(self, method, url, *, json=None, headers=None):
        self.requests.append((method, url))
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def default_transport_case(monkeypatch, outcomes):
    """Patch httpx.Client to serve `outcomes`; return (scripted, sleeps list to
    inject as the transport's `sleep`). The module's real time.sleep stays
    unpatched and the harness fails on any use of it: only the injected sleep
    keeps the suite fast, which is exactly what this pins."""
    sleeps = []
    scripted = ScriptedTransport(outcomes)
    monkeypatch.setattr(client_mod.httpx, "Client", lambda **kw: scripted)
    monkeypatch.setattr(
        client_mod.time, "sleep", lambda s: pytest.fail("real time.sleep must not run")
    )
    return scripted, sleeps


def resp(status, body):
    return httpx.Response(
        status,
        content=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


OK_BODY = {"status": 200, "error": None, "payload": {"data": {"id": 1}}}
RETRY_BODIES = {
    429: {"status": 429, "error": {"type": "TooManyRequests", "detail": "slow down"}},
    500: {"status": 500, "error": {"type": "ServerError", "detail": "boom"}},
    502: {"status": 502, "error": {"type": "BadGateway", "detail": "bad gateway"}},
    503: {"status": 503, "error": {"type": "Unavailable", "detail": "down"}},
    504: {"status": 504, "error": {"type": "GatewayTimeout", "detail": "timeout"}},
}


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_default_transport_retries_transient_status(monkeypatch, status):
    scripted, sleeps = default_transport_case(
        monkeypatch,
        [
            resp(status, RETRY_BODIES[status]),
            resp(status, RETRY_BODIES[status]),
            resp(200, OK_BODY),
        ],
    )
    body = client_mod._default_transport(
        "GET", "https://example.test/x", sleep=sleeps.append
    )
    assert body == OK_BODY
    assert len(scripted.requests) == 3
    assert sleeps == [2, 4]  # 2*(attempt+1) between attempts, none after success


def test_default_transport_retries_transport_error(monkeypatch):
    err = httpx.TransportError("connection reset")
    scripted, sleeps = default_transport_case(
        monkeypatch, [err, err, resp(200, OK_BODY)]
    )
    assert (
        client_mod._default_transport(
            "GET", "https://example.test/x", sleep=sleeps.append
        )
        == OK_BODY
    )
    assert len(scripted.requests) == 3
    assert sleeps == [2, 4]


def test_default_transport_ignores_hostile_proxy_env(monkeypatch):
    """The air-laptop crash: an ambient no_proxy/ALL_PROXY entry with an IDN
    wildcard (`*.рф`) made httpx crash with InvalidURL: Invalid IDNA hostname
    IN THE CONSTRUCTOR — before any request. trust_env=False must always be
    passed, so the client never depends on the host's proxy bypass list."""
    captured = {}

    def fake_client(**kw):
        captured.update(kw)
        return ScriptedTransport([resp(200, OK_BODY)])

    monkeypatch.setattr(client_mod.httpx, "Client", fake_client)
    monkeypatch.setenv("ALL_PROXY", "socks5://proxy.lan:1080")
    monkeypatch.setenv("NO_PROXY", "*.рф,.lan")
    body = client_mod._default_transport("GET", "https://example.test/x", sleep=lambda s: None)
    assert body == OK_BODY
    assert captured["trust_env"] is False


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_default_transport_no_retry_on_other_http_error(monkeypatch, status):
    body = {"status": status, "error": {"type": "HttpError", "detail": "nope"}}
    scripted, sleeps = default_transport_case(monkeypatch, [resp(status, body)])
    with pytest.raises(LivelibApiError) as ei:
        client_mod._default_transport(
            "GET", "https://example.test/x", sleep=sleeps.append
        )
    assert ei.value.status == status
    assert len(scripted.requests) == 1
    assert sleeps == []


def test_default_transport_gives_up_after_three_attempts(monkeypatch):
    err = httpx.TransportError("connection reset")
    scripted, sleeps = default_transport_case(monkeypatch, [err, err, err])
    with pytest.raises(httpx.TransportError):
        client_mod._default_transport(
            "GET", "https://example.test/x", sleep=sleeps.append
        )
    assert len(scripted.requests) == 3
    assert sleeps == [2, 4]  # two gaps, three attempts total


def test_default_transport_error_envelope_after_retries_raises_api_error(monkeypatch):
    # 429 three times: the third attempt's envelope must surface as
    # LivelibApiError (the check is the same _check as before, just later).
    body = RETRY_BODIES[429]
    scripted, sleeps = default_transport_case(monkeypatch, [resp(429, body)] * 3)
    with pytest.raises(LivelibApiError) as ei:
        client_mod._default_transport(
            "GET", "https://example.test/x", sleep=sleeps.append
        )
    assert len(scripted.requests) == 3
    assert ei.value.status == 429
    assert ei.value.type == "TooManyRequests"
    assert sleeps == [2, 4]


def test_fixture_transport_does_not_retry(monkeypatch):
    # An injected transport behaves exactly as told: one call, immediate raise;
    # a transport-level exception from the transport also propagates at once.
    monkeypatch.setattr(
        client_mod.time, "sleep", lambda s: pytest.fail("must not sleep")
    )
    t = fixture_transport(**{EDITION_URL: "error_404.json"})
    with pytest.raises(LivelibApiError):
        TrisolarisClient(transport=t).get_edition(1004124394)
    assert len(t.calls) == 1

    class Exploding:
        def __call__(self, method, url, *, json_body=None, headers=None):
            raise httpx.TransportError("connection reset")

    with pytest.raises(httpx.TransportError):
        TrisolarisClient(transport=Exploding()).get_edition(1)


# ---------------------------------------------------------------------------
# Edition contents (task P2-A 2).
# ---------------------------------------------------------------------------

CONTENTS_ID = 1001482955
CONTENTS_URL = f"art-editions/{CONTENTS_ID}/contents"


def test_get_edition_contents_fixture():
    t = fixture_transport(**{CONTENTS_URL: "contents_1001482955.json"})
    out = TrisolarisClient(transport=t).get_edition_contents(CONTENTS_ID)
    assert urls(t) == [("GET", f"{BASE}/{CONTENTS_URL}")]
    data = fixture_payload("contents_1001482955.json")["data"]
    # one entry (id 2658136) has no url -> no work relation -> skipped
    expected = []
    for e in data:
        m = re.search(r"/work/(\d+)", e.get("url") or "")
        if m and e.get("title"):
            expected.append((e["title"], m.group(1)))
    assert len(data) == 37 and len(expected) == 36  # entry 2658136 has no url
    assert out == expected
    assert out[0] == ("О Викторе Юзефовиче Драгунском", "1004575603")


def test_get_edition_contents_skips_no_url_and_empty_title():
    # Synthetic single-page body: only /work/ entries with a non-empty title
    # survive. Served through a minimal inline transport (no fixture file).
    class Inline:
        def __init__(self):
            self.calls = []

        def __call__(self, method, url, *, json_body=None, headers=None):
            self.calls.append((method, url))
            return {
                "status": 200,
                "error": None,
                "payload": {
                    "data": [
                        {"id": 1, "title": "Есть работа", "url": "/work/777-a"},
                        {"id": 2, "title": "Без url", "url": None},
                        {"id": 3, "title": "", "url": "/work/888-b"},
                        {"id": 4, "title": "Не работа", "url": "/author/999-x"},
                    ]
                },
            }

    t = Inline()
    assert TrisolarisClient(transport=t).get_edition_contents(42) == [
        ("Есть работа", "777")
    ]
    assert t.calls == [("GET", f"{BASE}/art-editions/42/contents")]


# ---------------------------------------------------------------------------
# Shelf records (task P2-A 3).
# ---------------------------------------------------------------------------

READS_URL = "users/345497/arts/reads?limit=100&offset=0"


def test_shelf_records_shapes_and_dedupes():
    # Real marks from the user's reads shelf, trimmed to 4: rating 10, rating 0,
    # multi-author rating 7, and a duplicate edition id (first record wins).
    t = fixture_transport(**{READS_URL: "shelf_records_reads.json"})
    out = TrisolarisClient(transport=t).shelf_records(345497, "reads")
    assert urls(t) == [("GET", f"{BASE}/{READS_URL}")]
    assert out == [
        {
            "collection_id": 52718470,
            "edition_id": "1006734349",
            "title": "Всё, что должно разрешиться. Хроника почти бесконечной войны: 2013-2021",
            "author": "Захар Прилепин",
            "url": "/book/1006734349-vsjo-chto-dolzhno-razreshitsya-hronika-pochti-beskonechnoj-vojny-20132021-zahar-prilepin",
            "rating": 10,
            "my_mark": 5,
        },
        {
            "collection_id": 52675639,
            "edition_id": "1001000090",
            "title": "Тётя дяди Фёдора",
            "author": "Эдуард Успенский",
            "url": "/book/1001000090-tjotya-dyadi-fjodora-eduard-uspenskij",
            "rating": 0,
            "my_mark": None,
        },
        {
            "collection_id": 52408219,
            "edition_id": "1001773922",
            "title": "Главное внимание – главным вещам",
            "author": "Стивен Р. Кови, А. Роджер Меррилл, Ребекка Р. Меррилл",
            "url": "/book/1001773922-glavnoe-vnimanie-glavnym-vescham-stiven-r-kovi",
            "rating": 7,
            "my_mark": 4,
        },
    ]  # 4 marks in, 3 records out: the duplicate edition id is dropped


def test_shelf_records_rejects_bad_shelf():
    t = fixture_transport()
    with pytest.raises(ValueError):
        TrisolarisClient(transport=t).shelf_records(345497, "nope")
    assert t.calls == []


def test_parse_shelf_record_rules():
    # rating -> stars uses round() as-is (inherited from the old text-forge
    # mapper): 7/2 -> round(3.5) -> 4 (banker's rounding), 10/2 -> 5.
    # Missing url -> canonical /book/{id}; authors joined, blanks dropped;
    # mark without usable art_edition.id -> None (caller skips).
    rec = {
        "id": 1,
        "rating": 7,
        "art_edition": {
            "id": 55,
            "title": "T",
            "authors": [{"full_name": "A"}, {"full_name": ""}, {"x": 1}],
        },
    }
    assert client_mod.parse_shelf_record(rec) == {
        "collection_id": 1,
        "edition_id": "55",
        "title": "T",
        "author": "A",
        "url": "/book/55",
        "rating": 7,
        "my_mark": 4,  # round(7/2) == round(3.5) == 4 (banker's rounding)
    }
    assert client_mod.parse_shelf_record({"id": 2, "art_edition": {}}) is None
    assert client_mod.parse_shelf_record({"id": 3}) is None


def test_parse_shelf_record_is_the_single_mapping_source():
    # shelf_records output == [parse_shelf_record(m) for kept marks], so the
    # mapping lives in exactly one place.
    t = fixture_transport(**{READS_URL: "shelf_records_reads.json"})
    marks = fixture_payload("shelf_records_reads.json")["data"]
    expected = [client_mod.parse_shelf_record(m) for m in marks]
    expected = [r for r in expected if r]
    seen, deduped = set(), []
    for r in expected:
        if r["edition_id"] not in seen:
            seen.add(r["edition_id"])
            deduped.append(r)
    assert TrisolarisClient(transport=t).shelf_records(345497, "reads") == deduped


# ---------------------------------------------------------------------------
# Reviews write contract (sniffed live 2026-10-29): POST
# /api/art-editions/{eid}/reviews requires title, text_json, rating, emotions,
# has_spoilers — all five at once; rating lands on the collection mark, not
# inside the review. DELETE lives at /api/reviews/{id} (users/me path is 404).
# ---------------------------------------------------------------------------


def test_add_review_posts_full_contract_and_returns_id():
    t = fixture_transport(**{
        "art-editions/1001616054/reviews": ("POST", "review_created.json"),
        "art-editions/999999999/reviews": ("POST", "error_404.json"),
    })
    c = TrisolarisClient(transport=t)
    out = c.add_review(1001616054, "Заголовок", "Текст рецензии.", rating=10)
    assert out == {"id": 6413020}
    method, url, _body = t.calls[0]
    assert (method, url) == ("POST", f"{BASE}/art-editions/1001616054/reviews")
    body = sent_json(t)
    assert set(body) == {"title", "text_json", "rating", "emotions", "has_spoilers"}
    block = body["text_json"]["editor_blocks"][0]
    assert len(block["id"]) == 10 and block["type"] == "paragraph"
    assert block["data"]["text"] == "Текст рецензии."
    assert body["rating"] == 10 and body["emotions"] == [] and body["has_spoilers"] is False


def test_add_review_text_is_byte_exact_and_rating_range_checked():
    t = fixture_transport(**{"art-editions/1/reviews": ("POST", "review_created.json")})
    text = "Строка 1.\n\nСтрока 2 — с тире и «ёлочками».  "
    TrisolarisClient(transport=t).add_review(1, "T", text, rating=8)
    assert sent_json(t)["rating"] == 8
    blocks = sent_json(t)["text_json"]["editor_blocks"]
    assert [b["data"]["text"] for b in blocks] == ["Строка 1.", "Строка 2 — с тире и «ёлочками».  "]
    assert all(len(b["id"]) == 10 for b in blocks)
    # single-paragraph text: exactly one block, byte-exact
    TrisolarisClient(transport=t).add_review(1, "T", "Один абзац.\nВторая строка.", rating=10)
    blocks2 = sent_json(t, 1)["text_json"]["editor_blocks"]
    assert len(blocks2) == 1 and blocks2[0]["data"]["text"] == "Один абзац.\nВторая строка."
    with pytest.raises(ValueError):
        TrisolarisClient(transport=t).add_review(1, "T", "x", rating=-1)
    with pytest.raises(ValueError):
        TrisolarisClient(transport=t).add_review(1, "T", "x", rating=11)
    assert len(t.calls) == 2  # no http for the invalid ratings


def test_delete_review_uses_root_path_and_204():
    t = fixture_transport(**{"reviews/6413020": ("DELETE", "empty.json")})
    assert TrisolarisClient(transport=t).delete_review(6413020) is True
    assert t.calls == [("DELETE", f"{BASE}/reviews/6413020", None)]


def test_review_writes_tolerate_bodyless_and_envelope_2xx_both():
    # Delete/PATCH expect no body: both a bodyless 2xx and a 200 envelope
    # (the API has been seen to answer either) succeed with True.
    t = fixture_transport(
        **{
            "reviews/1": ("PATCH", "empty.json"),
            "reviews/2": ("PATCH", "review_updated_200.json"),
            "reviews/3": ("DELETE", "empty.json"),
            "reviews/4": ("DELETE", "review_deleted_200.json"),
        },
        status=200,
    )
    c = TrisolarisClient(llsid="tok", transport=t)
    assert c.update_review(1, "T", "x") is True
    assert c.update_review(2, "T", "x") is True
    assert c.delete_review(3) is True
    assert c.delete_review(4) is True


def test_update_review_patches_full_contract_returns_true_on_204():
    t = fixture_transport(**{
        "reviews/6413194": ("PATCH", "empty.json"),
        "reviews/404000": ("PATCH", "error_404.json"),
    })
    out = TrisolarisClient(transport=t).update_review(
        6413194, "Новый заголовок", "Новый текст.\n\nВторой абзац.", rating=9
    )
    assert out is True  # 204, no body (live-verified)
    method, url, body = t.calls[0]
    assert (method, url) == ("PATCH", f"{BASE}/reviews/6413194")
    assert set(body) == {"title", "text_json", "rating", "emotions", "has_spoilers", "visibility"}
    assert body["visibility"] == "public"
    assert [b["data"]["text"] for b in body["text_json"]["editor_blocks"]] == [
        "Новый текст.", "Второй абзац."]
    with pytest.raises(LivelibApiError):
        TrisolarisClient(transport=t).update_review(404000, "T", "x")
