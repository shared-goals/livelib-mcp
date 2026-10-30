# Book platform alternatives (Oct 2026 research)

Decision context: replace or supplement LiveLib. Requirements: Russian book metadata, Russian community, MCP access, comfortable quote entry.

## No platform intersects all four

| Platform | RU community | RU metadata | MCP | Quotes | Verdict |
|---|---|---|---|---|---|
| LiveLib | ✅ dominant | ✅ | ❌ | ⚠️ legacy UX | keep for community |
| BookMix | ⚠️ dying | ✅ | ❌ | ⚠️ | worse than LiveLib |
| Hardcover | ❌ | partial (classic only) | ✅ several MCP servers (GraphQL API) | ❌ | good tracker, no quotes |
| StoryGraph | ❌ | ❌ | ❌ no public API | ✅ | out |
| Goodreads | ❌ | ❌ | API dead | weak | out |
| BookWyrm | ❌ | ❌ | no official MCP, self-hostable | ⚠️ | out |
| Readwise | n/a | ❌ no Russian books | ✅ official server (`https://mcp2.readwise.io/mcp`) | ✅ best-in-class | out (paid, RU-blind) |

## Chosen architecture

- Quotes during reading → **Obsidian vault** (a custom hotkey extension; agent access via obsidian skill/MCP).
- Community, advice, reviews → **LiveLib** (cross-post reviews from Obsidian via automation).
- Backup → export books + quotes to CSV/Markdown; quotes exist nowhere else.

## Export tooling

- No official export. Tools: `pplevar/livelib-backup` (books + quotes → CSV — the only one covering quotes), `chimildic/livelib-backup`, livelibexporter.com.
