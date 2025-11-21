"""
Datenbank-Management für den IB Trading Bot.
Verwaltet historische Marktdaten, Trades und Performance-Metriken in SQLite.
"""

import sqlite3
import pandas as pd
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
import config

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Verwaltet alle Datenbankoperationen für historische Daten,
    Trades und Performance-Tracking.
    """

    def __init__(self, db_path: str = config.DB_PATH):
        """
        Initialisiert den DatabaseManager.

        Args:
            db_path: Pfad zur SQLite-Datenbankdatei
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._initialize_database()

    def _initialize_database(self):
        """Erstellt die notwendigen Tabellen, falls sie nicht existieren."""
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = self.conn.cursor()

            # Tabelle für historische Preisdaten
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS historical_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    sec_type TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, sec_type, date)
                )
            """)

            # Tabelle für Trades
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    sec_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL,
                    commission REAL,
                    order_id INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    strategy TEXT,
                    notes TEXT
                )
            """)

            # Tabelle für Positionen
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    sec_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    avg_cost REAL,
                    current_price REAL,
                    unrealized_pnl REAL,
                    realized_pnl REAL,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, sec_type)
                )
            """)

            # Tabelle für Performance-Tracking
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    equity REAL NOT NULL,
                    cash REAL,
                    positions_value REAL,
                    total_pnl REAL,
                    daily_pnl REAL
                )
            """)

            # Tabelle für Options-spezifische Daten
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS options_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    strike REAL NOT NULL,
                    expiry TEXT NOT NULL,
                    right TEXT NOT NULL,
                    implied_volatility REAL,
                    delta REAL,
                    gamma REAL,
                    theta REAL,
                    vega REAL,
                    open_interest INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, strike, expiry, right)
                )
            """)

            # Tabelle für fundamentale Unternehmensdaten
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fundamental_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    market_cap REAL,
                    pe_ratio REAL,
                    free_cash_flow REAL,
                    revenue REAL,
                    earnings REAL,
                    sector TEXT,
                    industry TEXT,
                    avg_volume_20d REAL,
                    sector_median_pe REAL,
                    next_earnings_date TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, timestamp)
                )
            """)

            # Tabelle für IV Historie (für IV Rank Berechnung)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS iv_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    implied_volatility REAL NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, date)
                )
            """)

            # Tabelle für Branchen-Benchmarks
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sector_benchmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sector TEXT NOT NULL,
                    pe_median REAL,
                    pe_mean REAL,
                    pe_std REAL,
                    fcf_yield_median REAL,
                    update_date TEXT NOT NULL,
                    UNIQUE(sector, update_date)
                )
            """)

            self.conn.commit()
            logger.info(f"Datenbank initialisiert: {self.db_path}")

        except sqlite3.Error as e:
            logger.error(f"Fehler bei Datenbank-Initialisierung: {e}")
            raise

    def save_historical_data(self, symbol: str, sec_type: str, df: pd.DataFrame) -> bool:
        """
        Speichert historische Daten in der Datenbank.

        Args:
            symbol: Tickersymbol
            sec_type: Wertpapiertyp (STK, OPT, etc.)
            df: DataFrame mit Spalten: date, open, high, low, close, volume

        Returns:
            True bei Erfolg, False bei Fehler
        """
        try:
            if df.empty:
                logger.warning(f"Keine Daten zum Speichern für {symbol}")
                return False

            df = df.copy()
            df['symbol'] = symbol
            df['sec_type'] = sec_type

            if 'date' in df.columns and pd.api.types.is_datetime64_any_dtype(df['date']):
                df['date'] = df['date'].dt.strftime('%Y-%m-%d')

            # Nutze INSERT OR IGNORE um Duplikate zu überspringen
            cursor = self.conn.cursor()
            inserted = 0
            for _, row in df.iterrows():
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO historical_data
                        (symbol, sec_type, date, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        row['symbol'], row['sec_type'], row['date'],
                        row['open'], row['high'], row['low'], row['close'], row['volume']
                    ))
                    if cursor.rowcount > 0:
                        inserted += 1
                except Exception as e:
                    logger.warning(f"Überspringe Duplikat für {symbol} am {row['date']}")
                    continue
            
            self.conn.commit()
            
            if inserted > 0:
                logger.info(f"Gespeichert: {inserted} neue Bars für {symbol} ({sec_type})")
            else:
                logger.debug(f"Keine neuen Daten für {symbol} ({sec_type})")
            
            return True

        except Exception as e:
            logger.error(f"Fehler beim Speichern historischer Daten für {symbol}: {e}")
            return False

    def get_latest_date(self, symbol: str, sec_type: str = "STK") -> Optional[str]:
        """Holt das neueste Datum für ein Symbol aus der DB."""
        try:
            query = """
                SELECT MAX(date) as max_date
                FROM historical_data
                WHERE symbol = ? AND sec_type = ?
            """
            cursor = self.conn.cursor()
            cursor.execute(query, (symbol, sec_type))
            result = cursor.fetchone()
            return result[0] if result and result[0] else None
        except Exception as e:
            logger.error(f"Fehler beim Abrufen des letzten Datums für {symbol}: {e}")
            return None

    def needs_update(self, symbol: str, sec_type: str = "STK", max_age_days: int = 1) -> bool:
        """Prüft ob Daten für Symbol aktualisiert werden müssen."""
        from datetime import datetime, timedelta
        
        latest_date = self.get_latest_date(symbol, sec_type)
        if not latest_date:
            return True  # Keine Daten vorhanden
        
        try:
            latest_dt = datetime.strptime(latest_date, '%Y-%m-%d')
            age_days = (datetime.now() - latest_dt).days
            return age_days > max_age_days
        except Exception as e:
            logger.error(f"Fehler beim Prüfen des Datenalters für {symbol}: {e}")
            return True

    def load_historical_data(
        self,
        symbol: str,
        sec_type: str = "STK",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """Lädt historische Daten aus der Datenbank."""
        try:
            query = """
                SELECT date, open, high, low, close, volume, timestamp
                FROM historical_data
                WHERE symbol = ? AND sec_type = ?
            """
            params = [symbol, sec_type]

            if start_date:
                query += " AND date >= ?"
                params.append(start_date)
            if end_date:
                query += " AND date <= ?"
                params.append(end_date)

            query += " ORDER BY date ASC"

            df = pd.read_sql_query(query, self.conn, params=params)

            if not df.empty:
                df['date'] = pd.to_datetime(df['date'])
                logger.info(f"Geladen: {len(df)} Bars für {symbol}")
            else:
                logger.warning(f"Keine Daten gefunden für {symbol} ({sec_type})")

            return df

        except Exception as e:
            logger.error(f"Fehler beim Laden historischer Daten für {symbol}: {e}")
            return pd.DataFrame()

    def cleanup_old_data(self, days_to_keep: int = 730) -> bool:
        """Löscht historische Daten älter als X Tage (Standard: 2 Jahre)."""
        try:
            from datetime import datetime, timedelta
            cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime('%Y-%m-%d')
            
            cursor = self.conn.cursor()
            cursor.execute("""
                DELETE FROM historical_data
                WHERE date < ?
            """, (cutoff_date,))
            
            deleted_rows = cursor.rowcount
            self.conn.commit()
            
            if deleted_rows > 0:
                logger.info(f"Bereinigung: {deleted_rows} alte Datensätze gelöscht (älter als {cutoff_date})")
            
            # VACUUM zum Freigeben von Speicherplatz
            cursor.execute("VACUUM")
            logger.info("Datenbank optimiert (VACUUM)")
            
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Bereinigen alter Daten: {e}")
            return False

    def save_trade(self, symbol: str, sec_type: str, action: str, quantity: int, 
                   price: float, commission: float = 0.0, order_id: Optional[int] = None,
                   strategy: Optional[str] = None, notes: Optional[str] = None) -> bool:
        """Speichert einen ausgeführten Trade."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO trades (symbol, sec_type, action, quantity, price, 
                                    commission, order_id, strategy, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, sec_type, action, quantity, price, commission,
                  order_id, strategy, notes))

            self.conn.commit()
            logger.info(f"Trade gespeichert: {action} {quantity} {symbol} @ {price}")
            return True

        except Exception as e:
            logger.error(f"Fehler beim Speichern des Trades: {e}")
            return False

    def get_trade_history(self, symbol: Optional[str] = None, days: int = 30) -> pd.DataFrame:
        """Lädt Trade-Historie."""
        try:
            query = """
                SELECT * FROM trades
                WHERE timestamp >= datetime('now', ? || ' days')
            """
            params = [f'-{days}']

            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)

            query += " ORDER BY timestamp DESC"

            df = pd.read_sql_query(query, self.conn, params=params)
            if not df.empty:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df
        except Exception as e:
            logger.error(f"Fehler beim Laden der Trade-Historie: {e}")
            return pd.DataFrame()

    def save_performance_snapshot(self, equity: float, cash: float, positions_value: float,
                                   total_pnl: float, daily_pnl: float = 0.0) -> bool:
        """Speichert einen Performance-Snapshot."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO performance (equity, cash, positions_value, total_pnl, daily_pnl)
                VALUES (?, ?, ?, ?, ?)
            """, (equity, cash, positions_value, total_pnl, daily_pnl))

            self.conn.commit()
            return True

        except Exception as e:
            logger.error(f"Fehler beim Speichern der Performance: {e}")
            return False

    def get_performance_history(self, days: int = 30) -> pd.DataFrame:
        """Lädt Performance-Historie."""
        try:
            query = """
                SELECT * FROM performance
                WHERE timestamp >= datetime('now', ? || ' days')
                ORDER BY timestamp ASC
            """
            df = pd.read_sql_query(query, self.conn, params=(f'-{days}',))
            if not df.empty:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df
        except Exception as e:
            logger.error(f"Fehler beim Laden der Performance-Historie: {e}")
            return pd.DataFrame()

    def save_fundamental_data(self, symbol: str, data: Dict) -> bool:
        """
        Speichert fundamentale Daten für ein Symbol.
        
        Args:
            symbol: Ticker Symbol
            data: Dict mit Fundamentaldaten (market_cap, pe_ratio, fcf, etc.)
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO fundamental_data 
                (symbol, market_cap, pe_ratio, free_cash_flow, revenue, earnings, 
                 sector, industry, avg_volume_20d, sector_median_pe, next_earnings_date, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                symbol,
                data.get('market_cap'),
                data.get('pe_ratio'),
                data.get('free_cash_flow'),
                data.get('revenue'),
                data.get('earnings'),
                data.get('sector'),
                data.get('industry'),
                data.get('avg_volume_20d'),
                data.get('sector_median_pe'),
                data.get('next_earnings_date')
            ))
            
            self.conn.commit()
            logger.debug(f"Fundamentaldaten gespeichert: {symbol}")
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Speichern der Fundamentaldaten für {symbol}: {e}")
            return False
    
    def get_fundamental_data(self, symbol: str) -> Optional[Dict]:
        """Lädt aktuellste fundamentale Daten für ein Symbol."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT * FROM fundamental_data
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (symbol,))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))
            
        except Exception as e:
            logger.error(f"Fehler beim Laden der Fundamentaldaten für {symbol}: {e}")
            return None
    
    def save_iv_data(self, symbol: str, date: str, iv: float) -> bool:
        """Speichert IV Daten für ein Symbol."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO iv_history (symbol, date, implied_volatility)
                VALUES (?, ?, ?)
            """, (symbol, date, iv))
            
            self.conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Speichern der IV-Daten für {symbol}: {e}")
            return False
    
    def get_iv_history(self, symbol: str, days: int = 252) -> pd.Series:
        """
        Lädt IV Historie für ein Symbol (für IV Rank Berechnung).
        
        Args:
            symbol: Ticker Symbol
            days: Anzahl Tage Historie (default 252 = 1 Jahr)
            
        Returns:
            pd.Series mit IV-Werten
        """
        try:
            query = """
                SELECT date, implied_volatility 
                FROM iv_history
                WHERE symbol = ?
                AND date >= date('now', ? || ' days')
                ORDER BY date ASC
            """
            
            df = pd.read_sql_query(query, self.conn, params=(symbol, f'-{days}'))
            
            if df.empty:
                return pd.Series()
            
            return df.set_index('date')['implied_volatility']
            
        except Exception as e:
            logger.error(f"Fehler beim Laden der IV-Historie für {symbol}: {e}")
            return pd.Series()
    
    def save_sector_benchmark(self, sector: str, pe_median: float, 
                             pe_mean: float, pe_std: float,
                             fcf_yield_median: float, update_date: str) -> bool:
        """Speichert Branchen-Benchmarks."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sector_benchmarks 
                (sector, pe_median, pe_mean, pe_std, fcf_yield_median, update_date)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (sector, pe_median, pe_mean, pe_std, fcf_yield_median, update_date))
            
            self.conn.commit()
            logger.debug(f"Branchen-Benchmark gespeichert: {sector}")
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Speichern des Branchen-Benchmarks für {sector}: {e}")
            return False
    
    def get_sector_benchmark(self, sector: str) -> Optional[Dict]:
        """Lädt aktuellsten Branchen-Benchmark."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT * FROM sector_benchmarks
                WHERE sector = ?
                ORDER BY update_date DESC
                LIMIT 1
            """, (sector,))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))
            
        except Exception as e:
            logger.error(f"Fehler beim Laden des Branchen-Benchmarks für {sector}: {e}")
            return None

    def close(self):
        """Schließt die Datenbankverbindung."""
        if self.conn:
            self.conn.close()
            logger.info("Datenbankverbindung geschlossen")
