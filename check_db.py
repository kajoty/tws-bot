import sqlite3
import config

conn = sqlite3.connect(config.DB_PATH)
cursor = conn.cursor()

# Liste Tabellen
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print('Vorhandene Tabellen:', [t[0] for t in tables])

# Zähle Einträge
for table in tables:
    table_name = table[0]
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f"  - {table_name}: {count} Einträge")

conn.close()
