# Contributing to TicketX

## Development setup

1. Create a Python 3.11+ virtual environment.
2. Install `requirements.txt`.
3. Copy `.env.example` to `.env` and use local-only values.
4. Run `python -m unittest discover -s tests -v` before opening a pull request.

## Pull requests

- Keep changes focused and explain the user-visible behavior.
- Add tests for authorization, workflow or data-handling changes.
- Never commit `.env`, databases, logs, uploads, backups or real user data.
- Keep code, comments, commit messages and documentation in English.
- Preserve backward compatibility for existing SQLite databases unless a migration is included.

## Security changes

Treat route decorators as authentication only. Every route that reads or changes a ticket must also verify authorization against the current database state. File paths must be resolved against an approved root before use.
