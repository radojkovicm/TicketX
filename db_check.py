# db_check.py
import sqlite3
import argparse
import textwrap

def q(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    return rows, [d[0] for d in cur.description] if cur.description else []

def pretty_print(title, rows, cols=None):
    print("\n" + "="*40)
    print(title)
    print("-"*40)
    if not rows:
        print("(no rows)")
        return
    if cols:
        print("\t".join(cols))
    for r in rows:
        print(r)

def main():
    p = argparse.ArgumentParser(description="DB checks for ticket notifications")
    p.add_argument("db", help="Path to sqlite database file")
    p.add_argument("--ticket", type=int, required=True, help="Ticket id to inspect")
    p.add_argument("--assigned", type=int, required=False, help="Assigned user id (optional)")
    p.add_argument("--other", help="Optional other DB path to compare emails (optional)")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = None

    ticket_id = args.ticket

    rows, cols = q(conn, "SELECT id, title, assigned_to, typeof(assigned_to) AS assigned_type FROM tickets WHERE id = ?", (ticket_id,))
    pretty_print(f"Ticket #{ticket_id} (tickets table)", rows, cols)

    # If assigned value present, derive assigned id
    assigned_val = None
    if rows:
        assigned_val = rows[0][2]

    # Show watchers
    rows, cols = q(conn, "SELECT tw.user_id, u.full_name, u.email FROM ticket_watchers tw LEFT JOIN users u ON tw.user_id = u.id WHERE tw.ticket_id = ?", (ticket_id,))
    pretty_print(f"Watchers for ticket #{ticket_id}", rows, cols)

    # Show muted users
    rows, cols = q(conn, "SELECT * FROM ticket_muted_users WHERE ticket_id = ?", (ticket_id,))
    pretty_print(f"Muted entries for ticket #{ticket_id}", rows, cols)

    # Show assigned user details if we know assigned id (or if --assigned provided)
    check_user_id = args.assigned if args.assigned is not None else assigned_val
    if check_user_id is not None:
        try:
            # try both numeric and string lookups
            rows, cols = q(conn, "SELECT * FROM users WHERE id = ?", (check_user_id,))
            if not rows:
                rows, cols = q(conn, "SELECT * FROM users WHERE id = ?", (str(check_user_id),))
            pretty_print(f"User row for assigned id = {check_user_id}", rows, cols)
            # Show email trimmed / typed
            rows, cols = q(conn, "SELECT id, full_name, email, '|' || email || '|' as quoted_email, LENGTH(TRIM(COALESCE(email, ''))) as email_len, typeof(email) as email_type FROM users WHERE id = ?", (check_user_id,))
            pretty_print("Selected user (email diagnostics)", rows, cols)
        except Exception as e:
            print("Error fetching user info:", e)
    else:
        print("\nNo assigned user id found for this ticket (assigned_to is NULL or not set).")

    # Show users table structure
    rows = q(conn, "PRAGMA table_info(users);")[0]
    print("\n" + "="*40)
    print("users table schema (PRAGMA table_info(users))")
    print("-"*40)
    for r in rows:
        print(r)

    # List tables that might be relevant
    rows, cols = q(conn, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    pretty_print("All tables in DB", rows, cols)

    # Optionally compare with another DB if provided
    if args.other:
        try:
            oconn = sqlite3.connect(args.other)
            print("\nComparing emails with other DB (if other has users table)...")
            rows, cols = q(conn, "SELECT id, TRIM(COALESCE(email,'')) as email FROM users ORDER BY id;")
            rows2, cols2 = q(oconn, "SELECT id, TRIM(COALESCE(email,'')) as email FROM users ORDER BY id;")
            print("Sample from problem DB (first 10):")
            for r in rows[:10]:
                print(r)
            print("Sample from other DB (first 10):")
            for r in rows2[:10]:
                print(r)
            oconn.close()
        except Exception as e:
            print("Error comparing with other DB:", e)

    conn.close()

if __name__ == "__main__":
    main()