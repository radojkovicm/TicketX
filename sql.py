import sqlite3  # Zamena za druge baze (npr. psycopg2 za PostgreSQL)

# Povežite se sa bazom (zamenite "ticketx.db" sa vašim fajlom baze)
db_path = "database.db"  # ili apsolutna putanja: r"C:\putanja\do\baze.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    # Pokrenite SQL upit
    cursor.execute("ALTER TABLE users ADD COLUMN department_name TEXT;")

    # Prikaz rezultata
    results = cursor.fetchall()
    if results:
        print("Pronađeni tiketi:")
        for row in results:
            print(row)
    else:
        print("Nema rezultata za ovaj upit.")

except sqlite3.Error as e:
    print(f"Greška u bazi: {e}")

finally:
    # Zatvorite konekciju
    conn.close()