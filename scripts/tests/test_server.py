"""Tests for the livelib MCP server.

Both layers exercise real MCP protocol mechanics:

  * In-memory sessions (mcp 2.x: Client(SERVER) connects the MCPServer over
    the SDK's in-memory transport — initialize handshake, tools/list, real
    tools/call). Each tool's wire result must decode back to exactly the
    client method's return value (no shaping), and the transport must
    record exactly the client URLs. Only the HTTP transport is
    fixture-backed; the protocol is not bypassed (a bare function call
    would skip wrapping, text blocks, and parsing entirely).
  * Stdio smoke: spawn the `livelib-mcp` console script, hand-talk
    line-delimited JSON MCP (protocolVersion 2024-11-05), including one
    REAL-API get_edition call.
"""
import json
import shutil
import subprocess
import urllib.parse
from typing import Any

import anyio
import test_client as tc
from mcp.client.client import Client
from mcp.types import TextContent

from livelib_mcp import server as srv
from livelib_mcp.client import TrisolarisClient
from livelib_mcp.server import SERVER

BASE = tc.BASE

TOOL_NAMES = {
    "get_edition",
    "get_shelf",
    "get_edition_quotes",
    "get_quotes",
    "get_reviews",
    "search_editions",
    "add_quote",
    "delete_quote",
    "add_review",
    "delete_review",
    "update_review",
}

EDITION_URL = "art-editions/1004124394"

LIST_TOOLS = {"get_quotes", "get_reviews", "search_editions"}
BOOL_TOOLS = {"delete_quote"}  # bool returns are readable only via structuredContent


def raw_call(
    name, arguments, mapping, *, llsid: str | None = "test-llsid"
) -> tuple[Any, tc.FixtureTransport]:
    """One tools/call through a complete in-memory MCP session.

    mapping: exact URL (GET) or (method, URL) -> fixture body. srv._client, the
    factory every tool calls, is swapped for a fixture-backed client and its
    invocation counted, so the pin proves the tool built its client this way.
    The response status comes from the recorded envelope's own "status" field.
    Returns (raw CallToolResult, FixtureTransport).
    """
    t = tc.FixtureTransport(mapping)
    made = []

    def factory():
        made.append(1)
        return tc.TrisolarisClient(llsid=llsid, transport=t)

    async def run():
        orig = srv._client
        srv._client = factory
        try:
            async with Client(SERVER, mode="legacy") as client:
                tools = await client.list_tools()
                assert {x.name for x in tools.tools} == TOOL_NAMES
                assert len(tools.tools) == 11
                raw = await client.call_tool(name, arguments)
        finally:
            srv._client = orig
        assert len(made) == 1, f"tool {name} built {len(made)} clients, expected 1"
        return raw

    return anyio.run(run), t


def urls(t):
    return [(m, u) for m, u, _ in t.calls]


def via_mcp(name, arguments, mapping):
    raw, t = raw_call(name, arguments, mapping)
    assert not raw.is_error, f"tool {name} failed: {raw.content}"
    return raw, t


def wire_decode(name, raw) -> Any:
    """Decode a tools/call result back to the tool's Python value.

    Both emission styles are real mcp 2.x output:
      * structuredContent — present when the return annotation yields an output
        schema. A BaseModel/wrapped return (bool) comes as {"result": ...};
        dict[str, Any] comes unwrapped but coerced (see test output_schema
        coercion pin below), so a bool is only trustworthy via structuredContent
        on a wrapped schema — hence the BOOL_TOOLS branch.
      * content text blocks — pydantic_core.to_json(indent=2) for a dict, one
        block per item for a list, "true"/"false" for a bool.
    Wire mechanics stay real: the value crossed a protocol round-trip.

    Returns Any: the decoded shape depends on the tool's return annotation
    (dict / list / bool); the wire itself does not carry it.
    """
    sc = getattr(raw, "structured_content", None)
    if name in BOOL_TOOLS:
        assert isinstance(sc, dict) and set(sc) == {"result"}, f"{name}: {sc!r}"
        assert isinstance(sc["result"], bool), f"{name}: {sc!r} must carry a real bool"
        return sc["result"]
    if sc is not None:
        return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
    if not raw.content:
        return None
    if isinstance(raw.content[0], TextContent):
        if name in LIST_TOOLS:  # SDK unrolls a list return into per-item blocks
            return [json.loads(c.text) for c in raw.content if isinstance(c, TextContent)]
        return json.loads(raw.content[0].text)
    raise AssertionError(f"unexpected content type for {name}: {raw.content!r}")


