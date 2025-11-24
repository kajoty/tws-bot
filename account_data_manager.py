"""
Account Data Manager - Holt Account-Daten automatisch von TWS.
"""

import time
import logging
from typing import Dict, Optional
from ibapi.client import EClient
from ibapi.wrapper import EWrapper

import config

logger = logging.getLogger(__name__)


class AccountDataManager(EWrapper, EClient):
    """Holt Account-Informationen von TWS (Net Liquidation, Buying Power, etc.)."""
    
    def __init__(self):
        EClient.__init__(self, self)
        
        self.host = config.IB_HOST
        self.port = config.IB_PORT
        self.client_id = 4  # Eigene Client ID
        
        self.connected = False
        self.account_data = {}
        self.account_name = None
        
    # ========================================================================
    # TWS CALLBACKS
    # ========================================================================
    
    def nextValidId(self, orderId: int):
        """Callback wenn Verbindung steht."""
        super().nextValidId(orderId)
        self.connected = True
        logger.info(f"[OK] TWS verbunden für Account Data Abruf")
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        """Error Handler."""
        if errorCode in [2104, 2106, 2158]:
            logger.debug(f"TWS Info [{errorCode}]: {errorString}")
        else:
            logger.warning(f"[WARNUNG] TWS Error {errorCode}: {errorString}")
    
    def managedAccounts(self, accountsList: str):
        """Empfängt Liste der verwalteten Accounts."""
        accounts = accountsList.split(",")
        if accounts:
            self.account_name = accounts[0]
            logger.info(f"[OK] Account gefunden: {self.account_name}")
    
    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str):
        """Empfängt Account Summary Daten."""
        logger.debug(f"[DEBUG] Account Summary: {tag} = {value} {currency}")
        
        # Speichere relevante Werte
        if tag in ['NetLiquidation', 'BuyingPower', 'TotalCashValue', 
                   'AvailableFunds', 'ExcessLiquidity', 'Cushion']:
            try:
                self.account_data[tag] = float(value) if value else 0.0
            except:
                self.account_data[tag] = value
    
    def accountSummaryEnd(self, reqId: int):
        """Callback wenn Account Summary vollständig empfangen."""
        logger.info("[OK] Account Summary empfangen")
    
    # ========================================================================
    # PUBLIC METHODS
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
            
            return self.connected
                
        except Exception as e:
            logger.error(f"[FEHLER] TWS Verbindungsfehler: {e}")
            return False
    
    def get_account_data(self) -> Dict[str, float]:
        """
        Holt Account-Daten von TWS.
        
        Returns:
            Dict mit Account-Daten:
            - NetLiquidation: Gesamtwert des Accounts
            - BuyingPower: Verfügbare Kaufkraft
            - TotalCashValue: Cash
            - AvailableFunds: Verfügbare Mittel
            - ExcessLiquidity: Überschüssige Liquidität
            - Cushion: TWS Cushion (%)
        """
        if not self.connected:
            if not self.connect_to_tws():
                logger.error("[FEHLER] Keine TWS Verbindung für Account Data")
                return {}
        
        # Request Account Summary
        self.reqAccountSummary(9001, "All", "NetLiquidation,BuyingPower,TotalCashValue,AvailableFunds,ExcessLiquidity,Cushion")
        
        # Warte auf Daten
        time.sleep(3)
        
        # Cancel Request
        self.cancelAccountSummary(9001)
        
        return self.account_data
    
    def get_net_liquidation(self) -> float:
        """Holt Net Liquidation Value (= Account Size)."""
        data = self.get_account_data()
        return data.get('NetLiquidation', 0.0)
    
    def get_buying_power(self) -> float:
        """Holt Buying Power."""
        data = self.get_account_data()
        return data.get('BuyingPower', 0.0)
    
    def get_cushion(self) -> float:
        """Holt TWS Cushion (%)."""
        data = self.get_account_data()
        cushion_str = data.get('Cushion', '0')
        try:
            # TWS gibt Cushion als Dezimalzahl (z.B. "0.95" für 95%)
            return float(cushion_str) * 100
        except:
            return 0.0
    
    def disconnect_from_tws(self):
        """Trennt Verbindung."""
        if self.connected:
            self.disconnect()
            logger.info("[OK] TWS Verbindung getrennt")


def get_account_size_from_tws() -> Optional[float]:
    """
    Helper-Funktion zum schnellen Abruf der Account Size.
    
    Returns:
        Net Liquidation Value oder None bei Fehler
    """
    try:
        manager = AccountDataManager()
        net_liq = manager.get_net_liquidation()
        manager.disconnect_from_tws()
        
        if net_liq > 0:
            logger.info(f"[OK] Account Size von TWS: ${net_liq:,.2f}")
            return net_liq
        else:
            logger.warning("[WARNUNG] Keine valide Account Size von TWS erhalten")
            return None
            
    except Exception as e:
        logger.error(f"[FEHLER] Account Data Abruf fehlgeschlagen: {e}")
        return None


if __name__ == "__main__":
    """Test Account Data Manager."""
    logging.basicConfig(level=logging.INFO)
    
    print("\n" + "="*70)
    print("  TWS ACCOUNT DATA TEST")
    print("="*70 + "\n")
    
    manager = AccountDataManager()
    
    if manager.connect_to_tws():
        print("\n[OK] Verbunden - Hole Account Daten...\n")
        
        data = manager.get_account_data()
        
        print("="*70)
        print("  ACCOUNT SUMMARY")
        print("="*70)
        
        if data:
            print(f"Net Liquidation:   ${data.get('NetLiquidation', 0):,.2f}")
            print(f"Buying Power:      ${data.get('BuyingPower', 0):,.2f}")
            print(f"Total Cash:        ${data.get('TotalCashValue', 0):,.2f}")
            print(f"Available Funds:   ${data.get('AvailableFunds', 0):,.2f}")
            print(f"Excess Liquidity:  ${data.get('ExcessLiquidity', 0):,.2f}")
            
            cushion_val = data.get('Cushion', 0)
            if isinstance(cushion_val, (int, float)):
                print(f"TWS Cushion:       {cushion_val*100:.1f}%")
            else:
                print(f"TWS Cushion:       {cushion_val}")
        else:
            print("[FEHLER] Keine Daten empfangen")
        
        print("="*70 + "\n")
        
        manager.disconnect_from_tws()
    else:
        print("\n[FEHLER] TWS Verbindung fehlgeschlagen!\n")
