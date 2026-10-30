---
name: livelib
version: 2.2.0
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

## MCP server (11 tools)

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

- Anonymous reads: `get_edition`, `get_shelf(uid, shelf)`,
  `get_edition_quotes`, `get_quotes(uid)`, `get_reviews(uid)`,
  `search_editions(query)`.
- Authenticated writes (need the `LIVELIB_LLSID` env — the raw `llsid` session
  cookie value): quotes `add_quote(edition_id, text, visibility="public")` /
  `delete_quote(user_quote_id)`; reviews `add_review(edition_id, title, text,
  rating)` / `update_review(review_id, ...)` / `delete_review(review_id)`.
  Review rating scale is 0–10 (10 = five stars on the site); paragraphs of a
  review become separate editor blocks (blank-line split).
- **MCP-only runtime rule (user directive)**: in the agent runtime, write
  operations go through the MCP tools ONLY — curl/urllib are contract-sniffing
  tools used once when discovering a NEW endpoint's shape, never the runtime
  channel. If an MCP tool is missing for an endpoint, add the tool first.
- Write contract, visibility enum (`public|friends|private`), response shapes,
  the two-level quote model and the old-backend nuance:
  references/api-contract.md.

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

- Quote text is passed through byte-exact — never strip or normalize.
- Writes (add/delete quote, review posting) need the exact text confirmed by
  the user before submitting.
- Quote dedupe by text is a first-class API behavior: a new quote may attach
  to another member's canonical quote — the card then shows the original
  adder. Normal state; never alter quote text to force authorship
  (references/api-contract.md).
- TDD: tests are network-free against real recorded fixtures.

## Roadmap

1. MCP reads + server: DONE. 2. Write tools — quotes AND reviews
   (`add_quote`/`delete_quote`, `add_review`/`update_review`/`delete_review`):
   DONE. 3. Collection write contracts documented in
   references/livelib-data-access.md (koplugin) — unused by the runtime.
   4. Optional: cross-post reviews from Obsidian; community reading
   (friends' shelves, new reviews on a book).

- README conventions (user-set): the README serves two audiences — humans
  get Russian prose with NO tooling names (readers need an AI agent only;
  the agent installs uv etc.; Hermes is the default with a docs link),
  agents get a strict imperative English section at the end. Section
  headers carry no quality labels («честно» etc.) — the content carries
  them; honesty is the default, not a feature.

Cross-repo consumers (e.g. export pipelines) should depend on this package
via its git URL, never a machine-local path — see the gitignored AGENTS.md
for the local linkage map.
