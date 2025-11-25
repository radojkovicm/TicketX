# create_tickets_old_sync.py
import sqlite3, sys

DB = sys.argv[1] if len(sys.argv) > 1 else "database.db"

def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # Check tickets table exists
    cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name='tickets'")
    t = cur.fetchone()
    if not t:
        print("ERROR: 'tickets' table not found. Aborting.")
        conn.close()
        return

    # Get tickets columns
    cur.execute("PRAGMA table_info('tickets')")
    cols = cur.fetchall()
    if not cols:
        print("ERROR: could not read 'tickets' schema.")
        conn.close()
        return

    # Build column definitions and detect primary keys
    col_defs = []
    pk_cols = []
    for cid, name, ctype, notnull, dflt_value, pk in cols:
        typ = ctype if ctype else "TEXT"
        col_defs.append(f'"{name}" {typ}')
        if pk:
            pk_cols.append(name)

    # If single PK named 'id', mark it PRIMARY KEY in column defs
    if len(pk_cols) == 1 and pk_cols[0].lower() == 'id':
        # find index of that column in cols and update col_defs accordingly
        for i, c in enumerate(cols):
            if c[1] == pk_cols[0]:
                id_type = c[2] if c[2] else "INTEGER"
                col_defs[i] = f'"{c[1]}" {id_type} PRIMARY KEY'
                break

    # If no single-id pk, but there are pk_cols (composite), we'll add PRIMARY KEY(...) later
    create_sql = 'CREATE TABLE IF NOT EXISTS "tickets_old" (' + ", ".join(col_defs)
    if len(pk_cols) > 1:
        pk_list = ", ".join([f'"{c}"' for c in pk_cols])
        create_sql += f", PRIMARY KEY ({pk_list})"
    create_sql += ")"

    print("CREATE SQL for tickets_old:")
    print(create_sql)

    # Create table
    cur.execute(create_sql)
    conn.commit()

    # Copy data from tickets -> tickets_old (explicit column list)
    column_names = [f'"{c[1]}"' for c in cols]
    col_list = ", ".join(column_names)
    copy_sql = f'INSERT OR REPLACE INTO "tickets_old" ({col_list}) SELECT {col_list} FROM "tickets"'
    print("Copying rows from tickets into tickets_old (may be many rows).")
    cur.execute(copy_sql)
    conn.commit()
    print("Copied rows (approx):", cur.rowcount)

    # Prepare triggers to keep tickets_old in sync
    # Build NEW.* list for insert and update assignment list
    new_values = ", ".join([f'NEW."{c[1]}"' for c in cols])
    update_assigns = ", ".join([f'"{c[1]}" = NEW."{c[1]}"' for c in cols])

    if pk_cols:
        where_clause = " AND ".join([f'"{pk}" = OLD.{pk}' for pk in pk_cols])
    else:
        where_clause = 'id = OLD.id'

    triggers = {
        "tickets_sync_insert": f"""
            CREATE TRIGGER IF NOT EXISTS tickets_sync_insert
            AFTER INSERT ON tickets
            BEGIN
                INSERT OR REPLACE INTO tickets_old ({col_list}) VALUES ({new_values});
            END;""",
        "tickets_sync_update": f"""
            CREATE TRIGGER IF NOT EXISTS tickets_sync_update
            AFTER UPDATE ON tickets
            BEGIN
                UPDATE tickets_old SET {update_assigns} WHERE {where_clause};
            END;""",
        "tickets_sync_delete": f"""
            CREATE TRIGGER IF NOT EXISTS tickets_sync_delete
            AFTER DELETE ON tickets
            BEGIN
                DELETE FROM tickets_old WHERE {where_clause};
            END;"""
    }

    # Create triggers
    for name, sql in triggers.items():
        print("Creating trigger:", name)
        cur.execute(sql)
    conn.commit()

    print("Done. tickets_old table created, data copied, and triggers installed.")
    conn.close()

if __name__ == "__main__":
    main()