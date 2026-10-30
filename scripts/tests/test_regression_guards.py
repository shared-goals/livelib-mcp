"""Regression guards for the P4 refactor — behaviors that must NOT change.

1. Shelf walks step by the API's next_page offset, not by a hardcoded +100
   (a page may report a different step).
2. A delete/patch answering with an envelope stays a success (True) — it must
   not start raising EmptyResponse.
"""

import urllib.parse

import test_client as tc

from livelib_mcp.client import TrisolarisClient


class OnePageThenDone:
    """Serves a single shelf page whose next_page offset differs from the
    request limit, then the empty tail page at exactly that offset."""

    def __init__(self, *, limit_step):
        self.step = limit_step
        self.calls = []

    def __call__(self, method, url, *, json_body=None, headers=None):
        import urllib.parse

        self.calls.append((method, url))
        offset = int(
            urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("offset", ["0"])[0]
        )
        if offset == 0:
            next_page = f"/api/users/1/arts/reads?limit=100&offset={self.step}"
            data = [{"id": 1, "art_edition": {"id": 11, "title": "A"}}]
        else:
            next_page = None
            data = []
        return {
            "status": 200,
            "error": None,
            "payload": {"data": data, "pagination": {"next_page": next_page}},
        }


def _offset_of(url: str) -> int:
    return int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("offset", ["0"])[0])


def test_shelf_walks_step_by_next_page_not_by_100():
    # next_page asks for offset 40: the walk must obey it, not offset += 100.
    for name, call in (
        ("get_shelf", lambda c: c.get_shelf(1, "reads")),
        ("shelf_records", lambda c: c.shelf_records(1, "reads")),
    ):
        t = OnePageThenDone(limit_step=40)
        call(TrisolarisClient(transport=t))
        offsets = [_offset_of(u) for _, u in t.calls]
        assert offsets == [0, 40], f"{name}: walked {offsets}, expected [0, 40]"


def test_delete_and_patch_accept_envelope_200_as_success():
    t = tc.fixture_transport(
        **{
            "reviews/1": ("PATCH", "review_updated_200.json"),
            "reviews/2": ("DELETE", "review_deleted_200.json"),
            "users/me/quotes/5": ("DELETE", "quote_created.json"),
        },
    )
    c = TrisolarisClient(llsid="tok", transport=t)
    assert c.update_review(1, "T", "x") is True
    assert c.delete_review(2) is True
    assert c.delete_quote(5) is True
