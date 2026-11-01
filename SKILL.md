---
name: livelib
version: 2.4.0
author: shared-goals
license: MIT
tags:
  - shared-goals
  - livelib
  - books
  - mcp
title: LiveLib access — MCP server + skill
description: LiveLib via MCP — reads, quote and review writes, install and contracts.
---

# livelib-mcp — LiveLib access (MCP server + skill)

One repo, two roles: a **skill** for working with LiveLib (this document) and
the **MCP server package** in `scripts/` that exposes LiveLib as tools.
The Russian README.md is the repo's front page for readers — the project's
"why" lives there; this skill is the operational how-to.

## When to Use

Any LiveLib (livelib.ru) work: login, exporting books/quotes/reviews, adding
or deleting quotes, posting/updating reviews, running or extending the MCP
server in `scripts/`.

## Repo layout

- `SKILL.md` + `references/` — the skill (also the repo front page).
- `scripts/` — the `livelib-mcp` uv package: `TrisolarisClient`
  (`src/livelib_mcp/client.py`), thin stdio MCP server (`server.py`), tests
  with REAL recorded fixtures (network-free). Run pytest/uv from `scripts/`
  with `UV_PROJECT_ENVIRONMENT` pointed at a cache path — a `.venv` created
  inside the skill tree breaks the outer skills scan.
- Client design: pure `httpx` + `mcp`, zero browser deps. Reads are
  100% API; writes are plain HTTP with the session cookie — a browser only
  MINTS the cookie. No proxy logic: the transport runs with
  `trust_env=False` — LiveLib needs no proxy routing, and an operator's
  ambient proxy env (e.g. an IDN/wildcard entry in no_proxy) crashes httpx
  in the constructor before any request. If a proxy is ever genuinely
  needed, add an explicit `LIVELIB_PROXY` env (YAGNI now).

## MCP server (16 tools)

Run: `uv run --project <this-repo>/scripts livelib-mcp` (stdio). Example
Hermes wiring (`mcp_servers` in config.yaml; secrets stay out of config.yaml
and git):

```yaml
mcp_servers:
  livelib:
    command: /usr/local/bin/uv
    args: ["run", "--project", "<this-repo>/scripts", "livelib-mcp"]
    env:
      LIVELIB_LLSID: ${LIVELIB_LLSID}
      UV_PROJECT_ENVIRONMENT: <cache-path>/livelib-venv
```

- Anonymous reads: `get_edition`, `get_shelf(uid, shelf)`, `get_user_marks(uid,
  edition_id)` (which shelves hold ONE book + record ids),
  `get_edition_quotes`, `get_edition_reviews`, `get_quotes(uid)`,
  `get_reviews(uid)`, `search_editions(query)`.
- Authenticated writes (need the `LIVELIB_LLSID` env — the raw `llsid` session
  cookie value): quotes `add_quote(edition_id, text, visibility="public")` /
  `delete_quote(user_quote_id)`; reviews `add_review(edition_id, title, text,
  rating)` / `update_review(review_id, ...)` / `delete_review(review_id)`;
  shelves `add_to_shelf(edition_id, art_status)` /
  `remove_from_shelf(collection_id)` /
  `update_shelf_status(collection_id, art_status)` — the MOVE between shelves
  (PATCH), preferred over delete+add because record id, rating and read_date
  survive.
  Review rating scale is 0–10 (10 = five stars on the site); paragraphs of a
  review become separate editor blocks (blank-line split). Shelf `art_status`
  uses the plural vocabulary `wants|readings|reads|unreads`; `add_to_shelf`
  returns the collection record id that `remove_from_shelf` needs.
- **MCP-only runtime rule (user directive, 2026-10-31)**: in the agent runtime
  BOTH reads and writes go through the MCP tools ONLY — curl/urllib are
  contract-sniffing tools used once when discovering a NEW endpoint's shape
  (e.g. recording a fixture), never the runtime channel. If an MCP tool is
  missing for an endpoint, add the tool first — do not route around it. Direct
  API calls live only in pipeline code (text-forge), which owns its own
  client.
- Write contract, visibility enum (`public|friends|private`), response shapes,
  the two-level quote model and the old-backend nuance:
  references/api-contract.md.

### Adding a tool to the server (workflow)

1. Write the client method first, network-free tests against a REAL recorded
   fixture — sniff the live endpoint ONCE with curl to record the envelope
   when the contract is new, then never again (see the MCP-only runtime rule).
2. Add the thin server tool; grep `tests/test_server.py` for `TOOL_NAMES` and
   the `len(tools.tools)` pin BEFORE running pytest — both are hard pins that
   fail on every new tool, and `BOOL_TOOLS` must list any bool-returning tool
   or the wire-decode helper misreads its result.
3. Update this SKILL.md tool list and the relevant contract section in the
   same change — docs and code ship atomically.
