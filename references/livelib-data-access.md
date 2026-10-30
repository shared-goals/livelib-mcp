# LiveLib data access

## Priority order: API → anonymous SSR → browser

Try channels in this order. Do NOT start a long browser crawl before exhausting the first two — the browser daemon can wedge for hours (and its chromium processes may belong to another user, unkillable), while API and SSR finish the same work in minutes.

## 1. Internal API (shelves, quotes, reviews, user data)

- Base: `https://beta.api.livelib.ru/trisolaris/api/` — works ANONYMOUSLY (plain urllib/curl with a browser UA, no cookies, no antifrog, no pauses): `users/<id>/quotes?limit=50&offset=N`, `users/<id>/reviews?limit=50&offset=N`, `users/<id>/detailed`, `art-editions/{id}`.
- **Shelves are API-served, first channel always** (verified live): `users/<id>/arts/{reads|readings|unreads|wants}?limit=100&offset=N` — paged JSON of collection records, `payload.counters.all` gives the exact total. The status name comes from the shelf slug via the livelib pair (url concept `want` → status `wish`; API plural `wants`). Livelib keeps a SECOND record when a book is re-marked read — dedupe by edition id (a `parse_api_record`-style helper plus a shelf-walk dedupe; see any downstream client implementation); the counter counts records, the site render dedupes, so expect records > unique books.
- Edition and work cards (verified sweep — before writing ANY HTML parser, check this list):
  - `art-editions/{id}` — edition card: title, authors, cover_url, `art_id`, stats (rating/marks/quotes/reviews counts), isbn, publication_year, publishers, url. Sub-resources: `/contents`, `/quotes`, `/reviews`, `/stats`, `/sections`, `/selections`, `/similars`, `/cycles`, `/views` — all plain JSON.
  - `art-editions/{id}/contents` — the «Содержание» payload as plain JSON: one entry per WORK the edition carries, each with its `/work/<id>` url. This is the edition→work relation; it made the HTML flight-stream parser obsolete (the JSON call is ~0.15 s/edition against ~2.7 s for the full HTML page — an order of magnitude, and no escaping layer).
  - `art-works/{work_id}` — work card; `/art-works/{work_id}/editions` lists EVERY edition of the work in ONE call (sibling editions without per-edition probing). `arts/{art_id}/editions` is the art_id variant.
  - Sibling edition detection nuance: LiveLib often splits the same book/collection across multiple distinct `art_id`s created over the years (e.g. «Денискины рассказы» has art_ids 559, 3178990, 3160573, 688105), and `cycles` is empty. Sibling detection for such collections cannot rely on `art_id`/`cycles` alone; use `/art-editions/{id}/contents` (`client._contents_stream(id)`): high overlap (>85–100% containment) of component story `work_id`s proves they are the same collection. Sibling editions are then consolidated via `curation.jsonc` `merges: [...]`.
  - `search?query=…` returns type `art_works` items carrying the work id directly in `instance.id` — the fallback for editions whose contents list is empty.
- Pagination: follow `payload.pagination.next_page` until null. A full 890-quote export takes seconds.
- `users/<id>/collections*` answers 404/405 and there is no user-shelves GET under that name — shelves live under `/arts/<plural>`, never probe `/books|/bookshelf|/collections` first. OpenAPI/docs are disabled.
- All matching between books/quotes/reviews must go through the NUMERIC edition id: HTML shelf URLs are slugless (`/book/1017962035`), API URLs carry slugs. Never match by URL string.

## 2. Shelves via SSR (fallback / debugging only)

Superseded by the `/arts/*` API above; reach for SSR only when the API is down or a specific SSR-only field is needed.

