"""
Trading-Kosten-Rechner für Aktien und Optionen.
Berücksichtigt Kommissionen, Regulierungsgebühren und Slippage.
"""

import logging
import config

logger = logging.getLogger(__name__)


class TradingCostCalculator:
    """
    Berechnet realistische Trading-Kosten für verschiedene Instrumententypen.
    """
    
    def __init__(self):
        self.stock_commission = config.STOCK_COMMISSION_PER_ORDER
        self.stock_min = config.STOCK_MIN_COMMISSION
        self.stock_max = config.STOCK_MAX_COMMISSION
        
        self.option_commission = config.OPTION_COMMISSION_PER_CONTRACT
        self.option_min = config.OPTION_MIN_COMMISSION
        self.option_max = config.OPTION_MAX_COMMISSION
        
        self.slippage_pct = config.SLIPPAGE_PCT
    
    def calculate_stock_commission(self, quantity: int, price: float, 
                                   is_sell: bool = False) -> dict:
        """
        Berechnet Kommission für Aktien-Trade.
        
        Args:
            quantity: Anzahl Aktien
            price: Preis pro Aktie
            is_sell: Ob es ein Verkauf ist (für Regulatory Fees)
            
        Returns:
            Dict mit Kostenaufschlüsselung
        """
        trade_value = quantity * price
        
        # Basis-Kommission
        commission = self.stock_commission
        
        # Minimum/Maximum
        if self.stock_min and commission < self.stock_min:
            commission = self.stock_min
        
        if self.stock_max and commission > self.stock_max:
            commission = self.stock_max
        
        # Regulatory Fees (nur bei Verkäufen in USA)
        sec_fee = 0.0
        finra_taf = 0.0
        
        if is_sell:
            # SEC Fee: $27.80 per $1M
            sec_fee = (trade_value / 1_000_000) * config.SEC_FEE_PER_MILLION
            
            # FINRA TAF: $0.000166 per share, max $8.30
            finra_taf = min(quantity * config.FINRA_TAF_PER_SHARE, 8.30)
        
        # Slippage
        slippage = trade_value * self.slippage_pct
        
        total_cost = commission + sec_fee + finra_taf + slippage
        
        return {
            'commission': commission,
            'sec_fee': sec_fee,
            'finra_taf': finra_taf,
            'slippage': slippage,
            'total_cost': total_cost,
            'cost_per_share': total_cost / quantity if quantity > 0 else 0,
            'cost_pct': (total_cost / trade_value * 100) if trade_value > 0 else 0
        }
    
    def calculate_option_commission(self, contracts: int, premium: float,
                                    is_sell: bool = False) -> dict:
        """
        Berechnet Kommission für Options-Trade.
        
        Args:
            contracts: Anzahl Contracts
            premium: Prämie pro Contract
            is_sell: Ob es ein Verkauf ist
            
        Returns:
            Dict mit Kostenaufschlüsselung
        """
        # Gesamtwert (1 Contract = 100 Shares bei US-Optionen)
        multiplier = 100
        trade_value = contracts * premium * multiplier
        
        # Kommission pro Contract
        commission = contracts * self.option_commission
        
        # Minimum/Maximum
        if self.option_min and commission < self.option_min:
            commission = self.option_min
        
        if self.option_max and commission > self.option_max:
            commission = self.option_max
        
        # Options haben keine SEC/FINRA Fees
        
        # Slippage (höher bei Optionen)
        option_slippage_pct = self.slippage_pct * 2  # Doppelte Slippage bei Optionen
        slippage = trade_value * option_slippage_pct
        
        total_cost = commission + slippage
        
        return {
            'commission': commission,
            'slippage': slippage,
            'total_cost': total_cost,
            'cost_per_contract': total_cost / contracts if contracts > 0 else 0,
            'cost_pct': (total_cost / trade_value * 100) if trade_value > 0 else 0
        }
    
    def calculate_round_trip_cost(self, instrument_type: str, quantity: int,
                                  entry_price: float, exit_price: float) -> dict:
        """
        Berechnet Kosten für kompletten Round-Trip (Buy + Sell).
        
        Args:
            instrument_type: "stock" oder "option"
            quantity: Anzahl (Shares oder Contracts)
            entry_price: Entry-Preis
            exit_price: Exit-Preis
            
        Returns:
            Dict mit Round-Trip Kosten
        """
        if instrument_type.lower() == "stock":
            buy_costs = self.calculate_stock_commission(quantity, entry_price, is_sell=False)
            sell_costs = self.calculate_stock_commission(quantity, exit_price, is_sell=True)
        else:  # option
            buy_costs = self.calculate_option_commission(quantity, entry_price, is_sell=False)
            sell_costs = self.calculate_option_commission(quantity, exit_price, is_sell=True)
        
        total_commission = buy_costs['commission'] + sell_costs['commission']
        total_fees = buy_costs.get('sec_fee', 0) + buy_costs.get('finra_taf', 0) + \
                    sell_costs.get('sec_fee', 0) + sell_costs.get('finra_taf', 0)
        total_slippage = buy_costs['slippage'] + sell_costs['slippage']
        
        total_cost = total_commission + total_fees + total_slippage
        
        return {
            'buy_costs': buy_costs,
            'sell_costs': sell_costs,
            'total_commission': total_commission,
            'total_fees': total_fees,
            'total_slippage': total_slippage,
            'total_cost': total_cost
        }
    
    def adjust_profit_for_costs(self, gross_profit: float, costs: dict) -> float:
        """
        Berechnet Net Profit nach Kosten.
        
        Args:
            gross_profit: Brutto-Gewinn
            costs: Dict mit Kosten (z.B. von calculate_round_trip_cost)
            
        Returns:
            Net Profit
        """
        return gross_profit - costs['total_cost']
    
    def print_cost_breakdown(self, costs: dict, title: str = "Trade Kosten"):
        """Gibt formatierte Kostenübersicht aus."""
        print(f"\n{'='*60}")
        print(f" {title}")
        print(f"{'='*60}")
        
        if 'commission' in costs:
            print(f"  Kommission:        ${costs['commission']:.2f}")
        
        if 'sec_fee' in costs and costs['sec_fee'] > 0:
            print(f"  SEC Fee:           ${costs['sec_fee']:.2f}")
        
        if 'finra_taf' in costs and costs['finra_taf'] > 0:
            print(f"  FINRA TAF:         ${costs['finra_taf']:.2f}")
        
        if 'slippage' in costs:
            print(f"  Slippage:          ${costs['slippage']:.2f}")
        
        print(f"  {'─'*58}")
        print(f"  Total:             ${costs['total_cost']:.2f}")
        
        if 'cost_pct' in costs:
            print(f"  Als % vom Trade:   {costs['cost_pct']:.3f}%")
        
        print(f"{'='*60}\n")


