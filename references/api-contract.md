# LiveLib trisolaris API contract (validated live 2026-10-28)

Base: `https://beta.api.livelib.ru/trisolaris/api`. All reads anonymous; all
matching by NUMERIC edition id. OpenAPI/docs are disabled; openapi-typescript
schemas are ERASED at runtime — body shapes are learned from real responses,
never from chunk grep.

## Reads (anonymous)

- Quote/review walks: `users/{uid}/{quotes|reviews}?limit=50&offset=N&ordering=created_desc`
  — full metadata (text HTML, `art_edition` card, `pagination.next_page`).
- Shelf lists: `users/{uid}/arts/{reads|readings|unreads|wants}?limit=100&offset=N`
  (`limit` caps at 100; >100 → 422). Shape and pitfalls: references/shelf-api.md.
- Edition card: `art-editions/{id}` (+ `/contents`, `/quotes`, `/reviews`,
  `/cycles`, `/similars`, …). Work card: `art-works/{work_id}`;
  `art-works/{work_id}/editions` lists every edition of the work.
- Search: `search?query=…` — `type=="art_editions"` items only.
- 404 envelope: `{"status":404,"error":{...}}`; errors carry
  `error_descriptions[].loc`.

## Write: add quote

```
POST /api/art-editions/{edition_id}/quotes
Cookie: llsid=<LIVELIB_LLSID>          (single cookie — headless-verified)
Content-Type: application/json
{"text_json": {"editor_blocks": [{"id": "<10 chars [A-Za-z0-9]>", "type": "paragraph",
                                  "data": {"text": "<quote text, byte-exact>"}}]},
 "visibility": "public" | "friends" | "private"}
→ 201 {"status":201,"error":null,"payload":{"data":{"id":<user_quote_id>}}}
```

- **Block `id` is MANDATORY** (Editor.js block id, ~10 chars) — missing →
  422 with loc `["body","text_json","editor_blocks",0,"paragraph","id"]`
  (pydantic discriminated-union path; misleading).
- **visibility enum is `public | friends | private`** (NOT `restricted` —
  bad value → 422, msg "Input should be 'public', 'friends' or 'private'").
- The 201 response carries ONLY the new user-quote id — NO canonical/dedupe
  fields. Discover dedupe by reading the quote back.
- Quote text passes through byte-exact; never strip/normalize.
- Server adds `tunes:{}` itself.

## Write: delete quote

`DELETE /api/users/me/quotes/{user_quote_id}` → 204 empty body; the quote is
then 404 on direct GET. Unauthenticated → 401
`{"type":"AuthenticationRequired", "detail":"You must be logged in to access
this resource."}`.

## Two-level quote model — dedupe by text (live-verified)

A POST creates a *user-quote* (your collection record) that may attach to an
EXISTING *canonical quote*: LiveLib matches quote TEXT across all editions of
a work. If the same text was already added by anyone, your user-quote
references THEIR canonical — quoter/date/edition of the card stay the
original's, and the quote page shows the original adder (with a «В друзья»
button), not you. Normal state, not an API artifact (sampled recent profiles
routinely reference other users' canonicals, including different editions).
Card authorship cannot be forced without altering the text — never do that.

## Old site/app = old backend (confirmed live 2026-10-28)

Quotes created via the beta trisolaris API do NOT show as "in my collection"
on the OLD web site nor in the mobile app — while the beta layer is complete
and consistent (profile list, book-scoped `art-editions/{id}/user-quotes`,
public per-work quote stream, counters). If old-site visibility matters:
delete the user-quote and re-add from the old site/app, then check counters
for duplicates.

## Auth model (headless-verified 2026-10-28)

- The single httpOnly cookie `llsid` is SUFFICIENT: GET `/users/me/quotes`
  → 200, POST quote → 201, DELETE → 204, with ONLY `Cookie: llsid=<value>`.
- `llsid` never appears in `document.cookie` — after a vault login (beta SPA,
  two-step popup), capture the jar via CDP `Network.getCookies` (urls:
  beta.livelib.ru + beta.api.livelib.ru), extract `llsid`, store the raw
  value as `LIVELIB_LLSID` in `~/.hermes/.env`. Probe the MINIMAL credential
  set first; do not carry a full cookie string into env config.
- In-page fetch with `credentials:'include'` on beta.livelib.ru also works
  (CORS allows it) — the browser fallback when no env cookie exists.
## Reviews write contract (sniffed live 2026-10-29)

```
POST /api/art-editions/{edition_id}/reviews
{"title": "<заголовок>", "text_json": {"editor_blocks": [
    {"id": "<10 chars>", "type": "paragraph", "data": {"text": "<абзац 1>"}},
    {"id": "<10 chars>", "type": "paragraph", "data": {"text": "<абзац 2>"}},
    ...]},
 "rating": 0-10, "emotions": [], "has_spoilers": false}
→ 201 {"payload":{"data":{"id":<review_id>}}}
```

- All five fields REQUIRED at once (missing → 422, one loc each).
- **Rating scale is 0–10** (live-verified: `rating: 5` renders as 2.5 stars;
  «пятёрка» = 10). It lands on the user's collection mark of the book
  (`reviewer_art_rating` in review records) — a review record itself never
  carries a rating field. The shelf-record my_mark rule (rating/2) applies.
- **Paragraphs = separate editor blocks** (live-verified: 10 blocks → 10
  `<p>` in the rendered HTML). A single block flattens line breaks;
  line breaks WITHIN one block (`\n`) are preserved in the rendered text.
- DELETE: `DELETE /api/reviews/{id}` → 204 (NOT `/users/me/reviews/...` —
  that path is 404). DELETE works even while PATCH is locked (both
  live-verified).
- UPDATE: `PATCH /api/reviews/{id}` → 204 with NO body (not 200+payload).
  Body = the full add_review contract PLUS the REQUIRED `visibility`
  ("public"). **Prefer update over delete+recreate once a review has
  engagement** — the id and URL are stable, so likes/views/comments survive;
  delete+create resets them and orphans inbound links.
- **Legacy-editor lock (live-verified)**: once a review is edited in the old
  web editor, the record flips to legacy format (`text_json: null`,
  `is_edited_by_new: false`) and API PATCH answers `409 NotEditableByNewEditor`
  ("The text was changed in the legacy editor. Edit it there."). The API can
  create and delete; after a legacy edit the old editor owns the record.
  DELETE still works while PATCH is locked.
- The review list carries engagement under `reactions` (views/likes/comments
  counts) and the reviewer's mark under `reviewer_art_rating` — never look
  for a rating field on the review record itself.
