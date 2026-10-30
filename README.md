# livelib-mcp

**Живой доступ к LiveLib для ИИ-агентов** — MCP-сервер + скилл в одном репозитории.

Железная обложка сайта. Скрытая керамика API, живая и тёплая.

## Зачем это нужно

Сайты, к которым мы привыкли, стареют. LiveLib — не исключение: старый интерфейс,
навязчивая и низкокачественная реклама, за которую не отвечают владельцы сайта, UI, который сопротивляется вместо того, чтобы помогать.

При этом главная ценность LiveLib — **читатели**:
русскоязычное книжное сообщество со списками книг, цита и рецензий.

Этот проект делает простую вещь: **ИИ-агент говорит с LiveLib напрямую**,
через JSON API — мимо legacy-интерфейса и рекламы. Ты просто разговариваешь
со своим агентом («добавь цитату из главы», «что я читал в прошлом году»,
«опубликуй рецензию на эту книгу»), а он делает остальное.

Возможно, это один из ответов на вопрос, куда движется книжная онлайн-среда:
не миграция на другую платформу, а **живой API-слой поверх сообщества читателей**.
MCP-подписка на любимый сайт — потенциальное спасение книжного бизнеса: читатель
платит за чистый доступ, а не смотрит рекламу.

## Что умеет (11 MCP-инструментов)

**Чтение — анонимно, без всякой регистрации:**

| Инструмент | Что возвращает |
|---|---|
| `get_edition(id)` | карточка издания: название, авторы, ISBN, год, обложка |
| `get_shelf(uid, shelf)` | полка читателя (reads/readings/unreads/wants) с дедупликацией |
| `get_edition_quotes(id)` | цитаты издания |
| `get_quotes(uid)` | все цитаты пользователя |
| `get_reviews(uid)` | все рецензии пользователя |
| `search_editions(query)` | поиск книг |

**Запись — с авторизацией (твоя сессия):**

| Инструмент | Что делает |
|---|---|
| `add_quote(edition_id, text, visibility)` | добавляет цитату |
| `delete_quote(id)` | удаляет твою цитату |
| `add_review(edition_id, title, text, rating)` | публикует рецензию |
| `update_review(id, title, text, rating)` | обновляет рецензию (лайки и статистика сохраняются) |
| `delete_review(id)` | удаляет рецензию |

Важно: **текст цитат и рецензий проходит байт-в-байт** — агент не переписывает
твои слова. Оценка в рецензии по шкале 0–10 (10 = «пятёрка» на сайте).

## Как это работает (для новичков)

**MCP** (Model Context Protocol) — открытый стандарт, позволяющий ИИ-агенту
пользоваться внешними инструментами. Ты один раз описываешь сервер в конфиге —
и агент получает новые «руки»: умеет искать книги, добавлять цитаты, публиковать
рецензии. Всё общение идёт локально: агент запускает сервер как дочерний процесс
и говорит с ним по stdin/stdout. Никаких браузеров, никаких парсеров вёрстки —
только чистый JSON API LiveLib.

**Где живут данные.** Всё, что ты видишь на LiveLib (полки, цитаты, рецензии),
на самом деле лежит в JSON API (`beta.api.livelib.ru/trisolaris`). Сайт — лишь
одна из его витрин. MCP-сервер обращается к API напрямую: быстрее, стабильнее
и без рекламы.

**Почему это законно и безопасно.** MCP делает ровно то, что делаешь ты в
браузере: читает открытые данные и пишет от **твоего имени** с **твоей сессией**.
Креды не проходят через чат и не хранятся в коде — только в локальном
`~/.hermes/.env`. Агент публикует текст только после того, как ты его подтвердил.

## Установка