def demo():
    """Demonstriert die Kostenberechnung."""
    calc = TradingCostCalculator()
    
    print("\n" + "="*70)
    print(" TRADING COST CALCULATOR - DEMO")
    print("="*70)
    
    # Beispiel 1: Aktien-Trade
    print("\n📈 Beispiel 1: Aktien-Trade")
    print("-" * 70)
    print("  100 Aktien AAPL @ $150.00")
    
    stock_costs = calc.calculate_stock_commission(100, 150.00, is_sell=False)
    calc.print_cost_breakdown(stock_costs, "AAPL Buy Order")
    
    # Beispiel 2: Options-Trade
    print("\n📊 Beispiel 2: Options-Trade")
    print("-" * 70)
    print("  5 Contracts AAPL 150 Call @ $5.00 Prämie")
    
    option_costs = calc.calculate_option_commission(5, 5.00, is_sell=False)
    calc.print_cost_breakdown(option_costs, "AAPL Call Buy Order")
    
    # Beispiel 3: Round-Trip
    print("\n🔄 Beispiel 3: Kompletter Round-Trip (Buy + Sell)")
    print("-" * 70)
    print("  10 Options Contracts")
    print("  Entry: $3.50, Exit: $5.25")
    
    rt_costs = calc.calculate_round_trip_cost("option", 10, 3.50, 5.25)
    
    print("\n  Buy-Side Kosten:")
    for key, val in rt_costs['buy_costs'].items():
        if key != 'cost_per_contract' and key != 'cost_pct':
            print(f"    {key:20s}: ${val:.2f}")
    
    print("\n  Sell-Side Kosten:")
    for key, val in rt_costs['sell_costs'].items():
        if key != 'cost_per_contract' and key != 'cost_pct':
            print(f"    {key:20s}: ${val:.2f}")
    
    print(f"\n  {'─'*68}")
    print(f"  Total Round-Trip:    ${rt_costs['total_cost']:.2f}")
    
    # Gewinnberechnung
    gross_profit = (5.25 - 3.50) * 10 * 100  # 10 contracts * 100 multiplier
    net_profit = calc.adjust_profit_for_costs(gross_profit, rt_costs)
    
    print(f"\n  Brutto-Gewinn:       ${gross_profit:.2f}")
    print(f"  Nach Kosten:         ${net_profit:.2f}")
    print(f"  Kosten-Impact:       {(rt_costs['total_cost']/gross_profit*100):.2f}%")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    demo()
