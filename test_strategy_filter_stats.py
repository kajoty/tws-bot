# test_strategy_filter_stats.py
"""
Test-Script: Prüft für alle S&P 500 Symbole und alle drei Strategien (LONG PUT, LONG CALL, BEAR CALL SPREAD), wie viele Filter jeweils erfüllt sind.
Gibt für jedes Symbol und jede Strategie die Prozentzahl erfüllter Filter und die Kategorie (≥70%, ≥80%, ≥90%, 100%) aus.
Greift auf die echte Datenbank und .env-Konfiguration zu.
"""
import config
from database import DatabaseManager
from signal_service import SignalService


def print_stats():
    db = DatabaseManager()
    service = SignalService()
    service.db = db
    service.watchlist = config.WATCHLIST_STOCKS

    print("Filter-Statistik für S&P 500 Symbole:")
    print("Symbol | Strategie | Erfüllt / Gesamt | Prozent | Kategorie")
    for symbol in service.watchlist:
        put = service.check_long_put_filters(symbol)
        call = service.check_long_call_filters(symbol)
        spread = service.check_bear_call_spread_filters(symbol)
        for strat, result in zip([
            "LONG PUT", "LONG CALL", "BEAR CALL SPREAD"], [put, call, spread]):
            num_passed = sum(result.values())
            total = len(result)
            percent = num_passed / total * 100 if total > 0 else 0
            if percent == 100:
                cat = "100% (ALLE Filter)"
            elif percent >= 90:
                cat = ">=90%"
            elif percent >= 80:
                cat = ">=80%"
            elif percent >= 70:
                cat = ">=70%"
            else:
                cat = "<70%"
            print(f"{symbol} | {strat} | {int(num_passed)}/{total} | {percent:.1f}% | {cat}")

if __name__ == "__main__":
    print_stats()
