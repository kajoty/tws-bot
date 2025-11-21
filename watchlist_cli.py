"""
Kommandozeilen-Tool zur Verwaltung der Watchlist.
"""

import sys
import argparse
from watchlist_manager import WatchlistManager
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')


def cmd_list(wl: WatchlistManager, args):
    """Liste alle Symbole."""
    wl.print_summary()


def cmd_add(wl: WatchlistManager, args):
    """Füge Symbol hinzu."""
    metadata = {
        'market_cap': args.market_cap or 0,
        'avg_volume': args.volume or 0,
        'sector': args.sector or '',
        'pe_ratio': args.pe or 0.0,
        'fcf': args.fcf or 0.0,
        'enabled': not args.disabled,
        'notes': args.notes or ''
    }
    
    if wl.add_symbol(args.symbol, metadata):
        print(f"✓ Symbol {args.symbol} hinzugefügt")
    else:
        print(f"✗ Fehler beim Hinzufügen von {args.symbol}")


def cmd_remove(wl: WatchlistManager, args):
    """Entferne Symbol."""
    if wl.remove_symbol(args.symbol):
        print(f"✓ Symbol {args.symbol} entfernt")
    else:
        print(f"✗ Symbol {args.symbol} nicht gefunden")


def cmd_enable(wl: WatchlistManager, args):
    """Aktiviere Symbol."""
    if wl.enable_symbol(args.symbol, True):
        print(f"✓ Symbol {args.symbol} aktiviert")
    else:
        print(f"✗ Fehler")


def cmd_disable(wl: WatchlistManager, args):
    """Deaktiviere Symbol."""
    if wl.enable_symbol(args.symbol, False):
        print(f"✓ Symbol {args.symbol} deaktiviert")
    else:
        print(f"✗ Fehler")


def cmd_update(wl: WatchlistManager, args):
    """Aktualisiere Metadaten."""
    updates = {}
    
    if args.market_cap is not None:
        updates['market_cap'] = args.market_cap
    if args.volume is not None:
        updates['avg_volume'] = args.volume
    if args.sector is not None:
        updates['sector'] = args.sector
    if args.pe is not None:
        updates['pe_ratio'] = args.pe
    if args.fcf is not None:
        updates['fcf'] = args.fcf
    if args.notes is not None:
        updates['notes'] = args.notes
    
    if not updates:
        print("Keine Updates angegeben")
        return
    
    if wl.update_symbol_metadata(args.symbol, updates):
        print(f"✓ Metadaten für {args.symbol} aktualisiert: {updates}")
    else:
        print(f"✗ Fehler beim Aktualisieren")


def cmd_info(wl: WatchlistManager, args):
    """Zeige Details zu Symbol."""
    meta = wl.get_symbol_metadata(args.symbol)
    
    if not meta:
        print(f"Symbol {args.symbol} nicht in Watchlist")
        return
    
    print(f"\n{'='*60}")
    print(f" {args.symbol} - DETAILS")
    print(f"{'='*60}")
    
    for key, value in meta.items():
        if key == 'market_cap' and value > 0:
            print(f"  {key:20s}: ${value/1e9:.2f}B")
        elif key == 'avg_volume' and value > 0:
            print(f"  {key:20s}: {value:,.0f}")
        elif key == 'fcf' and value > 0:
            print(f"  {key:20s}: ${value/1e9:.2f}B")
        else:
            print(f"  {key:20s}: {value}")
    
    print(f"{'='*60}\n")


def cmd_filter(wl: WatchlistManager, args):
    """Filtere Symbole."""
    symbols = wl.get_symbols_by_filter(
        min_market_cap=args.min_market_cap,
        min_volume=args.min_volume
    )
    
    print(f"\nGefilterte Symbole ({len(symbols)}):")
    for sym in symbols:
        meta = wl.get_symbol_metadata(sym)
        mkt_cap = f"${meta['market_cap']/1e9:.1f}B" if meta['market_cap'] > 0 else "N/A"
        vol = f"{meta['avg_volume']:,.0f}" if meta['avg_volume'] > 0 else "N/A"
        print(f"  {sym:6s} | MktCap: {mkt_cap:10s} | Volume: {vol:12s}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Watchlist Manager für IB Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  # Liste alle Symbole
  python watchlist_cli.py list
  
  # Füge Symbol hinzu
  python watchlist_cli.py add TSLA --market-cap 700000000000 --volume 90000000 --sector Automotive
  
  # Deaktiviere Symbol
  python watchlist_cli.py disable TSLA
  
  # Aktualisiere P/E Ratio
  python watchlist_cli.py update AAPL --pe 28.5
  
  # Zeige Details
  python watchlist_cli.py info AAPL
  
  # Filtere nach Marktkapitalisierung
  python watchlist_cli.py filter --min-market-cap 1000000000000
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Kommando')
    
    # list
    subparsers.add_parser('list', help='Liste alle Symbole')
    
    # add
    parser_add = subparsers.add_parser('add', help='Füge Symbol hinzu')
    parser_add.add_argument('symbol', help='Ticker Symbol')
    parser_add.add_argument('--market-cap', type=float, help='Marktkapitalisierung in USD')
    parser_add.add_argument('--volume', type=float, help='Durchschnittliches Volumen')
    parser_add.add_argument('--sector', help='Sektor')
    parser_add.add_argument('--pe', type=float, help='P/E Ratio')
    parser_add.add_argument('--fcf', type=float, help='Free Cash Flow in USD')
    parser_add.add_argument('--notes', help='Notizen')
    parser_add.add_argument('--disabled', action='store_true', help='Füge als deaktiviert hinzu')
    
    # remove
    parser_remove = subparsers.add_parser('remove', help='Entferne Symbol')
    parser_remove.add_argument('symbol', help='Ticker Symbol')
    
    # enable
    parser_enable = subparsers.add_parser('enable', help='Aktiviere Symbol')
    parser_enable.add_argument('symbol', help='Ticker Symbol')
    
    # disable
    parser_disable = subparsers.add_parser('disable', help='Deaktiviere Symbol')
    parser_disable.add_argument('symbol', help='Ticker Symbol')
    
    # update
    parser_update = subparsers.add_parser('update', help='Aktualisiere Metadaten')
    parser_update.add_argument('symbol', help='Ticker Symbol')
    parser_update.add_argument('--market-cap', type=float, help='Marktkapitalisierung')
    parser_update.add_argument('--volume', type=float, help='Durchschnittliches Volumen')
    parser_update.add_argument('--sector', help='Sektor')
    parser_update.add_argument('--pe', type=float, help='P/E Ratio')
    parser_update.add_argument('--fcf', type=float, help='Free Cash Flow')
    parser_update.add_argument('--notes', help='Notizen')
    
    # info
    parser_info = subparsers.add_parser('info', help='Zeige Symbol-Details')
    parser_info.add_argument('symbol', help='Ticker Symbol')
    
    # filter
    parser_filter = subparsers.add_parser('filter', help='Filtere Symbole')
    parser_filter.add_argument('--min-market-cap', type=float, help='Minimum Marktkapitalisierung')
    parser_filter.add_argument('--min-volume', type=float, help='Minimum Volumen')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Initialisiere WatchlistManager
    wl = WatchlistManager()
    
    # Führe Kommando aus
    commands = {
        'list': cmd_list,
        'add': cmd_add,
        'remove': cmd_remove,
        'enable': cmd_enable,
        'disable': cmd_disable,
        'update': cmd_update,
        'info': cmd_info,
        'filter': cmd_filter
    }
    
    if args.command in commands:
        commands[args.command](wl, args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
