# TWS Signal Service - AI Agent Instructions

## Project Overview
Signal-only trading bot that connects to Interactive Brokers TWS, generates entry/exit signals via technical analysis, and sends push notifications via Pushover. **No automated trading** - only signal generation and notifications.

## Architecture

### Core Components (4 files)
- **`signal_service.py`**: Main service implementing `EWrapper`/`EClient` for TWS API, runs scan loop
- **`config.py`**: Centralized configuration using `.env` with fallback defaults
- **`database.py`**: SQLite manager for historical OHLCV data and signal history
- **`pushover_notifier.py`**: Push notification wrapper for entry/exit alerts

### Data Flow
1. Service connects to TWS via `ibapi` (port 7497 paper / 7496 live)
2. Scanner fetches historical bars for watchlist symbols every `SCAN_INTERVAL` (default 5min)
3. Indicators calculated in-memory (MA, RSI, MACD) on pandas DataFrames
4. Entry signals checked when no position; exit signals checked when position exists
5. Signals saved to SQLite + Pushover notification sent
6. Position tracking in `active_positions` dict (in-memory only, lost on restart)

## Configuration Pattern

**All settings via `.env` file** - never hardcode values. Config structure:
```python
SETTING = os.getenv("SETTING_NAME", "default_value")
IS_PAPER_TRADING = os.getenv("IS_PAPER_TRADING", "True").lower() in ("true", "1", "yes")
```

Key settings: `WATCHLIST_STOCKS` (comma-separated), `SCAN_INTERVAL`, `MIN_SIGNALS_FOR_ENTRY`, risk management percentages, indicator parameters.

## Signal Generation Logic

### Entry Signals (`check_entry_signal`)
- Requires `MIN_SIGNALS_FOR_ENTRY` indicators to agree (default: 2)
- Available indicators (toggled via `USE_MA_CROSSOVER`, `USE_RSI`, `USE_MACD`):
  - MA Crossover: `ma_short` crosses above `ma_long`
  - RSI Oversold: `rsi < RSI_OVERSOLD` (default: 30)
  - MACD Crossover: MACD line crosses above signal line
- Position sizing based on risk: `risk_amount / stop_distance`
- Only generates signals if no existing position for that symbol

### Exit Signals (`check_exit_signal`)
- Stop Loss: `current_price <= position['stop_loss']`
- Take Profit: `current_price >= position['take_profit']`
- RSI Overbought: `rsi > RSI_OVERBOUGHT` (default: 70)
- Calculates P&L in USD and percentage

## Development Workflows

### Setup & Testing
```bash
# Initial setup
pip install -r requirements.txt
cp .env.example .env  # Edit with TWS ports & Pushover credentials

# Test Pushover connectivity
python pushover_notifier.py

# Run service (TWS must be running first!)
python signal_service.py
```

### TWS Connection Requirements
- TWS/Gateway must be running BEFORE starting service
- API Settings: Enable "ActiveX and Socket Clients", add `127.0.0.1` to Trusted IPs
- Paper: port 7497, Live: port 7496
- Connection handled via threading; waits max 10s for `nextValidId` callback

### Debugging
- Logs written to `logs/signal_service.log` and console (level: `config.LOG_LEVEL`)
- TWS error codes 2104/2106/2158 are info-level connection messages (not errors)
- Error 502 = TWS not connected
- Historical data stored in `data/trading_signals.db`

## Project-Specific Conventions

### Language & Comments
All code, comments, logs, and documentation in **German** (matching existing codebase style).

### Indicator Calculation
Indicators calculated in `calculate_indicators()` returning DataFrame with columns: `ma_short`, `ma_long`, `rsi`, `macd`, `macd_signal`. Always use pandas `.ewm()` for moving averages, not `.rolling()`.

### Database Usage
- Use `DatabaseManager` for persistence - don't access SQLite directly
- Historical data cached in `historical_data_cache` dict (symbol → DataFrame)
- Signal tracking via `save_signal()` method (auto-timestamps)

### Position Tracking
Positions stored in-memory dict: `active_positions[symbol] = {'entry_price', 'quantity', 'stop_loss', 'take_profit', 'timestamp'}`. Not persisted - lost on restart (intentional design).

### Threading & TWS API
- TWS connection runs in daemon thread (`self.run()` from EClient)
- Never block main thread during TWS callbacks
- Request IDs tracked in `pending_requests` dict

## Common Patterns

**Adding new indicator:**
1. Add config parameters to `config.py` with `os.getenv()`
2. Calculate in `calculate_indicators()` - assign to DataFrame column
3. Add logic to `check_entry_signal()` or `check_exit_signal()`
4. Add toggle flag like `USE_INDICATOR_NAME`

**Modifying signal logic:**
Edit `check_entry_signal()` or `check_exit_signal()` - both return `Optional[Dict]` with keys: `type`, `symbol`, `price`, `quantity`, `reason`, `timestamp`, plus signal-specific fields.

**Error handling:**
Use logger (already configured) - `logger.info()`, `logger.warning()`, `logger.error()` with `exc_info=True` for stack traces.
