"""
Options-Scanner für konträre 52-Wochen-Extrem-Strategie.
Identifiziert Long Put (Short) und Long Call (Long) Kandidaten.
"""

import logging
import time
import os
import sys
import signal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
from pytz import timezone
from dotenv import load_dotenv

# Lade Environment Variables
load_dotenv(override=True)

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

import config
import options_config as opt_config
from tws_bot.data.database import DatabaseManager
from tws_bot.notifications.pushover import PushoverNotifier
from tws_bot.api.tws_connector import TWSConnector

logger = logging.getLogger(__name__)


class OptionsScanner(EWrapper, EClient):
    """Scanner für konträre Options-Strategien basierend auf 52-Wochen-Extrema."""
    
    def __init__(self, host: str = config.IB_HOST, port: int = config.IB_PORT, 
                 client_id: int = 2):  # Andere Client-ID als Aktien-Scanner
        EClient.__init__(self, self)
        EWrapper.__init__(self)
        
        self.host = host
        self.port = port
        self.client_id = client_id
        
        self.db = DatabaseManager()
        self.notifier = PushoverNotifier()
        
        # Watchlist (wird dynamisch gefiltert)
        self.watchlist = config.WATCHLIST_STOCKS
        
        # Lade Portfolio-Daten für Covered Calls
        self.portfolio_data = self._load_portfolio_data()
        
        # Erweitere Watchlist um Portfolio-Symbole für Covered Calls
        portfolio_symbols = set(self.portfolio_data.keys())
        watchlist_symbols = set(self.watchlist)
        self.watchlist = list(watchlist_symbols.union(portfolio_symbols))
        
        logger.info(f"Watchlist erweitert: {len(self.watchlist)} Symbole ({len(portfolio_symbols)} aus Portfolio)")
        
        # Lade Earnings-Daten intelligent:
        # 1. Portfolio-Symbole immer laden (für Covered Calls)
        # 2. Watchlist-Symbole nur wenn Rate-Limits erlauben
        self.earnings_data = self._load_earnings_data_smart()
        
        self.connected = False
        self.next_valid_order_id = None
        
        # Request Management
        self.request_id_counter = 1000  # Start bei 1000 um Konflikte zu vermeiden
        self.pending_requests: Dict[int, Dict] = {}
        
        # Daten-Cache
        self.historical_data_cache: Dict[str, pd.DataFrame] = {}
        self.historical_data_last_update: Dict[str, datetime] = {}  # Timestamp des letzten Updates
        self.fundamental_data_cache: Dict[str, Dict] = {}
        self.options_chain_cache: Dict[str, List] = {}
        
        # Aktive Positionen
        self.active_positions: Dict[str, Dict] = {}
        
        self.running = False
        
        logger.info(f"Options-Scanner initialisiert: {host}:{port} (Client ID: {client_id})")
    
    def _load_portfolio_data(self) -> Dict[str, Dict]:
        """
        Lädt Portfolio-Daten von TWS für Covered Call Strategie.
        
        Returns:
            Dict mit Symbol -> {'quantity': int, 'avg_cost': float, ...}
        """
        try:
            logger.info("Lade Portfolio-Daten für Covered Calls...")
            tws_connector = TWSConnector()
            
            if tws_connector.connect_to_tws():
                portfolio_data = tws_connector.get_portfolio_data()
                tws_connector.disconnect()
                
                # Filtere nur Aktien-Positionen (keine Optionen)
                stock_positions = {}
                positions_list = portfolio_data.get('positions', [])
                
                for pos in positions_list:
                    symbol = pos.get('symbol', '')
                    position_qty = pos.get('position', 0)
                    sec_type = pos.get('secType', 'STK')  # Annahme: Default STK wenn nicht angegeben
                    
                    if sec_type == 'STK' and position_qty > 0:
                        avg_cost = pos.get('avgCost', 0)
                        unrealized_pnl = pos.get('unrealizedPNL', 0)
                        market_price = pos.get('marketPrice', 0)
                        is_approximation = False
                        
                        # Fallback: Wenn avgCost 0 ist, berechne aus marketPrice und unrealizedPNL
                        if avg_cost == 0 and market_price > 0 and position_qty > 0:
                            # Einstandspreis = Aktueller Preis - (Unrealized P&L / Anzahl Aktien)
                            avg_cost = market_price - (unrealized_pnl / position_qty)
                            is_approximation = True
                            logger.warning(f"[WARNUNG] {symbol}: avgCost war 0, berechne aus P&L ${avg_cost:.2f} (Preis: ${market_price:.2f}, P&L: ${unrealized_pnl:.2f})")
                        
                        logger.info(f"[INFO] Portfolio Position: {symbol} - Qty: {position_qty}, avgCost: ${avg_cost:.2f} {'(APPROX)' if is_approximation else ''}")
                        stock_positions[symbol] = {
                            'quantity': position_qty,
                            'avg_cost': avg_cost,
                            'market_value': pos.get('marketValue', 0),
                            'unrealized_pnl': unrealized_pnl,
                            'is_approximation': is_approximation
                        }
                
                logger.info(f"Portfolio geladen: {len(stock_positions)} Aktien-Positionen")
                return stock_positions
            else:
                logger.warning("TWS Verbindung für Portfolio-Daten fehlgeschlagen")
                return {}
                
        except Exception as e:
            logger.error(f"Fehler beim Laden von Portfolio-Daten: {e}")
            return {}
    
    def _load_earnings_data_smart(self) -> Dict[str, Dict]:
        """
        Intelligentes Laden von Earnings-Daten mit Bulk-API-Call.
        
        Strategie:
        1. Einmalig alle Earnings-Daten von Alpha Vantage laden (falls nicht schon getan)
        2. Alle benötigten Symbole aus Datenbank holen
        3. Fehlende Symbole mit Simulation auffüllen
        
        Returns:
            Dict mit Symbol -> earnings data
        """
        earnings_data = {}
        
        # 1. Bulk-Earnings-Kalender laden (falls noch nicht getan oder alt)
        bulk_loaded = self._load_earnings_calendar_bulk()
        
        # 2. Alle benötigten Symbole aus Datenbank holen
        all_symbols = list(set(self.portfolio_data.keys()) | set(self.watchlist))
        
        logger.info(f"Lade Earnings-Daten für {len(all_symbols)} Symbole aus Datenbank...")
        
        for symbol in all_symbols:
            # Versuche Daten aus DB zu holen
            cached_data = self.db.get_earnings_date(symbol)
            
            if cached_data and cached_data.get('earnings_date'):
                earnings_date = cached_data['earnings_date']
                days_until = (earnings_date - datetime.now()).days
                is_earnings_week = days_until <= 7 and days_until >= -1
                
                earnings_data[symbol] = {
                    'earnings_date': earnings_date,
                    'days_until': days_until,
                    'is_earnings_week': is_earnings_week,
                    'status': 'cached' if bulk_loaded else 'alpha_vantage'
                }
            else:
                # Fallback auf Simulation
                earnings_data[symbol] = self._simulate_earnings_date(symbol)
        
        # Statistiken loggen
        cached = len([s for s in earnings_data.values() if s.get('status') == 'cached'])
        simulated = len([s for s in earnings_data.values() if s.get('status') in ['simulated', 'simulated_fallback']])
        
        logger.info(f"Earnings-Daten geladen: {len(earnings_data)} Symbole "
                   f"({cached} gecached, {simulated} simuliert)")
        
        return earnings_data
    
    def _ensure_earnings_data(self, symbol: str) -> None:
        """
        Stellt sicher, dass Earnings-Daten für ein Symbol verfügbar sind.
        Lazy loading für Symbole außerhalb des Portfolios.
        
        Args:
            symbol: Das Symbol für das Earnings-Daten benötigt werden
        """
        if symbol in self.earnings_data:
            return  # Bereits geladen
        
        # Lazy loading für dieses Symbol
        logger.debug(f"Lade Earnings-Daten lazy für {symbol}...")
        
        earnings_info = self._fetch_alpha_vantage_earnings(symbol)
        if earnings_info and earnings_info.get('earnings_date'):
            earnings_date = earnings_info['earnings_date']
            days_until = (earnings_date - datetime.now()).days
            is_earnings_week = days_until <= 7 and days_until >= -1
            
            self.earnings_data[symbol] = {
                'earnings_date': earnings_date,
                'days_until': days_until,
                'is_earnings_week': is_earnings_week,
                'status': 'lazy_loaded'
            }
        else:
            # Fallback: Simuliere Earnings
            self.earnings_data[symbol] = self._simulate_earnings_date(symbol)
    
    def _load_earnings_calendar_bulk(self) -> bool:
        """
        Lädt alle erwarteten Earnings-Daten auf einmal von Alpha Vantage EARNINGS_CALENDAR.
        
        Diese Methode macht einen einzigen API-Call für alle erwarteten Earnings
        in den nächsten 12 Monaten und speichert sie in der Datenbank.
        
        Wird nur einmal pro Tag ausgeführt (Cache-Check).
        
        Returns:
            True wenn erfolgreich, False bei Fehler
        """
        try:
            # Prüfe, ob wir heute schon Bulk-Daten geladen haben
            today = datetime.now().date()
            cache_key = f"bulk_earnings_loaded_{today.isoformat()}"
            
            # Einfache In-Memory Cache-Prüfung (nicht perfekt, aber für diesen Zweck OK)
            if hasattr(self, '_bulk_cache_date') and self._bulk_cache_date == today:
                logger.debug("[CACHE] Bulk-Earnings-Daten bereits heute geladen")
                return True
            
            import requests
            import csv
            import io
            
            # Alpha Vantage API Key aus Config laden
            api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "")
            if not api_key:
                logger.warning("Alpha Vantage API Key nicht konfiguriert - verwende Simulation")
                return False
            
            logger.info("[API] Lade Earnings-Kalender für alle Symbole (12 Monate) von Alpha Vantage")
            
            # EARNINGS_CALENDAR für alle Symbole (ohne symbol Parameter)
            url = f"https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&horizon=12month&apikey={api_key}"
            
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            # Parse CSV Response
            csv_content = response.content.decode('utf-8')
            
            # Prüfe auf Fehlermeldungen
            if 'Information' in csv_content or 'I,n,f,o,r,m,a' in csv_content:
                logger.warning("[RATE LIMIT] Alpha Vantage Information Nachricht - Rate-Limit erreicht")
                return False
            
            csv_reader = csv.DictReader(io.StringIO(csv_content))
            
            earnings_count = 0
            now = datetime.now()
            
            for row in csv_reader:
                symbol = row.get('symbol', '').strip()
                report_date_str = row.get('reportDate', '')
                
                if symbol and report_date_str:
                    try:
                        report_date = datetime.strptime(report_date_str, '%Y-%m-%d')
                        
                        # Nur zukünftige Earnings speichern
                        if report_date > now:
                            self.db.save_earnings_date(symbol, report_date)
                            earnings_count += 1
                            
                    except ValueError:
                        continue
            
            # Cache-Flag setzen
            self._bulk_cache_date = today
            
            logger.info(f"[OK] {earnings_count} zukünftige Earnings-Daten gespeichert")
            return True
            
        except Exception as e:
            logger.error(f"[FEHLER] Fehler beim Laden des Earnings-Kalenders: {e}")
            return False
    
    def _simulate_earnings_date(self, symbol: str) -> Dict:
        """
        Simuliert Earnings-Daten als Fallback wenn keine API verfügbar ist.
        
        In Produktion: Verwende Alpha Vantage, Financial Modeling Prep oder IEX Cloud API.
        Diese Simulation basiert auf typischen Quartalsberichten (alle 3 Monate).
        
        Args:
            symbol: Ticker Symbol
            
        Returns:
            Simulierte Earnings-Daten
        """
        # Simuliere: Earnings alle 3 Monate, zufälliger Tag im Monat
        import random
        now = datetime.now()
        
        # Finde nächsten simulierten Earnings-Termin (alle 3 Monate)
        months_until_next = 3 - (now.month % 3)
        if months_until_next == 0:
            months_until_next = 3
            
        next_earnings = now.replace(day=1) + timedelta(days=32)
        next_earnings = next_earnings.replace(day=random.randint(1, 28))
        
        days_until = (next_earnings - now).days
        
        return {
            'earnings_date': next_earnings,
            'days_until': days_until,
            'is_earnings_week': days_until <= 7 and days_until >= -1,
            'status': 'simulated_fallback'
        }
    
    def _is_earnings_risk_period(self, symbol: str) -> bool:
        """
        Prüft, ob ein Symbol in einer risikoreichen Earnings-Periode ist.
        
        Returns:
            True wenn Signal blockiert werden sollte (zu nah an Earnings)
        """
        if symbol not in self.earnings_data:
            return False  # Keine Daten = kein Risiko bekannt
        
        earnings_info = self.earnings_data[symbol]
        return earnings_info.get('is_earnings_week', False)
    
    # ========================================================================
    # TWS CALLBACKS
    # ========================================================================
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        """Error Callback von TWS."""
        if errorCode in [2104, 2106, 2158]:
            logger.info(f"TWS Info [{errorCode}]: {errorString}")
        elif errorCode == 502:
            logger.error(f"[FEHLER] TWS nicht verbunden [{errorCode}]: {errorString}")
            self.connected = False
        else:
            logger.warning(f"TWS Error [{errorCode}] Req {reqId}: {errorString}")
    
    def nextValidId(self, orderId: int):
        """Callback: Next valid order ID."""
        self.next_valid_order_id = orderId
        self.connected = True
        logger.info(f"[OK] TWS verbunden - Next Order ID: {orderId}")
    
    def historicalData(self, reqId: int, bar):
        """Callback: Historische Bar-Daten."""
        if reqId not in self.pending_requests:
            return
        
        request_data = self.pending_requests[reqId]
        symbol = request_data.get('symbol')
        
        if 'data' not in request_data:
            request_data['data'] = []
        
        request_data['data'].append({
            'date': bar.date,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume
        })
    
    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Callback: Ende der historischen Daten."""
        if reqId not in self.pending_requests:
            return
        
        request_data = self.pending_requests[reqId]
        symbol = request_data.get('symbol')
        is_incremental = request_data.get('incremental', False)
        
        if 'data' in request_data and request_data['data']:
            df_new = pd.DataFrame(request_data['data'])
            df_new['date'] = pd.to_datetime(df_new['date'])
            df_new = df_new.sort_values('date').reset_index(drop=True)
            
            if is_incremental and symbol in self.historical_data_cache:
                # Inkrementeller Update: Neue Daten anhängen
                df_old = self.historical_data_cache[symbol]
                df_combined = pd.concat([df_old, df_new], ignore_index=True)
                # Entferne Duplikate (behalte neueste Werte)
                df_combined = df_combined.drop_duplicates(subset=['date'], keep='last')
                df_combined = df_combined.sort_values('date').reset_index(drop=True)
                self.historical_data_cache[symbol] = df_combined
                logger.info(f"[OK] {symbol}: +{len(df_new)} neue Bars (gesamt: {len(df_combined)})")
            else:
                # Vollständiger Load beim ersten Mal
                self.historical_data_cache[symbol] = df_new
                logger.info(f"[OK] {symbol}: {len(df_new)} Bars geladen (vollständig)")
            
            # Update Timestamp
            self.historical_data_last_update[symbol] = datetime.now()
        
        self.pending_requests[reqId]['completed'] = True
    
    def fundamentalData(self, reqId: int, data: str):
        """Callback: Fundamentale Daten (XML)."""
        if reqId not in self.pending_requests:
            return
        
        request_data = self.pending_requests[reqId]
        symbol = request_data.get('symbol')
        
        # Parse XML für P/E, FCF, Market Cap
        fundamental_data = self._parse_fundamental_data(data)
        self.fundamental_data_cache[symbol] = fundamental_data
        
        # Speichere in DB für Caching
        self.db.save_fundamental_data(symbol, fundamental_data)
        
        logger.info(f"[OK] {symbol}: Fundamentaldaten geladen")
        self.pending_requests[reqId]['completed'] = True
    
    def contractDetails(self, reqId: int, contractDetails):
        """Callback: Contract Details (für Options)."""
        if reqId not in self.pending_requests:
            return
        
        request_data = self.pending_requests[reqId]
        
        if 'contracts' not in request_data:
            request_data['contracts'] = []
        
        # Speichere relevante Contract-Infos
        contract = contractDetails.contract
        request_data['contracts'].append({
            'symbol': contract.symbol,
            'strike': contract.strike,
            'right': contract.right,
            'expiry': contract.lastTradeDateOrContractMonth,
            'multiplier': contract.multiplier,
            'conId': contract.conId
        })
    
    def contractDetailsEnd(self, reqId: int):
        """Callback: Ende der Contract Details."""
        if reqId not in self.pending_requests:
            return
        
        self.pending_requests[reqId]['completed'] = True
        
        contracts = self.pending_requests[reqId].get('contracts', [])
        symbol = self.pending_requests[reqId].get('symbol')
        
        logger.info(f"[OK] {symbol}: {len(contracts)} Options-Contracts geladen")
    
    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        """Callback: Market Data - Prices."""
        if reqId not in self.pending_requests:
            return
        
        request_data = self.pending_requests[reqId]
        
        # tickType 4 = Last Price, 1 = Bid, 2 = Ask
        if tickType == 4:  # Last
            request_data['last_price'] = price
        elif tickType == 1:  # Bid
            request_data['bid'] = price
        elif tickType == 2:  # Ask
            request_data['ask'] = price
    
    def tickOptionComputation(self, reqId: int, tickType: int, tickAttrib: int,
                              impliedVol: float, delta: float, optPrice: float,
                              pvDividend: float, gamma: float, vega: float,
                              theta: float, undPrice: float):
        """Callback: Options Greeks und IV."""
        if reqId not in self.pending_requests:
            return
        
        request_data = self.pending_requests[reqId]
        
        # Speichere Greeks und IV
        if 'greeks' not in request_data:
            request_data['greeks'] = {}
        
        request_data['greeks'].update({
            'implied_volatility': impliedVol if impliedVol != -1 else None,
            'delta': delta if delta != -2 else None,
            'gamma': gamma if gamma != -2 else None,
            'vega': vega if vega != -2 else None,
            'theta': theta if theta != -2 else None,
            'option_price': optPrice if optPrice != -1 else None,
            'underlying_price': undPrice if undPrice != -1 else None
        })
    
    # ========================================================================
    # HELPER FUNCTIONS
    # ========================================================================
    
    def _get_next_request_id(self) -> int:
        """Generiert neue Request-ID."""
        req_id = self.request_id_counter
        self.request_id_counter += 1
        return req_id
    
    def _parse_fundamental_data(self, xml_data: str) -> Dict:
        """Parst fundamentale Daten aus TWS ReportSnapshot XML."""
        import xml.etree.ElementTree as ET
        
        fundamental = {
            'pe_ratio': None,
            'fcf': None,
            'market_cap': None,
            'sector': None,
            'avg_volume': None
        }
        
        try:
            root = ET.fromstring(xml_data)
            
            # Parse ReportSnapshot Ratios
            # P/E Ratio: <Ratio FieldName="PEEXCLXOR">
            pe_elem = root.find(".//Ratio[@FieldName='PEEXCLXOR']")
            if pe_elem is not None and pe_elem.text:
                fundamental['pe_ratio'] = float(pe_elem.text)
            
            # Market Cap: <Ratio FieldName="MKTCAP"> (in Millionen USD)
            mktcap_elem = root.find(".//Ratio[@FieldName='MKTCAP']")
            if mktcap_elem is not None and mktcap_elem.text:
                fundamental['market_cap'] = float(mktcap_elem.text) * 1_000_000  # Konvertiere zu USD
            
            # Free Cash Flow: Verwende Cash Flow per Share * Shares Outstanding
            # <Ratio FieldName="TTMCFSHR"> (TTM Cash Flow per Share)
            cfshr_elem = root.find(".//Ratio[@FieldName='TTMCFSHR']")
            shares_elem = root.find(".//SharesOut")
            if cfshr_elem is not None and shares_elem is not None:
                try:
                    cf_per_share = float(cfshr_elem.text)
                    shares_out = float(shares_elem.text)
                    fundamental['fcf'] = cf_per_share * shares_out  # Approximation
                except (ValueError, AttributeError):
                    pass
            
            # Sector/Industry: <Industry type="TRBC"> Element
            sector_elem = root.find(".//Industry[@type='TRBC']")
            if sector_elem is not None and sector_elem.text:
                fundamental['sector'] = sector_elem.text.strip()
            
            # Average Volume: <Ratio FieldName="VOL10DAVG"> (10-day avg in millions)
            avgvol_elem = root.find(".//Ratio[@FieldName='VOL10DAVG']")
            if avgvol_elem is not None and avgvol_elem.text:
                fundamental['avg_volume'] = float(avgvol_elem.text) * 1_000_000  # Konvertiere zu Aktien
            
        except Exception as e:
            logger.error(f"[FEHLER] Fundamental-Parsing: {e}", exc_info=True)
        
        return fundamental
    
    def _is_trading_hours(self) -> bool:
        """Prüft ob aktuell Handelszeiten sind (EST)."""
        # Wenn Handelszeiten-Check deaktiviert, immer True zurückgeben
        if not opt_config.ENFORCE_TRADING_HOURS:
            return True
        
        est = timezone('US/Eastern')
        now = datetime.now(est)
        
        start_time = now.replace(
            hour=opt_config.TRADING_START_HOUR,
            minute=opt_config.TRADING_START_MINUTE,
            second=0
        )
        end_time = now.replace(
            hour=opt_config.TRADING_END_HOUR,
            minute=opt_config.TRADING_END_MINUTE,
            second=0
        )
        
        return start_time <= now <= end_time
    
    def _create_stock_contract(self, symbol: str) -> Contract:
        """Erstellt Stock Contract für TWS."""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract
    
    def _create_option_contract(self, symbol: str, strike: float, 
                                right: str, expiry: str) -> Contract:
        """Erstellt Options Contract für TWS."""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        contract.strike = strike
        contract.right = right  # "C" oder "P"
        contract.lastTradeDateOrContractMonth = expiry  # Format: YYYYMMDD
        contract.multiplier = "100"
        return contract
    
    # ========================================================================
    # KOSTENBERECHNUNG
    # ========================================================================
    
    def calculate_strategy_costs(self, strategy_type: str, quantity: int = 1, 
                                net_premium: float = 0.0) -> Dict[str, float]:
        """
        Berechnet die Kommissionskosten für verschiedene Optionsstrategien.
        
        Args:
            strategy_type: "LONG_PUT", "LONG_CALL", "BEAR_CALL_SPREAD", etc.
            quantity: Anzahl der Kontrakte (default: 1)
            net_premium: Netto-Prämie des Spreads (für Spread-Strategien)
            
        Returns:
            Dict mit 'commission', 'total_cost', 'breakeven_adjusted'
        """
        from tws_bot.config.settings import (
            OPTIONS_COMMISSION_PER_CONTRACT, 
            SPREAD_COMMISSION_MULTIPLIER
        )
        
        base_commission = OPTIONS_COMMISSION_PER_CONTRACT
        
        if strategy_type in ["LONG_PUT", "LONG_CALL"]:
            # Single Option Position: 1 Kontrakt = 1 Kommission
            commission = base_commission * quantity
            
        elif strategy_type == "BEAR_CALL_SPREAD":
            # Spread: 2 Beine (Short Call + Long Call) = 2 Kommissionen
            commission = base_commission * SPREAD_COMMISSION_MULTIPLIER * 2 * quantity
            
        elif strategy_type == "BULL_PUT_SPREAD":
            # Spread: 2 Beine (Short Put + Long Put) = 2 Kommissionen  
            commission = base_commission * SPREAD_COMMISSION_MULTIPLIER * 2 * quantity
            
        elif strategy_type == "IRON_CONDOR":
            # 4 Beine (2 Short + 2 Long) = 4 Kommissionen
            commission = base_commission * SPREAD_COMMISSION_MULTIPLIER * 4 * quantity
            
        elif strategy_type == "SHORT_PUT":
            # Single Short Option Position: 1 Kontrakt
            commission = base_commission * quantity
            
        elif strategy_type == "COVERED_CALL":
            # Covered Call: Verkauf von 1 Call (Short Position)
            commission = base_commission * quantity
            
        else:
            # Fallback für unbekannte Strategien
            commission = base_commission * quantity
            logger.warning(f"[WARNUNG] Unbekannte Strategie {strategy_type} - verwende Single-Option Kosten")
        
        # Gesamtkosten = Kommission (bereits in €)
        total_cost = commission
        
        # Break-even angepasst um Kommission
        # Bei Long-Positionen erhöht sich Break-even um Kommission
        # Bei Short-Positionen/Spreads wird Netto-Prämie um Kommission reduziert
        if strategy_type in ["LONG_PUT", "LONG_CALL"]:
            # Long Option: Höherer Break-even
            breakeven_adjusted = net_premium + (commission / (quantity * 100))  # Pro Aktie
        else:
            # Spreads: Niedrigere Netto-Prämie
            breakeven_adjusted = net_premium - (commission / (quantity * 100))  # Pro Aktie
        
        return {
            'commission': commission,
            'total_cost': total_cost,
            'breakeven_adjusted': breakeven_adjusted,
            'cost_per_contract': commission / quantity if quantity > 0 else 0
        }
    
    def calculate_strategy_profitability(self, strategy_type: str, signal_data: Dict) -> Dict[str, float]:
        """
        Berechnet Rentabilität einer Strategie inkl. Kommissionen und Ausstiegsszenarien.
        
        Args:
            strategy_type: Typ der Strategie
            signal_data: Signal-Daten aus check_*_setup()
            
        Returns:
            Dict mit Rentabilitäts-Kennzahlen und Ausstiegsszenarien
        """
        # Basis-Daten aus Signal
        max_profit = signal_data.get('max_profit', 0)
        max_risk = signal_data.get('max_risk', 0)
        net_premium = signal_data.get('net_premium', 0)
        quantity = signal_data.get('quantity', 1)
        
        # Kosten berechnen
        costs = self.calculate_strategy_costs(strategy_type, quantity, net_premium)
        
        # Ausstiegsszenarien berechnen
        exit_scenarios = self.calculate_exit_scenarios(strategy_type, signal_data)
        
        # Angepasste Kennzahlen
        adjusted_max_profit = max_profit - costs['commission']
        adjusted_net_premium = net_premium - costs['commission']
        
        # Risk-Reward Ratio (bereinigt)
        if max_risk > 0:
            rr_ratio = adjusted_max_profit / max_risk
        else:
            rr_ratio = 0
        
        # Profitabilität in %
        if max_risk > 0:
            profitability_pct = (adjusted_max_profit / max_risk) * 100
        else:
            profitability_pct = 0
        
        # Break-even Wahrscheinlichkeit (grob geschätzt)
        # Annahme: 30% Chance auf Max Profit, 70% Chance auf Max Loss
        expected_value = (adjusted_max_profit * 0.3) + (-max_risk * 0.7)
        
        # Empfehlung basierend auf Szenarien
        recommendation = self._get_profitability_recommendation(exit_scenarios, strategy_type)
        
        return {
            'adjusted_max_profit': adjusted_max_profit,
            'adjusted_net_premium': adjusted_net_premium,
            'rr_ratio': rr_ratio,
            'profitability_pct': profitability_pct,
            'expected_value': expected_value,
            'costs': costs,
            'exit_scenarios': exit_scenarios,
            'recommendation': recommendation
        }
    
    def calculate_exit_scenarios(self, strategy_type: str, signal_data: Dict) -> Dict[str, Dict]:
        """
        Berechnet verschiedene Ausstiegsszenarien inkl. aller Kosten.
        
        Args:
            strategy_type: Typ der Strategie
            signal_data: Signal-Daten aus check_*_setup()
            
        Returns:
            Dict mit Szenarien: 'expires_worthless', 'early_profit_exit', 'early_loss_exit'
        """
        quantity = signal_data.get('quantity', 1)
        entry_premium = signal_data.get('net_premium', signal_data.get('premium', 0))
        
        # Einstiegskosten
        entry_costs = self.calculate_strategy_costs(strategy_type, quantity, entry_premium)
        
        scenarios = {}
        
        # Szenario 1: Option verfällt wertlos (nur Einstiegskosten)
        if strategy_type in ['SHORT_PUT', 'BEAR_CALL_SPREAD']:
            # Bei Short-Positionen: wertlos verfallen = Max Profit
            scenarios['expires_worthless'] = {
                'description': 'Option verfällt wertlos',
                'total_costs': entry_costs['commission'],
                'net_result': entry_premium - entry_costs['commission'],
                'profitability': 'Max Profit'
            }
        else:
            # Bei Long-Positionen: wertlos verfallen = Max Loss
            max_loss = signal_data.get('max_risk', abs(entry_premium))
            scenarios['expires_worthless'] = {
                'description': 'Option verfällt wertlos',
                'total_costs': entry_costs['commission'],
                'net_result': -max_loss - entry_costs['commission'],
                'profitability': 'Max Loss'
            }
        
        # Szenario 2: Vorzeitiger Ausstieg mit Gewinn (50% des Max Profits)
        exit_profit = 0
        if strategy_type in ['SHORT_PUT', 'BEAR_CALL_SPREAD']:
            exit_profit = entry_premium * 0.5  # 50% Gewinn
        else:
            exit_profit = entry_premium * 1.5  # 50% über Break-even
        
        exit_costs = self.calculate_strategy_costs(strategy_type, quantity, exit_profit)
        total_costs_profit = entry_costs['commission'] + exit_costs['commission']
        net_profit = exit_profit - total_costs_profit
        
        scenarios['early_profit_exit'] = {
            'description': f'Vorzeitiger Ausstieg mit {exit_profit:.2f}€ Gewinn',
            'total_costs': total_costs_profit,
            'net_result': net_profit,
            'profitability': f'{"Profit" if net_profit > 0 else "Loss"} ({net_profit:.2f}€)'
        }
        
        # Szenario 3: Vorzeitiger Ausstieg mit Verlust (50% Verlust)
        exit_loss = entry_premium * 0.3  # 70% Verlust
        exit_costs_loss = self.calculate_strategy_costs(strategy_type, quantity, exit_loss)
        total_costs_loss = entry_costs['commission'] + exit_costs_loss['commission']
        net_loss = exit_loss - total_costs_loss
        
        scenarios['early_loss_exit'] = {
            'description': f'Vorzeitiger Ausstieg mit {exit_loss:.2f}€ Verlust',
            'total_costs': total_costs_loss,
            'net_result': net_loss,
            'profitability': f'Loss ({net_loss:.2f}€)'
        }
        
        return scenarios
    
    def check_covered_call_setup(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Prüft Covered Call Setup (Verkauf von Calls auf eigene Aktien-Positionen).
        
        Strategie: Verkaufe Calls auf Aktien nahe dem 52W-Hoch
        - Konträre Erwartung: Aktien am Hoch fallen eher (Mean Reversion)
        - Zusätzliche Filter: Position muss profitabel sein (Preis > Einstandspreis)
        - Ziel: Prämie kassieren + Aktien mit Gewinn halten
        
        Covered Call = Long Stock + Short Call
        - Max Profit: Premium + (Strike - Einstandspreis) pro Aktie
        - Max Risk: Einstandspreis - Strike + Premium (wenn Aktie fällt)
        - Break-even: Einstandspreis - Premium
        
        Returns:
            Signal-Dict oder None
        """
        if len(df) == 0:
            return None
        
        # Stelle sicher, dass Earnings-Daten verfügbar sind (lazy loading)
        self._ensure_earnings_data(symbol)
        
        # Earnings-Risiko-Prüfung: Blockiere Signale während Earnings-Periode
        if self._is_earnings_risk_period(symbol):
            logger.info(f"[INFO] {symbol}: Covered Call Signal blockiert - Earnings-Periode")
            return None
        
        current_price = df.iloc[-1]['close']
        high_52w, low_52w = self.calculate_52w_extremes(df)
        
        # 1. Portfolio-Prüfung: Hat der User diese Aktie?
        if symbol not in self.portfolio_data:
            return None
        
        position = self.portfolio_data[symbol]
        owned_quantity = position.get('quantity', 0)
        avg_cost = position.get('avg_cost', 0)
        is_approximation = position.get('is_approximation', False)
        
        if owned_quantity < 100:  # Mindestens 1 Kontrakt (100 Aktien)
            logger.debug(f"[DEBUG] {symbol}: Nicht genügend Aktien ({owned_quantity} < 100)")
            return None
        
        # 1.5. Approximation prüfen - überspringe Positionen ohne echten avg_cost
        if is_approximation:
            logger.debug(f"[DEBUG] {symbol}: avg_cost ist Approximation, überspringe für Covered Calls")
            return None
        
        # 1.6. Profitabilität der Position prüfen
        if current_price <= avg_cost:
            logger.debug(f"[DEBUG] {symbol}: Position nicht profitabel (Preis: ${current_price:.2f} <= Einstand: ${avg_cost:.2f})")
            return None
        
        # 2. Technischer Trigger: Nahe 52W-Hoch (für Covered Calls geeignet)
        proximity_threshold = high_52w * (1 - opt_config.COVERED_CALL_PROXIMITY_TO_HIGH_PCT)
        if current_price < proximity_threshold:
            return None
        
        # 3. Fundamentale Prüfung: Nicht überbewertet
        if symbol not in self.fundamental_data_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Fundamentaldaten")
            return None
        
        fundamentals = self.fundamental_data_cache[symbol]
        pe_ratio = fundamentals.get('pe_ratio')
        sector = fundamentals.get('sector', 'Unknown')
        market_cap = fundamentals.get('market_cap')
        avg_volume = fundamentals.get('avg_volume')
        
        # Filter: Marktkapitalisierung und Volumen
        if market_cap and market_cap < opt_config.MIN_MARKET_CAP:
            return None
        
        if avg_volume and avg_volume < opt_config.MIN_AVG_VOLUME:
            return None
        
        if not pe_ratio or pe_ratio <= 0:
            return None
        
        sector_pe_median = self._get_sector_median_pe(sector)
        
        # Für Covered Calls: Nicht extrem überbewertet (aber höher als für Long Puts)
        if pe_ratio > sector_pe_median * opt_config.COVERED_CALL_PE_RATIO_MULTIPLIER:
            logger.debug(f"[DEBUG] {symbol}: Zu überbewertet für Covered Call (P/E {pe_ratio:.1f})")
            return None
        
        # 4. Finde passenden Call Strike
        call_strike = self.find_covered_call_strike(symbol, current_price, position)
        
        if not call_strike:
            return None
        
        # 5. IV Rank Prüfung (hohes IV für Prämieneinnahme)
        self.request_option_greeks(
            symbol,
            call_strike['strike'],
            'C',
            call_strike['expiry']
        )
        
        self.wait_for_requests(timeout=10)
        
        # Hole IV
        current_iv = None
        for req_data in self.pending_requests.values():
            if (req_data.get('symbol') == symbol and
                req_data.get('strike') == call_strike['strike']):
                greeks = req_data.get('greeks', {})
                current_iv = greeks.get('implied_volatility')
                break
        
        if current_iv is None:
            # Fallback
            if symbol in self.historical_data_cache:
                df_temp = self.historical_data_cache[symbol]
                returns = np.log(df_temp['close'] / df_temp['close'].shift(1))
                current_iv = returns.std() * np.sqrt(252) * 100
        
        if current_iv:
            iv_rank = self.calculate_iv_rank(symbol, current_iv)
        else:
            iv_rank = 50.0
        
        if iv_rank < opt_config.COVERED_CALL_MIN_IV_RANK:
            logger.debug(f"[DEBUG] {symbol}: IV Rank {iv_rank:.1f} < {opt_config.COVERED_CALL_MIN_IV_RANK}")
            return None
        
        # Berechne Rentabilität
        max_contracts = owned_quantity // 100  # Wie viele Kontrakte können verkauft werden
        premium_per_contract = call_strike['premium']
        
        # Max Profit: Premium + Upside bis Strike
        max_profit_per_contract = premium_per_contract + (call_strike['strike'] - position['avg_cost']) * 100
        max_risk_per_contract = (position['avg_cost'] - call_strike['strike']) * 100 + premium_per_contract
        
        # Kostenberechnung
        costs = self.calculate_strategy_costs('COVERED_CALL', max_contracts, premium_per_contract)
        profitability = self.calculate_strategy_profitability('COVERED_CALL', {
            'max_profit': max_profit_per_contract,
            'max_risk': max_risk_per_contract,
            'net_premium': premium_per_contract,
            'quantity': max_contracts
        })
        
        return {
            'type': 'COVERED_CALL',
            'symbol': symbol,
            'underlying_price': current_price,
            'high_52w': high_52w,
            'proximity_pct': ((current_price / high_52w) - 1) * 100,
            'pe_ratio': pe_ratio,
            'sector_pe': sector_pe_median,
            'owned_quantity': owned_quantity,
            'avg_cost': position['avg_cost'],
            'market_value': position['market_value'],
            'unrealized_pnl': position['unrealized_pnl'],
            'iv_rank': iv_rank,
            'call_strike': call_strike['strike'],
            'call_delta': call_strike['delta'],
            'premium_per_contract': premium_per_contract,
            'max_contracts': max_contracts,
            'max_profit_per_contract': max_profit_per_contract,
            'max_risk_per_contract': max_risk_per_contract,
            'recommended_expiry': call_strike['expiry'],
            'recommended_dte': call_strike['dte'],
            # Kosten & Rentabilität
            'commission': costs['commission'],
            'total_cost': costs['total_cost'],
            'adjusted_max_profit': profitability['adjusted_net_premium'],
            'rr_ratio': profitability['rr_ratio'],
            'profitability_pct': profitability['profitability_pct'],
            'expected_value': profitability['expected_value'],
            'exit_scenarios': profitability.get('exit_scenarios', {}),
            'recommendation': profitability.get('recommendation', ''),
            'timestamp': datetime.now()
        }
    
    def _get_profitability_recommendation(self, exit_scenarios: Dict[str, Dict], strategy_type: str) -> str:
        """
        Gibt eine Empfehlung basierend auf den Ausstiegsszenarien.
        
        Args:
            exit_scenarios: Die berechneten Ausstiegsszenarien
            strategy_type: Typ der Strategie
            
        Returns:
            String mit Empfehlung
        """
        worthless_result = exit_scenarios['expires_worthless']['net_result']
        profit_exit_result = exit_scenarios['early_profit_exit']['net_result']
        loss_exit_result = exit_scenarios['early_loss_exit']['net_result']
        
        # Für Short-Positionen ist wertlos verfallen das beste Szenario
        if strategy_type in ['SHORT_PUT', 'BEAR_CALL_SPREAD']:
            if worthless_result > 0:
                return "Empfohlen: Option sollte wertlos verfallen"
            elif profit_exit_result > 0:
                return "Alternativ: Vorzeitiger Ausstieg bei 50% Gewinn"
            else:
                return "Vorsicht: Hohe Kosten - nur bei hoher Erfolgswahrscheinlichkeit"
        
        # Für Long-Positionen ist vorzeitiger Ausstieg besser als wertlos verfallen
        else:
            if profit_exit_result > 0:
                return "Empfohlen: Vorzeitiger Ausstieg bei 50% Gewinn"
            elif worthless_result > loss_exit_result:
                return "Alternativ: Wertlos verfallen besser als Verlust-Ausstieg"
            else:
                return "Vorsicht: Hohe Kosten - nur bei starkem Bewegungsimpuls"
    
    # ========================================================================
    # TWS REQUEST FUNCTIONS
    # ========================================================================
    
    def request_historical_data(self, symbol: str, days: int = 252, incremental: bool = True):
        """
        Request historische Daten von TWS mit Smart-Update.
        
        Args:
            symbol: Ticker Symbol
            days: Anzahl Tage (default: 252 für 52 Wochen)
            incremental: Bei True nur neue Daten laden, bei False alles neu laden
        """
        req_id = self._get_next_request_id()
        contract = self._create_stock_contract(symbol)
        
        # Prüfe ob inkrementeller Update möglich
        actual_incremental = incremental and symbol in self.historical_data_cache
        
        if actual_incremental:
            # Nur die letzten 5 Tage laden (schnell!)
            days_to_load = 5
            logger.debug(f"Lade neue Daten für {symbol} ({days_to_load} Tage, inkrementell)...")
        else:
            # Vollständiger Load beim ersten Mal
            days_to_load = days
            logger.info(f"Lade historische Daten für {symbol} ({days_to_load} Tage, vollständig)...")
        
        self.pending_requests[req_id] = {
            'type': 'historical',
            'symbol': symbol,
            'completed': False,
            'incremental': actual_incremental
        }
        
        self.reqHistoricalData(
            req_id, contract, "", f"{days_to_load} D", "1 day",
            "TRADES", 1, 1, False, []
        )
    
    def request_fundamental_data(self, symbol: str):
        """
        Request Fundamentaldaten von TWS.
        
        Args:
            symbol: Ticker Symbol
        """
        # Prüfe zuerst Cache
        cached = self.db.get_fundamental_data(symbol, max_age_days=7)
        if cached:
            logger.info(f"[CACHE] {symbol}: Fundamentaldaten aus Cache")
            self.fundamental_data_cache[symbol] = cached
            return
        
        req_id = self._get_next_request_id()
        contract = self._create_stock_contract(symbol)
        
        self.pending_requests[req_id] = {
            'type': 'fundamental',
            'symbol': symbol,
            'completed': False
        }
        
        self.reqFundamentalData(req_id, contract, "ReportSnapshot", [])
        logger.info(f"Lade Fundamentaldaten für {symbol}...")
    
    def request_options_chain(self, symbol: str):
        """
        Request Options-Chain von TWS.
        
        Args:
            symbol: Ticker Symbol
        """
        req_id = self._get_next_request_id()
        
        self.pending_requests[req_id] = {
            'type': 'options_chain',
            'symbol': symbol,
            'completed': False
        }
        
        # Request Options-Parameter (Strikes, Expirations)
        self.reqSecDefOptParams(req_id, symbol, "", "STK", 0)
        logger.info(f"Lade Options-Chain für {symbol}...")
    
    def securityDefinitionOptionalParameter(self, reqId: int, exchange: str,
                                            underlyingConId: int, tradingClass: str,
                                            multiplier: str, expirations: set,
                                            strikes: set):
        """Callback: Options-Parameter."""
        if reqId not in self.pending_requests:
            return
        
        symbol = self.pending_requests[reqId].get('symbol')
        
        # Speichere verfügbare Strikes und Expirations
        self.options_chain_cache[symbol] = {
            'expirations': sorted(list(expirations)),
            'strikes': sorted(list(strikes)),
            'multiplier': multiplier,
            'exchange': exchange
        }
        
        logger.info(f"[OK] {symbol}: {len(expirations)} Expirations, {len(strikes)} Strikes")
        self.pending_requests[reqId]['completed'] = True
    
    def request_option_greeks(self, symbol: str, strike: float, right: str, expiry: str):
        """
        Request Greeks und IV für spezifische Option.
        
        Args:
            symbol: Underlying Symbol
            strike: Strike Price
            right: "C" oder "P"
            expiry: Expiration Date (YYYYMMDD)
        """
        req_id = self._get_next_request_id()
        contract = self._create_option_contract(symbol, strike, right, expiry)
        
        self.pending_requests[req_id] = {
            'type': 'option_greeks',
            'symbol': symbol,
            'strike': strike,
            'right': right,
            'expiry': expiry,
            'completed': False
        }
        
        # Request Market Data mit Generic Tick Types für Greeks
        self.reqMktData(req_id, contract, "106", False, False, [])
        # 106 = Option Volume and Open Interest
    
    def wait_for_requests(self, timeout: int = 30):
        """Wartet bis alle Requests completed sind."""
        start = time.time()
        
        while time.time() - start < timeout:
            incomplete = [req_id for req_id, data in self.pending_requests.items()
                         if not data.get('completed', False)]
            
            if not incomplete:
                break
            
            time.sleep(0.5)
        
        # Cleanup completed requests
        self.pending_requests = {
            req_id: data for req_id, data in self.pending_requests.items()
            if not data.get('completed', False)
        }
    
    # ========================================================================
    # 52-WOCHEN ANALYSE
    # ========================================================================
    
    def calculate_52w_extremes(self, df: pd.DataFrame) -> Tuple[float, float]:
        """
        Berechnet 52-Wochen-Hoch und -Tief.
        
        Args:
            df: DataFrame mit historischen Daten
            
        Returns:
            (52w_high, 52w_low)
        """
        if len(df) < opt_config.WEEKS_52_DAYS:
            logger.warning(f"[WARNUNG] Nicht genug Daten für 52W-Berechnung: {len(df)} Tage")
        
        high_52w = df['high'].max()
        low_52w = df['low'].min()
        
        return high_52w, low_52w
    
    def calculate_iv_rank(self, symbol: str, current_iv: float) -> float:
        """
        Berechnet IV Rank: Position der aktuellen IV im 52-Wochen-Bereich.
        
        Args:
            symbol: Ticker Symbol
            current_iv: Aktuelle implizite Volatilität
            
        Returns:
            IV Rank (0-100)
        """
        # Versuche IV-Historie aus DB zu laden
        iv_history = self.db.get_iv_history(symbol, days=252)
        
        if not iv_history.empty and 'implied_volatility' in iv_history.columns:
            # Nutze echte IV-Historie
            iv_values = iv_history['implied_volatility'].dropna()
            
            if len(iv_values) >= 20:  # Mindestens 20 Datenpunkte
                iv_min = iv_values.min()
                iv_max = iv_values.max()
                
                if iv_max > iv_min:
                    iv_rank = ((current_iv - iv_min) / (iv_max - iv_min)) * 100
                    
                    # Speichere aktuelle IV
                    today = datetime.now().strftime('%Y-%m-%d')
                    self.db.save_iv_data(symbol, today, current_iv, None)
                    
                    return iv_rank
        
        # Fallback: Nutze historische Volatilität als Proxy
        if symbol not in self.historical_data_cache:
            return 50.0
        
        df = self.historical_data_cache[symbol]
        
        # Berechne historische Volatilität (annualisiert)
        returns = np.log(df['close'] / df['close'].shift(1))
        hist_vol = returns.rolling(window=20).std() * np.sqrt(252) * 100
        
        if len(hist_vol) < 2:
            return 50.0
        
        iv_min = hist_vol.min()
        iv_max = hist_vol.max()
        
        if iv_max == iv_min:
            return 50.0
        
        iv_rank = ((current_iv - iv_min) / (iv_max - iv_min)) * 100
        
        # Speichere als historische Volatilität
        today = datetime.now().strftime('%Y-%m-%d')
        current_hist_vol = hist_vol.iloc[-1] if not hist_vol.empty else None
        if current_hist_vol and not pd.isna(current_hist_vol):
            self.db.save_iv_data(symbol, today, None, current_hist_vol)
        
        return iv_rank
    
    # ========================================================================
    # OPTIONS-AUSWAHL
    # ========================================================================
    
    def find_suitable_option(self, symbol: str, option_type: str, 
                            current_price: float) -> Optional[Dict]:
        """
        Findet passende Option basierend auf Strategie-Parametern.
        
        Args:
            symbol: Underlying Symbol
            option_type: "LONG_PUT" oder "LONG_CALL"
            current_price: Aktueller Underlying Price
            
        Returns:
            Dictionary mit Option-Details oder None
        """
        if symbol not in self.options_chain_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Options-Chain verfügbar")
            return None
        
        chain = self.options_chain_cache[symbol]
        expirations = chain['expirations']
        strikes = chain['strikes']
        
        # Filtere Expirations nach DTE
        if option_type == "LONG_PUT":
            min_dte = opt_config.PUT_MIN_DTE
            max_dte = opt_config.PUT_MAX_DTE
            right = "P"
        else:  # LONG_CALL
            min_dte = opt_config.CALL_MIN_DTE
            max_dte = opt_config.CALL_MAX_DTE
            right = "C"
        
        # Finde passende Expiration
        today = datetime.now()
        suitable_expirations = []
        
        for exp_str in expirations:
            try:
                # Parse Expiration (Format: YYYYMMDD)
                exp_date = datetime.strptime(exp_str, '%Y%m%d')
                dte = (exp_date - today).days
                
                if min_dte <= dte <= max_dte:
                    suitable_expirations.append((exp_str, dte))
            except:
                continue
        
        if not suitable_expirations:
            logger.warning(f"[WARNUNG] {symbol}: Keine Expirations im DTE-Bereich {min_dte}-{max_dte}")
            return None
        
        # Wähle Expiration in der Mitte des DTE-Bereichs
        suitable_expirations.sort(key=lambda x: abs(x[1] - (min_dte + max_dte) / 2))
        selected_expiry, selected_dte = suitable_expirations[0]
        
        # Finde passenden Strike
        if option_type == "LONG_PUT":
            # ATM Strike (nächster zum Current Price)
            atm_strike = min(strikes, key=lambda x: abs(x - current_price))
            selected_strike = atm_strike
        else:  # LONG_CALL
            # OTM Strike mit Target Delta ~0.40
            # Approximation: OTM Call Delta ~0.40 ist typisch 5-10% OTM
            # Wähle Strike 5% über Current Price als Start
            target_strike = current_price * 1.05
            otm_strike = min([s for s in strikes if s >= current_price], 
                            key=lambda x: abs(x - target_strike),
                            default=None)
            
            if otm_strike is None:
                logger.warning(f"[WARNUNG] {symbol}: Kein passender OTM Strike gefunden")
                return None
            
            selected_strike = otm_strike
        
        return {
            'symbol': symbol,
            'strike': selected_strike,
            'expiry': selected_expiry,
            'dte': selected_dte,
            'right': right,
            'option_type': option_type
        }
    
    def find_short_put_strike(self, symbol: str, current_price: float) -> Optional[Dict]:
        """
        Findet passenden Strike für Short Put (Cash Secured Put).
        
        Wählt Strike 5-10% unter Current Price für gute Prämie
        aber nicht zu weit weg für Risikomanagement
        
        Returns:
            Dict mit strike, expiry, dte, premium
        """
        if symbol not in self.options_chain_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Options-Chain verfügbar")
            return None
        
        chain = self.options_chain_cache[symbol]
        expirations = chain['expirations']
        strikes = chain['strikes']
        
        # Filtere Expirations nach DTE (30-60 Tage für Short Put)
        min_dte = 30
        max_dte = 60
        
        today = datetime.now()
        suitable_expirations = []
        
        for exp_str in expirations:
            try:
                exp_date = datetime.strptime(exp_str, '%Y%m%d')
                dte = (exp_date - today).days
                
                if min_dte <= dte <= max_dte:
                    suitable_expirations.append((exp_str, dte))
            except:
                continue
        
        if not suitable_expirations:
            logger.warning(f"[WARNUNG] {symbol}: Keine Expirations im DTE-Bereich {min_dte}-{max_dte}")
            return None
        
        # Wähle Expiration in der Mitte
        suitable_expirations.sort(key=lambda x: abs(x[1] - 45))  # Ziel 45 Tage
        selected_expiry, selected_dte = suitable_expirations[0]
        
        # Finde Strike 5-8% unter Current Price
        target_put_strike = current_price * 0.925  # 7.5% OTM für gute Prämie
        
        # Finde verfügbare Strikes unter Current Price
        put_strikes = [s for s in strikes if s < current_price]
        
        if not put_strikes:
            logger.warning(f"[WARNUNG] {symbol}: Keine Put Strikes verfügbar")
            return None
        
        # Wähle Strike nahe Target
        selected_strike = min(put_strikes, key=lambda x: abs(x - target_put_strike))
        
        # Schätze Premium (vereinfacht - in Realität von TWS)
        # Approximation: ATM Put ~ 3-5% des Strikes bei 45 Tagen
        distance_pct = (current_price - selected_strike) / current_price
        estimated_premium = selected_strike * (0.03 + distance_pct * 0.02)  # 3-5% je nach Distance
        
        return {
            'strike': selected_strike,
            'expiry': selected_expiry,
            'dte': selected_dte,
            'premium': estimated_premium
        }
    
    # ========================================================================
    # SIGNAL-ERKENNUNG
    # ========================================================================
    
    def check_long_put_setup(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Prüft Long Put Setup (Short am 52W-Hoch).
        
        Returns:
            Signal-Dict oder None
        """
        if len(df) == 0:
            return None
        
        # Stelle sicher, dass Earnings-Daten verfügbar sind (lazy loading)
        self._ensure_earnings_data(symbol)
        
        # Earnings-Risiko-Prüfung: Blockiere Signale während Earnings-Periode
        if self._is_earnings_risk_period(symbol):
            logger.info(f"[INFO] {symbol}: Long Put Signal blockiert - Earnings-Periode")
            return None
        
        current_price = df.iloc[-1]['close']
        high_52w, low_52w = self.calculate_52w_extremes(df)
        
        # 1. Technischer Trigger: Nahe 52W-Hoch
        proximity_threshold = high_52w * (1 - opt_config.PUT_PROXIMITY_TO_HIGH_PCT)
        if current_price < proximity_threshold:
            return None
        
        # 2. Fundamentale Prüfung
        if symbol not in self.fundamental_data_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Fundamentaldaten")
            return None
        
        fundamentals = self.fundamental_data_cache[symbol]
        pe_ratio = fundamentals.get('pe_ratio')
        market_cap = fundamentals.get('market_cap')
        avg_volume = fundamentals.get('avg_volume')
        
        if pe_ratio is None:
            logger.debug(f"[DEBUG] {symbol}: P/E Ratio nicht verfügbar")
            return None
        
        # Filter: Marktkapitalisierung
        if market_cap and market_cap < opt_config.MIN_MARKET_CAP:
            return None
        
        # Filter: Volumen
        if avg_volume and avg_volume < opt_config.MIN_AVG_VOLUME:
            return None
        
        # Branchen-Median-KGV (vereinfacht - in Produktion: externe API)
        sector = fundamentals.get('sector', 'Unknown')
        sector_median_pe = self._get_sector_median_pe(sector)
        
        if pe_ratio < sector_median_pe * opt_config.PUT_PE_RATIO_MULTIPLIER:
            logger.debug(f"[DEBUG] {symbol}: P/E {pe_ratio:.1f} < {sector_median_pe * opt_config.PUT_PE_RATIO_MULTIPLIER:.1f}")
            return None
        
        # 3. IV Rank Prüfung - Hole von Options-Chain
        option_candidate = self.find_suitable_option(symbol, "LONG_PUT", current_price)
        
        if not option_candidate:
            return None
        
        # Request Greeks für diese Option um IV zu bekommen
        self.request_option_greeks(
            symbol, 
            option_candidate['strike'],
            option_candidate['right'],
            option_candidate['expiry']
        )
        
        # Warte auf Greeks
        self.wait_for_requests(timeout=10)
        
        # Suche Greeks im Cache
        current_iv = None
        for req_data in self.pending_requests.values():
            if (req_data.get('symbol') == symbol and 
                req_data.get('strike') == option_candidate['strike']):
                greeks = req_data.get('greeks', {})
                current_iv = greeks.get('implied_volatility')
                break
        
        if current_iv is None:
            logger.warning(f"[WARNUNG] {symbol}: Keine IV-Daten verfügbar")
            # Fallback: Nutze historische Volatilität
            if symbol in self.historical_data_cache:
                df_temp = self.historical_data_cache[symbol]
                returns = np.log(df_temp['close'] / df_temp['close'].shift(1))
                current_iv = returns.std() * np.sqrt(252) * 100
        
        if current_iv:
            iv_rank = self.calculate_iv_rank(symbol, current_iv)
        else:
            iv_rank = 50.0  # Neutral
        
        if iv_rank < opt_config.PUT_MIN_IV_RANK:
            logger.debug(f"[DEBUG] {symbol}: IV Rank {iv_rank:.1f} < {opt_config.PUT_MIN_IV_RANK}")
            return None
        
        # Alle Kriterien erfüllt!
        # Schätze max_profit für Long Put (Strike - Current Price, begrenzt auf 50%)
        estimated_max_profit = max(0, option_candidate['strike'] - current_price) * 0.5  # Konservative Schätzung
        max_risk = option_candidate['strike'] - current_price  # Premium bezahlt
        
        # Kostenberechnung
        costs = self.calculate_strategy_costs('LONG_PUT', 1, option_candidate['strike'] - current_price)
        profitability = self.calculate_strategy_profitability('LONG_PUT', {
            'max_profit': estimated_max_profit,
            'max_risk': max_risk,
            'net_premium': option_candidate['strike'] - current_price,
            'quantity': 1
        })
        
        return {
            'type': 'LONG_PUT',
            'symbol': symbol,
            'underlying_price': current_price,
            'high_52w': high_52w,
            'proximity_pct': ((current_price / high_52w) - 1) * 100,
            'pe_ratio': pe_ratio,
            'sector_pe': sector_median_pe,
            'market_cap': market_cap,
            'avg_volume': avg_volume,
            'iv_rank': iv_rank,
            'recommended_strike': option_candidate['strike'],
            'recommended_expiry': option_candidate['expiry'],
            'recommended_dte': option_candidate['dte'],
            # Kosten & Rentabilität
            'estimated_max_profit': estimated_max_profit,
            'max_risk': max_risk,
            'commission': costs['commission'],
            'total_cost': costs['total_cost'],
            'adjusted_max_profit': profitability['adjusted_max_profit'],
            'rr_ratio': profitability['rr_ratio'],
            'profitability_pct': profitability['profitability_pct'],
            'expected_value': profitability['expected_value'],
            'timestamp': datetime.now()
        }
    
    def _get_sector_median_pe(self, sector: str) -> float:
        """
        Gibt Branchen-Median-KGV zurück (vereinfacht).
        In Produktion: Externe API oder manuell gepflegte Tabelle.
        """
        sector_pe_medians = {
            'Technology': 25.0,
            'Healthcare': 22.0,
            'Financial': 15.0,
            'Consumer Cyclical': 20.0,
            'Consumer Defensive': 18.0,
            'Industrials': 20.0,
            'Energy': 12.0,
            'Utilities': 16.0,
            'Real Estate': 35.0,
            'Communication Services': 22.0,
            'Basic Materials': 18.0,
            'Unknown': 20.0
        }
        
        return sector_pe_medians.get(sector, 20.0)
    
    def check_long_call_setup(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Prüft Long Call Setup (Long am 52W-Tief).
        
        Returns:
            Signal-Dict oder None
        """
        if len(df) == 0:
            return None
        
        # Stelle sicher, dass Earnings-Daten verfügbar sind (lazy loading)
        self._ensure_earnings_data(symbol)
        
        # Earnings-Risiko-Prüfung: Blockiere Signale während Earnings-Periode
        if self._is_earnings_risk_period(symbol):
            logger.info(f"[INFO] {symbol}: Long Call Signal blockiert - Earnings-Periode")
            return None
        
        current_price = df.iloc[-1]['close']
        high_52w, low_52w = self.calculate_52w_extremes(df)
        
        # 1. Technischer Trigger: Nahe 52W-Tief
        proximity_threshold = low_52w * (1 + opt_config.CALL_PROXIMITY_TO_LOW_PCT)
        if current_price > proximity_threshold:
            return None
        
        # 2. Fundamentale Prüfung: Positive FCF
        if symbol not in self.fundamental_data_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Fundamentaldaten")
            return None
        
        fundamentals = self.fundamental_data_cache[symbol]
        fcf = fundamentals.get('fcf', 0)
        market_cap = fundamentals.get('market_cap', 1)
        avg_volume = fundamentals.get('avg_volume')
        
        # Filter: Marktkapitalisierung und Volumen
        if market_cap and market_cap < opt_config.MIN_MARKET_CAP:
            return None
        
        if avg_volume and avg_volume < opt_config.MIN_AVG_VOLUME:
            return None
        
        fcf_yield = fcf / market_cap if market_cap > 0 else 0
        
        if fcf_yield <= opt_config.CALL_MIN_FCF_YIELD:
            logger.debug(f"[DEBUG] {symbol}: FCF Yield {fcf_yield:.4f} <= {opt_config.CALL_MIN_FCF_YIELD}")
            return None
        
        # 3. IV Rank Prüfung
        option_candidate = self.find_suitable_option(symbol, "LONG_CALL", current_price)
        
        if not option_candidate:
            return None
        
        # Request Greeks
        self.request_option_greeks(
            symbol,
            option_candidate['strike'],
            option_candidate['right'],
            option_candidate['expiry']
        )
        
        self.wait_for_requests(timeout=10)
        
        # Hole IV
        current_iv = None
        for req_data in self.pending_requests.values():
            if (req_data.get('symbol') == symbol and
                req_data.get('strike') == option_candidate['strike']):
                greeks = req_data.get('greeks', {})
                current_iv = greeks.get('implied_volatility')
                break
        
        if current_iv is None:
            # Fallback
            if symbol in self.historical_data_cache:
                df_temp = self.historical_data_cache[symbol]
                returns = np.log(df_temp['close'] / df_temp['close'].shift(1))
                current_iv = returns.std() * np.sqrt(252) * 100
        
        if current_iv:
            iv_rank = self.calculate_iv_rank(symbol, current_iv)
        else:
            iv_rank = 50.0
        
        if iv_rank > opt_config.CALL_MAX_IV_RANK:
            logger.debug(f"[DEBUG] {symbol}: IV Rank {iv_rank:.1f} > {opt_config.CALL_MAX_IV_RANK}")
            return None
        
        # Alle Kriterien erfüllt!
        # Schätze max_profit für Long Call (begrenzt auf 50% Aufwärtspotenzial)
        estimated_max_profit = (option_candidate['strike'] - current_price) * 0.5  # Konservative Schätzung
        max_risk = current_price - option_candidate['strike']  # Premium bezahlt (negativ)
        
        # Kostenberechnung
        costs = self.calculate_strategy_costs('LONG_CALL', 1, current_price - option_candidate['strike'])
        profitability = self.calculate_strategy_profitability('LONG_CALL', {
            'max_profit': estimated_max_profit,
            'max_risk': abs(max_risk),
            'net_premium': current_price - option_candidate['strike'],
            'quantity': 1
        })
        
        return {
            'type': 'LONG_CALL',
            'symbol': symbol,
            'underlying_price': current_price,
            'low_52w': low_52w,
            'proximity_pct': ((current_price / low_52w) - 1) * 100,
            'fcf_yield': fcf_yield,
            'market_cap': market_cap,
            'avg_volume': avg_volume,
            'iv_rank': iv_rank,
            'recommended_strike': option_candidate['strike'],
            'recommended_expiry': option_candidate['expiry'],
            'recommended_dte': option_candidate['dte'],
            # Kosten & Rentabilität
            'estimated_max_profit': estimated_max_profit,
            'max_risk': max_risk,
            'commission': costs['commission'],
            'total_cost': costs['total_cost'],
            'adjusted_max_profit': profitability['adjusted_max_profit'],
            'rr_ratio': profitability['rr_ratio'],
            'profitability_pct': profitability['profitability_pct'],
            'expected_value': profitability['expected_value'],
            'timestamp': datetime.now()
        }
    
    def check_short_put_setup(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Prüft Short Put Setup (Cash Secured Put am 52W-Tief).
        
        Strategie: Verkaufe Put nahe 52W-Tief bei starker Fundamentaldaten
        Erwartung: Aktie fällt nicht weiter, Prämie kassieren
        
        Returns:
            Signal-Dict oder None
        """
        if len(df) == 0:
            return None
        
        # Stelle sicher, dass Earnings-Daten verfügbar sind (lazy loading)
        self._ensure_earnings_data(symbol)
        
        # Earnings-Risiko-Prüfung: Blockiere Signale während Earnings-Periode
        if self._is_earnings_risk_period(symbol):
            logger.info(f"[INFO] {symbol}: Short Put Signal blockiert - Earnings-Periode")
            return None
        
        current_price = df.iloc[-1]['close']
        high_52w, low_52w = self.calculate_52w_extremes(df)
        
        # 1. Technischer Trigger: Nahe 52W-Tief (konträre Erwartung)
        proximity_threshold = low_52w * (1 + opt_config.CALL_PROXIMITY_TO_LOW_PCT)
        if current_price > proximity_threshold:
            return None
        
        # 2. Fundamentale Prüfung: Sehr starke Fundamentaldaten
        if symbol not in self.fundamental_data_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Fundamentaldaten")
            return None
        
        fundamentals = self.fundamental_data_cache[symbol]
        pe_ratio = fundamentals.get('pe_ratio')
        fcf = fundamentals.get('fcf', 0)
        market_cap = fundamentals.get('market_cap')
        avg_volume = fundamentals.get('avg_volume')
        
        # Filter: Marktkapitalisierung und Volumen
        if market_cap and market_cap < opt_config.MIN_MARKET_CAP:
            return None
        
        if avg_volume and avg_volume < opt_config.MIN_AVG_VOLUME:
            return None
        
        # Sehr starke Fundamentaldaten für Short Put
        if not pe_ratio or pe_ratio > 15:  # Günstig bewertet
            return None
        
        market_cap_val = market_cap or 1
        fcf_yield = fcf / market_cap_val if market_cap_val > 0 else 0
        
        if fcf_yield < 0.08:  # Mindestens 8% FCF Yield
            logger.debug(f"[DEBUG] {symbol}: FCF Yield {fcf_yield:.4f} < 0.08")
            return None
        
        # 3. Finde passenden Strike für Short Put
        option_candidate = self.find_short_put_strike(symbol, current_price)
        
        if not option_candidate:
            return None
        
        # 4. IV Rank Prüfung (niedriger IV für stabile Aktien)
        self.request_option_greeks(
            symbol,
            option_candidate['strike'],
            'P',
            option_candidate['expiry']
        )
        
        self.wait_for_requests(timeout=10)
        
        # Hole IV
        current_iv = None
        for req_data in self.pending_requests.values():
            if (req_data.get('symbol') == symbol and
                req_data.get('strike') == option_candidate['strike']):
                greeks = req_data.get('greeks', {})
                current_iv = greeks.get('implied_volatility')
                break
        
        if current_iv is None:
            # Fallback
            if symbol in self.historical_data_cache:
                df_temp = self.historical_data_cache[symbol]
                returns = np.log(df_temp['close'] / df_temp['close'].shift(1))
                current_iv = returns.std() * np.sqrt(252) * 100
        
        if current_iv:
            iv_rank = self.calculate_iv_rank(symbol, current_iv)
        else:
            iv_rank = 30.0  # Konservativ niedrig
        
        if iv_rank > 40:  # Max 40% IV Rank für Short Put
            logger.debug(f"[DEBUG] {symbol}: IV Rank {iv_rank:.1f} > 40")
            return None
        
        # Alle Kriterien erfüllt!
        # Short Put: Max Profit = Prämie, Max Risk = Strike - Prämie
        premium = option_candidate['premium']
        max_profit = premium
        max_risk = option_candidate['strike'] - current_price - premium  # Unbegrenzt, aber konservativ
        
        # Kostenberechnung
        costs = self.calculate_strategy_costs('SHORT_PUT', 1, premium)
        profitability = self.calculate_strategy_profitability('SHORT_PUT', {
            'max_profit': max_profit,
            'max_risk': max_risk,
            'net_premium': premium,
            'quantity': 1
        })
        
        return {
            'type': 'SHORT_PUT',
            'symbol': symbol,
            'underlying_price': current_price,
            'low_52w': low_52w,
            'proximity_pct': ((current_price / low_52w) - 1) * 100,
            'pe_ratio': pe_ratio,
            'fcf_yield': fcf_yield,
            'market_cap': market_cap,
            'avg_volume': avg_volume,
            'iv_rank': iv_rank,
            'recommended_strike': option_candidate['strike'],
            'recommended_expiry': option_candidate['expiry'],
            'recommended_dte': option_candidate['dte'],
            'premium': premium,
            'max_profit': max_profit,
            'max_risk': max_risk,
            # Kosten & Rentabilität
            'commission': costs['commission'],
            'total_cost': costs['total_cost'],
            'adjusted_max_profit': profitability['adjusted_max_profit'],
            'rr_ratio': profitability['rr_ratio'],
            'profitability_pct': profitability['profitability_pct'],
            'expected_value': profitability['expected_value'],
            'timestamp': datetime.now()
        }
    
    def check_bear_call_spread_setup(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Prüft Bear Call Spread Setup (Short am 52W-Hoch mit Protection).
        
        Returns:
            Signal-Dict oder None
        """
        if len(df) == 0:
            return None
        
        # Stelle sicher, dass Earnings-Daten verfügbar sind (lazy loading)
        self._ensure_earnings_data(symbol)
        
        # Earnings-Risiko-Prüfung: Blockiere Signale während Earnings-Periode
        if self._is_earnings_risk_period(symbol):
            logger.info(f"[INFO] {symbol}: Bear Call Spread Signal blockiert - Earnings-Periode")
            return None
        
        current_price = df.iloc[-1]['close']
        high_52w, low_52w = self.calculate_52w_extremes(df)
        
        # 1. Technischer Trigger: Nahe 52W-Hoch (wie Long Put)
        proximity_threshold = high_52w * (1 - opt_config.SPREAD_PROXIMITY_TO_HIGH_PCT)
        if current_price < proximity_threshold:
            return None
        
        # 2. Fundamentale Prüfung: Überbewertung
        if symbol not in self.fundamental_data_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Fundamentaldaten")
            return None
        
        fundamentals = self.fundamental_data_cache[symbol]
        pe_ratio = fundamentals.get('pe_ratio')
        sector = fundamentals.get('sector', 'Unknown')
        market_cap = fundamentals.get('market_cap')
        avg_volume = fundamentals.get('avg_volume')
        
        # Filter: Marktkapitalisierung und Volumen
        if market_cap and market_cap < opt_config.MIN_MARKET_CAP:
            return None
        
        if avg_volume and avg_volume < opt_config.MIN_AVG_VOLUME:
            return None
        
        if not pe_ratio or pe_ratio <= 0:
            return None
        
        sector_pe_median = self._get_sector_median_pe(sector)
        pe_threshold = sector_pe_median * opt_config.SPREAD_PE_RATIO_MULTIPLIER
        
        if pe_ratio < pe_threshold:
            logger.debug(f"[DEBUG] {symbol}: P/E {pe_ratio:.1f} < {pe_threshold:.1f}")
            return None
        
        # 3. Finde passende Spread-Strikes
        spread_candidate = self.find_spread_strikes(symbol, current_price)
        
        if not spread_candidate:
            return None
        
        # 4. IV Rank Prüfung (hohes IV für Prämieneinnahme)
        # Request Greeks für Short Strike
        self.request_option_greeks(
            symbol,
            spread_candidate['short_strike'],
            'C',
            spread_candidate['expiry']
        )
        
        self.wait_for_requests(timeout=10)
        
        # Hole IV
        current_iv = None
        for req_data in self.pending_requests.values():
            if (req_data.get('symbol') == symbol and
                req_data.get('strike') == spread_candidate['short_strike']):
                greeks = req_data.get('greeks', {})
                current_iv = greeks.get('implied_volatility')
                break
        
        if current_iv is None:
            # Fallback
            if symbol in self.historical_data_cache:
                df_temp = self.historical_data_cache[symbol]
                returns = np.log(df_temp['close'] / df_temp['close'].shift(1))
                current_iv = returns.std() * np.sqrt(252) * 100
        
        if current_iv:
            iv_rank = self.calculate_iv_rank(symbol, current_iv)
        else:
            iv_rank = 50.0
        
        if iv_rank < opt_config.SPREAD_MIN_IV_RANK:
            logger.debug(f"[DEBUG] {symbol}: IV Rank {iv_rank:.1f} < {opt_config.SPREAD_MIN_IV_RANK}")
            return None
        
        # Alle Kriterien erfüllt!
        # Kostenberechnung
        costs = self.calculate_strategy_costs('BEAR_CALL_SPREAD', 1, spread_candidate['net_premium'])
        profitability = self.calculate_strategy_profitability('BEAR_CALL_SPREAD', {
            'max_profit': spread_candidate['net_premium'],
            'max_risk': spread_candidate['max_risk'],
            'net_premium': spread_candidate['net_premium'],
            'quantity': 1
        })
        
        return {
            'type': 'BEAR_CALL_SPREAD',
            'symbol': symbol,
            'underlying_price': current_price,
            'high_52w': high_52w,
            'proximity_pct': ((current_price / high_52w) - 1) * 100,
            'pe_ratio': pe_ratio,
            'sector_pe': sector_pe_median,
            'market_cap': market_cap,
            'avg_volume': avg_volume,
            'iv_rank': iv_rank,
            'short_strike': spread_candidate['short_strike'],
            'long_strike': spread_candidate['long_strike'],
            'short_delta': spread_candidate['short_delta'],
            'net_premium': spread_candidate['net_premium'],
            'max_risk': spread_candidate['max_risk'],
            'recommended_expiry': spread_candidate['expiry'],
            'recommended_dte': spread_candidate['dte'],
            # Kosten & Rentabilität
            'commission': costs['commission'],
            'total_cost': costs['total_cost'],
            'adjusted_net_premium': profitability['adjusted_net_premium'],
            'rr_ratio': profitability['rr_ratio'],
            'profitability_pct': profitability['profitability_pct'],
            'expected_value': profitability['expected_value'],
            'timestamp': datetime.now()
        }
    
    def check_bull_put_spread_setup(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Prüft Bull Put Spread Setup (Short am 52W-Tief mit Protection).
        
        Bull Put Spread = Short Put + Long Put (höherer Strike)
        - Short Put: Höherer Strike (bullish, weniger wahrscheinlich)
        - Long Put: Tieferer Strike (Protection)
        - Max Profit: Net Premium
        - Max Risk: Strike-Differenz - Net Premium
        
        Returns:
            Signal-Dict oder None
        """
        if len(df) == 0:
            return None
        
        # Stelle sicher, dass Earnings-Daten verfügbar sind (lazy loading)
        self._ensure_earnings_data(symbol)
        
        # Earnings-Risiko-Prüfung: Blockiere Signale während Earnings-Periode
        if self._is_earnings_risk_period(symbol):
            logger.info(f"[INFO] {symbol}: Bull Put Spread Signal blockiert - Earnings-Periode")
            return None
        
        current_price = df.iloc[-1]['close']
        high_52w, low_52w = self.calculate_52w_extremes(df)
        
        # 1. Technischer Trigger: Nahe 52W-Tief (wie Long Call)
        proximity_threshold = low_52w * (1 + opt_config.SPREAD_PROXIMITY_TO_LOW_PCT)
        if current_price > proximity_threshold:
            return None
        
        # 2. Fundamentale Prüfung: Unterbewertung
        if symbol not in self.fundamental_data_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Fundamentaldaten")
            return None
        
        fundamentals = self.fundamental_data_cache[symbol]
        pe_ratio = fundamentals.get('pe_ratio')
        sector = fundamentals.get('sector', 'Unknown')
        market_cap = fundamentals.get('market_cap')
        avg_volume = fundamentals.get('avg_volume')
        fcf_yield = fundamentals.get('fcf_yield', 0)
        
        # Filter: Marktkapitalisierung und Volumen
        if market_cap and market_cap < opt_config.MIN_MARKET_CAP:
            return None
        
        if avg_volume and avg_volume < opt_config.MIN_AVG_VOLUME:
            return None
        
        if not pe_ratio or pe_ratio <= 0:
            return None
        
        sector_pe_median = self._get_sector_median_pe(sector)
        pe_threshold = sector_pe_median * opt_config.SPREAD_PE_RATIO_MULTIPLIER_LOW
        
        if pe_ratio > pe_threshold:
            logger.debug(f"[DEBUG] {symbol}: P/E {pe_ratio:.1f} > {pe_threshold:.1f}")
            return None
        
        # FCF Yield Check (für Bull Put Spread: hoher FCF Yield bevorzugt)
        if fcf_yield < opt_config.SPREAD_MIN_FCF_YIELD:
            logger.debug(f"[DEBUG] {symbol}: FCF Yield {fcf_yield:.4f} < {opt_config.SPREAD_MIN_FCF_YIELD}")
            return None
        
        # 3. Finde passende Spread-Strikes
        spread_candidate = self.find_bull_put_spread_strikes(symbol, current_price)
        
        if not spread_candidate:
            return None
        
        # 4. IV Rank Prüfung (hohes IV für Prämieneinnahme)
        # Request Greeks für Short Strike
        self.request_option_greeks(
            symbol,
            spread_candidate['short_strike'],
            'P',
            spread_candidate['expiry']
        )
        
        self.wait_for_requests(timeout=10)
        
        # Hole IV
        current_iv = None
        for req_data in self.pending_requests.values():
            if (req_data.get('symbol') == symbol and
                req_data.get('strike') == spread_candidate['short_strike']):
                greeks = req_data.get('greeks', {})
                current_iv = greeks.get('implied_volatility')
                break
        
        if current_iv is None:
            # Fallback
            if symbol in self.historical_data_cache:
                df_temp = self.historical_data_cache[symbol]
                returns = np.log(df_temp['close'] / df_temp['close'].shift(1))
                current_iv = returns.std() * np.sqrt(252) * 100
        
        if current_iv:
            iv_rank = self.calculate_iv_rank(symbol, current_iv)
        else:
            iv_rank = 50.0
        
        if iv_rank < opt_config.SPREAD_MIN_IV_RANK:
            logger.debug(f"[DEBUG] {symbol}: IV Rank {iv_rank:.1f} < {opt_config.SPREAD_MIN_IV_RANK}")
            return None
        
        # Alle Kriterien erfüllt!
        # Kostenberechnung
        costs = self.calculate_strategy_costs('BULL_PUT_SPREAD', 1, spread_candidate['net_premium'])
        profitability = self.calculate_strategy_profitability('BULL_PUT_SPREAD', {
            'max_profit': spread_candidate['net_premium'],
            'max_risk': spread_candidate['max_risk'],
            'net_premium': spread_candidate['net_premium'],
            'quantity': 1
        })
        
        return {
            'type': 'BULL_PUT_SPREAD',
            'symbol': symbol,
            'underlying_price': current_price,
            'low_52w': low_52w,
            'proximity_pct': ((current_price / low_52w) - 1) * 100,
            'pe_ratio': pe_ratio,
            'sector_pe': sector_pe_median,
            'fcf_yield': fcf_yield,
            'market_cap': market_cap,
            'avg_volume': avg_volume,
            'iv_rank': iv_rank,
            'short_strike': spread_candidate['short_strike'],
            'long_strike': spread_candidate['long_strike'],
            'short_delta': spread_candidate['short_delta'],
            'net_premium': spread_candidate['net_premium'],
            'max_risk': spread_candidate['max_risk'],
            'recommended_expiry': spread_candidate['expiry'],
            'recommended_dte': spread_candidate['dte'],
            # Kosten & Rentabilität
            'commission': costs['commission'],
            'total_cost': costs['total_cost'],
            'adjusted_net_premium': profitability['adjusted_net_premium'],
            'rr_ratio': profitability['rr_ratio'],
            'profitability_pct': profitability['profitability_pct'],
            'expected_value': profitability['expected_value'],
            'exit_scenarios': profitability.get('exit_scenarios', {}),
            'recommendation': profitability.get('recommendation', ''),
            'timestamp': datetime.now()
        }
        """
        Findet passende Strikes für Bear Call Spread.
        
        Short Call: Delta 0.25-0.35
        Long Call: $5 über Short Strike
        
        Returns:
            Dict mit short_strike, long_strike, expiry, dte, net_premium, max_risk
        """
        if symbol not in self.options_chain_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Options-Chain verfügbar")
            return None
        
        chain = self.options_chain_cache[symbol]
        expirations = chain['expirations']
        strikes = chain['strikes']
        
        # Filtere Expirations nach DTE (30-45 Tage)
        min_dte = opt_config.SPREAD_MIN_DTE
        max_dte = opt_config.SPREAD_MAX_DTE
        
        today = datetime.now()
        suitable_expirations = []
        
        for exp_str in expirations:
            try:
                exp_date = datetime.strptime(exp_str, '%Y%m%d')
                dte = (exp_date - today).days
                
                if min_dte <= dte <= max_dte:
                    suitable_expirations.append((exp_str, dte))
            except:
                continue
        
        if not suitable_expirations:
            logger.warning(f"[WARNUNG] {symbol}: Keine Expirations im DTE-Bereich {min_dte}-{max_dte}")
            return None
        
        # Wähle Expiration in der Mitte
        suitable_expirations.sort(key=lambda x: abs(x[1] - (min_dte + max_dte) / 2))
        selected_expiry, selected_dte = suitable_expirations[0]
        
        # Finde Short Strike mit Delta 0.25-0.35
        # Approximation: Delta ~0.30 ist typisch 2-3 Standard-Deviationen OTM
        # Für Call: Strike deutlich über Current Price
        target_short_strike = current_price * 1.10  # 10% OTM als Start
        
        otm_strikes = [s for s in strikes if s >= current_price * 1.05]  # Mind. 5% OTM
        
        if not otm_strikes:
            logger.warning(f"[WARNUNG] {symbol}: Keine OTM Strikes gefunden")
            return None
        
        # Wähle Strike nahe Target
        short_strike = min(otm_strikes, key=lambda x: abs(x - target_short_strike))
        
        # Long Strike: $5 über Short Strike
        long_strike = short_strike + opt_config.SPREAD_STRIKE_WIDTH
        
        # Prüfe ob Long Strike verfügbar
        if long_strike not in strikes:
            # Finde nächsten verfügbaren Strike über Short
            higher_strikes = [s for s in strikes if s > short_strike]
            if not higher_strikes:
                return None
            long_strike = min(higher_strikes)
        
        # Berechne Max Risk
        strike_diff = long_strike - short_strike
        max_risk = strike_diff * 100  # 100 Aktien pro Kontrakt
        
        # Geschätzte Net Premium (würde in Realität von TWS kommen)
        # Konservative Schätzung: 20-30% der Strike-Differenz bei Delta 0.30
        estimated_net_premium = max_risk * 0.25  # 25% der Max Risk
        
        return {
            'short_strike': short_strike,
            'long_strike': long_strike,
            'expiry': selected_expiry,
            'dte': selected_dte,
            'short_delta': 0.30,  # Approximation
            'net_premium': estimated_net_premium,
            'max_risk': max_risk
        }
    
    def find_bull_put_spread_strikes(self, symbol: str, current_price: float) -> Optional[Dict]:
        """
        Findet passende Strikes für Bull Put Spread.
        
        Short Put: Delta 0.25-0.35 (bullish, weniger wahrscheinlich)
        Long Put: $5 unter Short Strike
        
        Returns:
            Dict mit short_strike, long_strike, expiry, dte, net_premium, max_risk
        """
        if symbol not in self.options_chain_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Options-Chain verfügbar")
            return None
        
        chain = self.options_chain_cache[symbol]
        expirations = chain['expirations']
        strikes = chain['strikes']
        
        # Filtere Expirations nach DTE (30-45 Tage)
        min_dte = opt_config.SPREAD_MIN_DTE
        max_dte = opt_config.SPREAD_MAX_DTE
        
        today = datetime.now()
        suitable_expirations = []
        
        for exp_str in expirations:
            try:
                exp_date = datetime.strptime(exp_str, '%Y%m%d')
                dte = (exp_date - today).days
                
                if min_dte <= dte <= max_dte:
                    suitable_expirations.append((exp_str, dte))
            except:
                continue
        
        if not suitable_expirations:
            logger.warning(f"[WARNUNG] {symbol}: Keine Expirations im DTE-Bereich {min_dte}-{max_dte}")
            return None
        
        # Wähle Expiration in der Mitte
        suitable_expirations.sort(key=lambda x: abs(x[1] - (min_dte + max_dte) / 2))
        selected_expiry, selected_dte = suitable_expirations[0]
        
        # Finde Short Strike mit Delta 0.25-0.35
        # Für Put: Strike leicht über Current Price (bullish, weniger wahrscheinlich)
        target_short_strike = current_price * 1.05  # 5% OTM als Start
        
        otm_strikes = [s for s in strikes if s >= current_price * 1.02]  # Mind. 2% OTM
        
        if not otm_strikes:
            logger.warning(f"[WARNUNG] {symbol}: Keine OTM Put Strikes gefunden")
            return None
        
        # Wähle Strike nahe Target
        short_strike = min(otm_strikes, key=lambda x: abs(x - target_short_strike))
        
        # Long Strike: $5 unter Short Strike
        long_strike = short_strike - opt_config.SPREAD_STRIKE_WIDTH
        
        # Prüfe ob Long Strike verfügbar
        if long_strike not in strikes:
            # Finde nächsten verfügbaren Strike unter Short
            lower_strikes = [s for s in strikes if s < short_strike]
            if not lower_strikes:
                return None
            long_strike = max(lower_strikes)
        
        # Berechne Max Risk
        strike_diff = short_strike - long_strike
        max_risk = (strike_diff * 100) - (0.25 * strike_diff * 100)  # Strike-Diff minus Net Premium
        
        # Geschätzte Net Premium (würde in Realität von TWS kommen)
        # Konservative Schätzung: 20-30% der Strike-Differenz bei Delta 0.30
        estimated_net_premium = max_risk * 0.25  # 25% der Max Risk
        
        return {
            'short_strike': short_strike,
            'long_strike': long_strike,
            'expiry': selected_expiry,
            'dte': selected_dte,
            'short_delta': 0.30,  # Approximation
            'net_premium': estimated_net_premium,
            'max_risk': max_risk
        }
    
    def find_covered_call_strike(self, symbol: str, current_price: float, position: Dict) -> Optional[Dict]:
        """
        Findet passenden Call Strike für Covered Call.
        
        Strike sollte: 
        - Über aktuellem Preis liegen (OTM)
        - Nicht zu weit über dem aktuellen Preis
        - Hohe Prämie bieten
        
        Returns:
            Dict mit strike, expiry, dte, premium, delta
        """
        if symbol not in self.options_chain_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Options-Chain verfügbar")
            return None
        
        chain = self.options_chain_cache[symbol]
        expirations = chain['expirations']
        strikes = chain['strikes']
        
        # Filtere Expirations nach DTE (30-60 Tage für Covered Calls)
        min_dte = opt_config.COVERED_CALL_MIN_DTE
        max_dte = opt_config.COVERED_CALL_MAX_DTE
        
        today = datetime.now()
        suitable_expirations = []
        
        for exp_str in expirations:
            try:
                exp_date = datetime.strptime(exp_str, '%Y%m%d')
                dte = (exp_date - today).days
                
                if min_dte <= dte <= max_dte:
                    suitable_expirations.append((exp_str, dte))
            except:
                continue
        
        if not suitable_expirations:
            logger.warning(f"[WARNUNG] {symbol}: Keine Expirations im DTE-Bereich {min_dte}-{max_dte}")
            return None
        
        # Wähle Expiration in der Mitte
        suitable_expirations.sort(key=lambda x: abs(x[1] - (min_dte + max_dte) / 2))
        selected_expiry, selected_dte = suitable_expirations[0]
        
        # Finde OTM Call Strikes (5-15% über aktuellem Preis)
        min_strike = current_price * 1.05  # Mindestens 5% OTM
        max_strike = current_price * 1.15  # Maximal 15% OTM
        
        otm_strikes = [s for s in strikes if min_strike <= s <= max_strike]
        
        if not otm_strikes:
            logger.warning(f"[WARNUNG] {symbol}: Keine geeigneten OTM Call Strikes gefunden")
            return None
        
        # Wähle Strike mit bester Prämien-Rendite
        # Approximation: Höhere Strikes haben tendenziell höhere Prämien
        # Wähle Strike bei 8-10% OTM als gute Balance
        target_strike = current_price * 1.08
        selected_strike = min(otm_strikes, key=lambda x: abs(x - target_strike))
        
        # Geschätzte Premium (würde in Realität von TWS kommen)
        # Approximation basierend auf DTE und Entfernung zum Strike
        distance_pct = (selected_strike - current_price) / current_price
        base_premium = current_price * 0.02  # 2% Basisprämie
        
        # Höhere Prämie für längere Laufzeit und größeren Abstand
        time_factor = selected_dte / 45  # Normalisiert auf 45 Tage
        distance_factor = distance_pct * 5  # 5x Multiplikator für Entfernung
        
        estimated_premium = base_premium * (1 + time_factor) * (1 + distance_factor)
        
        return {
            'strike': selected_strike,
            'expiry': selected_expiry,
            'dte': selected_dte,
            'premium': estimated_premium,
            'delta': 0.25  # Approximation für OTM Call
        }
    
    def check_covered_call_exit_signals(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Prüft Exit-Signale für bestehende Covered Call Positionen.
        
        Exit-Signale wenn:
        1. Option läuft stark ins Geld (Aktienkurs > Strike + Buffer)
        2. Hoher unrealisierter Verlust auf der Aktienposition
        3. Wenige Tage bis Verfall
        
        Returns:
            Exit-Signal oder None
        """
        if len(df) == 0 or symbol not in self.portfolio_data:
            return None
        
        current_price = df.iloc[-1]['close']
        position = self.portfolio_data[symbol]
        
        # Prüfe ob es offene Covered Call Positionen gibt
        # (vereinfacht: wenn Aktien gehalten werden, könnte es Covered Calls geben)
        if position.get('quantity', 0) < 100:
            return None
        
        # 1. Option läuft ins Geld - Aktienkurs nahe/am Strike
        # Hole aktive Covered Call Positionen aus der Datenbank
        active_covered_calls = self.db.get_active_covered_calls(symbol)
        
        for covered_call in active_covered_calls:
            strike = covered_call.get('strike')
            expiry = covered_call.get('expiry')
            entry_premium = covered_call.get('premium', 0)
            
            # Berechne Tage bis Verfall
            try:
                expiry_date = datetime.strptime(expiry, '%Y%m%d')
                dte = (expiry_date - datetime.now()).days
            except:
                continue
            
            # Exit Signal 1: Option läuft stark ins Geld
            if current_price >= strike * 1.02:  # 2% über Strike
                return {
                    'type': 'COVERED_CALL_EXIT',
                    'symbol': symbol,
                    'reason': 'OPTION_IN_THE_MONEY',
                    'current_price': current_price,
                    'strike': strike,
                    'dte': dte,
                    'entry_premium': entry_premium,
                    'unrealized_pnl': position.get('unrealized_pnl', 0),
                    'message': f'Covered Call @ {strike} läuft ins Geld - Aktie bei ${current_price:.2f}'
                }
            
            # Exit Signal 2: Wenige Tage bis Verfall (< 7 Tage)
            if dte <= 7:
                return {
                    'type': 'COVERED_CALL_EXIT',
                    'symbol': symbol,
                    'reason': 'EXPIRING_SOON',
                    'current_price': current_price,
                    'strike': strike,
                    'dte': dte,
                    'entry_premium': entry_premium,
                    'unrealized_pnl': position.get('unrealized_pnl', 0),
                    'message': f'Covered Call @ {strike} verfällt in {dte} Tagen'
                }
            
            # Exit Signal 3: Hoher unrealisierter Verlust auf Aktienposition
            if position.get('unrealized_pnl', 0) < -1000:  # >$1000 Verlust
                return {
                    'type': 'COVERED_CALL_EXIT',
                    'symbol': symbol,
                    'reason': 'LARGE_UNREALIZED_LOSS',
                    'current_price': current_price,
                    'strike': strike,
                    'dte': dte,
                    'entry_premium': entry_premium,
                    'unrealized_pnl': position.get('unrealized_pnl', 0),
                    'message': f'Covered Call @ {strike} - Aktienposition mit ${position["unrealized_pnl"]:.2f} Verlust'
                }
        
        return None
    
    def scan_for_options_signals(self):
        """Scannt Watchlist nach Options-Signalen."""
        if not self._is_trading_hours():
            logger.info("[INFO] Außerhalb der Handelszeiten - Scan übersprungen")
            return
        
        logger.info("\n" + "="*70)
        logger.info(f"  OPTIONS SCAN - {datetime.now()}")
        logger.info("="*70)
        
        for symbol in self.watchlist:
            try:
                logger.info(f"\nAnalysiere {symbol}...")
                
                # 1. Lade historische Daten (Smart Update: nur neue Bars)
                # Beim ersten Scan: 252 Tage laden, danach nur 5 Tage ergänzen
                self.request_historical_data(symbol, days=opt_config.WEEKS_52_DAYS, incremental=True)
                self.wait_for_requests(timeout=30)
                
                if symbol not in self.historical_data_cache:
                    logger.warning(f"[WARNUNG] {symbol}: Keine historischen Daten")
                    continue
                
                # 2. Lade Fundamentaldaten
                self.request_fundamental_data(symbol)
                self.wait_for_requests(timeout=10)
                
                if symbol not in self.fundamental_data_cache:
                    logger.warning(f"[WARNUNG] {symbol}: Keine Fundamentaldaten")
                    continue
                
                # 3. Lade Options-Chain
                self.request_options_chain(symbol)
                self.wait_for_requests(timeout=10)
                
                if symbol not in self.options_chain_cache:
                    logger.warning(f"[WARNUNG] {symbol}: Keine Options-Chain")
                    continue
                
                # 4. Prüfe Setups
                df = self.historical_data_cache[symbol]
                
                # Long Put Setup (Short am 52W-Hoch)
                put_signal = self.check_long_put_setup(symbol, df)
                if put_signal:
                    logger.info(f"\n{'='*70}")
                    logger.info(f"[SIGNAL] LONG PUT SETUP: {symbol}")
                    logger.info(f"  Preis: ${put_signal['underlying_price']:.2f}")
                    logger.info(f"  52W-Hoch: ${put_signal['high_52w']:.2f} ({put_signal['proximity_pct']:+.2f}%)")
                    logger.info(f"  P/E Ratio: {put_signal['pe_ratio']:.1f} (Branche: {put_signal['sector_pe']:.1f})")
                    logger.info(f"  IV Rank: {put_signal['iv_rank']:.1f}")
                    logger.info(f"  Option: {put_signal['recommended_strike']} PUT {put_signal['recommended_expiry']}")
                    logger.info(f"  DTE: {put_signal['recommended_dte']}")
                    logger.info(f"  Max Risk: ${put_signal['max_risk']:.2f}")
                    logger.info(f"  Kommission: €{put_signal['commission']:.2f}")
                    logger.info(f"  R/R Ratio: {put_signal['rr_ratio']:.2f}")
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(put_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[LONG PUT] {symbol}",
                        message=f"52W-Hoch Setup @ ${put_signal['underlying_price']:.2f}\\n" +
                               f"Strike: {put_signal['recommended_strike']} DTE: {put_signal['recommended_dte']}\\n" +
                               f"P/E: {put_signal['pe_ratio']:.1f} | IV Rank: {put_signal['iv_rank']:.1f}\\n" +
                               f"Max Risk: ${put_signal['max_risk']:.2f} | Kommission: €{put_signal['commission']:.2f}\\n" +
                               f"💰 {put_signal['recommendation']}",
                        priority=1
                    )
                
                # Long Call Setup (Long am 52W-Tief)
                call_signal = self.check_long_call_setup(symbol, df)
                if call_signal:
                    logger.info(f"\n{'='*70}")
                    logger.info(f"[SIGNAL] LONG CALL SETUP: {symbol}")
                    logger.info(f"  Preis: ${call_signal['underlying_price']:.2f}")
                    logger.info(f"  52W-Tief: ${call_signal['low_52w']:.2f} ({call_signal['proximity_pct']:+.2f}%)")
                    logger.info(f"  FCF Yield: {call_signal['fcf_yield']:.4f}")
                    logger.info(f"  IV Rank: {call_signal['iv_rank']:.1f}")
                    logger.info(f"  Option: {call_signal['recommended_strike']} CALL {call_signal['recommended_expiry']}")
                    logger.info(f"  DTE: {call_signal['recommended_dte']}")
                    logger.info(f"  Max Risk: ${abs(call_signal['max_risk']):.2f}")
                    logger.info(f"  Kommission: €{call_signal['commission']:.2f}")
                    logger.info(f"  R/R Ratio: {call_signal['rr_ratio']:.2f}")
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(call_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[LONG CALL] {symbol}",
                        message=f"52W-Tief Setup @ ${call_signal['underlying_price']:.2f}\\n" +
                               f"Strike: {call_signal['recommended_strike']} DTE: {call_signal['recommended_dte']}\\n" +
                               f"FCF Yield: {call_signal['fcf_yield']:.4f} | IV Rank: {call_signal['iv_rank']:.1f}\\n" +
                               f"Max Risk: ${abs(call_signal['max_risk']):.2f} | Kommission: €{call_signal['commission']:.2f}\\n" +
                               f"💰 {call_signal['recommendation']}",
                        priority=1
                    )
                
                # Short Put Setup (Cash Secured Put am 52W-Tief)
                short_put_signal = self.check_short_put_setup(symbol, df)
                if short_put_signal:
                    logger.info(f"\n{'='*70}")
                    logger.info(f"[SIGNAL] SHORT PUT SETUP: {symbol}")
                    logger.info(f"  Preis: ${short_put_signal['underlying_price']:.2f}")
                    logger.info(f"  52W-Tief: ${short_put_signal['low_52w']:.2f} ({short_put_signal['proximity_pct']:+.2f}%)")
                    logger.info(f"  P/E Ratio: {short_put_signal['pe_ratio']:.1f}")
                    logger.info(f"  FCF Yield: {short_put_signal['fcf_yield']:.4f}")
                    logger.info(f"  IV Rank: {short_put_signal['iv_rank']:.1f}")
                    logger.info(f"  Strike: {short_put_signal['recommended_strike']} PUT {short_put_signal['recommended_expiry']}")
                    logger.info(f"  DTE: {short_put_signal['recommended_dte']}")
                    logger.info(f"  Premium: ${short_put_signal['premium']:.2f} (bereinigt: ${short_put_signal['adjusted_max_profit']:.2f})")
                    logger.info(f"  Max Risk: ${short_put_signal['max_risk']:.2f}")
                    logger.info(f"  Kommission: €{short_put_signal['commission']:.2f}")
                    logger.info(f"  R/R Ratio: {short_put_signal['rr_ratio']:.2f}")
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(short_put_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[SHORT PUT] {symbol}",
                        message=f"52W-Tief Setup @ ${short_put_signal['underlying_price']:.2f}\\n" +
                               f"Strike: {short_put_signal['recommended_strike']} DTE: {short_put_signal['recommended_dte']}\\n" +
                               f"P/E: {short_put_signal['pe_ratio']:.1f} | FCF Yield: {short_put_signal['fcf_yield']:.4f}\\n" +
                               f"Premium: ${short_put_signal['premium']:.2f} | Kommission: €{short_put_signal['commission']:.2f}\\n" +
                               f"Max Risk: ${short_put_signal['max_risk']:.2f} | R/R: {short_put_signal['rr_ratio']:.2f}\\n" +
                               f"💰 {short_put_signal['recommendation']}",
                        priority=1
                    )
                
                # Bull Put Spread Setup (Short am 52W-Tief mit Protection)
                bull_put_spread_signal = self.check_bull_put_spread_setup(symbol, df)
                if bull_put_spread_signal:
                    logger.info(f"\n{'='*70}")
                    logger.info(f"[SIGNAL] BULL PUT SPREAD SETUP: {symbol}")
                    logger.info(f"  Preis: ${bull_put_spread_signal['underlying_price']:.2f}")
                    logger.info(f"  52W-Tief: ${bull_put_spread_signal['low_52w']:.2f} ({bull_put_spread_signal['proximity_pct']:+.2f}%)")
                    logger.info(f"  P/E Ratio: {bull_put_spread_signal['pe_ratio']:.1f} (Branche: {bull_put_spread_signal['sector_pe']:.1f})")
                    logger.info(f"  FCF Yield: {bull_put_spread_signal['fcf_yield']:.4f}")
                    logger.info(f"  IV Rank: {bull_put_spread_signal['iv_rank']:.1f}")
                    logger.info(f"  Short Put: {bull_put_spread_signal['short_strike']} (Delta ~{bull_put_spread_signal['short_delta']:.2f})")
                    logger.info(f"  Long Put: {bull_put_spread_signal['long_strike']}")
                    logger.info(f"  DTE: {bull_put_spread_signal['recommended_dte']}")
                    logger.info(f"  Net Premium: ${bull_put_spread_signal['net_premium']:.2f} (bereinigt: ${bull_put_spread_signal['adjusted_net_premium']:.2f})")
                    logger.info(f"  Max Risk: ${bull_put_spread_signal['max_risk']:.2f}")
                    logger.info(f"  Kommission: €{bull_put_spread_signal['commission']:.2f}")
                    logger.info(f"  R/R Ratio: {bull_put_spread_signal['rr_ratio']:.2f}")
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(bull_put_spread_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[BULL PUT SPREAD] {symbol}",
                        message=f"52W-Tief Setup @ ${bull_put_spread_signal['underlying_price']:.2f}\\n" +
                               f"Spread: {bull_put_spread_signal['short_strike']}/{bull_put_spread_signal['long_strike']} DTE: {bull_put_spread_signal['recommended_dte']}\\n" +
                               f"P/E: {bull_put_spread_signal['pe_ratio']:.1f} | FCF Yield: {bull_put_spread_signal['fcf_yield']:.4f}\\n" +
                               f"Net Premium: ${bull_put_spread_signal['net_premium']:.2f} (€{bull_put_spread_signal['commission']:.2f} Kommission)\\n" +
                               f"Max Risk: ${bull_put_spread_signal['max_risk']:.2f} | R/R: {bull_put_spread_signal['rr_ratio']:.2f}\\n" +
                               f"💰 {bull_put_spread_signal['recommendation']}",
                        priority=1
                    )
                
                # Bear Call Spread Setup (Short am 52W-Hoch)
                spread_signal = self.check_bear_call_spread_setup(symbol, df)
                if spread_signal:
                    logger.info(f"\n{'='*70}")
                    logger.info(f"[SIGNAL] BEAR CALL SPREAD SETUP: {symbol}")
                    logger.info(f"  Preis: ${spread_signal['underlying_price']:.2f}")
                    logger.info(f"  52W-Hoch: ${spread_signal['high_52w']:.2f} ({spread_signal['proximity_pct']:+.2f}%)")
                    logger.info(f"  P/E Ratio: {spread_signal['pe_ratio']:.1f} (Branche: {spread_signal['sector_pe']:.1f})")
                    logger.info(f"  IV Rank: {spread_signal['iv_rank']:.1f}")
                    logger.info(f"  Short Call: {spread_signal['short_strike']} (Delta ~{spread_signal['short_delta']:.2f})")
                    logger.info(f"  Long Call: {spread_signal['long_strike']}")
                    logger.info(f"  DTE: {spread_signal['recommended_dte']}")
                    logger.info(f"  Net Premium: ${spread_signal['net_premium']:.2f} (bereinigt: ${spread_signal['adjusted_net_premium']:.2f})")
                    logger.info(f"  Max Risk: ${spread_signal['max_risk']:.2f}")
                    logger.info(f"  Kommission: €{spread_signal['commission']:.2f}")
                    logger.info(f"  R/R Ratio: {spread_signal['rr_ratio']:.2f}")
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(spread_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[BEAR CALL SPREAD] {symbol}",
                        message=f"52W-Hoch Setup @ ${spread_signal['underlying_price']:.2f}\\n" +
                               f"Spread: {spread_signal['short_strike']}/{spread_signal['long_strike']} DTE: {spread_signal['recommended_dte']}\\n" +
                               f"P/E: {spread_signal['pe_ratio']:.1f} | IV Rank: {spread_signal['iv_rank']:.1f}\\n" +
                               f"Net Premium: ${spread_signal['net_premium']:.2f} (€{spread_signal['commission']:.2f} Kommission)\\n" +
                               f"Max Risk: ${spread_signal['max_risk']:.2f} | R/R: {spread_signal['rr_ratio']:.2f}\\n" +
                               f"💰 {spread_signal['recommendation']}",
                        priority=1
                    )
                
                # Covered Call Setup (Verkauf von Calls auf eigene Aktien)
                covered_call_signal = self.check_covered_call_setup(symbol, df)
                if covered_call_signal:
                    logger.info(f"\n{'='*70}")
                    logger.info(f"[SIGNAL] COVERED CALL SETUP: {symbol}")
                    logger.info(f"  Preis: ${covered_call_signal['underlying_price']:.2f}")
                    logger.info(f"  52W-Hoch: ${covered_call_signal['high_52w']:.2f} ({covered_call_signal['proximity_pct']:+.2f}%)")
                    logger.info(f"  Portfolio: {covered_call_signal['owned_quantity']} Aktien @ ${covered_call_signal['avg_cost']:.2f}")
                    logger.info(f"  Unrealized P&L: ${covered_call_signal['unrealized_pnl']:.2f}")
                    logger.info(f"  P/E Ratio: {covered_call_signal['pe_ratio']:.1f} (Branche: {covered_call_signal['sector_pe']:.1f})")
                    logger.info(f"  IV Rank: {covered_call_signal['iv_rank']:.1f}")
                    logger.info(f"  Call Strike: {covered_call_signal['call_strike']} (Delta ~{covered_call_signal['call_delta']:.2f})")
                    logger.info(f"  Premium/Kontrakt: ${covered_call_signal['premium_per_contract']:.2f}")
                    logger.info(f"  Max Kontrakte: {covered_call_signal['max_contracts']}")
                    logger.info(f"  Max Profit/Kontrakt: ${covered_call_signal['max_profit_per_contract']:.2f}")
                    logger.info(f"  Max Risk/Kontrakt: ${covered_call_signal['max_risk_per_contract']:.2f}")
                    logger.info(f"  DTE: {covered_call_signal['recommended_dte']}")
                    logger.info(f"  Kommission: €{covered_call_signal['commission']:.2f}")
                    logger.info(f"  R/R Ratio: {covered_call_signal['rr_ratio']:.2f}")
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(covered_call_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[COVERED CALL] {symbol}",
                        message=f"Portfolio Position @ ${covered_call_signal['underlying_price']:.2f}\\n" +
                               f"Strike: {covered_call_signal['call_strike']} DTE: {covered_call_signal['recommended_dte']}\\n" +
                               f"Premium: ${covered_call_signal['premium_per_contract']:.2f} | Max Kontrakte: {covered_call_signal['max_contracts']}\\n" +
                               f"Max Profit: ${covered_call_signal['max_profit_per_contract']:.2f} | Risk: ${covered_call_signal['max_risk_per_contract']:.2f}\\n" +
                               f"P/E: {covered_call_signal['pe_ratio']:.1f} | IV Rank: {covered_call_signal['iv_rank']:.1f}\\n" +
                               f"💰 {covered_call_signal['recommendation']}",
                        priority=1
                    )
                
                # Covered Call Exit Signals (für bestehende Positionen)
                covered_call_exit = self.check_covered_call_exit_signals(symbol, df)
                if covered_call_exit:
                    logger.info(f"\n{'='*70}")
                    logger.info(f"[EXIT SIGNAL] COVERED CALL EXIT: {symbol}")
                    logger.info(f"  Grund: {covered_call_exit['reason']}")
                    logger.info(f"  Aktueller Preis: ${covered_call_exit['current_price']:.2f}")
                    logger.info(f"  Strike: {covered_call_exit['strike']}")
                    logger.info(f"  DTE: {covered_call_exit['dte']}")
                    logger.info(f"  Entry Premium: ${covered_call_exit['entry_premium']:.2f}")
                    logger.info(f"  Unrealized P&L: ${covered_call_exit['unrealized_pnl']:.2f}")
                    logger.info(f"  Nachricht: {covered_call_exit['message']}")
                    logger.info(f"{'='*70}")
                    
                    # Speichere Exit-Signal
                    self.db.save_options_signal(covered_call_exit)
                    
                    # Sende dringende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[COVERED CALL EXIT] {symbol}",
                        message=f"🚨 {covered_call_exit['message']}\\n" +
                               f"Strike: {covered_call_exit['strike']} | DTE: {covered_call_exit['dte']}\\n" +
                               f"Aktueller Preis: ${covered_call_exit['current_price']:.2f}\\n" +
                               f"Unrealized P&L: ${covered_call_exit['unrealized_pnl']:.2f}",
                        priority=2  # Hohe Priorität für Exit-Signale
                    )
                
                time.sleep(2)  # Rate Limiting zwischen Symbolen
                
            except Exception as e:
                logger.error(f"[FEHLER] Fehler bei {symbol}: {e}", exc_info=True)
        
        logger.info(f"\n{'='*70}")
        logger.info(f"Scan abgeschlossen")
        logger.info(f"Naechster Options-Scan in {opt_config.OPTIONS_SCAN_INTERVAL}s ({opt_config.OPTIONS_SCAN_INTERVAL/60:.0f} min)")
        logger.info(f"{'='*70}\n")
    
    # ========================================================================
    # SERVICE CONTROL
    # ========================================================================
    
    def connect_to_tws(self) -> bool:
        """Verbindet mit TWS."""
        try:
            logger.info(f"Verbinde mit TWS: {self.host}:{self.port}")
            self.connect(self.host, self.port, self.client_id)
            
            import threading
            api_thread = threading.Thread(target=self.run, daemon=True)
            api_thread.start()
            
            timeout = 10
            start_time = time.time()
            while not self.connected and (time.time() - start_time) < timeout:
                time.sleep(0.1)
            
            if self.connected:
                logger.info("[OK] TWS Verbindung aktiv")
                return True
            else:
                logger.error("[FEHLER] TWS Verbindung Timeout")
                return False
                
        except Exception as e:
            logger.error(f"[FEHLER] TWS Verbindungsfehler: {e}")
            return False
    
    def disconnect_from_tws(self):
        """Trennt Verbindung zu TWS."""
        if self.connected:
            self.disconnect()
            logger.info("[OK] TWS Verbindung getrennt")
    
    def run_service(self):
        """Startet den Options-Scanner Service."""
        self.running = True
        
        logger.info("\n" + "="*70)
        logger.info("  OPTIONS-SCANNER GESTARTET")
        logger.info("="*70)
        logger.info(f"Watchlist: {len(self.watchlist)} Symbole")
        logger.info(f"Scan-Intervall: {opt_config.OPTIONS_SCAN_INTERVAL}s")
        logger.info(f"Handelszeiten: {opt_config.TRADING_START_HOUR}:{opt_config.TRADING_START_MINUTE:02d} - {opt_config.TRADING_END_HOUR}:{opt_config.TRADING_END_MINUTE:02d} EST")
        logger.info("="*70 + "\n")
        
        # Initial Scan
        self.scan_for_options_signals()
        
        # Hauptschleife
        while self.running:
            try:
                time.sleep(opt_config.OPTIONS_SCAN_INTERVAL)
                self.scan_for_options_signals()
                
            except KeyboardInterrupt:
                logger.info("\n[WARNUNG] Shutdown Signal empfangen...")
                break
            except Exception as e:
                logger.error(f"[FEHLER] Fehler im Scanner: {e}", exc_info=True)
                time.sleep(60)
    
    def stop_service(self):
        """Stoppt den Service."""
        self.running = False
        self.disconnect_from_tws()
        self.db.close()
        logger.info("[OK] Service gestoppt")


def signal_handler(sig, frame):
    """Signal Handler für sauberes Beenden."""
    print("\n\n[STOP] Beende Options-Scanner...")
    if scanner_instance:
        scanner_instance.stop_service()
    sys.exit(0)


scanner_instance = None


def main():
    """Hauptfunktion."""
    global scanner_instance
    
    import signal as sig_module
    sig_module.signal(sig_module.SIGINT, signal_handler)
    sig_module.signal(sig_module.SIGTERM, signal_handler)
    
    print("\n" + "="*70)
    print("  TWS OPTIONS-SCANNER")
    print("  Konträre 52-Wochen-Extrem-Strategie")
    print("="*70)
    print(f"  TWS: {config.IB_HOST}:{config.IB_PORT}")
    print(f"  Modus: {'PAPER' if config.IS_PAPER_TRADING else 'LIVE'}")
    print(f"  Pushover: {'Aktiv' if config.PUSHOVER_USER_KEY else 'Inaktiv'}")
    print(f"  Scan-Intervall: {opt_config.OPTIONS_SCAN_INTERVAL}s")
    print("="*70 + "\n")
    
    try:
        scanner = OptionsScanner()
        scanner_instance = scanner
        
        # Test Pushover
        if config.PUSHOVER_USER_KEY:
            logger.info("Teste Pushover Verbindung...")
            scanner.notifier.test_notification()
        
        # Verbinde TWS
        if not scanner.connect_to_tws():
            logger.error("[FEHLER] TWS Verbindung fehlgeschlagen!")
            logger.error("Stelle sicher, dass TWS läuft und API aktiviert ist.")
            return
        
        # Starte Service
        scanner.run_service()
        
    except Exception as e:
        logger.error(f"[FEHLER] Kritischer Fehler: {e}", exc_info=True)
    finally:
        if scanner_instance:
            scanner_instance.stop_service()


if __name__ == "__main__":
    import sys
    
    try:
        logging.basicConfig(
            level=getattr(logging, config.LOG_LEVEL),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(os.path.join(os.path.dirname(__file__), "logs", "options_scanner.log")),
                logging.StreamHandler()
            ]
        )
    except PermissionError:
        # Fallback: Nur Konsolen-Logging wenn Datei-Lock
        logging.basicConfig(
            level=getattr(logging, config.LOG_LEVEL),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler()
            ]
        )
        print("WARNUNG: Options-Scanner Log-Datei gesperrt - verwende nur Konsolen-Logging")
    
    # Erstelle Logs-Verzeichnis
    os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)
    
    main()