4. A restart of the Hermes gateway is required before the new tool appears as
   `mcp__livelib__*` — and restarting the gateway kills the running session.
   Finish all other work first, then ask the user to restart (or accept the
   session loss); verify presence with `tool_search` afterwards.

### Diagnosing a 'lost stdio subprocess' error

When a Hermes MCP call fails with "server lost its stdio subprocess", do NOT
conclude the server is broken and do not switch channels. Probe manually
first: pipe `initialize` + `tools/list` (or the failing `tools/call`) as
newline-delimited JSON-RPC into `uv run --project <scripts> livelib-mcp` with
`LIVELIB_LLSID` and `UV_PROJECT_ENVIRONMENT` exported, stderr to a file. A
healthy server answers both within seconds; if the manual probe succeeds, the
failure was a one-off spawn glitch — the next gateway call just works. Measure
startup before blaming `connect_timeout`: a warm venv starts in ~2 s, so a
brief hang is the first-call uv sync, not a config error.

## Login (validated)

1. Open `https://beta.livelib.ru/login`, click `Войти` → `Войти по почте или
   логину`; fill the `login` input with your LiveLib username.
2. Submit the password. In an agent workflow, prefer a credential vault that
   types the password without it passing through chat or logs (e.g. Hermes
   `browser_vault`); vault records are origin-bound — the login form lives on
   beta.livelib.ru.
3. Success = redirect to `beta.livelib.ru/users/me`; authenticated probe:
   page-context fetch of `/users/me/quotes?limit=1` → 200.
4. Cookie minting for headless writes: the `llsid` cookie is httpOnly —
   capture the browser cookie jar via CDP `Network.getCookies` (urls:
   beta.livelib.ru + beta.api.livelib.ru), extract `llsid`, and store the raw
   value as `LIVELIB_LLSID` in the agent's secret env (e.g. `~/.hermes/.env`).
   Probe the MINIMAL credential set first — the single llsid cookie is
   verified sufficient (GET 200, POST 201, DELETE 204).
- Never accept LiveLib credentials in chat or store them in memory/git.

## Data access — priority order: API → anonymous SSR → browser

- **Trisolaris JSON API is the primary channel** (anonymous, no cookies, no
  antifraud pauses): quotes/reviews walks, shelf lists via
  `/users/{uid}/arts/{reads|readings|unreads|wants}`, edition/work cards,
  `/contents` for edition→work, `search` fallback. Details and response
  shapes: references/livelib-data-access.md, references/shelf-api.md.
- SSR flight-chunk parsing is debug/fallback only (the old site ignores
  `?page`; beta SSR caps below the counter). The browser is the last resort
  (login/cookie minting, SPA-only views); antifraud throttles ~5 pages/10
  min; never sleep inside a long browser call.
- Match everything by NUMERIC edition id, never by URL string.

## Standing rules

- Shelf moves go through `update_shelf_status` (PATCH) — NEVER delete+add:
  the collection record id, rating and read_date survive the move. delete+add
  is only for creating/removing a record outright.
- To move a book you must first find its current collection record id (there
  is no GET for user collections): walk the anonymous shelves
  `/users/{uid}/arts/{reads|readings|unreads|wants}` and match by edition id —
  the record `id` on each mark IS the collection id for update/remove.
- User-set shelf semantics: `unreads` = «не дочитал» (partially read), not
  «to read» — «to read» is `wants`. Never normalize these onto each other.
- Quote text is passed through byte-exact — never strip or normalize.
- Writes (add/delete quote, review posting, shelf changes) need the exact text
  confirmed by the user before submitting.
- Quote dedupe by text is a first-class API behavior: a new quote may attach
  to another member's canonical quote — the card then shows the original
  adder. Normal state; never alter quote text to force authorship
  (references/api-contract.md).
- TDD: tests are network-free against real recorded fixtures.

## Roadmap

1. MCP reads + server: DONE. 2. Write tools — quotes AND reviews
   (`add_quote`/`delete_quote`, `add_review`/`update_review`/`delete_review`):
   DONE. 3. Shelf/collection writes (`add_to_shelf`/`remove_from_shelf`/
   `update_shelf_status`): DONE. 4. Optional: cross-post reviews from
   Obsidian; community reading (friends' shelves, new reviews on a book).

- README conventions (user-set): the README serves two audiences — humans
  get Russian prose with NO tooling names (readers need an AI agent only;
  the agent installs uv etc.; Hermes is the default with a docs link),
  agents get a strict imperative English section at the end. Section
  headers carry no quality labels («честно» etc.) — the content carries
  them; honesty is the default, not a feature.

Cross-repo consumers (e.g. export pipelines) should depend on this package
via its git URL, never a machine-local path — see the gitignored AGENTS.md
for the local linkage map.
