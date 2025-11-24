# test_filter_stats.py
import random

# Beispiel: 10 Filter pro Strategie
FILTERS = [
    "Market Cap", "Avg Volume", "PE Ratio", "IV Rank", "DTE", "Delta",
    "Proximity High/Low", "FCF Yield", "Strike Width", "Take Profit"
]

SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]


def check_filters(symbol):
    # Simuliere zufällig erfüllte Filter (True/False)
    results = [random.choice([True, False]) for _ in FILTERS]
    num_passed = sum(results)
    percent = num_passed / len(FILTERS) * 100
    return num_passed, percent


def main():
    print("Filter-Statistik für Beispiel-Symbole:")
    for symbol in SYMBOLS:
        num_passed, percent = check_filters(symbol)
        status = ""
        if percent >= 100:
            status = "100% (ALLE Filter erfüllt)"
        elif percent >= 90:
            status = "≥90%"
        elif percent >= 80:
            status = "≥80%"
        elif percent >= 70:
            status = "≥70%"
        else:
            status = "<70%"
        print(f"{symbol}: {num_passed}/{len(FILTERS)} Filter ({percent:.1f}%) → {status}")


if __name__ == "__main__":
    main()
