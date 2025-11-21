from database import DatabaseManager
import config

db = DatabaseManager(config.DB_PATH)
cursor = db.conn.cursor()
cursor.execute('''
    SELECT symbol, market_cap, avg_volume_20d, pe_ratio, free_cash_flow 
    FROM fundamental_data 
    ORDER BY symbol 
    LIMIT 10
''')
rows = cursor.fetchall()

print('Symbol | MktCap (B)  | AvgVol (M) | P/E   | FCF (B)')
print('-'*60)
for r in rows:
    mkt = f"${r[1]/1e9:.1f}" if r[1] else "NULL"
    vol = f"{r[2]/1e6:.1f}M" if r[2] else "NULL"
    pe = f"{r[3]:.1f}" if r[3] else "NULL"
    fcf = f"${r[4]/1e9:.1f}" if r[4] else "NULL"
    print(f"{r[0]:6s} | {mkt:11s} | {vol:10s} | {pe:5s} | {fcf}")

db.close()