def pin(name, arguments, mapping, expected) -> tc.FixtureTransport:
    """Tool result over the wire == the client method's return, exactly.

    Expected is computed by a direct TrisolarisClient call on the same
    fixture; the decoded wire value must equal it with no shaping, and the
    transport pins the URLs the server fetched.
    """
    raw, t = via_mcp(name, arguments, mapping)
    assert wire_decode(name, raw) == expected
    return t


# ---------------------------------------------------------------------------
# Protocol pins: tool output == client method output, same fixture.
# ---------------------------------------------------------------------------


def test_tools_list_exposes_all_eleven_names():
    raw, _ = via_mcp("get_edition", {"edition_id": 1004124394},
                     {f"{BASE}/{EDITION_URL}": "edition_1004124394.json"})
    assert wire_decode("get_edition", raw)["id"] == 1004124394  # tools/list asserted in via_mcp


def test_pin_get_edition():
    url = f"{BASE}/{EDITION_URL}"
    expected = tc.TrisolarisClient(
        transport=tc.FixtureTransport({url: "edition_1004124394.json"})
    ).get_edition(1004124394)
    t = pin("get_edition", {"edition_id": 1004124394}, {url: "edition_1004124394.json"}, expected)
    assert expected["title"] == "Экономика всего. Как институты определяют нашу жизнь"
    assert urls(t) == [("GET", url)]


def test_pin_get_shelf():
    url = f"{BASE}/users/345497/arts/readings?limit=100&offset=0"
    mapping = {url: "shelf_readings_dupes.json"}
    expected = tc.TrisolarisClient(transport=tc.FixtureTransport(mapping)).get_shelf(345497, "readings")
    t = pin("get_shelf", {"uid": 345497, "shelf": "readings"}, mapping, expected)
    assert set(expected) == {"editions", "marks_seen", "duplicate_marks", "counters_all"}
    assert urls(t) == [("GET", url)]


def test_pin_get_edition_quotes():
    url = f"{BASE}/art-editions/1004124394/quotes?limit=5&offset=0"
    mapping = {url: "edition_quotes_page1.json"}
    expected = tc.TrisolarisClient(transport=tc.FixtureTransport(mapping)).get_edition_quotes(1004124394, limit=5)
    t = pin("get_edition_quotes", {"edition_id": 1004124394, "limit": 5}, mapping, expected)
    assert expected["next_offset"] == 5
    assert urls(t) == [("GET", url)]


def test_pin_get_quotes():
    mapping = {
        f"{BASE}/users/345497/quotes?limit=50&offset=0&ordering=created_desc": "quotes_walk_p1.json",
        f"{BASE}/users/345497/quotes?limit=50&offset=50&ordering=created_desc": "quotes_walk_p2.json",
    }
    expected = tc.TrisolarisClient(transport=tc.FixtureTransport(mapping)).get_quotes(345497)
    t = pin("get_quotes", {"uid": 345497}, mapping, expected)
    assert len(expected) == 5
    assert len(t.calls) == 2  # the pagination walk happens inside the tool


def test_pin_get_reviews():
    url = f"{BASE}/users/345497/reviews?limit=50&offset=0"
    mapping = {url: "reviews_walk_last.json"}
    expected = tc.TrisolarisClient(transport=tc.FixtureTransport(mapping)).get_reviews(345497)
    t = pin("get_reviews", {"uid": 345497}, mapping, expected)
    assert len(expected) == 2
    assert urls(t) == [("GET", url)]


def test_pin_search_editions():
    query = "Аузан"
    url = f"{BASE}/search?query={urllib.parse.quote(query, safe='')}&limit=3"
    mapping = {url: "search_auzan.json"}
    expected = tc.TrisolarisClient(transport=tc.FixtureTransport(mapping)).search_editions(query, limit=3)
    t = pin("search_editions", {"query": query, "limit": 3}, mapping, expected)
    assert len(expected) == 2  # mixed results filtered to art_editions only
    assert urls(t) == [("GET", url)]