- Shelf pages are PUBLIC and server-rendered: `https://www.livelib.ru/users/<id>/books/<slug>?page=N`. Two live-verified bugs make them unusable as the primary channel: the OLD site ignores `?page` (every page returns page 1), and the NEW site's SSR list caps below its own counter (delivered 343 of 370 read records — the missing rows are simply not rendered, anonymously and to the API alike). Never trust an SSR walk count without comparing it to the API `counters.all`.
- Do NOT parse visual cards or markdown. The SSR HTML embeds structured collection records as Next.js flight chunks (`self.__next_f.push([1,"..."])`): per record — collection id (usable later for write ops), edition id, title, authors, user rating 0–10 (`my_mark` = rating/2 rounded, None when 0), read_date. Extract them as follows: json.loads each flight string, then `JSONDecoder.raw_decode` from each `{"id":N,"art_edition":{` occurrence — regex-splitting the payload breaks on titles containing quotes.
- Pagination: page N returns pages 1..N concatenated; walk pages until a page adds no NEW edition ids, then stop. The early "want ignores ?page, first 20 only" limit did NOT hold in later runs (45 want books walked) — re-verify against a live count before trusting a pagination cap.
- The shelf URL slug and the status NAME are two different livelib constants that
  do not match for one shelf: url `/books/want`, status `wish`. Never "unify" one
  onto the other (renaming the shelf key to the status name turns every page into
  0 records and silently empties the whole wish shelf out of the export — always
  rebuild and compare the status distribution after touching SHELF_STATUS).
- When a book sits on several shelves keep the higher-priority status (read > reading > unread > wish) — implement this in the shelf-walk.
- Cards show a generic «Хочу прочитать» button on every shelf — a UI label, not the book's status. The shelf URL defines status; never re-derive it from card labels.

## 3. Write operations

The quote write contract (POST/DELETE, visibility enum `public|friends|private`,
headless `llsid`-only auth) is VERIFIED and lives in references/api-contract.md —
the client implements it (`add_quote`/`delete_quote`). Reviews write contracts
are not sniffed yet.

Collection write contracts, verified against the active `advokatb/livelib.koplugin`
— documented for completeness, UNUSED by the runtime (do not reverse-engineer
from scratch if ever needed):

- `POST /trisolaris/api/users/me/collections` `{"art_edition_id":N,"art_status":"wants"}` → 201 with `user_collection_art_id`; some builds accept only `wants` on create — then PATCH to the target status.
- `PATCH .../collections/{id}` `{"art_status":"...","notes":null}` (+ a `read_date` object when marking reads) → 204; `PATCH .../collections/{id}/rating` `{"rating":0-10}` → 204; `DELETE .../collections/{id}` → 204 (404 = already gone, treat as success).
- `art_status` values: `wants | readings | reads | unreads` (plural — distinct from the note vocabulary).
- `GET /trisolaris/api/search?query=&limit=` works anonymously — use `type=="art_editions"` items only; watch the URL-encoding. There is no GET for user collections (write-only, 405 on GET).
- Auth = browser session cookies (`LiveLibId`, `llsid`) as one Cookie header + `Origin`/`Referer: https://beta.livelib.ru/`; rotate `__ddg*` cookies from every response's Set-Cookie. 401/403 or a login-form body means expired session.
- Login contract (from the beta SPA JS chunks): `POST https://beta.livelib.ru/api/auth/sessions` `{identity_token, password, remember:true}` — password travels in the JSON body, but `identity_token` is minted by the SPA, so headless password-only login needs a browser context; cookie harvesting from a browser_vault login is the working route. 403 responses carry a `Retry-After` header (rate limiting) — honor it.

## 4. Browser session (last resort)

- Needed only when the cookie-free channels fail. Login via browser_vault, labels `livelib` / `livelib-beta` — the login form lives on beta.livelib.ru; vault records are origin-bound.
- Antifrog: more than ~5 pages per 10 minutes returns empty shells. One page per call with ~110 s pauses; a page that comes back as an empty shell needs a cooldown retry, not a different URL.

## Canonical short URLs

- Quote: `https://www.livelib.ru/quote/<id>` — redirect appends the slug; store and emit short.
- Book: `https://www.livelib.ru/book/<id>` — same rule, used in note frontmatter `livelib_book`.
- Review: `https://www.livelib.ru/review/<id>` — same rule, used in frontmatter `livelib_review`.
- `/work/<id>` URLs may stay in TEXT chapters as the user writes them; the short-form contract applies to generated note attributes and the p3 projection.
