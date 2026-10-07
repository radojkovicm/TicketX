## TicketX (personal tracker)

When the user says "dodaj na ticketx", "ticketx", or asks what is open or in what state a project is, use the TicketX CLI. Always run it as `TICKETX_AS=codex ~/.local/bin/ticketx ...` (the token name is the actor label in comments). It needs Tailscale; if the server is unreachable, say so.

1. Look first: `ticketx context --project <P> --comments 2 2>&1 | head -c 6000` and `ticketx find <keywords> 2>&1 | head -c 2000` (includes closed tickets). Another chat may already have created or finished the task.
2. Matching ticket: `ticketx comment <id> "..."`. Otherwise `ticketx add --project <P> --title "..." --desc "..." [--priority high] [--due YYYY-MM-DD]`. Projects: GetSuper, Homelab, TicketX, or a new name the user gives. The server refuses likely duplicates (exit code 3, prints the existing tickets): then comment on the right ticket instead; `--force` only for a really different task. Tickets are assigned to the user automatically.
3. Comment with what was done, decided and what is next. A few lines, no transcripts, no secrets.
4. Status: `in_progress` when work starts, `awaiting_confirmation` when done, never `closed` without the user's confirmation.
5. Report the ticket number and what you wrote. GitHub stays the place for code issues and PRs; link them from the ticket.
6. Past questions ("kada smo uradili X", "sta smo radili"): do not guess. `ticketx timeline [--project P] [--since YYYY-MM-DD] 2>&1 | head -c 6000`, `ticketx find <words>`, `ticketx show <id>`. Answer with the date and ticket number; if there is no record, say so.

### TicketX import mode (only when the user explicitly asks, e.g. "uploaduj sve na ticketx")

Never start this on your own. When asked:
1. See what exists first: `ticketx projects` and `ticketx timeline 2>&1 | head -c 8000`.
2. Collect candidates only from what you already know: this conversation, your memory, and the notes/AGENTS.md/git history of the projects you work in. Do not scan the disk. Show the user a compact list (project, title, status, real dates, source) and wait for approval before writing anything.
3. After approval, for every item run `ticketx find <words>` first. Finished work: `ticketx add --project P --title "..." --desc "..." --created YYYY-MM-DD --closed YYYY-MM-DD`. Open or ongoing work: no `--closed`, then set `ticketx status`. Use the real date; if only the month is known use the first of that month and write "tacan dan nepoznat" in the description. Never invent dates.
4. Description: 2-5 factual lines plus the source (repo, commit, file). No secrets, no transcripts.
5. Reuse existing project names from `ticketx projects` (GetSuper, Homelab, VPS, TicketX, Pupin). Propose a new project name to the user before creating one.
6. Assignment is automatic (every ticket is assigned to the user); do not try to assign anyone else.
7. Exit code 3 = likely duplicate: comment on the existing ticket instead of using `--force`, unless the task is really different.
8. End with a summary: created N, commented M, skipped K (with reasons).
9. Work for the employer (Intersocks, Business Central, Jira) does NOT belong in TicketX: the user keeps it in Jira. Never add or import such items; skip them and tell the user.