ADD_URL = "art-editions/1004124394/quotes"
DELETE_URL = "users/me/quotes/25804270"


def test_pin_add_quote():
    url = f"{BASE}/{ADD_URL}"
    mapping = {("POST", url): "quote_created.json"}
    expected = tc.TrisolarisClient(transport=tc.FixtureTransport(mapping)).add_quote(
        1004124394, "Текст цитаты", visibility="friends"
    )
    t = pin(
        "add_quote",
        {"edition_id": 1004124394, "text": "Текст цитаты", "visibility": "friends"},
        mapping,
        expected,
    )
    assert expected == {"id": 25804270}  # only the user-quote id; no invented fields
    assert urls(t) == [("POST", url)]
    assert t.requests[0].headers["cookie"] == "llsid=test-llsid"
    sent = json.loads(t.requests[0].content.decode("utf-8"))
    assert sent["text_json"]["editor_blocks"][0]["data"]["text"] == "Текст цитаты"
    assert sent["visibility"] == "friends"
    bid = sent["text_json"]["editor_blocks"][0]["id"]
    assert len(bid) == 10 and bid.isascii() and all(ch.isalnum() for ch in bid)


def test_pin_delete_quote():
    url = f"{BASE}/{DELETE_URL}"
    t = pin("delete_quote", {"user_quote_id": 25804270}, {("DELETE", url): "empty.json"}, True)
    assert urls(t) == [("DELETE", url)]
    assert t.requests[0].headers["cookie"] == "llsid=test-llsid"


def test_delete_quote_404_is_a_tool_error_not_success():
    # "Already gone" stays an error: is_error on the wire, no True anywhere.
    # The API's own text reaches the model — the tool re-raises the API error as
    # mcp ToolError (an unexpected exception would be masked by the SDK).
    raw, t = raw_call(
        "delete_quote", {"user_quote_id": 25804270}, {("DELETE", f"{BASE}/{DELETE_URL}"): "error_404.json"}
    )
    assert raw.is_error
    text = text_of(raw)
    assert "delete_quote" in text
    assert "404" in text and "ResourceNotFoundByID" in text
    assert "true" not in text.lower().replace("delete_quote", "")
    assert urls(t) == [("DELETE", f"{BASE}/{DELETE_URL}")]


def test_add_quote_401_reports_auth_requirement():
    # llsid=None (as when LIVELIB_LLSID is unset) -> the API's 401 surfaces as a
    # tool error naming the auth failure, not as a created id.
    raw, t = raw_call("add_quote", {"edition_id": 1004124394, "text": "x"},
                      {("POST", f"{BASE}/{ADD_URL}"): "error_401.json"}, llsid=None)
    assert raw.is_error
    assert raw.is_error
    assert "AuthenticationRequired" in text_of(raw)
    assert t.requests[0].headers.get("cookie") is None  # anonymous attempt
    assert urls(t) == [("POST", f"{BASE}/{ADD_URL}")]


def text_of(raw) -> str:
    """Concatenated text of a tools/call result's text blocks.

    An error result carries a TextContent block; anything else on the wire is a
    real test failure, named rather than silently returning nothing.
    """
    blocks = [c for c in raw.content if isinstance(c, TextContent)]
    assert blocks, f"no TextContent in result: {raw.content!r}"
    return "\n".join(c.text for c in blocks)


def _call_tool_env(name, arguments, pairs):
    """tools/call whose client is built by the REAL client_from_env (only the
    transport is injected), so llsid genuinely comes from os.environ."""
    factory, t = tc.env_transport(pairs)
    prev = srv._client
    srv._client = factory
    try:
        return _call_tool(name, arguments), t
    finally:
        srv._client = prev


def _call_tool(name, arguments):
    """tools/call through an in-memory session, no factory patch in scope."""

    async def run():
        async with Client(SERVER, mode="legacy") as client:
            return await client.call_tool(name, arguments)

    return anyio.run(run)


