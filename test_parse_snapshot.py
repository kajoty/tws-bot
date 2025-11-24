#!/usr/bin/env python3
"""
Test des neuen XML-Parsers für ReportSnapshot.
"""
import xml.etree.ElementTree as ET

def parse_fundamental_data(xml_data: str) -> dict:
    """Parst fundamentale Daten aus TWS ReportSnapshot XML."""
    
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
        print(f"[FEHLER] Fundamental-Parsing: {e}")
    
    return fundamental


if __name__ == "__main__":
    # XML-Datei laden
    xml_file = "snapshot_AAPL.xml"
    
    try:
        with open(xml_file, 'r', encoding='utf-8') as f:
            xml_data = f.read()
        
        print(f"Teste XML-Parsing für {xml_file}...\n")
        
        result = parse_fundamental_data(xml_data)
        
        print("=" * 70)
        print("ERGEBNIS:")
        print("=" * 70)
        for key, value in result.items():
            if value is not None:
                if key == 'market_cap':
                    print(f"{key:15s}: ${value:,.0f} (${value/1e9:.2f}B)")
                elif key == 'fcf':
                    print(f"{key:15s}: ${value:,.0f}")
                elif key == 'avg_volume':
                    print(f"{key:15s}: {value:,.0f} Aktien")
                elif key == 'pe_ratio':
                    print(f"{key:15s}: {value:.2f}")
                else:
                    print(f"{key:15s}: {value}")
            else:
                print(f"{key:15s}: None")
        
        print("\n✓ Parsing erfolgreich!")
        
    except FileNotFoundError:
        print(f"Fehler: Datei {xml_file} nicht gefunden!")
    except Exception as e:
        print(f"Fehler: {e}")
