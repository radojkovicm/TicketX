## TicketX (personal tracker)

When the user says "dodaj na ticketx", "ticketx", or asks what is open or in what state a project is, use the TicketX CLI. Always run it as `TICKETX_AS=codex ~/.local/bin/ticketx ...` (the token name is the actor label in comments). It needs Tailscale; if the server is unreachable, say so.

1. Look first: `ticketx context --project <P> --comments 2 2>&1 | head -c 6000`. Check for an existing ticket before creating one.
2. Matching ticket: `ticketx comment <id> "..."`. Otherwise `ticketx add --project <P> --title "..." --desc "..." [--priority high] [--due YYYY-MM-DD]`. Projects: GetSuper, Homelab, TicketX, or a new name the user gives.
3. Comment with what was done, decided and what is next. A few lines, no transcripts, no secrets.
4. Status: `in_progress` when work starts, `awaiting_confirmation` when done, never `closed` without the user's confirmation.
5. Report the ticket number and what you wrote. GitHub stays the place for code issues and PRs; link them from the ticket.