def test_llsid_env_reaches_the_request(monkeypatch):
    # Real client_from_env (only the transport is injected): the env value
    # reaches the request as a Cookie.
    monkeypatch.setenv("LIVELIB_LLSID", "env-llsid")
    factory, t = tc.env_transport({f"{BASE}/{EDITION_URL}": "edition_1004124394.json"})
    monkeypatch.setattr(srv, "_client", factory)
    raw = _call_tool("get_edition", {"edition_id": 1004124394})
    assert not raw.is_error, raw.content
    assert t.requests[0].headers["cookie"] == "llsid=env-llsid"


def test_default_client_is_lazy_and_anonymous_without_env(monkeypatch):
    # _client delegates to client_from_env; constructing a client hits no
    # network, and no env var means the request carries no Cookie header.
    monkeypatch.delenv("LIVELIB_LLSID", raising=False)
    assert isinstance(srv._client(), TrisolarisClient) and srv._client().llsid is None
    factory, t = tc.env_transport({f"{BASE}/{EDITION_URL}": "edition_1004124394.json"})
    monkeypatch.setattr(srv, "_client", factory)
    raw = _call_tool("get_edition", {"edition_id": 1004124394})
    assert not raw.is_error, raw.content
    assert "cookie" not in t.requests[0].headers


def test_add_quote_from_real_env(monkeypatch):
    # End-to-end, with NO factory patch: the tool's own _client() ->
    # client_from_env() reads LIVELIB_LLSID from os.environ and the POST goes
    # out with that Cookie. The fixture transport sits below the client, so the
    # wire/payload pins still hold while nothing leaves the process.
    monkeypatch.setenv("LIVELIB_LLSID", "env-quote-token")
    url = f"{BASE}/{ADD_URL}"
    raw, t = _call_tool_env("add_quote", {"edition_id": 1004124394, "text": "  Точность  \t", "visibility": "private"},
                            {("POST", url): "quote_created.json"})
    assert not raw.is_error, raw.content
    assert tc.read_body(raw) == {"id": 25804270}
    req = t.requests[0]
    assert req.headers["cookie"] == "llsid=env-quote-token"
    sent = json.loads(req.content.decode("utf-8"))
    assert sent["visibility"] == "private"
    assert sent["text_json"]["editor_blocks"][0]["data"]["text"] == "  Точность  \t"  # byte-exact


def test_delete_quote_from_real_env(monkeypatch):
    monkeypatch.setenv("LIVELIB_LLSID", "env-delete-token")
    url = f"{BASE}/{DELETE_URL}"
    raw, t = _call_tool_env("delete_quote", {"user_quote_id": 25804270}, {("DELETE", url): "empty.json"})
    assert not raw.is_error, raw.content
    assert wire_decode("delete_quote", raw) is True
    assert t.requests[0].headers["cookie"] == "llsid=env-delete-token"
    assert tc.urls(t) == [("DELETE", url)]


# ---------------------------------------------------------------------------
# Stdio smoke: spawn the console script, speak MCP by hand (legacy framing),
# one real-API call.
# ---------------------------------------------------------------------------


def _msg(obj):
    return json.dumps(obj, separators=(",", ":")) + "\n"


def test_stdio_smoke_real_api():
    binary = shutil.which("livelib-mcp")
    assert binary is not None, "livelib-mcp console script not on PATH"
    proc = subprocess.Popen(
        [binary],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    # The PIPEs above guarantee these streams exist; binding narrowed locals
    # once keeps the type checker honest (a None here is a real bug, named).
    stdin, stdout = proc.stdin, proc.stdout
    assert stdin is not None and stdout is not None
    try:
        def rpc(obj):
            stdin.write(_msg(obj))
            stdin.flush()
            line = stdout.readline()
            assert line, "server closed stdout"
            return json.loads(line)

        init = rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest-stdio", "version": "0"},
                },
            }
        )
        assert init["result"]["serverInfo"]["name"] == "livelib"
        stdin.write(
            _msg({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        stdin.flush()

        tools = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert {t["name"] for t in tools["result"]["tools"]} == TOOL_NAMES

        call = rpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_edition", "arguments": {"edition_id": 1004124394}},
            }
        )
        assert not call["result"].get("isError")
        text = call["result"]["content"][0]["text"]
        assert "Экономика" in json.loads(text)["title"]
    finally:
        stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    assert proc.returncode == 0