Вам нужен только **личный AI-агент** — он сам установит всё остальное.
Проще всего это сделать в [Hermes](https://hermes-agent.nousresearch.com/docs):
скопируйте фразу из раздела ниже и отправьте её своему агенту.

Технический читатель найдёт пошаговую машинную инструкцию в конце файла
(«For agents — step by step»).

### Для людей — попроси своего агента

Самый простой путь — **логин и пароль от LiveLib в credential vault твоего
агента** (в Hermes это `browser_vault`; в других агентах есть аналоги).
Пароль при этом никогда не проходит через чат и не хранится в памяти агента —
vault сам вводит его в маскированное поле на странице входа.

Скопируй агенту (Hermes, Claude, pi.dev — кому доверяешь свою LiveLib-сессию):

> Install the livelib-mcp MCP server from github.com/shared-goals/livelib-mcp.
> My LiveLib credentials are in your credential vault (label `livelib`).
> Follow the agent instructions in that repo's README: install and verify,
> log in via the vault, extract the `llsid` cookie, store it in secrets,
> wire the server. Then list the tools you got and smoke-test a search.

Дальше агент сделает сам: установит и проверит сервер, войдёт на сайт через
vault, достанет из живой сессии куку `llsid` (она httpOnly — руками её из
браузера не достать, агент умеет), спрячет её в секреты и пропишет конфиг.
От тебя — только подтверждать действия, когда агент спросит.

Читатели, у которых нет своего агента: чтение (полки, цитаты, рецензии,
поиск) работает и без всякой установки — это уже умеет любой агент,
обращающийся к открытому API LiveLib. Сервер нужен для записи от твоего имени.



### For agents — step by step

1. Clone and verify:
```bash
git clone https://github.com/shared-goals/livelib-mcp.git ~/livelib-mcp
cd ~/livelib-mcp/scripts && uv sync && uv run pytest -q   # all green
```

2. Obtain the user's `llsid` cookie (writes require the user's session).
   Primary path — **credential vault**: if the owner stored LiveLib
   credentials there (Hermes `browser_vault`; items are origin-bound to
   `beta.livelib.ru`), log in through it: open
   `https://beta.livelib.ru/login`, click «Войти» → «Войти по почте или
   логину», fill the login, submit the password via `vault fill` with the
   stored handle (the password never passes through chat). Success =
   redirect to `/users/me`.

   Alternatives:
   - a managed browser without a vault — ask the owner to type the password
     in the open login window, then continue (redirect → cookie);
   - no browser at all — ask the owner to extract the cookie manually
     (F12 → Application → Cookies → www.livelib.ru → llsid; httpOnly) and
     hand it over through a safe channel, never a shared chat.

   Cookie extraction after a successful login: grab the jar via CDP
   `Network.getCookies` (urls: beta.livelib.ru + beta.api.livelib.ru) and
   pick `llsid` (httpOnly, domain `.livelib.ru`).

3. Store it in the agent's secrets (Hermes: `~/.hermes/.env`; never in
   config.yaml and never in git):
```bash
echo "LIVELIB_LLSID=<value>" >> ~/.hermes/.env
```

4. Wire the server (Hermes: `~/.hermes/config.yaml`):
```yaml
mcp_servers:
  livelib:
    command: uv
    args: ["run", "--project", "<path>/livelib-mcp/scripts", "livelib-mcp"]
    env:
      LIVELIB_LLSID: ${LIVELIB_LLSID}
    connect_timeout: 15
    timeout: 60
```
If the MCP server places its venv inside the skill tree
(`UV_PROJECT_ENVIRONMENT` unset), the outer Hermes skills scan can break —
set `UV_PROJECT_ENVIRONMENT: <cache-path>/livelib-venv` in `env`.

5. Restart the agent and verify live: `tools/list` must show 11 tools;
   smoke — `get_edition(1004124394)` returns «Экономика всего…». Never
   submit a write without the owner confirming the exact text.

**Other harnesses** (Claude Code, pi.dev, OpenCode): the server is a plain
stdio-MCP; same command and env, per your harness's rules.

## Известные нюансы

- **Оценка в рецензии** — по шкале 0–10; «пятёрка» = 10.
- **Абзацы текста** разделяй пустой строкой — каждый станет отдельным блоком
  (один блок схлопывает переносы строк).
- **Рецензия, отредактированная в старом веб-редакторе**, закрывается для API
  (`409 NotEditableByNewEditor`) — правь её на сайте. Пока правок там не было,
  `update_review` работает.
- **Цитаты дедуплицируются по тексту**: если такой текст уже кто-то добавил,
  твоя запись привяжется к его канонической цитате, и страница цитаты покажет
  первоавтора. Это нормальное поведение LiveLib.
- **Старый сайт и мобильное приложение** могут не показывать свежие
  API-созданные записи сразу — beta-слой (web + API) консистентен.
- **Кука `llsid` живёт долго, но не вечно** — при 401 просто обнови её в `.env`.

## Проект Shared Goals

Это часть [Shared Goals](https://github.com/shared-goals) — открытой платформы
совместных целей, где ИИ-агенты помогают людям договариваться, действовать
вместе и не тонуть в информационном шуме. LiveLib для нас — сообщество читателей;
этот проект — мост между ним и агентами.

## License

MIT — [shared-goals/livelib-mcp](https://github.com/shared-goals/livelib-mcp)
