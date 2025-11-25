import sqlite3
import shutil
from datetime import datetime

# Putanja do baze
DB_PATH = 'database.db'
BACKUP_PATH = f'database_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'

print("=" * 60)
print("MIGRACIJA BAZE PODATAKA - TicketX v2")
print("=" * 60)

# 1. Backup
print(f"\n1. Pravim backup: {BACKUP_PATH}")
try:
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print("   ✅ Backup kreiran!")
except Exception as e:
    print(f"   ❌ Greška pri backup-u: {e}")
    exit(1)

# 2. Konektuj se na bazu
print("\n2. Konektujem se na bazu...")
try:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    print("   ✅ Konekcija uspešna!")
except Exception as e:
    print(f"   ❌ Greška pri konekciji: {e}")
    exit(1)

# 3. Proveri broj tiketa PRE migracije
print("\n3. Proveravam trenutno stanje...")
cursor.execute("SELECT COUNT(*) FROM tickets")
ticket_count_before = cursor.fetchone()[0]
print(f"   📊 Broj tiketa u bazi: {ticket_count_before}")

# 4. Proveri trenutni tip assigned_to kolone
cursor.execute("PRAGMA table_info(tickets)")
columns = cursor.fetchall()
for col in columns:
    if col[1] == 'assigned_to':
        print(f"   📋 assigned_to tip: {col[2]}")
        if col[2] == 'TEXT':
            print("\n   ⚠️  Migracija već izvršena! assigned_to je već TEXT.")
            response = input("   Da li želiš da nastaviš? (da/ne): ")
            if response.lower() != 'da':
                print("   Izlazim...")
                conn.close()
                exit(0)

# 5. Migracija
print("\n4. Izvršavam migraciju...")
try:
    # Rename stare tabele
    cursor.execute("ALTER TABLE tickets RENAME TO tickets_old")
    print("   ✅ Stara tabela preimenovana u tickets_old")
    
    # Kreiraj novu tabelu
    cursor.execute("""
        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            created_by INTEGER NOT NULL,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'new',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            is_private INTEGER DEFAULT 0,
            due_date TEXT,
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            category_id INTEGER,
            assigned_to TEXT,
            FOREIGN KEY (created_by) REFERENCES users(id),
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )
    """)
    print("   ✅ Nova tabela kreirana")
    
    # Kopiraj podatke
    cursor.execute("""
        INSERT INTO tickets (id, title, description, created_by, priority, status, 
                            created_at, is_private, due_date, updated_at, 
                            category_id, assigned_to)
        SELECT id, title, description, created_by, priority, status, 
               created_at, 0, NULL, updated_at, 
               category_id, CAST(assigned_to AS TEXT)
        FROM tickets_old
    """)
    print("   ✅ Podaci kopirani")
    
    # Obriši staru tabelu
    cursor.execute("DROP TABLE tickets_old")
    print("   ✅ Stara tabela obrisana")
    
    # Commit promene
    conn.commit()
    print("   ✅ Promene sačuvane!")
    
except Exception as e:
    print(f"   ❌ Greška pri migraciji: {e}")
    conn.rollback()
    print("\n   🔄 Vraćam promene...")
    conn.close()
    
    # Vrati backup
    print(f"   Vraćam backup iz: {BACKUP_PATH}")
    shutil.copy2(BACKUP_PATH, DB_PATH)
    print("   ✅ Backup vraćen!")
    exit(1)

# 6. Provera POSLE migracije
print("\n5. Proveravam rezultate...")
cursor.execute("SELECT COUNT(*) FROM tickets")
ticket_count_after = cursor.fetchone()[0]
print(f"   📊 Broj tiketa nakon migracije: {ticket_count_after}")

if ticket_count_before == ticket_count_after:
    print("   ✅ Svi tiketi su sačuvani!")
else:
    print(f"   ⚠️  UPOZORENJE: Broj tiketa se promenio!")
    print(f"      Pre: {ticket_count_before}, Posle: {ticket_count_after}")

# Proveri novu strukturu
cursor.execute("PRAGMA table_info(tickets)")
columns_after = cursor.fetchall()
print("\n   📋 Nova struktura 'tickets' tabele:")
for col in columns_after:
    if col[1] in ['assigned_to', 'is_private', 'due_date']:
        print(f"      - {col[1]}: {col[2]}")

# 7. Proveri nove tabele
print("\n6. Proveravam nove tabele...")
new_tables = ['activity_logs', 'ticket_activity_log', 'ticket_templates', 'ticket_muted_users']
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
existing_tables = [row[0] for row in cursor.fetchall()]

for table in new_tables:
    if table in existing_tables:
        print(f"   ✅ {table} - već postoji")
    else:
        print(f"   ⚠️  {table} - NEĆE biti kreirana (database.py će je kreirati)")

# Zatvori konekciju
conn.close()

print("\n" + "=" * 60)
print("✅ MIGRACIJA USPEŠNO ZAVRŠENA!")
print("=" * 60)
print(f"\n📁 Backup sačuvan u: {BACKUP_PATH}")
print("\n📝 Sledeći koraci:")
print("   1. Kopiraj databaase.db na server: \\\\server\\G$\\www\\TicketX\\")
print("   2. Kopiraj sve .py fajlove na server")
print("   3. Kreiraj novi venv i instaliraj requirements.txt")
print("   4. Restartuj IIS")
print("\n" + "=" * 60)
