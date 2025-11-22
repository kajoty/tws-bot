"""
Vereinfachtes Datenbank-Management für TWS Signal Service.
Speichert nur historische Daten und Trading-Signale.
"""

import os
import sqlite3
import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import Optional, List
import config

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Verwaltet historische Daten und Trading-Signale."""
    
    def __init__(self, db_path: str = config.DATABASE_PATH):
        """
        Initialisiert Database Manager.
        
        Args:
            db_path: Pfad zur SQLite-Datenbank
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._initialize_database()
    
    def _initialize_database(self):
        """Erstellt Datenbank-Tabellen."""
        try:
            # Erstelle Verzeichnis falls nicht vorhanden
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = self.conn.cursor()
            
            # Historische Preisdaten
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS historical_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, date)
                )
            """)
            
            # Trading Signale
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity INTEGER,
                    reason TEXT,
                    pnl REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Indizes für Performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_historical_symbol 
                ON historical_data(symbol)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_timestamp 
                ON signals(timestamp)
            """)
            
            self.conn.commit()
            logger.info("✓ Datenbank initialisiert")
            
        except Exception as e:
            logger.error(f"❌ Datenbank-Fehler: {e}")
    
    # ========================================================================
    # HISTORISCHE DATEN
    # ========================================================================
    
    def save_historical_data(self, symbol: str, df: pd.DataFrame):
        """
        Speichert historische Daten.
        
        Args:
            symbol: Ticker Symbol
            df: DataFrame mit OHLCV Daten
        """
        try:
            cursor = self.conn.cursor()
            
            for _, row in df.iterrows():
                cursor.execute("""
                    INSERT OR REPLACE INTO historical_data 
                    (symbol, date, open, high, low, close, volume, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol,
                    row['date'].strftime('%Y-%m-%d') if pd.notna(row['date']) else None,
                    row['open'],
                    row['high'],
                    row['low'],
                    row['close'],
                    row['volume'],
                    datetime.now().isoformat()
                ))
            
            self.conn.commit()
            logger.debug(f"✓ {symbol}: {len(df)} Bars gespeichert")
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Speichern von {symbol}: {e}")
    
    def load_historical_data(self, symbol: str, days: int = None) -> pd.DataFrame:
        """
        Lädt historische Daten.
        
        Args:
            symbol: Ticker Symbol
            days: Anzahl Tage (None = alle)
            
        Returns:
            DataFrame mit historischen Daten
        """
        try:
            query = """
                SELECT date, open, high, low, close, volume
                FROM historical_data
                WHERE symbol = ?
            """
            
            if days:
                cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
                query += f" AND date >= '{cutoff}'"
            
            query += " ORDER BY date ASC"
            
            df = pd.read_sql_query(query, self.conn, params=(symbol,))
            
            if not df.empty:
                df['date'] = pd.to_datetime(df['date'])
            
            return df
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden von {symbol}: {e}")
            return pd.DataFrame()
    
    def needs_update(self, symbol: str, max_age_days: int = 1) -> bool:
        """
        Prüft ob Daten aktualisiert werden müssen.
        
        Args:
            symbol: Ticker Symbol
            max_age_days: Maximales Alter in Tagen
            
        Returns:
            True wenn Update nötig
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT MAX(timestamp) as last_update
                FROM historical_data
                WHERE symbol = ?
            """, (symbol,))
            
            result = cursor.fetchone()
            
            if not result or not result[0]:
                return True
            
            last_update = datetime.fromisoformat(result[0])
            age = datetime.now() - last_update
            
            return age.days >= max_age_days
            
        except Exception as e:
            logger.error(f"❌ Fehler bei Update-Check für {symbol}: {e}")
            return True
    
    # ========================================================================
    # SIGNALE
    # ========================================================================
    
    def save_signal(self, signal_type: str, symbol: str, price: float,
                   quantity: int = None, reason: str = None, pnl: float = None):
        """
        Speichert Trading-Signal.
        
        Args:
            signal_type: "ENTRY" oder "EXIT"
            symbol: Ticker Symbol
            price: Preis
            quantity: Anzahl Aktien
            reason: Grund für Signal
            pnl: Profit/Loss (nur bei EXIT)
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO signals (signal_type, symbol, price, quantity, reason, pnl, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                signal_type,
                symbol,
                price,
                quantity,
                reason,
                pnl,
                datetime.now().isoformat()
            ))
            
            self.conn.commit()
            logger.debug(f"✓ Signal gespeichert: {signal_type} {symbol}")
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Speichern von Signal: {e}")
    
    def get_signals(self, days: int = 7, signal_type: str = None) -> pd.DataFrame:
        """
        Lädt Signale.
        
        Args:
            days: Anzahl Tage zurück
            signal_type: "ENTRY", "EXIT" oder None (alle)
            
        Returns:
            DataFrame mit Signalen
        """
        try:
            query = "SELECT * FROM signals WHERE 1=1"
            params = []
            
            if days:
                cutoff = (datetime.now() - timedelta(days=days)).isoformat()
                query += " AND timestamp >= ?"
                params.append(cutoff)
            
            if signal_type:
                query += " AND signal_type = ?"
                params.append(signal_type)
            
            query += " ORDER BY timestamp DESC"
            
            df = pd.read_sql_query(query, self.conn, params=params)
            
            if not df.empty:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            return df
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden von Signalen: {e}")
            return pd.DataFrame()
    
    def get_signal_stats(self, days: int = 30) -> dict:
        """
        Berechnet Signal-Statistiken.
        
        Args:
            days: Zeitraum in Tagen
            
        Returns:
            Dictionary mit Statistiken
        """
        try:
            df = self.get_signals(days=days)
            
            if df.empty:
                return {
                    'total_signals': 0,
                    'entry_signals': 0,
                    'exit_signals': 0,
                    'total_pnl': 0.0,
                    'win_rate': 0.0,
                    'avg_pnl': 0.0
                }
            
            entry_count = len(df[df['signal_type'] == 'ENTRY'])
            exit_count = len(df[df['signal_type'] == 'EXIT'])
            
            exits = df[df['signal_type'] == 'EXIT']
            total_pnl = exits['pnl'].sum() if not exits.empty else 0.0
            wins = len(exits[exits['pnl'] > 0]) if not exits.empty else 0
            win_rate = (wins / len(exits) * 100) if len(exits) > 0 else 0.0
            avg_pnl = total_pnl / len(exits) if len(exits) > 0 else 0.0
            
            return {
                'total_signals': len(df),
                'entry_signals': entry_count,
                'exit_signals': exit_count,
                'total_pnl': total_pnl,
                'win_rate': win_rate,
                'avg_pnl': avg_pnl,
                'wins': wins,
                'losses': len(exits) - wins if not exits.empty else 0
            }
            
        except Exception as e:
            logger.error(f"❌ Fehler bei Statistik-Berechnung: {e}")
            return {}
    
    # ========================================================================
    # CLEANUP
    # ========================================================================
    
    def cleanup_old_data(self, days_to_keep: int = 365):
        """
        Löscht alte historische Daten.
        
        Args:
            days_to_keep: Wie viele Tage behalten
        """
        try:
            cutoff = (datetime.now() - timedelta(days=days_to_keep)).strftime('%Y-%m-%d')
            
            cursor = self.conn.cursor()
            cursor.execute("""
                DELETE FROM historical_data 
                WHERE date < ?
            """, (cutoff,))
            
            deleted = cursor.rowcount
            self.conn.commit()
            
            if deleted > 0:
                logger.info(f"✓ {deleted} alte Datenzeilen gelöscht")
            
        except Exception as e:
            logger.error(f"❌ Cleanup-Fehler: {e}")
    
    def close(self):
        """Schließt Datenbankverbindung."""
        if self.conn:
            self.conn.close()
            logger.info("✓ Datenbankverbindung geschlossen")


if __name__ == "__main__":
    """Test Script für Database."""
    logging.basicConfig(level=logging.INFO)
    
    db = DatabaseManager()
    
    # Test: Signal speichern
    db.save_signal(
        signal_type="ENTRY",
        symbol="AAPL",
        price=175.50,
        quantity=10,
        reason="MA Crossover + RSI < 30"
    )
    
    db.save_signal(
        signal_type="EXIT",
        symbol="AAPL",
        price=184.50,
        quantity=10,
        reason="Take Profit erreicht",
        pnl=90.00
    )
    
    # Test: Signale laden
    signals = db.get_signals(days=7)
    print(f"\n✓ {len(signals)} Signale gefunden")
    print(signals[['signal_type', 'symbol', 'price', 'pnl', 'timestamp']])
    
    # Test: Statistiken
    stats = db.get_signal_stats(days=30)
    print(f"\n✓ Statistiken:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    db.close()
