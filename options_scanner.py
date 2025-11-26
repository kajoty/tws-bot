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
from tws_bot.core.signals import calculate_options_trade_cushion_impact

# Yahoo Finance für VIX Fallback (direkt über requests)
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

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
        
        # Lade Fundamentaldaten für fundamentale Analyse
        self.fundamental_data_cache = self._load_fundamental_data_cache()
        
        self.connected = False
        self.next_valid_order_id = None
        
        # Request Management
        self.request_id_counter = 1000  # Start bei 1000 um Konflikte zu vermeiden
        self.pending_requests: Dict[int, Dict] = {}
        
        # Daten-Cache
        self.historical_data_cache: Dict[str, pd.DataFrame] = {}
        self.historical_data_last_update: Dict[str, datetime] = {}  # Timestamp des letzten Updates
        self.options_chain_cache: Dict[str, List] = {}
        
        # Aktive Positionen
        self.active_positions: Dict[str, Dict] = {}
        
        # VIX Cache für Marktrisiko-Analyse
        self.vix_cache: Optional[float] = None
        self.vix_last_update: Optional[datetime] = None
        
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
                earnings_date = datetime.fromisoformat(cached_data['earnings_date'])
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
    
    def _load_fundamental_data_cache(self) -> Dict[str, Dict]:
        """
        Lädt Fundamentaldaten für alle Watchlist-Symbole in den Cache.
        
        Returns:
            Dict mit Symbol -> fundamental data
        """
        fundamental_cache = {}
        
        try:
            logger.info("Lade Fundamentaldaten für fundamentale Analyse...")
            
            for symbol in self.watchlist:
                try:
                    data = self.db.get_fundamental_data(symbol, max_age_days=7)
                    if data:
                        fundamental_cache[symbol] = data
                        logger.debug(f"Fundamentaldaten geladen für {symbol}")
                    else:
                        logger.debug(f"Keine Fundamentaldaten für {symbol}")
                except Exception as e:
                    logger.warning(f"Fehler beim Laden von Fundamentaldaten für {symbol}: {e}")
            
            logger.info(f"Fundamentaldaten-Cache geladen: {len(fundamental_cache)}/{len(self.watchlist)} Symbole")
            
        except Exception as e:
            logger.error(f"Fehler beim Laden des Fundamentaldaten-Cache: {e}")
        
        return fundamental_cache
    
    def _calculate_fundamental_score(self, symbol: str) -> Dict[str, float]:
        """
        Berechnet fundamentale Scores für verschiedene Strategien.
        
        Args:
            symbol: Ticker Symbol
            
        Returns:
            Dict mit Scores: overall, value, growth, quality, momentum, risk
        """
        if symbol not in self.fundamental_data_cache:
            return {'overall': 0, 'value': 0, 'growth': 0, 'quality': 0, 'momentum': 0, 'risk': 0}
        
        data = self.fundamental_data_cache[symbol]
        
        scores = {
            'value': self._calculate_value_score(data),
            'growth': self._calculate_growth_score(data),
            'quality': self._calculate_quality_score(data),
            'momentum': self._calculate_momentum_score(data),
            'risk': self._calculate_risk_score(data)
        }
        
        # Gesamtscore als gewichteter Durchschnitt
        weights = {'value': 0.25, 'growth': 0.20, 'quality': 0.25, 'momentum': 0.15, 'risk': 0.15}
        overall = sum(scores[k] * weights[k] for k in scores.keys())
        
        scores['overall'] = round(overall, 2)
        
        return scores
    
    def _calculate_value_score(self, data: Dict) -> float:
        """Bewertet Unternehmen basierend auf Value-Metriken."""
        score = 0
        count = 0
        
        # PE Ratio (niedriger = besser, aber nicht negativ)
        if data.get('pe_ratio') and data['pe_ratio'] > 0:
            # Bessere Formel: Höhere Strafe für hohe PE Ratios
            pe_score = max(0, min(100, 100 - (data['pe_ratio'] - 10) * 3))  # Optimal ~10-15
            score += pe_score
            count += 1
        
        # PB Ratio (niedriger = besser)
        if data.get('pb_ratio') and data['pb_ratio'] > 0:
            pb_score = max(0, min(100, 100 - (data['pb_ratio'] - 1) * 25))  # Optimal ~1-2
            score += pb_score
            count += 1
        
        # Dividend Yield (höher = besser)
        if data.get('div_yield') and data['div_yield'] >= 0:
            div_score = min(100, data['div_yield'] * 200)  # 5% = 100 Punkte
            score += div_score
            count += 1
        
        # PS Ratio (niedriger = besser)
        if data.get('ps_ratio') and data['ps_ratio'] > 0:
            ps_score = max(0, min(100, 100 - (data['ps_ratio'] - 1) * 20))  # Optimal ~1-2
            score += ps_score
            count += 1
        
        # Wenn keine Daten verfügbar, return neutral score
        if count == 0:
            return 50.0
        
        return round(score / count, 2)
    
    def _calculate_growth_score(self, data: Dict) -> float:
        """Bewertet Unternehmen basierend auf Wachstumsmetriken."""
        score = 0
        count = 0
        
        # Revenue Growth (höher = besser)
        if data.get('revenue_growth'):
            growth_score = max(0, min(100, 50 + data['revenue_growth'] * 2))  # 0% = 50, 25% = 100
            score += growth_score
            count += 1
        
        # EPS Growth (höher = besser)
        if data.get('eps_growth'):
            eps_score = max(0, min(100, 50 + data['eps_growth'] * 2))
            score += eps_score
            count += 1
        
        # PEG Ratio (niedriger = besser, aber > 0)
        if data.get('peg_ratio') and data['peg_ratio'] > 0:
            peg_score = max(0, min(100, 100 - (data['peg_ratio'] - 1) * 25))  # Optimal ~1-2
            score += peg_score
            count += 1
        
        # Book Value Growth (höher = besser)
        if data.get('book_value_growth'):
            bv_score = max(0, min(100, 50 + data['book_value_growth'] * 2))
            score += bv_score
            count += 1
        
        # Wenn keine Daten verfügbar, return neutral score
        if count == 0:
            return 50.0
        
        return round(score / count, 2)
    
    def _calculate_quality_score(self, data: Dict) -> float:
        """Bewertet Unternehmen basierend auf Qualitätsmetriken."""
        score = 0
        count = 0
        
        # ROE (höher = besser)
        if data.get('roe') and data['roe'] >= 0:
            roe_score = min(100, data['roe'] * 2)  # 50% = 100 Punkte
            score += roe_score
            count += 1
        
        # ROA (höher = besser)
        if data.get('roa') and data['roa'] >= 0:
            roa_score = min(100, data['roa'] * 5)  # 20% = 100 Punkte
            score += roa_score
            count += 1
        
        # Profit Margin (höher = besser)
        if data.get('profit_margin'):
            margin_score = max(0, min(100, 50 + data['profit_margin'] * 2))  # 0% = 50, 25% = 100
            score += margin_score
            count += 1
        
        # Operating Margin (höher = besser)
        if data.get('operating_margin'):
            op_margin_score = max(0, min(100, 50 + data['operating_margin'] * 2))
            score += op_margin_score
            count += 1
        
        # Gross Margin (höher = besser)
        if data.get('gross_margin'):
            gross_margin_score = max(0, min(100, 50 + data['gross_margin'] * 2))
            score += gross_margin_score
            count += 1
        
        # Wenn keine Daten verfügbar, return neutral score
        if count == 0:
            return 50.0
        
        return round(score / count, 2)
    
    def _calculate_momentum_score(self, data: Dict) -> float:
        """Bewertet Unternehmen basierend auf Momentum-Metriken."""
        score = 0
        count = 0
        
        # Analyst Rating (höher = besser, 1-5 Skala)
        if data.get('analyst_rating') and 1 <= data['analyst_rating'] <= 5:
            rating_score = (data['analyst_rating'] - 1) * 25  # 1 = 0, 5 = 100
            score += rating_score
            count += 1
        
        # Target Price vs Current (höher = besser)
        # Hierfür bräuchten wir den aktuellen Preis - vereinfacht als verfügbar annehmen
        if data.get('target_price') and data.get('pe_ratio'):  # Proxy für Bewertung
            target_score = 70  # Basiswert, könnte verbessert werden
            score += target_score
            count += 1
        
        # Fair Value vs PE Ratio (niedriger PE bei hohem Fair Value = gut)
        if data.get('fair_value') and data.get('pe_ratio') and data['fair_value'] > 0:
            fair_value_score = min(100, max(0, 100 - abs(data['pe_ratio'] - 15) * 2))
            score += fair_value_score
            count += 1
        
        # Wenn keine Daten verfügbar, return neutral score
        if count == 0:
            return 50.0
        
        return round(score / count, 2)
    
    def _calculate_risk_score(self, data: Dict) -> float:
        """Bewertet Unternehmen basierend auf Risikometriken."""
        score = 100  # Start mit maximaler Sicherheit
        penalties = 0
        
        # Beta (niedriger = weniger volatil = besser)
        if data.get('beta'):
            if data['beta'] > 1.5:  # Hoch volatil
                penalties += 30
            elif data['beta'] > 1.2:  # Moderat volatil
                penalties += 15
            elif data['beta'] < 0.8:  # Niedrig volatil = Bonus
                penalties -= 10
        
        # Debt/Equity implizit durch niedrige ROE/ROA (bereits in quality score)
        
        # Market Cap (größer = stabiler = besser)
        if data.get('market_cap'):
            if data['market_cap'] < 1e9:  # < 1B = Small Cap Risiko
                penalties += 20
            elif data['market_cap'] < 10e9:  # < 10B = Mid Cap
                penalties += 10
            elif data['market_cap'] > 100e9:  # > 100B = Large Cap Bonus
                penalties -= 10
        
        # Payout Ratio (zu hoch = Risiko für Dividendenkürzung)
        if data.get('payout_ratio') and data['payout_ratio'] > 100:
            penalties += 25
        elif data.get('payout_ratio') and data['payout_ratio'] > 80:
            penalties += 15
        
        # Volume (höher = besserer Handel = weniger Risiko)
        if data.get('avg_volume') and data['avg_volume'] < 100000:  # Niedriges Volumen = Risiko
            penalties += 15
        
        return max(0, round(score - penalties, 2))
    
    def get_fundamental_analysis_report(self, symbol: str) -> Dict:
        """
        Erstellt vollständigen fundamentalen Analyse-Report.
        
        Args:
            symbol: Ticker Symbol
            
        Returns:
            Dict mit ratings, recommendations, analysis
        """
        if symbol not in self.fundamental_data_cache:
            return {
                'ratings': {'value': 'N/A', 'growth': 'N/A', 'quality': 'N/A', 'risk': 'N/A'},
                'recommendations': [],
                'analysis': 'Keine Fundamentaldaten verfügbar'
            }
        
        scores = self._calculate_fundamental_score(symbol)
        data = self.fundamental_data_cache[symbol]
        
        # Bewertung in Ratings umwandeln
        def score_to_rating(score: float) -> str:
            if score >= 80: return 'Ausgezeichnet'
            elif score >= 60: return 'Gut'
            elif score >= 40: return 'Durchschnitt'
            elif score >= 20: return 'Schlecht'
            else: return 'Sehr schlecht'
        
        ratings = {
            'value': score_to_rating(scores['value']),
            'growth': score_to_rating(scores['growth']),
            'quality': score_to_rating(scores['quality']),
            'risk': score_to_rating(scores['risk'])
        }
        
        # Empfehlungen basierend auf Scores
        recommendations = []
        
        if scores['overall'] >= 70:
            recommendations.append("⭐ Starke fundamentale Basis - geeignet für langfristige Investition")
        elif scores['overall'] >= 50:
            recommendations.append("✅ Solide Fundamentaldaten - moderate Position sizing")
        else:
            recommendations.append("⚠️ Schwache Fundamentaldaten - erhöhte Vorsicht empfohlen")
        
        if scores['value'] >= 70:
            recommendations.append("💰 Unterbewertet - Value-Investment Chance")
        
        if scores['growth'] >= 70:
            recommendations.append("📈 Starkes Wachstum - Growth-Investment geeignet")
        
        if scores['quality'] >= 70:
            recommendations.append("🏆 Hohe Qualität - Blue-Chip Charakteristik")
        
        if scores['risk'] >= 70:
            recommendations.append("🛡️ Niedriges Risiko - Defensive Position")
        elif scores['risk'] <= 30:
            recommendations.append("⚠️ Hohes Risiko - Reduzierte Positionsgröße empfohlen")
        
        # Analyse-Text
        analysis = f"Fundamentale Analyse für {symbol}: "
        analysis += f"Gesamtscore {scores['overall']}/100. "
        analysis += f"Value: {ratings['value']}, Growth: {ratings['growth']}, "
        analysis += f"Quality: {ratings['quality']}, Risk: {ratings['risk']}."
        
        return {
            'ratings': ratings,
            'recommendations': recommendations,
            'analysis': analysis,
            'scores': scores,
            'data': data
        }
    
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
            # Bewertung
            'pe_ratio': None,
            'pe_forward': None,
            'peg_ratio': None,
            'pb_ratio': None,
            'ps_ratio': None,
            'ev_ebitda': None,

            # Profitabilität
            'roe': None,  # Return on Equity
            'roa': None,  # Return on Assets
            'profit_margin': None,
            'gross_margin': None,
            'operating_margin': None,

            # Cash Flow & Dividenden
            'fcf': None,
            'div_yield': None,
            'payout_ratio': None,

            # Markt & Größe
            'market_cap': None,
            'avg_volume': None,
            'beta': None,
            'shares_outstanding': None,

            # Unternehmensinfo
            'sector': None,
            'industry': None,
            'employees': None,
            'revenue': None,
            'net_income': None,

            # Wachstum
            'revenue_growth': None,
            'eps_growth': None,
            'book_value_growth': None,

            # Technische Bewertung
            'fair_value': None,
            'target_price': None,
            'analyst_rating': None
        }

        try:
            root = ET.fromstring(xml_data)

            # === BEWERTUNGS-KENNZahlen ===
            # P/E Ratio (trailing)
            pe_elem = root.find(".//Ratio[@FieldName='PEEXCLXOR']")
            if pe_elem is not None and pe_elem.text:
                fundamental['pe_ratio'] = float(pe_elem.text)

            # Forward P/E
            pe_fwd_elem = root.find(".//Ratio[@FieldName='PE1TRF12']")
            if pe_fwd_elem is not None and pe_fwd_elem.text:
                fundamental['pe_forward'] = float(pe_fwd_elem.text)

            # PEG Ratio
            peg_elem = root.find(".//Ratio[@FieldName='PEG12']")
            if peg_elem is not None and peg_elem.text:
                fundamental['peg_ratio'] = float(peg_elem.text)

            # P/B Ratio
            pb_elem = root.find(".//Ratio[@FieldName='PBEXCLXOR']")
            if pb_elem is not None and pb_elem.text:
                fundamental['pb_ratio'] = float(pb_elem.text)

            # P/S Ratio
            ps_elem = root.find(".//Ratio[@FieldName='PSEXCLXOR']")
            if ps_elem is not None and ps_elem.text:
                fundamental['ps_ratio'] = float(ps_elem.text)

            # EV/EBITDA
            ev_ebitda_elem = root.find(".//Ratio[@FieldName='EVEBITDA12']")
            if ev_ebitda_elem is not None and ev_ebitda_elem.text:
                fundamental['ev_ebitda'] = float(ev_ebitda_elem.text)

            # === PROFITABILITÄT ===
            # ROE (Return on Equity)
            roe_elem = root.find(".//Ratio[@FieldName='TTMROE']")
            if roe_elem is not None and roe_elem.text:
                fundamental['roe'] = float(roe_elem.text)

            # ROA (Return on Assets)
            roa_elem = root.find(".//Ratio[@FieldName='TTMROA']")
            if roa_elem is not None and roa_elem.text:
                fundamental['roa'] = float(roa_elem.text)

            # Profit Margin
            pm_elem = root.find(".//Ratio[@FieldName='TTMPROFMGN']")
            if pm_elem is not None and pm_elem.text:
                fundamental['profit_margin'] = float(pm_elem.text)

            # Gross Margin
            gm_elem = root.find(".//Ratio[@FieldName='TTMGROSMGN']")
            if gm_elem is not None and gm_elem.text:
                fundamental['gross_margin'] = float(gm_elem.text)

            # Operating Margin
            om_elem = root.find(".//Ratio[@FieldName='TTMOPMGN']")
            if om_elem is not None and om_elem.text:
                fundamental['operating_margin'] = float(om_elem.text)

            # === CASH FLOW & DIVIDENDEN ===
            # Free Cash Flow (Approximation)
            cfshr_elem = root.find(".//Ratio[@FieldName='TTMCFSHR']")
            shares_elem = root.find(".//SharesOut")
            if cfshr_elem is not None and shares_elem is not None:
                try:
                    cf_per_share = float(cfshr_elem.text)
                    shares_out = float(shares_elem.text)
                    fundamental['fcf'] = cf_per_share * shares_out
                    fundamental['shares_outstanding'] = shares_out
                except (ValueError, AttributeError):
                    pass

            # Dividend Yield
            dy_elem = root.find(".//Ratio[@FieldName='TTMDIVYIELD']")
            if dy_elem is not None and dy_elem.text:
                fundamental['div_yield'] = float(dy_elem.text)

            # Payout Ratio
            pr_elem = root.find(".//Ratio[@FieldName='TTMPAYRATIO']")
            if pr_elem is not None and pr_elem.text:
                fundamental['payout_ratio'] = float(pr_elem.text)

            # === MARKT & GRÖSSE ===
            # Market Cap
            mktcap_elem = root.find(".//Ratio[@FieldName='MKTCAP']")
            if mktcap_elem is not None and mktcap_elem.text:
                fundamental['market_cap'] = float(mktcap_elem.text) * 1_000_000

            # Average Volume (10-day)
            avgvol_elem = root.find(".//Ratio[@FieldName='VOL10DAVG']")
            if avgvol_elem is not None and avgvol_elem.text:
                fundamental['avg_volume'] = float(avgvol_elem.text) * 1_000_000

            # Beta
            beta_elem = root.find(".//Ratio[@FieldName='BETA']")
            if beta_elem is not None and beta_elem.text:
                fundamental['beta'] = float(beta_elem.text)

            # === UNTERNEHMENSINFO ===
            # Sector/Industry
            sector_elem = root.find(".//Industry[@type='TRBC']")
            if sector_elem is not None and sector_elem.text:
                fundamental['sector'] = sector_elem.text.strip()

            # Employees
            emp_elem = root.find(".//Employees")
            if emp_elem is not None and emp_elem.text:
                fundamental['employees'] = int(float(emp_elem.text))

            # Revenue (TTM)
            rev_elem = root.find(".//Ratio[@FieldName='TTMREV']")
            if rev_elem is not None and rev_elem.text:
                fundamental['revenue'] = float(rev_elem.text) * 1_000_000

            # Net Income (TTM)
            ni_elem = root.find(".//Ratio[@FieldName='TTMNETINC']")
            if ni_elem is not None and ni_elem.text:
                fundamental['net_income'] = float(ni_elem.text) * 1_000_000

            # === WACHSTUM ===
            # Revenue Growth (YoY)
            rev_growth_elem = root.find(".//Ratio[@FieldName='REVGRWTH1YR']")
            if rev_growth_elem is not None and rev_growth_elem.text:
                fundamental['revenue_growth'] = float(rev_growth_elem.text)

            # EPS Growth (YoY)
            eps_growth_elem = root.find(".//Ratio[@FieldName='EPSGRWTH1YR']")
            if eps_growth_elem is not None and eps_growth_elem.text:
                fundamental['eps_growth'] = float(eps_growth_elem.text)

            # Book Value Growth
            bv_growth_elem = root.find(".//Ratio[@FieldName='BVGRWTH1YR']")
            if bv_growth_elem is not None and bv_growth_elem.text:
                fundamental['book_value_growth'] = float(bv_growth_elem.text)

        except Exception as e:
            logger.error(f"[FEHLER] Fundamental-Parsing: {e}", exc_info=True)

        return fundamental

    def _calculate_fundamental_score(self, symbol: str) -> Dict[str, float]:
        """
        Berechnet umfassende fundamentale Bewertung mit mehreren Scoring-Modellen.

        Returns:
            Dict mit verschiedenen Scores (0-100 Skala)
        """
        if symbol not in self.fundamental_data_cache:
            return {'overall': 0, 'value': 0, 'growth': 0, 'quality': 0, 'momentum': 0, 'risk': 0}

        fundamentals = self.fundamental_data_cache[symbol]

        scores = {
            'value': self._calculate_value_score(fundamentals),
            'growth': self._calculate_growth_score(fundamentals),
            'quality': self._calculate_quality_score(fundamentals),
            'momentum': self._calculate_momentum_score(fundamentals),
            'risk': self._calculate_risk_score(fundamentals)
        }

        # Gewichtete Gesamtbewertung
        weights = {
            'value': 0.25,      # 25% - Unterbewertung
            'growth': 0.20,     # 20% - Wachstumspotenzial
            'quality': 0.25,    # 25% - Profitabilität & Stabilität
            'momentum': 0.15,   # 15% - Markttrends
            'risk': 0.15        # 15% - Risiko-Adjustment
        }

        overall_score = sum(scores[metric] * weight for metric, weight in weights.items())
        scores['overall'] = overall_score

        return scores

    def _calculate_value_score(self, fundamentals: Dict) -> float:
        """Bewertet Unternehmen basierend auf Value-Metriken."""
        score = 0
        count = 0
        
        # PE Ratio (niedriger = besser, aber nicht negativ)
        if fundamentals.get('pe_ratio') and fundamentals['pe_ratio'] > 0:
            # Bessere Formel: Höhere Strafe für hohe PE Ratios
            pe_score = max(0, min(100, 100 - (fundamentals['pe_ratio'] - 10) * 3))  # Optimal ~10-15
            score += pe_score
            count += 1
        
        # PB Ratio (niedriger = besser)
        if fundamentals.get('pb_ratio') and fundamentals['pb_ratio'] > 0:
            pb_score = max(0, min(100, 100 - (fundamentals['pb_ratio'] - 1) * 25))  # Optimal ~1-2
            score += pb_score
            count += 1
        
        # Dividend Yield (höher = besser)
        if fundamentals.get('div_yield') and fundamentals['div_yield'] >= 0:
            div_score = min(100, fundamentals['div_yield'] * 200)  # 5% = 100 Punkte
            score += div_score
            count += 1
        
        # PS Ratio (niedriger = besser)
        if fundamentals.get('ps_ratio') and fundamentals['ps_ratio'] > 0:
            ps_score = max(0, min(100, 100 - (fundamentals['ps_ratio'] - 1) * 20))  # Optimal ~1-2
            score += ps_score
            count += 1
        
        # EV/EBITDA (niedriger = besser)
        if fundamentals.get('ev_ebitda') and fundamentals['ev_ebitda'] > 0:
            ev_score = max(0, min(100, 100 - (fundamentals['ev_ebitda'] - 8) * 5))  # Optimal ~8-12
            score += ev_score
            count += 1
        
        # Wenn keine Daten verfügbar, return neutral score
        if count == 0:
            return 50.0
        
        return round(score / count, 2)

    def _calculate_growth_score(self, fundamentals: Dict) -> float:
        """Growth Score - Bewertet Wachstumspotenzial."""
        score = 50  # Basis-Score

        # EPS Wachstum
        eps_growth = fundamentals.get('eps_growth')
        if eps_growth is not None:
            if eps_growth > 0.20: score += 20     # 20%+ Wachstum
            elif eps_growth > 0.10: score += 10   # 10%+ Wachstum
            elif eps_growth > 0.05: score += 0    # 5%+ Wachstum
            elif eps_growth > 0.02: score -= 5    # 2%+ Wachstum
            else: score -= 15                     # Schrumpfung

        # Umsatzwachstum
        revenue_growth = fundamentals.get('revenue_growth')
        if revenue_growth is not None:
            if revenue_growth > 0.15: score += 15
            elif revenue_growth > 0.08: score += 5
            elif revenue_growth > 0.03: score += 0
            else: score -= 10

        # PEG Ratio (niedriger = besseres Wachstum pro Risiko)
        peg = fundamentals.get('peg_ratio')
        if peg is not None:
            if peg < 1.0: score += 10     # Günstiges Wachstum
            elif peg < 1.5: score += 5    # Akzeptables Wachstum
            elif peg < 2.0: score += 0    # Fair
            else: score -= 10             # Teures Wachstum

        return max(0, min(100, score))

    def _calculate_quality_score(self, fundamentals: Dict) -> float:
        """Quality Score - Bewertet Profitabilität und Stabilität."""
        score = 50  # Basis-Score

        # ROE (höher = besser)
        roe = fundamentals.get('roe')
        if roe is not None:
            if roe > 0.20: score += 20     # Exzellente Profitabilität
            elif roe > 0.15: score += 10   # Sehr gute Profitabilität
            elif roe > 0.10: score += 0    # Gute Profitabilität
            elif roe > 0.05: score -= 5    # Akzeptabel
            else: score -= 15              # Schwache Profitabilität

        # ROA (höher = besser)
        roa = fundamentals.get('roa')
        if roa is not None:
            if roa > 0.10: score += 15
            elif roa > 0.07: score += 5
            elif roa > 0.03: score += 0
            else: score -= 10

        # Profit Margins (höher = besser)
        profit_margin = fundamentals.get('profit_margin')
        if profit_margin is not None:
            if profit_margin > 0.15: score += 10
            elif profit_margin > 0.08: score += 5
            elif profit_margin > 0.03: score += 0
            else: score -= 10

        # Operating Margin (höher = besser)
        op_margin = fundamentals.get('operating_margin')
        if op_margin is not None:
            if op_margin > 0.15: score += 10
            elif op_margin > 0.10: score += 5
            else: score -= 5

        # Free Cash Flow positiv?
        fcf = fundamentals.get('fcf')
        if fcf is not None and fcf > 0:
            score += 10
        elif fcf is not None and fcf < 0:
            score -= 15

        return max(0, min(100, score))

    def _calculate_momentum_score(self, fundamentals: Dict) -> float:
        """Momentum Score - Bewertet Markttrends und Analystenmeinungen."""
        score = 50  # Basis-Score

        # Analyst Rating (höher = besser)
        analyst_rating = fundamentals.get('analyst_rating')
        if analyst_rating is not None:
            if analyst_rating >= 4.5: score += 20    # Strong Buy
            elif analyst_rating >= 4.0: score += 10  # Buy
            elif analyst_rating >= 3.5: score += 0   # Hold
            elif analyst_rating >= 3.0: score -= 10  # Underperform
            else: score -= 20                        # Sell

        # Target Price vs aktuelle Bewertung
        target_price = fundamentals.get('target_price')
        fair_value = fundamentals.get('fair_value')

        # Vereinfacht: Wenn Target Price deutlich höher als Fair Value
        if target_price is not None and fair_value is not None and target_price > fair_value * 1.1:
            score += 10
        elif target_price is not None and fair_value is not None and target_price < fair_value * 0.9:
            score -= 10

        return max(0, min(100, score))

    def _calculate_risk_score(self, fundamentals: Dict) -> float:
        """Risk Score - Bewertet Risiko (höher = weniger Risiko)."""
        score = 50  # Basis-Score

        # Beta (niedriger = weniger volatil = besser)
        beta = fundamentals.get('beta')
        if beta is not None:
            if beta < 0.8: score += 20      # Sehr stabil
            elif beta < 1.0: score += 10    # Stabil
            elif beta < 1.2: score += 0     # Marktneutral
            elif beta < 1.5: score -= 10    # Volatile
            else: score -= 20               # Sehr volatil

        # Marktkapitalisierung (höher = weniger Risiko)
        market_cap = fundamentals.get('market_cap')
        if market_cap is not None:
            if market_cap > 100_000_000_000: score += 15  # Large Cap
            elif market_cap > 10_000_000_000: score += 10  # Mid Cap
            elif market_cap > 2_000_000_000: score += 0    # Small Cap
            else: score -= 10                              # Micro Cap

        # Volumen (höher = besser für Liquidität)
        avg_volume = fundamentals.get('avg_volume')
        if avg_volume is not None:
            if avg_volume > 10_000_000: score += 10   # Sehr liquide
            elif avg_volume > 1_000_000: score += 5   # Gut liquide
            elif avg_volume > 100_000: score += 0    # Akzeptabel
            else: score -= 10                         # Illiquide

        return max(0, min(100, score))

    def get_fundamental_analysis_report(self, symbol: str) -> Dict:
        """
        Erstellt detaillierten fundamentalen Analysebericht für ein Symbol.
        
        Returns:
            Dict mit Scores, Metriken und Empfehlungen
        """
        if symbol not in self.fundamental_data_cache:
            return {'error': 'Keine Fundamentaldaten verfügbar'}
        
        fundamentals = self.fundamental_data_cache[symbol]
        scores = self._calculate_fundamental_score(symbol)
        
        # Bewertungs-Kategorien
        value_rating = "Unterbewertet" if scores['value'] >= 70 else "Fair" if scores['value'] >= 50 else "Überbewertet"
        growth_rating = "Hohes Wachstum" if scores['growth'] >= 70 else "Moderates Wachstum" if scores['growth'] >= 50 else "Schwaches Wachstum"
        quality_rating = "Exzellent" if scores['quality'] >= 80 else "Gut" if scores['quality'] >= 60 else "Akzeptabel" if scores['quality'] >= 40 else "Schwach"
        risk_rating = "Sehr sicher" if scores['risk'] >= 80 else "Sicher" if scores['risk'] >= 60 else "Moderates Risiko" if scores['risk'] >= 40 else "Hohes Risiko"
        
        # Strategie-Empfehlungen basierend auf Scores
        recommendations = []
        
        # Covered Call Empfehlung
        if scores['overall'] >= 65 and scores['quality'] >= 60 and scores['risk'] >= 50:
            recommendations.append("Covered Call")
        
        # Long Put Empfehlung
        if scores['overall'] >= 60 and scores['value'] >= 65 and scores['quality'] >= 55:
            recommendations.append("Long Put")
        
        # Long Call Empfehlung
        if scores['overall'] >= 60 and scores['growth'] >= 60 and scores['momentum'] >= 55 and scores['quality'] >= 50:
            recommendations.append("Long Call")
        
        # Cash Secured Put Empfehlung (sehr konservativ)
        if scores['overall'] >= 75 and scores['value'] >= 70 and scores['quality'] >= 75 and scores['risk'] >= 65:
            recommendations.append("Cash Secured Put")
        
        return {
            'symbol': symbol,
            'scores': scores,
            'ratings': {
                'value': value_rating,
                'growth': growth_rating,
                'quality': quality_rating,
                'risk': risk_rating
            },
            'key_metrics': {
                'pe_ratio': fundamentals.get('pe_ratio'),
                'pb_ratio': fundamentals.get('pb_ratio'),
                'roe': fundamentals.get('roe'),
                'roa': fundamentals.get('roa'),
                'fcf_yield': (fundamentals.get('fcf', 0) / fundamentals.get('market_cap', 1)) if fundamentals.get('market_cap', 1) > 0 else 0,
                'beta': fundamentals.get('beta'),
                'div_yield': fundamentals.get('div_yield'),
                'revenue_growth': fundamentals.get('revenue_growth'),
                'eps_growth': fundamentals.get('eps_growth')
            },
            'recommendations': recommendations,
            'last_updated': fundamentals.get('last_updated')
        }

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
        
        # === ERWEITERTE FUNDAMENTALE BEWERTUNG ===
        fundamental_scores = self._calculate_fundamental_score(symbol)
        overall_score = fundamental_scores['overall']
        quality_score = fundamental_scores['quality']
        risk_score = fundamental_scores['risk']
        
        # Für Covered Calls: Hohe Qualität + moderate Bewertung + niedriges Risiko
        min_overall_score = 65  # Mindestens gute fundamentale Bewertung
        min_quality_score = 60  # Solide Profitabilität erforderlich
        min_risk_score = 50     # Moderate Risiko-Toleranz
        
        if overall_score < min_overall_score:
            logger.debug(f"[DEBUG] {symbol}: Fundamentale Bewertung zu schwach ({overall_score:.1f})")
            return None
            
        if quality_score < min_quality_score:
            logger.debug(f"[DEBUG] {symbol}: Profitabilität ungenügend ({quality_score:.1f})")
            return None
            
        if risk_score < min_risk_score:
            logger.debug(f"[DEBUG] {symbol}: Risiko zu hoch ({risk_score:.1f})")
            return None
        
        logger.info(f"[INFO] {symbol}: Fundamentale Scores - Overall: {overall_score:.1f}, Quality: {quality_score:.1f}, Risk: {risk_score:.1f}")
        
        # Traditionelle P/E Filter als Backup
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
        
        signal_data = {
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
        
        # VIX-basierte Risikofilterung anwenden
        filtered_signal = self.apply_vix_risk_filter(signal_data)
        return filtered_signal
    
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
    
    def calculate_adx(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        Berechnet Average Directional Index (ADX) für Trend-Stärke-Messung.
        
        Args:
            df: DataFrame mit OHLC Daten
            period: Periode für EMA-Berechnung (default: 14)
            
        Returns:
            ADX Wert (0-100), wobei >25 auf starken Trend hinweist
        """
        if len(df) < period + 1:
            return 0.0
        
        # True Range
        df = df.copy()
        df['prev_close'] = df['close'].shift(1)
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['prev_close']),
                abs(df['low'] - df['prev_close'])
            )
        )
        
        # Directional Movement
        df['dm_plus'] = np.where(
            (df['high'] - df['high'].shift(1)) > (df['low'].shift(1) - df['low']),
            np.maximum(df['high'] - df['high'].shift(1), 0),
            0
        )
        df['dm_minus'] = np.where(
            (df['low'].shift(1) - df['low']) > (df['high'] - df['high'].shift(1)),
            np.maximum(df['low'].shift(1) - df['low'], 0),
            0
        )
        
        # Directional Indicators (EMA)
        df['atr'] = df['tr'].ewm(span=period, adjust=False).mean()
        df['di_plus'] = (df['dm_plus'].ewm(span=period, adjust=False).mean() / df['atr']) * 100
        df['di_minus'] = (df['dm_minus'].ewm(span=period, adjust=False).mean() / df['atr']) * 100
        
        # ADX
        df['dx'] = (abs(df['di_plus'] - df['di_minus']) / (df['di_plus'] + df['di_minus'])) * 100
        adx = df['dx'].ewm(span=period, adjust=False).mean()
        
        # Rückgabe des letzten ADX-Werts (normalisiert auf 0-1)
        if not adx.empty:
            latest_adx = adx.iloc[-1]
            return latest_adx / 100.0  # Normalisiert auf 0-1 für einfachere Konfiguration
        
        return 0.0
    
    def get_vix_value(self) -> Optional[float]:
        """
        Ruft den aktuellen VIX-Wert ab mit mehreren Fallback-Quellen.
        Priorität: Cache -> Yahoo Finance -> TWS -> CBOE Website -> Historische Schätzung
        
        Returns:
            VIX-Wert oder None bei Fehler
        """
        # Cache prüfen (VIX ändert sich nicht so schnell)
        if (self.vix_cache is not None and 
            self.vix_last_update is not None and
            (datetime.now() - self.vix_last_update).seconds < 300):  # 5 Minuten Cache
            return self.vix_cache
        
        # Fallback 1: Versuche VIX von Yahoo Finance zu holen (zuverlässig und kostenlos)
        if REQUESTS_AVAILABLE:
            yahoo_vix = self.get_vix_from_yahoo_finance()
            if yahoo_vix:
                # Cache aktualisieren
                self.vix_cache = yahoo_vix
                self.vix_last_update = datetime.now()
                return yahoo_vix
        
        # Fallback 2: Versuche VIX von TWS zu holen (live Daten)
        try:
            # VIX als Index-Kontrakt definieren
            vix_contract = Contract()
            vix_contract.symbol = "VIX"
            vix_contract.secType = "IND"  # Index
            vix_contract.exchange = "CBOE"
            vix_contract.currency = "USD"
            
            # Request ID für diesen Request
            req_id = self.request_id_counter
            self.request_id_counter += 1
            
            # Market Data Request
            self.reqMktData(req_id, vix_contract, "", False, False, [])
            
            # Request in pending_requests speichern
            self.pending_requests[req_id] = {
                'type': 'vix_data',
                'symbol': 'VIX',
                'contract': vix_contract,
                'timestamp': datetime.now()
            }
            
            # Warte auf Response (max 10 Sekunden)
            timeout = 10
            start_time = datetime.now()
            
            while (datetime.now() - start_time).seconds < timeout:
                if req_id in self.pending_requests:
                    request_data = self.pending_requests[req_id]
                    if 'price' in request_data:
                        vix_value = request_data['price']
                        # Cache aktualisieren
                        self.vix_cache = vix_value
                        self.vix_last_update = datetime.now()
                        
                        # Market Data Request abbrechen
                        self.cancelMktData(req_id)
                        
                        logger.debug(f"[VIX] Aktueller VIX: {vix_value}")
                        return vix_value
                
                time.sleep(0.1)
            
            # Timeout
            logger.warning("[VIX] Timeout beim Abrufen des VIX-Werts von TWS")
            if req_id in self.pending_requests:
                del self.pending_requests[req_id]
            self.cancelMktData(req_id)
            
        except Exception as e:
            logger.warning(f"[VIX] TWS VIX Request fehlgeschlagen: {e}")
        
        # Fallback 3: Versuche VIX von CBOE-Website zu holen
        if REQUESTS_AVAILABLE and BS4_AVAILABLE:
            cboe_vix = self.get_vix_from_cboe()
            if cboe_vix:
                # Cache aktualisieren
                self.vix_cache = cboe_vix
                self.vix_last_update = datetime.now()
                return cboe_vix
        
        # Letzter Fallback: Verwende historische VIX-Schätzung
        logger.warning("[VIX] Alle externen Quellen fehlgeschlagen - verwende historische Schätzung")
        estimated_vix = self.get_estimated_vix_value()
        if estimated_vix:
            self.vix_cache = estimated_vix
            self.vix_last_update = datetime.now()
            return estimated_vix
        
        return None
    
    def get_vix_from_yahoo_finance(self) -> Optional[float]:
        """
        Holt VIX-Daten direkt von Yahoo Finance über requests.
        Alternative zu yfinance, das eingestellt wurde.
        """
        if not REQUESTS_AVAILABLE:
            logger.warning("[VIX] requests nicht verfügbar - Yahoo Finance Fallback nicht möglich")
            return None

        try:
            # Yahoo Finance API URL für VIX (^VIX)
            url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
            params = {
                'period1': int((datetime.now() - timedelta(days=1)).timestamp()),
                'period2': int(datetime.now().timestamp()),
                'interval': '1m',
                'includePrePost': 'false'
            }

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()

            data = response.json()

            # Extrahiere den letzten Close-Preis
            if 'chart' in data and 'result' in data['chart'] and data['chart']['result']:
                result = data['chart']['result'][0]
                if 'indicators' in result and 'quote' in result['indicators']:
                    quotes = result['indicators']['quote'][0]
                    if 'close' in quotes and quotes['close']:
                        # Nimm den letzten gültigen Close-Wert
                        close_prices = [price for price in quotes['close'] if price is not None]
                        if close_prices:
                            vix_value = close_prices[-1]
                            if vix_value > 0:
                                logger.info(f"[VIX] Yahoo Finance VIX erhalten: {vix_value}")
                                return vix_value

            logger.warning("[VIX] Keine gültigen VIX-Daten von Yahoo Finance erhalten")
            return None

        except Exception as e:
            logger.error(f"[VIX] Yahoo Finance Request fehlgeschlagen: {e}")
            return None
    
    def get_vix_from_cboe(self) -> Optional[float]:
        """
        Holt VIX-Daten von der offiziellen CBOE-Website.
        Verwendet Web-Scraping als zusätzliche Fallback-Quelle.
        """
        if not REQUESTS_AVAILABLE or not BS4_AVAILABLE:
            logger.warning("[VIX] requests oder BeautifulSoup nicht verfügbar - CBOE Fallback nicht möglich")
            return None

        try:
            url = "https://www.cboe.com/tradable-products/vix/"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Suche nach dem VIX Spot Price
            # Aus der Website-Struktur: "## $18.56" unter "VIX SPOT PRICE"
            vix_elements = soup.find_all(text=lambda text: text and '$' in text and any(char.isdigit() for char in text))

            for element in vix_elements:
                text = element.strip()
                if '$' in text:
                    # Extrahiere den numerischen Wert
                    import re
                    match = re.search(r'\$([0-9]+\.[0-9]+)', text)
                    if match:
                        vix_value = float(match.group(1))
                        if 5 <= vix_value <= 100:  # Plausibilitätsprüfung
                            logger.info(f"[VIX] CBOE VIX erhalten: {vix_value}")
                            return vix_value

            logger.warning("[VIX] Kein gültiger VIX-Wert auf CBOE-Website gefunden")
            return None

        except Exception as e:
            logger.error(f"[VIX] CBOE Web-Scraping fehlgeschlagen: {e}")
            return None
    
    def get_estimated_vix_value(self) -> Optional[float]:
        """
        Gibt eine geschätzte VIX-Schätzung basierend auf historischen Durchschnittswerten.
        Letzter Fallback wenn alle externen Quellen fehlschlagen.
        """
        # Historische VIX-Durchschnittswerte (basierend auf langfristigen Daten)
        # Normalerweise liegt VIX zwischen 10-30
        # Bei Unsicherheit: konservative Schätzung von 20
        estimated_vix = 20.0
        
        logger.info(f"[VIX] Verwende geschätzten VIX-Wert: {estimated_vix} (Fallback)")
        return estimated_vix
    
    def assess_market_risk_vix(self) -> Dict[str, any]:
        """
        Bewertet das aktuelle Marktrisiko basierend auf VIX.
        
        Returns:
            Dict mit Risiko-Level und Empfehlungen
        """
        vix_value = self.get_vix_value()
        
        if vix_value is None:
            return {
                'risk_level': 'UNKNOWN',
                'vix_value': None,
                'recommendation': 'VIX nicht verfügbar - normale Risikoparameter verwenden',
                'max_risk_multiplier': 1.0
            }
        
        # VIX-basierte Risiko-Einstufung
        if vix_value < opt_config.VIX_VERY_LOW_MAX:
            risk_level = 'VERY_LOW'
            recommendation = 'Sehr niedrige Volatilität - aggressive Strategien möglich'
            max_risk_multiplier = opt_config.VIX_RISK_MULTIPLIER_VERY_LOW
        elif vix_value < opt_config.VIX_LOW_MAX:
            risk_level = 'LOW'
            recommendation = 'Niedrige Volatilität - normale Strategien'
            max_risk_multiplier = opt_config.VIX_RISK_MULTIPLIER_LOW
        elif vix_value < opt_config.VIX_MODERATE_MAX:
            risk_level = 'MODERATE'
            recommendation = 'Moderate Volatilität - konservative Positionierung'
            max_risk_multiplier = opt_config.VIX_RISK_MULTIPLIER_MODERATE
        elif vix_value < opt_config.VIX_HIGH_MAX:
            risk_level = 'HIGH'
            recommendation = 'Hohe Volatilität - Risiko reduzieren, nur beste Setups'
            max_risk_multiplier = opt_config.VIX_RISK_MULTIPLIER_HIGH
        elif vix_value < opt_config.VIX_VERY_HIGH_MAX:
            risk_level = 'VERY_HIGH'
            recommendation = 'Sehr hohe Volatilität - nur Cash-Secured Puts oder warten'
            max_risk_multiplier = opt_config.VIX_RISK_MULTIPLIER_VERY_HIGH
        else:
            risk_level = 'EXTREME'
            recommendation = 'Extreme Volatilität - alle Options-Strategien pausieren'
            max_risk_multiplier = opt_config.VIX_RISK_MULTIPLIER_EXTREME
        
        return {
            'risk_level': risk_level,
            'vix_value': vix_value,
            'recommendation': recommendation,
            'max_risk_multiplier': max_risk_multiplier
        }
    
    def apply_vix_risk_filter(self, signal: Dict) -> Optional[Dict]:
        """
        Wendet VIX-basierte Risikofilter auf Signale an.
        
        Args:
            signal: Signal-Dict von check_*_setup()
            
        Returns:
            Modifiziertes Signal oder None wenn blockiert
        """
        if not opt_config.USE_VIX_FILTER:
            return signal
        
        market_risk = self.assess_market_risk_vix()
        
        # Log VIX-Status
        logger.info(f"[VIX] Markt-Risiko: {market_risk['risk_level']} (VIX: {market_risk['vix_value']})")
        logger.info(f"[VIX] {market_risk['recommendation']}")
        
        # Extreme Volatilität: Alle Signale blockieren
        if market_risk['risk_level'] == 'EXTREME':
            logger.warning(f"[VIX] Signal blockiert - Extreme Volatilität (VIX: {market_risk['vix_value']})")
            return None
        
        # Sehr hohe Volatilität: Nur konservative Strategien
        if market_risk['risk_level'] == 'VERY_HIGH':
            conservative_strategies = ['SHORT_PUT', 'COVERED_CALL']
            if signal.get('type') not in conservative_strategies:
                logger.warning(f"[VIX] {signal.get('type')} blockiert - nur konservative Strategien bei hohem VIX")
                return None
        
        # Hohe Volatilität: Risiko reduzieren
        if market_risk['risk_level'] in ['HIGH', 'VERY_HIGH']:
            # Risiko basierend auf VIX anpassen
            original_risk = signal.get('max_risk', 0)
            adjusted_risk = original_risk * market_risk['max_risk_multiplier']
            
            if adjusted_risk < original_risk:
                logger.info(f"[VIX] Risiko reduziert: ${original_risk:.2f} -> ${adjusted_risk:.2f}")
                signal['max_risk'] = adjusted_risk
                signal['vix_adjusted'] = True
        
        # VIX-Info zum Signal hinzufügen
        signal['vix_info'] = market_risk
        
        return signal
    
    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        """
        Callback für Market Data Updates (VIX).
        """
        if reqId in self.pending_requests:
            request_data = self.pending_requests[reqId]
            if request_data.get('type') == 'vix_data':
                # LAST Price (Tick Type 4)
                if tickType == 4 and price > 0:
                    request_data['price'] = price
    
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
        
        # === ERWEITERTE FUNDAMENTALE BEWERTUNG ===
        fundamental_scores = self._calculate_fundamental_score(symbol)
        overall_score = fundamental_scores['overall']
        value_score = fundamental_scores['value']
        quality_score = fundamental_scores['quality']
        
        # Für Long Puts: Starke Value-Charakteristik + gute Qualität
        min_overall_score = 60  # Solide fundamentale Bewertung
        min_value_score = 65    # Stark unterbewertet bevorzugt
        min_quality_score = 55  # Mindestens akzeptable Profitabilität
        
        if overall_score < min_overall_score:
            logger.debug(f"[DEBUG] {symbol}: Fundamentale Bewertung zu schwach ({overall_score:.1f})")
            return None
            
        if value_score < min_value_score:
            logger.debug(f"[DEBUG] {symbol}: Nicht genügend unterbewertet ({value_score:.1f})")
            return None
            
        if quality_score < min_quality_score:
            logger.debug(f"[DEBUG] {symbol}: Profitabilität ungenügend ({quality_score:.1f})")
            return None
        
        logger.info(f"[INFO] {symbol}: Fundamentale Scores - Overall: {overall_score:.1f}, Value: {value_score:.1f}, Quality: {quality_score:.1f}")
        
        # Traditionelle P/E Filter als Backup
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
        
        # PRE-TRADE CUSHION ANALYSIS
        try:
            portfolio_data = self.get_portfolio_data()
            cushion_impact = calculate_options_trade_cushion_impact(portfolio_data, max_risk, 'long_put')
            
            # Trade ablehnen wenn Cushion unter kritische Grenze fällt
            if not cushion_impact['acceptable']:
                logger.warning(f"[CUSHION] {symbol}: Long Put Signal abgelehnt - Cushion würde auf {cushion_impact['new_cushion']:.1%} fallen")
                return None
            
            # Warnung bei hohem Risiko
            if cushion_impact['risk_level'] in ['HIGH', 'CRITICAL']:
                logger.warning(f"[CUSHION] {symbol}: Long Put würde Cushion auf {cushion_impact['new_cushion']:.1%} reduzieren ({cushion_impact['risk_level']} Risiko)")
        except Exception as e:
            logger.warning(f"[CUSHION] {symbol}: Cushion-Analyse fehlgeschlagen: {e}")
            # Bei Fehler: Fortfahren (konservative Entscheidung)

        signal_data = {
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
            'cushion_impact': cushion_impact if 'cushion_impact' in locals() else None,
            'timestamp': datetime.now()
        }
        
        # VIX-basierte Risikofilterung anwenden
        filtered_signal = self.apply_vix_risk_filter(signal_data)
        return filtered_signal
    
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
        
        # === ERWEITERTE FUNDAMENTALE BEWERTUNG ===
        fundamental_scores = self._calculate_fundamental_score(symbol)
        overall_score = fundamental_scores['overall']
        growth_score = fundamental_scores['growth']
        momentum_score = fundamental_scores['momentum']
        quality_score = fundamental_scores['quality']
        
        # Für Long Calls: Wachstum + Momentum + solide Qualität
        min_overall_score = 60  # Solide fundamentale Bewertung
        min_growth_score = 60   # Wachstumspotenzial erforderlich
        min_momentum_score = 55 # Positive Marktmeinung
        min_quality_score = 50  # Mindestens akzeptable Profitabilität
        
        if overall_score < min_overall_score:
            logger.debug(f"[DEBUG] {symbol}: Fundamentale Bewertung zu schwach ({overall_score:.1f})")
            return None
            
        if growth_score < min_growth_score:
            logger.debug(f"[DEBUG] {symbol}: Wachstum ungenügend ({growth_score:.1f})")
            return None
            
        if momentum_score < min_momentum_score:
            logger.debug(f"[DEBUG] {symbol}: Momentum zu schwach ({momentum_score:.1f})")
            return None
            
        if quality_score < min_quality_score:
            logger.debug(f"[DEBUG] {symbol}: Qualität ungenügend ({quality_score:.1f})")
            return None
        
        logger.info(f"[INFO] {symbol}: Fundamentale Scores - Overall: {overall_score:.1f}, Growth: {growth_score:.1f}, Momentum: {momentum_score:.1f}, Quality: {quality_score:.1f}")
        
        # Traditionelle FCF Filter als Backup
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
        
        # TREND-FILTER: Vermeide starke Trends bei Mean-Reversion Strategien
        if opt_config.USE_TREND_FILTER:
            adx_value = self.calculate_adx(df, opt_config.ADX_PERIOD)
            if adx_value > opt_config.TREND_STRENGTH_MAX:
                logger.info(f"[TREND] {symbol}: Long Call Signal blockiert - ADX {adx_value:.2f} > {opt_config.TREND_STRENGTH_MAX} (starker Trend)")
                return None
            logger.debug(f"[TREND] {symbol}: ADX {adx_value:.2f} <= {opt_config.TREND_STRENGTH_MAX} (Trend OK)")
        
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
        
        # PRE-TRADE CUSHION ANALYSIS
        try:
            portfolio_data = self.get_portfolio_data()
            cushion_impact = calculate_options_trade_cushion_impact(portfolio_data, abs(max_risk), 'long_call')
            
            # Trade ablehnen wenn Cushion unter kritische Grenze fällt
            if not cushion_impact['acceptable']:
                logger.warning(f"[CUSHION] {symbol}: Long Call Signal abgelehnt - Cushion würde auf {cushion_impact['new_cushion']:.1%} fallen")
                return None
            
            # Warnung bei hohem Risiko
            if cushion_impact['risk_level'] in ['HIGH', 'CRITICAL']:
                logger.warning(f"[CUSHION] {symbol}: Long Call würde Cushion auf {cushion_impact['new_cushion']:.1%} reduzieren ({cushion_impact['risk_level']} Risiko)")
        except Exception as e:
            logger.warning(f"[CUSHION] {symbol}: Cushion-Analyse fehlgeschlagen: {e}")

        # Erstelle Signal-Dict
        signal_data = {
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
            'cushion_impact': cushion_impact if 'cushion_impact' in locals() else None,
            'timestamp': datetime.now()
        }
        
        # VIX-basierte Risiko-Filter anwenden
        filtered_signal = self.apply_vix_risk_filter(signal_data)
        return filtered_signal
    
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
        
        # === ERWEITERTE FUNDAMENTALE BEWERTUNG ===
        fundamental_scores = self._calculate_fundamental_score(symbol)
        overall_score = fundamental_scores['overall']
        value_score = fundamental_scores['value']
        quality_score = fundamental_scores['quality']
        risk_score = fundamental_scores['risk']
        
        # Für Short Puts: Extrem konservativ - nur beste Aktien
        min_overall_score = 75  # Exzellente fundamentale Bewertung
        min_value_score = 70    # Stark unterbewertet
        min_quality_score = 75  # Exzellente Profitabilität
        min_risk_score = 65     # Sehr niedriges Risiko
        
        if overall_score < min_overall_score:
            logger.debug(f"[DEBUG] {symbol}: Fundamentale Bewertung ungenügend ({overall_score:.1f})")
            return None
            
        if value_score < min_value_score:
            logger.debug(f"[DEBUG] {symbol}: Nicht genügend unterbewertet ({value_score:.1f})")
            return None
            
        if quality_score < min_quality_score:
            logger.debug(f"[DEBUG] {symbol}: Profitabilität ungenügend ({quality_score:.1f})")
            return None
            
        if risk_score < min_risk_score:
            logger.debug(f"[DEBUG] {symbol}: Risiko zu hoch ({risk_score:.1f})")
            return None
        
        logger.info(f"[INFO] {symbol}: Fundamentale Scores - Overall: {overall_score:.1f}, Value: {value_score:.1f}, Quality: {quality_score:.1f}, Risk: {risk_score:.1f}")
        
        # Traditionelle Filter als Backup
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
        
        # TREND-FILTER: Vermeide starke Trends bei Mean-Reversion Strategien
        if opt_config.USE_TREND_FILTER:
            adx_value = self.calculate_adx(df, opt_config.ADX_PERIOD)
            if adx_value > opt_config.TREND_STRENGTH_MAX:
                logger.info(f"[TREND] {symbol}: Short Put Signal blockiert - ADX {adx_value:.2f} > {opt_config.TREND_STRENGTH_MAX} (starker Trend)")
                return None
            logger.debug(f"[TREND] {symbol}: ADX {adx_value:.2f} <= {opt_config.TREND_STRENGTH_MAX} (Trend OK)")
        
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
        
        # PRE-TRADE CUSHION ANALYSIS
        try:
            portfolio_data = self.get_portfolio_data()
            cushion_impact = calculate_options_trade_cushion_impact(portfolio_data, abs(max_risk), 'short_put')
            
            # Trade ablehnen wenn Cushion unter kritische Grenze fällt
            if not cushion_impact['acceptable']:
                logger.warning(f"[CUSHION] {symbol}: Short Put Signal abgelehnt - Cushion würde auf {cushion_impact['new_cushion']:.1%} fallen")
                return None
            
            # Warnung bei hohem Risiko
            if cushion_impact['risk_level'] in ['HIGH', 'CRITICAL']:
                logger.warning(f"[CUSHION] {symbol}: Short Put würde Cushion auf {cushion_impact['new_cushion']:.1%} reduzieren ({cushion_impact['risk_level']} Risiko)")
        except Exception as e:
            logger.warning(f"[CUSHION] {symbol}: Cushion-Analyse fehlgeschlagen: {e}")

        signal_data = {
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
            'cushion_impact': cushion_impact if 'cushion_impact' in locals() else None,
            'timestamp': datetime.now()
        }
        
        # VIX-basierte Risikofilterung anwenden
        filtered_signal = self.apply_vix_risk_filter(signal_data)
        return filtered_signal
    
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
        
        # TREND-FILTER: Vermeide starke Trends bei Mean-Reversion Strategien
        if opt_config.USE_TREND_FILTER:
            adx_value = self.calculate_adx(df, opt_config.ADX_PERIOD)
            if adx_value > opt_config.TREND_STRENGTH_MAX:
                logger.info(f"[TREND] {symbol}: Bear Call Spread Signal blockiert - ADX {adx_value:.2f} > {opt_config.TREND_STRENGTH_MAX} (starker Trend)")
                return None
            logger.debug(f"[TREND] {symbol}: ADX {adx_value:.2f} <= {opt_config.TREND_STRENGTH_MAX} (Trend OK)")
        
        # Alle Kriterien erfüllt!
        # Kostenberechnung
        costs = self.calculate_strategy_costs('BEAR_CALL_SPREAD', 1, spread_candidate['net_premium'])
        profitability = self.calculate_strategy_profitability('BEAR_CALL_SPREAD', {
            'max_profit': spread_candidate['net_premium'],
            'max_risk': spread_candidate['max_risk'],
            'net_premium': spread_candidate['net_premium'],
            'quantity': 1
        })
        
        # PRE-TRADE CUSHION ANALYSIS
        try:
            portfolio_data = self.get_portfolio_data()
            cushion_impact = calculate_options_trade_cushion_impact(portfolio_data, abs(spread_candidate['max_risk']), 'bear_call_spread')
            
            # Trade ablehnen wenn Cushion unter kritische Grenze fällt
            if not cushion_impact['acceptable']:
                logger.warning(f"[CUSHION] {symbol}: Bear Call Spread Signal abgelehnt - Cushion würde auf {cushion_impact['new_cushion']:.1%} fallen")
                return None
            
            # Warnung bei hohem Risiko
            if cushion_impact['risk_level'] in ['HIGH', 'CRITICAL']:
                logger.warning(f"[CUSHION] {symbol}: Bear Call Spread würde Cushion auf {cushion_impact['new_cushion']:.1%} reduzieren ({cushion_impact['risk_level']} Risiko)")
        except Exception as e:
            logger.warning(f"[CUSHION] {symbol}: Cushion-Analyse fehlgeschlagen: {e}")

        signal_data = {
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
            'cushion_impact': cushion_impact if 'cushion_impact' in locals() else None,
            'timestamp': datetime.now()
        }
        
        # VIX-basierte Risikofilterung anwenden
        filtered_signal = self.apply_vix_risk_filter(signal_data)
        return filtered_signal
    
    def check_volatility_strategies(self, symbol: str, df: pd.DataFrame) -> Optional[Dict]:
        """
        Prüft Volatilitäts-Strategien für volatile Märkte.
        
        Strategien:
        1. Long Straddle: Long Call + Long Put gleichen Strike (erwartet Bewegung)
        2. Long Strangle: Long OTM Call + Long OTM Put (kostengünstiger)
        3. Short Straddle: Short Call + Short Put (nur bei hoher IV)
        
        Returns:
            Signal-Dict oder None
        """
        if len(df) == 0:
            return None
        
        # Stelle sicher, dass Earnings-Daten verfügbar sind
        self._ensure_earnings_data(symbol)
        
        # Earnings-Risiko-Prüfung: Blockiere während Earnings-Periode
        if self._is_earnings_risk_period(symbol):
            logger.info(f"[INFO] {symbol}: Volatilitäts-Strategien blockiert - Earnings-Periode")
            return None
        
        current_price = df.iloc[-1]['close']
        high_52w, low_52w = self.calculate_52w_extremes(df)
        
        # Volatilitäts-Filter: Hohe IV für Short-Strategien, moderate für Long
        if symbol not in self.options_chain_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Options-Chain verfügbar")
            return None
        
        # Finde optimale Strikes für Straddle/Strangle
        volatility_signal = self.find_volatility_strikes(symbol, current_price)
        
        if not volatility_signal:
            return None
        
        # IV Rank Prüfung
        self.request_option_greeks(
            symbol,
            volatility_signal['strike'],
            'C',
            volatility_signal['expiry']
        )
        self.request_option_greeks(
            symbol,
            volatility_signal['strike'],
            'P',
            volatility_signal['expiry']
        )
        
        self.wait_for_requests(timeout=10)
        
        # Sammle IV Werte
        iv_values = []
        for req_data in self.pending_requests.values():
            if req_data.get('symbol') == symbol:
                greeks = req_data.get('greeks', {})
                iv = greeks.get('implied_volatility')
                if iv:
                    iv_values.append(iv)
        
        avg_iv = np.mean(iv_values) if iv_values else None
        
        if avg_iv:
            iv_rank = self.calculate_iv_rank(symbol, avg_iv * 100)
        else:
            iv_rank = 50.0
        
        # Für Volatilitätsstrategien: Hohe IV bevorzugt (60-90%)
        if iv_rank < 60:
            logger.debug(f"[DEBUG] {symbol}: IV Rank {iv_rank:.1f} < 60 (zu niedrig für Vol-Strategien)")
            return None
        
        # Fundamentale Prüfung: Solide Unternehmen
        if symbol not in self.fundamental_data_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Fundamentaldaten")
            return None
        
        fundamentals = self.fundamental_data_cache[symbol]
        market_cap = fundamentals.get('market_cap', 0)
        avg_volume = fundamentals.get('avg_volume', 0)
        
        # Filter
        if market_cap < opt_config.MIN_MARKET_CAP or avg_volume < opt_config.MIN_AVG_VOLUME:
            return None
        
        # PRE-TRADE CUSHION ANALYSIS
        max_risk = volatility_signal['max_risk']
        try:
            portfolio_data = self.get_portfolio_data()
            cushion_impact = calculate_options_trade_cushion_impact(portfolio_data, abs(max_risk), volatility_signal['strategy_type'])
            
            if not cushion_impact['acceptable']:
                logger.warning(f"[CUSHION] {symbol}: {volatility_signal['strategy_type']} Signal abgelehnt - Cushion würde auf {cushion_impact['new_cushion']:.1%} fallen")
                return None
        except Exception as e:
            logger.warning(f"[CUSHION] {symbol}: Cushion-Analyse fehlgeschlagen: {e}")
        
        # Kostenberechnung
        costs = self.calculate_strategy_costs(volatility_signal['strategy_type'], 1, volatility_signal['net_premium'])
        profitability = self.calculate_strategy_profitability(volatility_signal['strategy_type'], {
            'max_profit': volatility_signal['max_profit'],
            'max_risk': volatility_signal['max_risk'],
            'net_premium': volatility_signal['net_premium'],
            'quantity': 1
        })
        
        return {
            'type': volatility_signal['strategy_type'],
            'symbol': symbol,
            'underlying_price': current_price,
            'high_52w': high_52w,
            'low_52w': low_52w,
            'iv_rank': iv_rank,
            'market_cap': market_cap,
            'avg_volume': avg_volume,
            # Strategie-spezifische Daten
            'strike': volatility_signal['strike'],
            'call_strike': volatility_signal.get('call_strike'),
            'put_strike': volatility_signal.get('put_strike'),
            'net_premium': volatility_signal['net_premium'],
            'max_profit': volatility_signal['max_profit'],
            'max_risk': volatility_signal['max_risk'],
            'breakeven_upper': volatility_signal['breakeven_upper'],
            'breakeven_lower': volatility_signal['breakeven_lower'],
            'recommended_expiry': volatility_signal['expiry'],
            'recommended_dte': volatility_signal['dte'],
            # Kosten & Rentabilität
            'commission': costs['commission'],
            'total_cost': costs['total_cost'],
            'adjusted_net_premium': profitability['adjusted_net_premium'],
            'rr_ratio': profitability['rr_ratio'],
            'profitability_pct': profitability['profitability_pct'],
            'expected_value': profitability['expected_value'],
            'cushion_impact': cushion_impact if 'cushion_impact' in locals() else None,
            'timestamp': datetime.now()
        }
        """
        Prüft Seitwärts-Trend Strategien für ruhige Märkte ohne 52W-Extreme.
        
        Strategien:
        1. Iron Condor: Verkauft OTM Call + Put Spreads (profitiert von Seitwärts)
        2. Butterfly: Long mittlerer Strike, Short äußere Strikes
        3. Calendar Spread: Long längerfristig, Short kurzfristig gleichen Strike
        
        Returns:
            Signal-Dict oder None
        """
        if len(df) == 0:
            return None
        
        # Stelle sicher, dass Earnings-Daten verfügbar sind
        self._ensure_earnings_data(symbol)
        
        # Earnings-Risiko-Prüfung
        if self._is_earnings_risk_period(symbol):
            logger.info(f"[INFO] {symbol}: Seitwärts-Strategien blockiert - Earnings-Periode")
            return None
        
        current_price = df.iloc[-1]['close']
        high_52w, low_52w = self.calculate_52w_extremes(df)
        
        # Trend-Filter: Nur Seitwärts-Trends (niedriger ADX)
        adx_value = self.calculate_adx(df, opt_config.ADX_PERIOD)
        if adx_value > 0.4:  # Nur sehr schwache Trends (ADX < 40%)
            logger.debug(f"[TREND] {symbol}: Seitwärts-Strategien übersprungen - ADX {adx_value:.2f} > 0.4")
            return None
        
        # Volatilitäts-Filter: Moderate IV für Seitwärts-Strategien
        if symbol not in self.options_chain_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Options-Chain verfügbar")
            return None
        
        # Finde optimale Strikes für Iron Condor
        iron_condor = self.find_iron_condor_strikes(symbol, current_price)
        
        if not iron_condor:
            return None
        
        # IV Rank Prüfung (moderate Volatilität bevorzugt)
        self.request_option_greeks(
            symbol,
            iron_condor['call_short_strike'],
            'C',
            iron_condor['expiry']
        )
        self.request_option_greeks(
            symbol,
            iron_condor['put_short_strike'],
            'P',
            iron_condor['expiry']
        )
        
        self.wait_for_requests(timeout=10)
        
        # Sammle IV Werte
        iv_values = []
        for req_data in self.pending_requests.values():
            if req_data.get('symbol') == symbol:
                greeks = req_data.get('greeks', {})
                iv = greeks.get('implied_volatility')
                if iv:
                    iv_values.append(iv)
        
        avg_iv = np.mean(iv_values) if iv_values else None
        
        if avg_iv:
            iv_rank = self.calculate_iv_rank(symbol, avg_iv * 100)  # IV in Prozent
        else:
            iv_rank = 50.0
        
        # Für Seitwärts-Strategien: Moderate IV bevorzugt (30-70%)
        if iv_rank < 30 or iv_rank > 70:
            logger.debug(f"[DEBUG] {symbol}: IV Rank {iv_rank:.1f} außerhalb 30-70% Bereich")
            return None
        
        # Fundamentale Prüfung: Stabile Unternehmen
        if symbol not in self.fundamental_data_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Fundamentaldaten")
            return None
        
        fundamentals = self.fundamental_data_cache[symbol]
        pe_ratio = fundamentals.get('pe_ratio')
        market_cap = fundamentals.get('market_cap', 0)
        avg_volume = fundamentals.get('avg_volume', 0)
        
        # Filter
        if market_cap < opt_config.MIN_MARKET_CAP or avg_volume < opt_config.MIN_AVG_VOLUME:
            return None
        
        # Moderate Bewertung (nicht überbewertet)
        if pe_ratio and pe_ratio > 25:
            logger.debug(f"[DEBUG] {symbol}: P/E {pe_ratio:.1f} > 25 (zu hoch für Seitwärts)")
            return None
        
        # PRE-TRADE CUSHION ANALYSIS
        max_risk = iron_condor['max_risk']
        try:
            portfolio_data = self.get_portfolio_data()
            cushion_impact = calculate_options_trade_cushion_impact(portfolio_data, abs(max_risk), 'iron_condor')
            
            if not cushion_impact['acceptable']:
                logger.warning(f"[CUSHION] {symbol}: Iron Condor Signal abgelehnt - Cushion würde auf {cushion_impact['new_cushion']:.1%} fallen")
                return None
        except Exception as e:
            logger.warning(f"[CUSHION] {symbol}: Cushion-Analyse fehlgeschlagen: {e}")
        
        # Kostenberechnung
        costs = self.calculate_strategy_costs('IRON_CONDOR', 1, iron_condor['net_credit'])
        profitability = self.calculate_strategy_profitability('IRON_CONDOR', {
            'max_profit': iron_condor['max_profit'],
            'max_risk': iron_condor['max_risk'],
            'net_premium': iron_condor['net_credit'],
            'quantity': 1
        })
        
        # Erstelle Signal-Dict
        signal_data = {
            'type': 'IRON_CONDOR',
            'symbol': symbol,
            'underlying_price': current_price,
            'high_52w': high_52w,
            'low_52w': low_52w,
            'adx_value': adx_value,
            'iv_rank': iv_rank,
            'market_cap': market_cap,
            'pe_ratio': pe_ratio,
            'avg_volume': avg_volume,
            # Iron Condor spezifische Daten
            'call_short_strike': iron_condor['call_short_strike'],
            'call_long_strike': iron_condor['call_long_strike'],
            'put_short_strike': iron_condor['put_short_strike'],
            'put_long_strike': iron_condor['put_long_strike'],
            'net_credit': iron_condor['net_credit'],
            'max_profit': iron_condor['max_profit'],
            'max_risk': iron_condor['max_risk'],
            'breakeven_upper': iron_condor['breakeven_upper'],
            'breakeven_lower': iron_condor['breakeven_lower'],
            'recommended_expiry': iron_condor['expiry'],
            'recommended_dte': iron_condor['dte'],
            # Kosten & Rentabilität
            'commission': costs['commission'],
            'total_cost': costs['total_cost'],
            'adjusted_net_credit': profitability['adjusted_net_credit'],
            'rr_ratio': profitability['rr_ratio'],
            'profitability_pct': profitability['profitability_pct'],
            'expected_value': profitability['expected_value'],
            'cushion_impact': cushion_impact if 'cushion_impact' in locals() else None,
            'timestamp': datetime.now()
        }
        
        # VIX-basierte Risiko-Filter anwenden
        filtered_signal = self.apply_vix_risk_filter(signal_data)
        return filtered_signal
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
        
        # TREND-FILTER: Vermeide starke Trends bei Mean-Reversion Strategien
        if opt_config.USE_TREND_FILTER:
            adx_value = self.calculate_adx(df, opt_config.ADX_PERIOD)
            if adx_value > opt_config.TREND_STRENGTH_MAX:
                logger.info(f"[TREND] {symbol}: Bull Put Spread Signal blockiert - ADX {adx_value:.2f} > {opt_config.TREND_STRENGTH_MAX} (starker Trend)")
                return None
            logger.debug(f"[TREND] {symbol}: ADX {adx_value:.2f} <= {opt_config.TREND_STRENGTH_MAX} (Trend OK)")
        
        # Alle Kriterien erfüllt!
        # Kostenberechnung
        costs = self.calculate_strategy_costs('BULL_PUT_SPREAD', 1, spread_candidate['net_premium'])
        profitability = self.calculate_strategy_profitability('BULL_PUT_SPREAD', {
            'max_profit': spread_candidate['net_premium'],
            'max_risk': spread_candidate['max_risk'],
            'net_premium': spread_candidate['net_premium'],
            'quantity': 1
        })
        
        # PRE-TRADE CUSHION ANALYSIS
        try:
            portfolio_data = self.get_portfolio_data()
            cushion_impact = calculate_options_trade_cushion_impact(portfolio_data, abs(spread_candidate['max_risk']), 'bull_put_spread')
            
            # Trade ablehnen wenn Cushion unter kritische Grenze fällt
            if not cushion_impact['acceptable']:
                logger.warning(f"[CUSHION] {symbol}: Bull Put Spread Signal abgelehnt - Cushion würde auf {cushion_impact['new_cushion']:.1%} fallen")
                return None
            
            # Warnung bei hohem Risiko
            if cushion_impact['risk_level'] in ['HIGH', 'CRITICAL']:
                logger.warning(f"[CUSHION] {symbol}: Bull Put Spread würde Cushion auf {cushion_impact['new_cushion']:.1%} reduzieren ({cushion_impact['risk_level']} Risiko)")
        except Exception as e:
            logger.warning(f"[CUSHION] {symbol}: Cushion-Analyse fehlgeschlagen: {e}")

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
            'cushion_impact': cushion_impact if 'cushion_impact' in locals() else None,
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
    
    def find_iron_condor_strikes(self, symbol: str, current_price: float) -> Optional[Dict]:
        """
        Findet optimale Strikes für Iron Condor (Seitwärts-Strategie).
        
        Iron Condor = Short OTM Call + Long weiter OTM Call + Short OTM Put + Long weiter OTM Put
        
        Returns:
            Dict mit allen Strikes, Premiums, Risiken
        """
        if symbol not in self.options_chain_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Options-Chain verfügbar")
            return None
        
        chain = self.options_chain_cache[symbol]
        expirations = chain['expirations']
        strikes = chain['strikes']
        
        # Filtere Expirations nach DTE (30-60 Tage für Seitwärts-Strategien)
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
        
        # Wähle mittlere Expiration
        suitable_expirations.sort(key=lambda x: abs(x[1] - 45))  # Ziel: 45 Tage
        selected_expiry, selected_dte = suitable_expirations[0]
        
        # Finde optimale Wing Width (Abstand zwischen Short und Long Strikes)
        # Für Seitwärts: 5-10% Wing Width
        wing_width_pct = 0.08  # 8% Wing Width
        wing_width = current_price * wing_width_pct
        
        # Short Strikes: 1 Standardabweichung OTM (konservativ)
        # Verwende historische Volatilität als Proxy für Strike-Abstand
        try:
            if symbol in self.historical_data_cache:
                df = self.historical_data_cache[symbol]
                returns = np.log(df['close'] / df['close'].shift(1))
                hist_vol = returns.std() * np.sqrt(252)
                strike_offset = current_price * hist_vol * np.sqrt(selected_dte / 365)
            else:
                strike_offset = current_price * 0.05  # Fallback: 5%
        except:
            strike_offset = current_price * 0.05
        
        # Short Call Strike: Leicht OTM über Current Price
        call_short_target = current_price + strike_offset
        call_short_strike = min(strikes, key=lambda x: abs(x - call_short_target) if x > current_price else float('inf'))
        
        # Long Call Strike: Weiter OTM
        call_long_target = call_short_strike + wing_width
        call_long_strike = min(strikes, key=lambda x: abs(x - call_long_target) if x > call_short_strike else float('inf'))
        
        # Short Put Strike: Leicht OTM unter Current Price
        put_short_target = current_price - strike_offset
        put_short_strike = min(strikes, key=lambda x: abs(x - put_short_target) if x < current_price else float('inf'))
        
        # Long Put Strike: Weiter OTM
        put_long_target = put_short_strike - wing_width
        put_long_strike = max(strikes, key=lambda x: abs(x - put_long_target) if x < put_short_strike else float('-inf'))
        
        # Berechne Risiko und Reward
        call_spread_width = call_long_strike - call_short_strike
        put_spread_width = put_short_strike - put_long_strike
        
        # Max Risk = Wing Width (kleinster Spread) minus Net Credit
        max_risk = min(call_spread_width, put_spread_width) * 100
        
        # Geschätzte Net Credit (konservativ: 20-30% der Max Risk)
        estimated_net_credit = max_risk * 0.25
        
        # Max Profit = Net Credit
        max_profit = estimated_net_credit
        
        # Breakeven Punkte
        breakeven_upper = call_short_strike + (estimated_net_credit / 100)
        breakeven_lower = put_short_strike - (estimated_net_credit / 100)
        
        return {
            'call_short_strike': call_short_strike,
            'call_long_strike': call_long_strike,
            'put_short_strike': put_short_strike,
            'put_long_strike': put_long_strike,
            'expiry': selected_expiry,
            'dte': selected_dte,
            'net_credit': estimated_net_credit,
            'max_profit': max_profit,
            'max_risk': max_risk,
            'breakeven_upper': breakeven_upper,
            'breakeven_lower': breakeven_lower,
            'wing_width': wing_width
        }
    
    def find_volatility_strikes(self, symbol: str, current_price: float) -> Optional[Dict]:
        """
        Findet optimale Strikes für Volatilitätsstrategien.
        
        Returns:
            Dict mit Strikes, Premiums, Risiken für Straddle/Strangle
        """
        if symbol not in self.options_chain_cache:
            logger.warning(f"[WARNUNG] {symbol}: Keine Options-Chain verfügbar")
            return None
        
        chain = self.options_chain_cache[symbol]
        expirations = chain['expirations']
        strikes = chain['strikes']
        
        # Filtere Expirations nach DTE (15-45 Tage für Volatilitätsstrategien)
        min_dte = 15
        max_dte = 45
        
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
        
        # Wähle kürzeste Expiration (höhere Volatilität)
        suitable_expirations.sort(key=lambda x: x[1])
        selected_expiry, selected_dte = suitable_expirations[0]
        
        # ATM Strike für Straddle
        atm_strike = min(strikes, key=lambda x: abs(x - current_price))
        
        # OTM Strikes für Strangle (kostengünstiger)
        strike_offset = current_price * 0.05  # 5% OTM
        call_strike = min(strikes, key=lambda x: abs(x - (current_price + strike_offset)) if x > current_price else float('inf'))
        put_strike = min(strikes, key=lambda x: abs(x - (current_price - strike_offset)) if x < current_price else float('inf'))
        
        # Geschätzte Premiums (würden von TWS kommen)
        # Konservative Schätzungen basierend auf IV
        estimated_atm_premium = current_price * 0.08  # 8% für ATM bei hoher Vol
        estimated_otm_premium = current_price * 0.04  # 4% für OTM
        
        # Entscheide Strategie basierend auf verfügbaren Daten
        # Long Straddle: Höheres Risiko, höhere Chance
        # Long Strangle: Niedrigeres Risiko, niedrigere Chance
        
        # Verwende Strangle als Standard (kostengünstiger)
        strategy_type = "LONG_STRANGLE"
        total_premium = estimated_otm_premium * 2  # Call + Put
        max_risk = total_premium  # Max Loss = Premium bezahlt
        max_profit = float('inf')  # Theoretisch unbegrenzt
        
        # Breakeven Punkte
        breakeven_upper = call_strike + total_premium
        breakeven_lower = put_strike - total_premium
        
        signal_data = {
            'strategy_type': strategy_type,
            'strike': atm_strike,  # Für Straddle
            'call_strike': call_strike,  # Für Strangle
            'put_strike': put_strike,  # Für Strangle
            'expiry': selected_expiry,
            'dte': selected_dte,
            'net_premium': total_premium,
            'max_profit': max_profit,
            'max_risk': max_risk,
            'breakeven_upper': breakeven_upper,
            'breakeven_lower': breakeven_lower
        }
        
        # VIX-basierte Risikofilterung anwenden
        filtered_signal = self.apply_vix_risk_filter(signal_data)
        return filtered_signal
    
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
                
                # Seitwärts-Strategien (Iron Condor für ruhige Märkte ohne 52W-Extreme)
                sideways_signal = self.check_sideways_strategies(symbol, df)
                if sideways_signal:
                    logger.info(f"\n{'='*70}")
                    logger.info(f"[SIGNAL] IRON CONDOR SETUP: {symbol}")
                    logger.info(f"  Preis: ${sideways_signal['underlying_price']:.2f}")
                    logger.info(f"  52W-Range: ${sideways_signal['low_52w']:.2f} - ${sideways_signal['high_52w']:.2f}")
                    logger.info(f"  ADX (Trend-Stärke): {sideways_signal['adx_value']:.2f} (< 0.4 = Seitwärts)")
                    logger.info(f"  P/E Ratio: {sideways_signal['pe_ratio']:.1f}")
                    logger.info(f"  IV Rank: {sideways_signal['iv_rank']:.1f}")
                    logger.info(f"  Iron Condor Spreads:")
                    logger.info(f"    Call Spread: {sideways_signal['call_short_strike']}/{sideways_signal['call_long_strike']}")
                    logger.info(f"    Put Spread: {sideways_signal['put_short_strike']}/{sideways_signal['put_long_strike']}")
                    logger.info(f"  DTE: {sideways_signal['recommended_dte']}")
                    logger.info(f"  Net Credit: ${sideways_signal['net_credit']:.2f}")
                    logger.info(f"  Max Profit: ${sideways_signal['max_profit']:.2f}")
                    logger.info(f"  Max Risk: ${sideways_signal['max_risk']:.2f}")
                    logger.info(f"  Breakeven: ${sideways_signal['breakeven_lower']:.2f} - ${sideways_signal['breakeven_upper']:.2f}")
                    logger.info(f"  Kommission: €{sideways_signal['commission']:.2f}")
                    logger.info(f"  R/R Ratio: {sideways_signal['rr_ratio']:.2f}")
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(sideways_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[IRON CONDOR] {symbol}",
                        message=f"Seitwärts Setup @ ${sideways_signal['underlying_price']:.2f}\\n" +
                               f"ADX: {sideways_signal['adx_value']:.2f} (ruhiger Markt)\\n" +
                               f"Call Spread: {sideways_signal['call_short_strike']}/{sideways_signal['call_long_strike']}\\n" +
                               f"Put Spread: {sideways_signal['put_short_strike']}/{sideways_signal['put_long_strike']}\\n" +
                               f"Net Credit: ${sideways_signal['net_credit']:.2f} | Max Risk: ${sideways_signal['max_risk']:.2f}\\n" +
                               f"Breakeven: ${sideways_signal['breakeven_lower']:.2f} - ${sideways_signal['breakeven_upper']:.2f}\\n" +
                               f"P/E: {sideways_signal['pe_ratio']:.1f} | IV Rank: {sideways_signal['iv_rank']:.1f}\\n" +
                               f"💰 {sideways_signal['recommendation']}",
                        priority=1
                    )
                
                # Volatilitäts-Strategien (für volatile Märkte)
                volatility_signal = self.check_volatility_strategies(symbol, df)
                if volatility_signal:
                    logger.info(f"\n{'='*70}")
                    logger.info(f"[SIGNAL] {volatility_signal['type']} SETUP: {symbol}")
                    logger.info(f"  Preis: ${volatility_signal['underlying_price']:.2f}")
                    logger.info(f"  52W-Range: ${volatility_signal['low_52w']:.2f} - ${volatility_signal['high_52w']:.2f}")
                    logger.info(f"  IV Rank: {volatility_signal['iv_rank']:.1f} (hoch - volatile Markt)")
                    
                    if volatility_signal['type'] == 'LONG_STRANGLE':
                        logger.info(f"  Long Strangle:")
                        logger.info(f"    Call Strike: {volatility_signal['call_strike']}")
                        logger.info(f"    Put Strike: {volatility_signal['put_strike']}")
                    else:
                        logger.info(f"  ATM Strike: {volatility_signal['strike']}")
                    
                    logger.info(f"  DTE: {volatility_signal['recommended_dte']}")
                    logger.info(f"  Net Premium: ${volatility_signal['net_premium']:.2f}")
                    logger.info(f"  Max Profit: Unlimited")
                    logger.info(f"  Max Risk: ${volatility_signal['max_risk']:.2f}")
                    logger.info(f"  Breakeven: ${volatility_signal['breakeven_lower']:.2f} - ${volatility_signal['breakeven_upper']:.2f}")
                    logger.info(f"  Kommission: €{volatility_signal['commission']:.2f}")
                    logger.info(f"  R/R Ratio: Unlimited")
                    logger.info(f"{'='*70}")
                    
                    # Speichere Signal
                    self.db.save_options_signal(volatility_signal)
                    
                    # Sende Benachrichtigung
                    self.notifier.send_notification(
                        title=f"[{volatility_signal['type']}] {symbol}",
                        message=f"Volatilitäts Setup @ ${volatility_signal['underlying_price']:.2f}\\n" +
                               f"IV Rank: {volatility_signal['iv_rank']:.1f} (hohe Volatilität)\\n" +
                               f"Net Premium: ${volatility_signal['net_premium']:.2f} | Max Risk: ${volatility_signal['max_risk']:.2f}\\n" +
                               f"Breakeven: ${volatility_signal['breakeven_lower']:.2f} - ${volatility_signal['breakeven_upper']:.2f}\\n" +
                               f"💰 {volatility_signal['recommendation']}",
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
