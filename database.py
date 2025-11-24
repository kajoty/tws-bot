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
            db_dir = os.path.dirname(self.db_path)
            if db_dir:  # Nur wenn Pfad ein Verzeichnis enthält
                os.makedirs(db_dir, exist_ok=True)
            
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = self.conn.cursor()
            
            # Aktiviere WAL-Modus für gleichzeitigen Lese-/Schreibzugriff
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA synchronous=NORMAL;')
            cursor.execute('PRAGMA cache_size=1000;')
            cursor.execute('PRAGMA temp_store=memory;')
            self.conn.commit()
            
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
            
            # Options-Positionen (erweitert für Spreads)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS options_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    position_type TEXT NOT NULL,
                    option_type TEXT NOT NULL,
                    strike REAL NOT NULL,
                    expiry TEXT NOT NULL,
                    right TEXT NOT NULL,
                    entry_premium REAL NOT NULL,
                    entry_underlying_price REAL NOT NULL,
                    dte_at_entry INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    stop_loss_underlying REAL,
                    take_profit_premium REAL,
                    auto_close_dte INTEGER,
                    current_premium REAL,
                    current_underlying_price REAL,
                    current_dte INTEGER,
                    pnl REAL,
                    pnl_pct REAL,
                    status TEXT NOT NULL,
                    short_strike REAL,
                    long_strike REAL,
                    spread_type TEXT,
                    net_premium REAL,
                    max_risk REAL,
                    entry_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    exit_timestamp DATETIME,
                    exit_reason TEXT
                )
            """)
            
            # Options-Signale (erweitert für Spreads)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS options_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    underlying_price REAL NOT NULL,
                    high_52w REAL,
                    low_52w REAL,
                    proximity_pct REAL,
                    iv_rank REAL,
                    pe_ratio REAL,
                    sector_pe REAL,
                    fcf_yield REAL,
                    market_cap REAL,
                    avg_volume REAL,
                    recommended_strike REAL,
                    recommended_expiry TEXT,
                    recommended_dte INTEGER,
                    short_strike REAL,
                    long_strike REAL,
                    short_delta REAL,
                    net_premium REAL,
                    max_risk REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Fundamentaldaten Cache
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fundamental_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    pe_ratio REAL,
                    market_cap REAL,
                    fcf REAL,
                    sector TEXT,
                    avg_volume REAL,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # IV Historie für IV Rank
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS iv_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    implied_volatility REAL,
                    historical_volatility REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, date)
                )
            """)
            
            # Indizes für Options-Tabellen
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_options_positions_symbol 
                ON options_positions(symbol)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_options_positions_status 
                ON options_positions(status)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_options_signals_timestamp 
                ON options_signals(timestamp)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_fundamental_data_symbol 
                ON fundamental_data(symbol)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_iv_history_symbol 
                ON iv_history(symbol)
            """)
            
            self.conn.commit()
            logger.info("[OK] Datenbank initialisiert (inkl. Options-Tabellen)")
            
        except Exception as e:
            logger.error(f"[FEHLER] Datenbank-Fehler: {e}")
    
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
            logger.debug(f"[OK] {symbol}: {len(df)} Bars gespeichert")
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Speichern von {symbol}: {e}")
    
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
            logger.error(f"[FEHLER] Fehler beim Laden von {symbol}: {e}")
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
            logger.error(f"[FEHLER] Fehler bei Update-Check für {symbol}: {e}")
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
            logger.debug(f"[OK] Signal gespeichert: {signal_type} {symbol}")
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Speichern von Signal: {e}")
    
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
            logger.error(f"[FEHLER] Fehler beim Laden von Signalen: {e}")
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
            logger.error(f"[FEHLER] Fehler bei Statistik-Berechnung: {e}")
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
                logger.info(f"[OK] {deleted} alte Datenzeilen gelöscht")
            
        except Exception as e:
            logger.error(f"[FEHLER] Cleanup-Fehler: {e}")
    
    # ========================================================================
    # OPTIONS-DATEN
    # ========================================================================
    
    def save_options_signal(self, signal: dict):
        """
        Speichert Options-Signal.
        
        Args:
            signal: Signal-Dictionary
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO options_signals (
                    signal_type, symbol, underlying_price, high_52w, low_52w,
                    proximity_pct, iv_rank, pe_ratio, sector_pe, fcf_yield,
                    market_cap, avg_volume, recommended_strike, recommended_expiry,
                    recommended_dte
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get('type'),
                signal.get('symbol'),
                signal.get('underlying_price'),
                signal.get('high_52w'),
                signal.get('low_52w'),
                signal.get('proximity_pct'),
                signal.get('iv_rank'),
                signal.get('pe_ratio'),
                signal.get('sector_pe'),
                signal.get('fcf_yield'),
                signal.get('market_cap'),
                signal.get('avg_volume'),
                signal.get('recommended_strike'),
                signal.get('recommended_expiry'),
                signal.get('recommended_dte')
            ))
            
            self.conn.commit()
            logger.debug(f"[OK] Options-Signal gespeichert: {signal.get('type')} {signal.get('symbol')}")
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Speichern von Options-Signal: {e}")
    
    def save_options_position(self, position: dict):
        """
        Speichert neue Options-Position.
        
        Args:
            position: Position-Dictionary
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO options_positions (
                    symbol, option_type, strike, expiry, right, entry_premium,
                    entry_underlying_price, dte_at_entry, quantity,
                    stop_loss_underlying, take_profit_premium, auto_close_dte,
                    current_premium, current_underlying_price, current_dte,
                    pnl, pnl_pct, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position.get('symbol'),
                position.get('option_type'),
                position.get('strike'),
                position.get('expiry'),
                position.get('right'),
                position.get('entry_premium'),
                position.get('entry_underlying_price'),
                position.get('dte_at_entry'),
                position.get('quantity'),
                position.get('stop_loss_underlying'),
                position.get('take_profit_premium'),
                position.get('auto_close_dte'),
                position.get('entry_premium'),  # current = entry at start
                position.get('entry_underlying_price'),
                position.get('dte_at_entry'),
                0.0,  # pnl starts at 0
                0.0,  # pnl_pct starts at 0
                'OPEN'
            ))
            
            self.conn.commit()
            logger.info(f"[OK] Options-Position gespeichert: {position.get('option_type')} {position.get('symbol')}")
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Speichern von Options-Position: {e}")
    
    def update_options_position(self, position_id: int, updates: dict):
        """
        Aktualisiert existierende Options-Position.
        
        Args:
            position_id: ID der Position
            updates: Dictionary mit zu aktualisierenden Feldern
        """
        try:
            # Baue UPDATE Query dynamisch
            set_clauses = []
            values = []
            
            for key, value in updates.items():
                set_clauses.append(f"{key} = ?")
                values.append(value)
            
            values.append(position_id)
            
            cursor = self.conn.cursor()
            cursor.execute(f"""
                UPDATE options_positions 
                SET {', '.join(set_clauses)}
                WHERE id = ?
            """, values)
            
            self.conn.commit()
            logger.debug(f"[OK] Options-Position {position_id} aktualisiert")
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Update von Options-Position: {e}")
    
    def close_options_position(self, position_id: int, exit_premium: float, 
                               exit_reason: str):
        """
        Schließt Options-Position.
        
        Args:
            position_id: ID der Position
            exit_premium: Premium beim Exit
            exit_reason: Grund für Exit
        """
        try:
            cursor = self.conn.cursor()
            
            # Hole Position für PnL-Berechnung
            cursor.execute("SELECT * FROM options_positions WHERE id = ?", (position_id,))
            row = cursor.fetchone()
            
            if not row:
                logger.warning(f"[WARNUNG] Position {position_id} nicht gefunden")
                return
            
            # PnL berechnen
            entry_premium = row[6]  # entry_premium
            quantity = row[9]  # quantity
            pnl = (exit_premium - entry_premium) * quantity * 100  # *100 für Options-Multiplikator
            pnl_pct = ((exit_premium / entry_premium) - 1) * 100 if entry_premium > 0 else 0
            
            # Bestimme Status
            status = 'CLOSED_PROFIT' if pnl > 0 else 'CLOSED_LOSS'
            if 'AUTO' in exit_reason:
                status = 'CLOSED_AUTO'
            
            # Update Position
            cursor.execute("""
                UPDATE options_positions 
                SET current_premium = ?,
                    pnl = ?,
                    pnl_pct = ?,
                    status = ?,
                    exit_timestamp = ?,
                    exit_reason = ?
                WHERE id = ?
            """, (exit_premium, pnl, pnl_pct, status, datetime.now().isoformat(), 
                  exit_reason, position_id))
            
            self.conn.commit()
            logger.info(f"[OK] Position {position_id} geschlossen: {exit_reason} | PnL: ${pnl:.2f} ({pnl_pct:+.2f}%)")
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Schließen von Position: {e}")
    
    def get_open_options_positions(self) -> list:
        """
        Lädt alle offenen Options-Positionen.
        
        Returns:
            Liste von Dictionaries mit offenen Positionen
        """
        try:
            df = pd.read_sql_query("""
                SELECT * FROM options_positions 
                WHERE status = 'OPEN'
                ORDER BY entry_timestamp DESC
            """, self.conn)
            
            if not df.empty:
                df['entry_timestamp'] = pd.to_datetime(df['entry_timestamp'])
                return df.to_dict('records')
            else:
                return []
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Laden von offenen Positionen: {e}")
            return []
    
    def save_fundamental_data(self, symbol: str, data: dict):
        """
        Speichert/Aktualisiert Fundamentaldaten.
        
        Args:
            symbol: Ticker Symbol
            data: Dictionary mit Fundamentaldaten
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO fundamental_data (
                    symbol, pe_ratio, market_cap, fcf, sector, avg_volume, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                data.get('pe_ratio'),
                data.get('market_cap'),
                data.get('fcf'),
                data.get('sector'),
                data.get('avg_volume'),
                datetime.now().isoformat()
            ))
            
            self.conn.commit()
            logger.debug(f"[OK] Fundamentaldaten gespeichert: {symbol}")
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Speichern von Fundamentaldaten: {e}")
    
    def get_fundamental_data(self, symbol: str, max_age_days: int = 7) -> Optional[dict]:
        """
        Lädt gecachte Fundamentaldaten.
        
        Args:
            symbol: Ticker Symbol
            max_age_days: Maximales Alter in Tagen
            
        Returns:
            Dictionary mit Daten oder None
        """
        try:
            cursor = self.conn.cursor()
            cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
            
            cursor.execute("""
                SELECT * FROM fundamental_data 
                WHERE symbol = ? AND last_updated >= ?
            """, (symbol, cutoff))
            
            row = cursor.fetchone()
            
            if row:
                return {
                    'pe_ratio': row[1],
                    'market_cap': row[2],
                    'fcf': row[3],
                    'sector': row[4],
                    'avg_volume': row[5],
                    'last_updated': row[6]
                }
            
            return None
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Laden von Fundamentaldaten: {e}")
            return None
    
    def save_iv_data(self, symbol: str, date: str, implied_vol: float, hist_vol: float):
        """
        Speichert IV-Daten für IV Rank Berechnung.
        
        Args:
            symbol: Ticker Symbol
            date: Datum
            implied_vol: Implizite Volatilität
            hist_vol: Historische Volatilität
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO iv_history (
                    symbol, date, implied_volatility, historical_volatility
                ) VALUES (?, ?, ?, ?)
            """, (symbol, date, implied_vol, hist_vol))
            
            self.conn.commit()
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Speichern von IV-Daten: {e}")
    
    def get_iv_history(self, symbol: str, days: int = 252) -> pd.DataFrame:
        """
        Lädt IV-Historie für IV Rank Berechnung.
        
        Args:
            symbol: Ticker Symbol
            days: Anzahl Tage (default: 252 = 52 Wochen)
            
        Returns:
            DataFrame mit IV-Daten
        """
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            df = pd.read_sql_query("""
                SELECT * FROM iv_history 
                WHERE symbol = ? AND date >= ?
                ORDER BY date ASC
            """, self.conn, params=(symbol, cutoff))
            
            if not df.empty:
                df['date'] = pd.to_datetime(df['date'])
            
            return df
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Laden von IV-Historie: {e}")
            return pd.DataFrame()
    
    def close(self):
        """Schließt Datenbankverbindung."""
        if self.conn:
            self.conn.close()
            logger.info("[OK] Datenbankverbindung geschlossen")


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
