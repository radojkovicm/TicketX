---
name: ticketx
description: Use when the user says "dodaj na ticketx", "ticketx", "upiši u ticketx", "azuriraj tiket", asks what is open / what the status of a project is, or at the start and end of work on a project that is tracked in TicketX (GetSuper, homelab, TicketX itself). Reads, creates, comments on and updates tickets through the TicketX API.
---

# TicketX (personal work tracker)

TicketX is the user's tracker for everything they work on, including things that are not on GitHub. "Dodaj na ticketx" means: look at the current state, then create or update a ticket. GitHub stays the place for code issues and PRs; link them from the ticket instead of copying them.

CLI: `~/.local/bin/ticketx` (always call it as `TICKETX_AS=claude-code ~/.local/bin/ticketx ...`; the token name is the actor label shown in comments). It needs Tailscale to be connected; if it cannot reach the server, say so instead of retrying.

## Workflow

1. **Look first.** `ticketx context --project <P> --comments 3` (or without `--project` for everything), and `ticketx find <keywords>` for the topic (it also shows closed tickets). Another chat may already have created or finished the task: never create a ticket before checking.
2. **Add:** if a matching ticket exists, add a comment; otherwise `ticketx add --project <P> --title "..." --desc "..." [--priority high] [--due YYYY-MM-DD]`. Projects: GetSuper, Homelab, TicketX, or a new name when the user names one. The server refuses likely duplicates (exit code 3, it prints the existing tickets): then comment on the right ticket instead. Use `--force` only when the task is really different. All tickets are assigned to the user automatically.
3. **Comment** with what was done, what was decided, and what comes next (a few lines, no transcripts, never secrets): `ticketx comment <id> "..."`.
4. **Status** when it changes: `in_progress` when work starts, `awaiting_confirmation` when finished and the user must check, `closed` only when the user confirms.
5. Finish by telling the user the ticket number and what was written.

## Pitanja o prošlosti

Kad korisnik pita "kada smo uradili X" / "šta smo radili": ne pogađaj, pogledaj TicketX. `ticketx timeline [--project P] [--since YYYY-MM-DD]` daje sve tikete po datumu (početak -> kraj), `ticketx find <reči>` traži po temi, `ticketx show <id>` daje opis i komentare. Istorijski tiketi imaju prave datume (backdated) i izvor u opisu (commit, decisions.md). Odgovori sa datumom i brojem tiketa; ako zapisa nema, reci da ga nema umesto da nagađaš.

## Rules

- Posao (Intersocks, Business Central, Jira) NE ide u TicketX: to korisnik vodi u Jiri. Ne upisuj ni ne uvozi te stavke; ako ih naidjes, preskoci i javi korisniku.
- Do not write secrets, passwords, tokens or personal data into tickets or comments.
- Do not close a ticket on your own; use `awaiting_confirmation`.
- Keep descriptions factual; use Serbian or English like the user does.
- For long text use stdin: `ticketx comment <id> -` and pipe the text in.
- Suggestions and proposals from a session go in as a comment on the relevant ticket (prefix "Predlog:") so the user can accept or reject them there.
