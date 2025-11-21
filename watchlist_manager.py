"""
Watchlist-Manager für dynamisches Laden von Symbolen und Metadaten.
"""

import pandas as pd
import logging
from typing import List, Dict, Optional
import os
import config

logger = logging.getLogger(__name__)


class WatchlistManager:
    """
    Verwaltet die Watchlist aus CSV-Datei mit erweiterten Metadaten.
    """
    
    def __init__(self, csv_path: str = None):
        """
        Args:
            csv_path: Pfad zur Watchlist-CSV. Falls None, nutzt default aus config.
        """
        self.csv_path = csv_path or os.path.join(
            os.path.dirname(__file__), 
            config.WATCHLIST_CSV_PATH
        )
        self.watchlist_df = None
        self.load_watchlist()
    
    def load_watchlist(self) -> bool:
        """
        Lädt Watchlist aus CSV-Datei.
        
        Returns:
            True wenn erfolgreich geladen
        """
        try:
            if not os.path.exists(self.csv_path):
                logger.warning(f"Watchlist-CSV nicht gefunden: {self.csv_path}")
                logger.info("Erstelle Standard-Watchlist...")
                self._create_default_watchlist()
            
            self.watchlist_df = pd.read_csv(self.csv_path)
            
            # Validiere erforderliche Spalten
            required_columns = ['symbol', 'enabled']
            missing = [col for col in required_columns if col not in self.watchlist_df.columns]
            
            if missing:
                logger.error(f"Fehlende Spalten in Watchlist: {missing}")
                return False
            
            # Konvertiere enabled zu bool
            self.watchlist_df['enabled'] = self.watchlist_df['enabled'].astype(bool)
            
            logger.info(f"Watchlist geladen: {len(self.watchlist_df)} Symbole aus {self.csv_path}")
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Laden der Watchlist: {e}")
            return False
    
    def _create_default_watchlist(self):
        """Erstellt eine Standard-Watchlist-Datei."""
        default_data = {
            'symbol': config.WATCHLIST_STOCKS,
            'market_cap': [0] * len(config.WATCHLIST_STOCKS),
            'avg_volume': [0] * len(config.WATCHLIST_STOCKS),
            'sector': [''] * len(config.WATCHLIST_STOCKS),
            'pe_ratio': [0.0] * len(config.WATCHLIST_STOCKS),
            'fcf': [0.0] * len(config.WATCHLIST_STOCKS),
            'enabled': [True] * len(config.WATCHLIST_STOCKS),
            'notes': [''] * len(config.WATCHLIST_STOCKS)
        }
        
        df = pd.DataFrame(default_data)
        
        # Erstelle Verzeichnis falls nicht vorhanden
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        
        df.to_csv(self.csv_path, index=False)
        logger.info(f"Standard-Watchlist erstellt: {self.csv_path}")
    
    def get_active_symbols(self) -> List[str]:
        """
        Gibt Liste der aktiven (enabled=True) Symbole zurück.
        
        Returns:
            Liste von Symbol-Strings
        """
        if self.watchlist_df is None:
            return []
        
        active = self.watchlist_df[self.watchlist_df['enabled'] == True]['symbol'].tolist()
        return active
    
    def get_symbol_metadata(self, symbol: str) -> Optional[Dict]:
        """
        Gibt Metadaten für ein Symbol zurück.
        
        Args:
            symbol: Ticker Symbol
            
        Returns:
            Dict mit Metadaten oder None
        """
        if self.watchlist_df is None:
            return None
        
        row = self.watchlist_df[self.watchlist_df['symbol'] == symbol]
        
        if row.empty:
            return None
        
        return row.iloc[0].to_dict()
    
    def update_symbol_metadata(self, symbol: str, updates: Dict) -> bool:
        """
        Aktualisiert Metadaten für ein Symbol.
        
        Args:
            symbol: Ticker Symbol
            updates: Dict mit zu aktualisierenden Feldern
            
        Returns:
            True wenn erfolgreich
        """
        try:
            if self.watchlist_df is None:
                return False
            
            # Finde Index des Symbols
            idx = self.watchlist_df[self.watchlist_df['symbol'] == symbol].index
            
            if len(idx) == 0:
                logger.warning(f"Symbol {symbol} nicht in Watchlist gefunden")
                return False
            
            # Aktualisiere Felder
            for key, value in updates.items():
                if key in self.watchlist_df.columns:
                    self.watchlist_df.loc[idx[0], key] = value
            
            # Speichere zurück in CSV
            self.save_watchlist()
            
            logger.debug(f"Metadaten aktualisiert für {symbol}: {updates}")
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren von {symbol}: {e}")
            return False
    
    def add_symbol(self, symbol: str, metadata: Optional[Dict] = None) -> bool:
        """
        Fügt neues Symbol zur Watchlist hinzu.
        
        Args:
            symbol: Ticker Symbol
            metadata: Optional Metadaten-Dict
            
        Returns:
            True wenn erfolgreich
        """
        try:
            if self.watchlist_df is None:
                self.watchlist_df = pd.DataFrame()
            
            # Prüfe ob Symbol bereits existiert
            if symbol in self.watchlist_df['symbol'].values:
                logger.warning(f"Symbol {symbol} bereits in Watchlist")
                return False
            
            # Erstelle neue Zeile mit Defaults
            new_row = {
                'symbol': symbol,
                'market_cap': 0,
                'avg_volume': 0,
                'sector': '',
                'pe_ratio': 0.0,
                'fcf': 0.0,
                'enabled': True,
                'notes': ''
            }
            
            # Überschreibe mit bereitgestellten Metadaten
            if metadata:
                new_row.update(metadata)
            
            # Füge hinzu
            self.watchlist_df = pd.concat([
                self.watchlist_df, 
                pd.DataFrame([new_row])
            ], ignore_index=True)
            
            # Speichere
            self.save_watchlist()
            
            logger.info(f"Symbol {symbol} zur Watchlist hinzugefügt")
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Hinzufügen von {symbol}: {e}")
            return False
    
    def remove_symbol(self, symbol: str) -> bool:
        """
        Entfernt Symbol aus Watchlist.
        
        Args:
            symbol: Ticker Symbol
            
        Returns:
            True wenn erfolgreich
        """
        try:
            if self.watchlist_df is None:
                return False
            
            initial_len = len(self.watchlist_df)
            self.watchlist_df = self.watchlist_df[self.watchlist_df['symbol'] != symbol]
            
            if len(self.watchlist_df) == initial_len:
                logger.warning(f"Symbol {symbol} nicht in Watchlist gefunden")
                return False
            
            self.save_watchlist()
            logger.info(f"Symbol {symbol} aus Watchlist entfernt")
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Entfernen von {symbol}: {e}")
            return False
    
    def enable_symbol(self, symbol: str, enabled: bool = True) -> bool:
        """
        Aktiviert/Deaktiviert Symbol.
        
        Args:
            symbol: Ticker Symbol
            enabled: True für aktivieren, False für deaktivieren
            
        Returns:
            True wenn erfolgreich
        """
        return self.update_symbol_metadata(symbol, {'enabled': enabled})
    
    def save_watchlist(self) -> bool:
        """
        Speichert aktuelle Watchlist zurück in CSV.
        
        Returns:
            True wenn erfolgreich
        """
        try:
            if self.watchlist_df is None:
                return False
            
            self.watchlist_df.to_csv(self.csv_path, index=False)
            logger.debug(f"Watchlist gespeichert: {self.csv_path}")
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Speichern der Watchlist: {e}")
            return False
    
    def get_symbols_by_sector(self, sector: str) -> List[str]:
        """Gibt aktive Symbole eines bestimmten Sektors zurück."""
        if self.watchlist_df is None:
            return []
        
        filtered = self.watchlist_df[
            (self.watchlist_df['enabled'] == True) & 
            (self.watchlist_df['sector'] == sector)
        ]
        
        return filtered['symbol'].tolist()
    
    def get_symbols_by_filter(self, min_market_cap: Optional[float] = None,
                             min_volume: Optional[float] = None) -> List[str]:
        """
        Gibt Symbole zurück, die Filter-Kriterien erfüllen.
        
        Args:
            min_market_cap: Minimum Marktkapitalisierung
            min_volume: Minimum durchschnittliches Volumen
            
        Returns:
            Liste von Symbolen
        """
        if self.watchlist_df is None:
            return []
        
        filtered = self.watchlist_df[self.watchlist_df['enabled'] == True].copy()
        
        if min_market_cap is not None:
            filtered = filtered[filtered['market_cap'] >= min_market_cap]
        
        if min_volume is not None:
            filtered = filtered[filtered['avg_volume'] >= min_volume]
        
        return filtered['symbol'].tolist()
    
    def print_summary(self):
        """Gibt Zusammenfassung der Watchlist aus."""
        if self.watchlist_df is None:
            print("Keine Watchlist geladen")
            return
        
        print("\n" + "="*70)
        print(" WATCHLIST SUMMARY")
        print("="*70)
        print(f" Gesamt Symbole:    {len(self.watchlist_df)}")
        print(f" Aktive Symbole:    {len(self.watchlist_df[self.watchlist_df['enabled']])}")
        print(f" Inaktive Symbole:  {len(self.watchlist_df[~self.watchlist_df['enabled']])}")
        
        if 'sector' in self.watchlist_df.columns:
            sectors = self.watchlist_df['sector'].value_counts()
            print(f"\n Sektoren:")
            for sector, count in sectors.items():
                if sector:
                    print(f"   {sector:25s}: {count}")
        
        print("="*70)
        print("\n Aktive Symbole:")
        active = self.watchlist_df[self.watchlist_df['enabled']]
        for _, row in active.iterrows():
            mkt_cap = f"${row['market_cap']/1e9:.1f}B" if row['market_cap'] > 0 else "N/A"
            print(f"   {row['symbol']:6s} | {row.get('sector', 'N/A'):20s} | {mkt_cap:10s}")
        print()


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    
    wl = WatchlistManager()
    wl.print_summary()
    
    print("\nAktive Symbole:", wl.get_active_symbols())
    
    # Test: Symbol-Metadaten abrufen
    meta = wl.get_symbol_metadata('AAPL')
    if meta:
        print(f"\nAAPL Metadaten: {meta}")
