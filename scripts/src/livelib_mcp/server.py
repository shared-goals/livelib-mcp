"""MCP stdio server exposing LiveLib reads and llsid-authenticated quote
writes as tools.

Thin wrapper: each tool is exactly one client call — no data shaping. All tools
build their client through the module-level `_client` factory, which delegates to
`client_from_env()` so LIVELIB_LLSID is read at call time. Tests monkeypatch
`_client` to inject a fixture-backed transport (call-counted, so the pins prove
the tool really built its client that way).

API failures are re-raised as `mcp` ToolError: that is the SDK's "anticipated
failure" channel, whose message reaches the model. Any other exception is
treated by the SDK as a crash and reaches the client with its text withheld.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .client import LivelibApiError, TrisolarisClient, client_from_env

SERVER = MCPServer(
    "livelib",
    instructions="LiveLib book data via the trisolaris API: edition cards, "
    "quotes, reviews, shelves, and search (anonymous), plus adding and deleting "
    "your own quotes (requires the LIVELIB_LLSID env var).",
)


def _client(**kwargs) -> TrisolarisClient:
    """Fresh stateless client per call; LIVELIB_LLSID is read at call time."""
    return client_from_env(**kwargs)


def _call(what, fn):
    """Run one client call, surfacing API errors to the model as ToolError."""
    try:
        return fn()
    except LivelibApiError as exc:
        raise ToolError(f"{what}: {exc}") from exc


@SERVER.tool()
def get_edition(edition_id: int) -> dict:
    """LiveLib edition card: title, authors, cover, series, publication meta."""
    return _call("get_edition", lambda: _client().get_edition(edition_id))


@SERVER.tool()
def get_shelf(uid: int, shelf: str) -> dict:
    """A user's bookshelf (reads/unreads/wants/readings) as edition records,
    with duplicate-mark dedupe and counters."""
    return _call("get_shelf", lambda: _client().get_shelf(uid, shelf))


@SERVER.tool()
def add_review(edition_id: int, title: str, text: str, rating: int = 10, has_spoilers: bool = False) -> dict:
    """Post a review of an edition (rating 0-10, 10 = five stars on the site).
    Requires the LIVELIB_LLSID env var; returns the created review id."""
    return _call("add_review", lambda: _client().add_review(
        edition_id, title, text, rating, has_spoilers=has_spoilers))


@SERVER.tool()
def delete_review(review_id: int) -> bool:
    """Delete the caller's own review. Requires the LIVELIB_LLSID env var.
    A review edited in the legacy web editor can only be deleted there."""
    return _call("delete_review", lambda: _client().delete_review(review_id))


@SERVER.tool()
def update_review(review_id: int, title: str, text: str, rating: int = 10, has_spoilers: bool = False) -> bool:
    """Update the caller's own review (title, text, rating 0-10). Requires the
    LIVELIB_LLSID env var. Fails 409 if the review was edited in the legacy
    web editor — edit it there instead."""
    return _call("update_review", lambda: _client().update_review(
        review_id, title, text, rating, has_spoilers=has_spoilers))


@SERVER.tool()
def get_edition_quotes(edition_id: int, limit: int = 50, offset: int = 0) -> dict:
    """One page of quotes marked on an edition, plus total count and next offset."""
    return _call("get_edition_quotes", lambda: _client().get_edition_quotes(edition_id, limit=limit, offset=offset))


@SERVER.tool()
def get_quotes(uid: int) -> list[dict]:
    """All quotes a user has marked, newest first (paginated walk)."""
    return _call("get_quotes", lambda: _client().get_quotes(uid))


@SERVER.tool()
def get_reviews(uid: int) -> list[dict]:
    """All reviews a user has written (paginated walk)."""
    return _call("get_reviews", lambda: _client().get_reviews(uid))


@SERVER.tool()
def search_editions(query: str, limit: int = 10) -> list[dict]:
    """Search LiveLib for book editions matching a query; returns edition records."""
    return _call("search_editions", lambda: _client().search_editions(query, limit=limit))


@SERVER.tool()
def add_quote(edition_id: int, text: str, visibility: str = "public") -> dict:
    """Add a quote to a LiveLib edition. Requires the LIVELIB_LLSID env var
    (livelib.ru session cookie value); an anonymous call gets a 401 from the
    API. Returns the created user-quote id as {"id": ...}."""
    return _call("add_quote", lambda: _client().add_quote(edition_id, text, visibility))


@SERVER.tool()
def delete_quote(user_quote_id: int) -> bool:
    """Delete a quote you marked on LiveLib. Requires the LIVELIB_LLSID env var
    (livelib.ru session cookie value). True on success; a quote that is not
    found, or is not yours, is reported as an error."""
    return _call("delete_quote", lambda: _client().delete_quote(user_quote_id))


def main() -> None:
    SERVER.run()


if __name__ == "__main__":
    main()
