# LiveLib trisolaris shelf API + auth contract (validated live 2026-10-26)

## Shelf lists via JSON API — anonymous, no cookies

```
GET https://beta.api.livelib.ru/trisolaris/api/users/{uid}/arts/reads?limit=100&offset=0
GET .../arts/unreads  |  .../arts/wants  |  .../arts/readings
```

- Response shape: `{status, payload: {pagination: {next_page, previous_page}, counters: {all}, data: [...]}}`.
- `limit` caps at 100 — `limit>100` (e.g. 500) returns HTTP 422 `ParamValidationError`. Page with `limit=100&offset=N`, follow `payload.pagination.next_page` until absent.
- Each record: `{id (collection-mark id), art_edition: {id, title, authors, url, cover_url, stats, is_*_blocked...}, rating (0–10), notes, read_date, status_set_at, art_formats_owned}`. `art_edition` does NOT carry a work id — work resolution still needs `/art-editions/{id}/contents` or curation merges.
- `counters.all` counts MARK RECORDS, not distinct editions. LiveLib keeps duplicate marks for one edition (same edition marked read twice, different mark ids/timestamps). Dedupe by `art_edition.id` and report the duplicate-mark count as a separate line — a "missing books" alarm against the counter is usually just this.
- Anonymous access shows the PUBLIC view: statuses the owner marked private simply do not appear, and they cannot be recovered without the owner's session. Compare API union vs counter before crying data loss.
- Shelf keyword mapping: API `reads/unreads/wants/readings` ↔ statuses `read/unread/wish/reading`. (URL of the old SSR want shelf stays `/books/want`; status name is `wish`.)

Shelf data channels, in preference order:
1. `/users/{uid}/arts/{shelf}` JSON — clean records, honest pagination, counters. Use this for all shelf export.
2. Old-site SSR shelf pages (`www.livelib.ru/users/{uid}/books/{shelf}?page=N`) — FLIGHT parse: regex the record pattern `"id":<digits>,"art_edition":{` **directly on the raw response body**; never unescape the whole payload first (escaped inner quotes inside `description` corrupt the record boundary and `raw_decode` failures read as "shelf is empty"). Pitfalls: the old www site IGNORES `?page` (serves page 1 for every N); the beta SSR honors it and accumulates. Both silently cap under SSR load at fewer records than the counter claims.
3. DOM scraping of the beta SPA — last resort.

## Count reconciliation (why UI numbers disagree)

- Old-site read counter, new-site logged-in counter, and anon counter can all differ: each counts a different set (marks vs editions; private marks visible to owner only). Never treat any single counter as the corpus size — the deduped edition-id set is.
- Same-work editions render as multiple shelf cards (different editions = different marks) — that is not the clone problem; curation `merges` is the answer to work-id fragmentation.

## Auth (from beta SPA JS chunk dissection, 36 chunks)

- Login endpoint: `POST https://beta.livelib.ru/api/auth/sessions` (served by beta.livelib.ru, NOT under /trisolaris) with body `{identity_token, password?, remember}`. Password IS sent in plain JSON when present.
- `identity_token` is MANDATORY even for password login — it is minted by the SPA's public key/OAuth bootstrap; a bare curl login cannot produce it. Password login is therefore only reproducible through a live browser session (browser_vault fill on beta origin), not headless curl.
- Session teardown: `DELETE /api/auth/sessions/current`. 403 on /api/auth/sessions with `Retry-After` header = throttled; honor the header.
- `GET /api/users/me` without cookies → 401 `AuthenticationRequired`; with the owner's cookies it resolves to the owner. Read-only shelf export needs NO auth (use `/users/{uid}/...` explicitly).
- API base for trisolaris: `https://beta.api.livelib.ru/trisolaris/api` (www variant 403 via DDoS-Guard; old site shows `x-powered-by: PHP`).

## API-endpoint discovery recipe (reusable for any Next.js SPA)

`curl` the SPA page → collect `/_next/static/chunks/*.js` URLs → download all → `grep -ho '"/api/[a-z0-9/_-]*"'`. Endpoint strings and request bodies appear verbatim in minified chunks; read ~200 chars around each hit for method + body fields. This is how the shelf endpoints and auth contract were mapped without docs.

## Old-site login endpoints (legacy, for reference)

`/account/authorizeform` (POST, `is_new_design=ll2019`) → returns the login form HTML; `/account/checkemail` (POST `email=...`) → password-step form (`user[login]` hidden + `user[password]`); submit target `/account/login` with fields `user[login]/user[password]/popup=regform`, cookie jar from a prior page visit (DDoS-Guard cookies). Prefer beta SPA login via browser.
