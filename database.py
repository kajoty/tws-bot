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

            df.to_sql('historical_data', self.conn, if_exists='append', index=False)
            self.conn.commit()

            logger.info(f"Gespeichert: {len(df)} Bars für {symbol} ({sec_type})")
            return True

        except Exception as e:
            logger.error(f"Fehler beim Speichern historischer Daten für {symbol}: {e}")
            return False

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

    def close(self):
        """Schließt die Datenbankverbindung."""
        if self.conn:
            self.conn.close()
            logger.info("Datenbankverbindung geschlossen")
