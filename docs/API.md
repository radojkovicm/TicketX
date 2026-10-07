# TicketX API

A small JSON API for scripts and AI agents (Claude Code, Codex). Authentication is a bearer token; the browser session cookie is **not** accepted. The API is disabled in `DEMO_MODE`.

```text
Authorization: Bearer tx_...
```

## Tokens

Tokens are created on the server and shown once; only their SHA-256 hash is stored.

```bash
python manage_api_tokens.py create --username milos --name claude-code   # write access
python manage_api_tokens.py create --username milos --name viewer --scope read
python manage_api_tokens.py list
python manage_api_tokens.py revoke 3
```

- A token acts as the user it was created for (admins see all tickets; other users only their own and non-private ones).
- The token **name** is the actor label. Every comment written through the API starts with `[name]`, and the ticket activity log records it, so the history shows who did what (`claude-code`, `codex`, ...).
- Use one token per tool. `read` tokens cannot write.
- Over 30 failed authentications per minute (all sources) returns 429.

## Endpoints (`/api/v1`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | check the token, shows user, actor and scope |
| GET | `/projects` | projects (TicketX categories) with open ticket counts |
| GET | `/tickets?project=&status=&q=` | list; `status` is `open` (default), `all` or one status |
| POST | `/tickets` | create: `project`, `title`, `description`, `priority`, `due_date`, `is_private` |
| GET | `/tickets/<id>` | one ticket with comments and activity |
| PATCH | `/tickets/<id>` | change `status`, `priority`, `title`, `description`, `due_date`, `project`, `is_private` |
| POST | `/tickets/<id>/comments` | add `{"comment": "..."}` |
| GET | `/context.md?project=&status=&comments=N` | **one Markdown file** with the current state (see below) |

Tickets created through the API are **assigned to the token's user** and start as `new`.

**Duplicate protection.** `POST /tickets` compares the new title with every visible open ticket and with tickets closed in the last 14 days (case, diacritics and punctuation are ignored; word order and sub-titles are tolerated). On a likely duplicate it answers `409` with the candidates (`id`, `title`, `status`, `project`, `similarity`) and creates nothing. Send `"force": true` only when it really is a different task. The check runs on the server, so it works no matter which chat or tool is creating the ticket. `GET /tickets?q=a+b` finds tickets containing **all** words in title or description.

**Recording past work.** `POST /tickets` accepts `created_at` and `closed_at` (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM`; a date alone means 12:00; future dates are rejected) and comments accept `created_at`. `closed_at` also sets status `closed`. The activity log marks such tickets as `backdated`. CLI: `ticketx add --created 2026-09-23 --closed 2026-09-24 ...` and `ticketx comment ID "..." --date 2026-09-23`.

A *project* is a TicketX category; a project that does not exist yet is created on first use. Statuses: `new`, `assigned`, `in_progress`, `awaiting_confirmation`, `closed`. Priorities: `low`, `medium`, `high`. Dates: `YYYY-MM-DD`.

There is deliberately no delete endpoint, and API actions do not send email notifications.

## `context.md`

Returns open tickets grouped by project (high priority first), with description, due date and the last N comments (default 3, max 20). This is what an agent reads at the start of work.

## Command line client

`tools/ticketx` is a single-file Python 3.8+ client without dependencies.

```bash
ticketx context --project GetSuper      # read the state
ticketx add --project GetSuper --title "Fix login" --desc "..." --priority high --due 2026-10-20
                                        # exit code 3 + list of similar tickets instead of a duplicate; --force overrides
ticketx comment 12 "Deployed to staging, next: smoke test"
ticketx status 12 in_progress
ticketx find backup vps                 # always before creating: open AND closed tickets
ticketx list --status all -q login
ticketx show 12
```

Configuration in `~/.config/ticketx/config.json` (mode 600):

```json
{"url": "https://ticketx.example.ts.net", "tokens": {"claude-code": "tx_...", "codex": "tx_..."}}
```

Select the token with `--as NAME` or `TICKETX_AS`. `TICKETX_URL` + `TICKETX_TOKEN` work as well.

## Agent integration

`integrations/claude-code/SKILL.md` and `integrations/codex/AGENTS-snippet.md` teach the agents the "add it to TicketX" workflow: read `ticketx context`, create or update the ticket, then add a comment with what was done and what comes next.
