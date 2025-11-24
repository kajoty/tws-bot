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

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

import config
import options_config as opt_config
from database import DatabaseManager
from pushover_notifier import PushoverNotifier

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
        
        # Watchlist (wird dynamisch gefiltert)
        self.watchlist = config.WATCHLIST_STOCKS
        
        self.running = False
        
        logger.info(f"Options-Scanner initialisiert: {host}:{port} (Client ID: {client_id})")
    
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
            'timestamp': datetime.now()
        }
    
    def find_spread_strikes(self, symbol: str, current_price: float) -> Optional[Dict]:
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
    
    # ========================================================================
    # HAUPTFUNKTION
    # ========================================================================
    
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
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(put_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[LONG PUT] {symbol}",
                        message=f"52W-Hoch Setup @ ${put_signal['underlying_price']:.2f}\\n" +
                               f"Strike: {put_signal['recommended_strike']} DTE: {put_signal['recommended_dte']}\\n" +
                               f"P/E: {put_signal['pe_ratio']:.1f} | IV Rank: {put_signal['iv_rank']:.1f}",
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
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(call_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[LONG CALL] {symbol}",
                        message=f"52W-Tief Setup @ ${call_signal['underlying_price']:.2f}\\n" +
                               f"Strike: {call_signal['recommended_strike']} DTE: {call_signal['recommended_dte']}\\n" +
                               f"FCF Yield: {call_signal['fcf_yield']:.4f} | IV Rank: {call_signal['iv_rank']:.1f}",
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
                    logger.info(f"  Net Premium: ${spread_signal['net_premium']:.2f}")
                    logger.info(f"  Max Risk: ${spread_signal['max_risk']:.2f}")
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(spread_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[BEAR CALL SPREAD] {symbol}",
                        message=f"52W-Hoch Setup @ ${spread_signal['underlying_price']:.2f}\\n" +
                               f"Spread: {spread_signal['short_strike']}/{spread_signal['long_strike']} DTE: {spread_signal['recommended_dte']}\\n" +
                               f"P/E: {spread_signal['pe_ratio']:.1f} | IV Rank: {spread_signal['iv_rank']:.1f}\\n" +
                               f"Net Premium: ${spread_signal['net_premium']:.2f} | Max Risk: ${spread_signal['max_risk']:.2f}",
                        priority=1
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
    
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(os.path.join(os.path.dirname(__file__), "logs", "options_scanner.log")),
            logging.StreamHandler()
        ]
    )
    
    # Erstelle Logs-Verzeichnis
    os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)
    
    main()
