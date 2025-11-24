import sqlite3

DB_PATH = "data/trading_signals.db"

query = """
SELECT COUNT(*) AS vollstaendig
FROM fundamental_data
WHERE pe_ratio IS NOT NULL
  AND market_cap IS NOT NULL
  AND fcf IS NOT NULL
  AND sector IS NOT NULL
  AND avg_volume IS NOT NULL;
"""

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(query)
    result = cur.fetchone()
    print(f"Vollständig gefüllte Datensätze: {result[0]}")
    conn.close()

if __name__ == "__main__":
    main()
