#!/usr/bin/env python3
"""Manage TicketX API tokens on the server.

  python manage_api_tokens.py create --username milos --name claude-code [--scope read]
  python manage_api_tokens.py list
  python manage_api_tokens.py revoke <id>

`create` prints the token once; only its hash is stored. Reads DB_PATH from .env.
"""
import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from api import create_token, list_tokens, revoke_token  # noqa: E402
from database import Database  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--username", required=True)
    create.add_argument("--name", required=True, help="actor label, e.g. claude-code or codex")
    create.add_argument("--scope", default="write", choices=["read", "write"])
    sub.add_parser("list")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("token_id", type=int)
    args = parser.parse_args()

    if args.command == "create":
        conn = Database().get_connection()
        row = conn.execute("SELECT id FROM users WHERE username = ?", (args.username,)).fetchone()
        conn.close()
        if not row:
            sys.exit("Unknown user: " + args.username)
        print(create_token(row[0], args.name, args.scope))
    elif args.command == "list":
        for r in list_tokens():
            state = "revoked " + r[6] if r[6] else "active"
            print("#%d  user=%s  name=%s  scope=%s  created=%s  last_used=%s  %s" % (
                r[0], r[1], r[2], r[3], r[4], r[5] or "never", state))
    else:
        print("revoked" if revoke_token(args.token_id) else "not found / already revoked")


if __name__ == "__main__":
    main()
