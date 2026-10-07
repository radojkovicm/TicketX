#!/usr/bin/env python3
"""Server-side ticket maintenance. Deliberately NOT part of the HTTP API.

  python manage_tickets.py list   --project P
  python manage_tickets.py delete --project P [--ids 12,13] [--drop-project] [--yes]

`delete` is a dry run unless --yes is given. With --yes it first writes a
consistent copy of the database to <database folder>/backups/pre-delete-*.db,
then deletes the tickets (comments, activity, watchers and attachment records
go with them; attachment files inside the upload folder are removed too).
Reads DB_PATH and UPLOAD_FOLDER from .env.
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from database import Database  # noqa: E402


def select_tickets(conn, project, ids):
    sql = ("SELECT t.id, t.status, t.title, c.name FROM tickets t "
           "LEFT JOIN categories c ON c.id = t.category_id WHERE 1=1")
    params = []
    if project:
        sql += " AND lower(c.name) = lower(?)"
        params.append(project)
    if ids:
        sql += " AND t.id IN (%s)" % ",".join("?" * len(ids))
        params += ids
    return conn.execute(sql + " ORDER BY t.id", params).fetchall()


def backup_database(db_path):
    folder = Path(db_path).resolve().parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("pre-delete-%s.db" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(target)
    with dest:
        source.backup(dest)
    dest.close()
    source.close()
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "delete"):
        p = sub.add_parser(name)
        p.add_argument("--project")
        p.add_argument("--ids", help="comma separated ticket ids")
        if name == "delete":
            p.add_argument("--drop-project", action="store_true", help="also remove the project if it ends up empty")
            p.add_argument("--yes", action="store_true", help="really delete (default is a dry run)")
    args = parser.parse_args()

    ids = [int(x) for x in args.ids.split(",")] if args.ids else []
    if not args.project and not ids:
        sys.exit("Give --project and/or --ids.")

    db = Database()
    conn = db.get_connection()
    tickets = select_tickets(conn, args.project, ids)
    for t in tickets:
        print("#%-4d %-22s %-12s %s" % (t[0], t[1], t[3] or "-", t[2][:80]))
    print("%d ticket(s) match." % len(tickets))
    if args.command == "list" or not tickets:
        return
    if not args.yes:
        print("Dry run: nothing deleted. Repeat with --yes to delete.")
        return

    backup = backup_database(db.db_path)
    print("Database backup written to", backup)
    ticket_ids = [t[0] for t in tickets]
    marks = ",".join("?" * len(ticket_ids))
    upload_root = Path(os.getenv("UPLOAD_FOLDER", "uploads")).resolve()
    files = [r[0] for r in conn.execute(
        "SELECT file_path FROM attachments WHERE ticket_id IN (%s)" % marks, ticket_ids)]
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("DELETE FROM tickets WHERE id IN (%s)" % marks, ticket_ids)
    conn.commit()
    removed = 0
    for path in files:
        p = Path(path).resolve()
        if upload_root in p.parents and p.is_file():
            p.unlink()
            removed += 1
    if args.drop_project and args.project:
        left = conn.execute(
            "SELECT COUNT(*) FROM tickets t JOIN categories c ON c.id = t.category_id WHERE lower(c.name)=lower(?)",
            (args.project,)).fetchone()[0]
        if left == 0:
            conn.execute("DELETE FROM categories WHERE lower(name) = lower(?)", (args.project,))
            conn.commit()
            print("Project", args.project, "removed.")
    print("Deleted %d ticket(s), %d attachment file(s)." % (len(ticket_ids), removed))


if __name__ == "__main__":
    main()
