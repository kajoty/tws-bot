# Copilot Instructions: IB Trading Bot

## Project Overview

Algorithmic trading bot for Interactive Brokers TWS in Python. Trades stocks/options using technical analysis, manages risk automatically, and tracks performance with SQLite.

**Architecture**: `IBTradingBot` inherits from IB's `EClient`/`EWrapper` for async API communication. Modular design with separate concerns for strategy, risk, database, and performance.

**Key Components**:
- `IBTradingBot` (EClient/EWrapper): TWS API orchestrator
- `TradingStrategy`: Technical analysis & signal generation
- `RiskManager`: Position sizing & limits enforcement
- `DatabaseManager`: SQLite persistence (returns bool/data, no exceptions)
- `PerformanceAnalyzer`: Metrics calculation & visualization

## Critical Concepts

### IB API Threading Model
- `EClient.run()` runs in **daemon thread** started in `connect_to_tws()`
- Callbacks (`nextValidId`, `historicalData`, `error`) execute in **API thread**
- Use `self.pending_requests` dict to track async requests by ID
- Wait for `self.connected = True` before making requests (10s timeout)
- Connection flow: `connect()` → `api_thread.start()` → wait for `nextValidId` callback

### Request ID Management
- Historical/market data requests need unique IDs via `_get_next_request_id()`
- Store metadata in `self.pending_requests[req_id]` for callback matching
- Data accumulates in `request_info['data']` list during `historicalData()` callbacks
- Mark `completed=True` when data arrives in `historicalDataEnd()`
- Use `wait_for_request(req_id, timeout=30)` to block until completion

### Order Placement Flow
1. Check `can_open_position()` for limits (max positions, existing position)
2. Calculate size with `calculate_position_size()` using stop-loss for risk
3. Create contract: `create_stock_contract()` or `create_option_contract()`
4. Get `self.next_valid_order_id`, call `placeOrder()`, increment ID
5. Track execution in `execDetails()` callback

## Module Responsibilities

### `config.py` - Single Source of Truth
All parameters live here. Never hardcode:
- Trading mode (`IS_PAPER_TRADING`), ports (7497 paper / 7496 live)
- Risk (`MAX_RISK_PER_TRADE_PCT`), limits (`MAX_CONCURRENT_POSITIONS`)
- Strategy params (MA periods, RSI thresholds)
- Paths (database, logs, plots)

### `ib_trading_bot.py` - API Orchestrator
- **Inherits**: `EClient` (requests) + `EWrapper` (callbacks)
- **Key Methods**:
  - `connect_to_tws()`: Connection + API thread
  - `request_historical_data()`: Async data with callbacks
  - `place_order()`: Creates/submits orders
  - `run_trading_cycle()`: Main trading logic

### `database.py` - SQLite Persistence
- **Tables**: `historical_data`, `trades`, `positions`, `performance`, `options_data`
- **Pattern**: Methods return bool/data, never raise exceptions
- **UNIQUE constraints** prevent duplicates (symbol/date)

### `risk_management.py` - Position Sizing
- **Formula**: `size = (account * risk_pct) / (entry - stop_loss)`
- **Enforces**:
  - Max risk per trade (`MAX_RISK_PER_TRADE_PCT`)
  - Max positions (`MAX_CONCURRENT_POSITIONS`)
  - Min position size (`MIN_POSITION_SIZE`)
  - Available cash
- **State**: `current_positions` dict tracks open positions

### `strategy.py` - Signal Generation
- **Indicators**: MA crossover, RSI, MACD, Bollinger Bands, ATR
- **Scoring**: Accumulates `buy_score`/`sell_score` from conditions
- **Output**: `(signal, confidence, details)` - BUY/SELL/HOLD
- **Threshold**: Requires `confidence > 0.6` to trade
- **Stop-loss**: `current_price ± (2 * ATR)`

### `performance.py` - Analysis & Charts
- **3 subplots**: Equity curve, drawdown, returns distribution
- **Metrics**: Sharpe, Sortino, max drawdown, win rate
- Saves to `config.PLOT_DIR` with timestamp

## Common Workflows

### Adding New Indicator
1. Add calc method to `strategy.py` (e.g., `_calculate_stochastic()`)
2. Call in `calculate_indicators()` to add DataFrame column
3. Use in `check_strategy()` to influence scores
4. Add config params to `config.py`

### Options Trading
1. Use `create_option_contract()` with strike, expiry, right ("C"/"P")
2. Track Greeks in `options_data` table
3. Consider IV percentile in `check_strategy()`
4. Multiplier is 100 for US equity options

### Extending Database
1. Add `CREATE TABLE` in `DatabaseManager._initialize_database()`
2. Create save/load methods (parameterized queries)
3. Always `commit()` after writes

### Debug Connection Issues
- Check `config.IB_PORT` (7497 paper, 7496 live)
- Verify TWS API: "Enable ActiveX and Socket Clients"
- Check logs for error codes (2000+ are informational)

## Code Patterns

### Error Handling
```python
try:
    # operation
    logger.info("Success")
    return True
except Exception as e:
    logger.error(f"Context: {e}")
    return False
```

### DataFrames
```python
df = df.copy()  # Avoid warnings
df['indicator'] = df['close'].rolling(20).mean()
if df.empty or len(df) < required:
    return default
```

### IB Contracts
```python
contract = Contract()
contract.symbol, contract.secType = symbol, "STK"  # or "OPT"
contract.exchange, contract.currency = "SMART", "USD"
# Options: add lastTradeDateOrContractMonth, strike, right, multiplier
```

## Safety Mechanisms

- **DRY_RUN**: Skip `placeOrder()` calls
- **IS_PAPER_TRADING**: Controls port (7497 vs 7496)
- **Live confirmation**: `main.py` requires "yes" for live mode
- **Stop-loss**: `check_stop_loss()` monitors every cycle
- **Position limits**: Enforced before opening

## Performance

- Historical data cached in `self.historical_data_cache`
- Database indexes on `(symbol, sec_type, date)`
- Batched requests in `run_trading_cycle()`
- Sleep intervals prevent excessive trading

## Key Files

- **main.py**: Entry point, signal handling, trading loop, live trading confirmation
- **config.py**: All tunable parameters (NEVER hardcode!)
- **ib_trading_bot.py**: Connection (70-110), callbacks (110-170), historical (190-240), orders (240+)
- **strategy.py**: Indicator calculations, `check_strategy` signal logic
- **risk_management.py**: `calculate_position_size` (50-110), position tracking
- **database.py**: SQLite tables, save/load methods (returns bool/data)
- **performance.py**: Metrics (Sharpe, Sortino, max DD), 3-subplot charts

## Project Structure
```
tws-bot/
├── config.py                        # Single source of truth
├── main.py                          # Entry point + signal handling
├── ib_trading_bot.py                # EClient/EWrapper implementation
├── strategy.py                      # Technical analysis (MA/RSI/MACD)
├── contrarian_options_strategy.py   # 52-Week extrema options strategy
├── risk_management.py               # Position sizing
├── database.py                      # SQLite persistence (8 tables)
├── performance.py                   # Analytics & visualization
├── trading_costs.py                 # Commission & fee calculator
├── watchlist.csv                    # S&P 500 symbols with metadata
├── watchlist_manager.py             # CSV watchlist handler
├── watchlist_cli.py                 # CLI for watchlist management
├── generate_sp500_watchlist.py      # Downloads S&P 500 data
├── import_fundamentals_ib.py        # IB fundamentals importer
├── import_fundamentals_simple.py    # Yahoo Finance alternative
├── backtest_criteria.py             # Filter analysis tool
├── show_trading_journal.py          # Trade log viewer
├── web_interface.py                 # Flask monitoring dashboard
├── service_wrapper.py               # Windows service wrapper
├── service_wrapper_linux.py         # Linux systemd service
├── install_service.ps1/sh           # Service installation scripts
├── run_console.ps1/sh               # Console runner scripts
├── requirements.txt                 # Dependencies (ibapi, pandas, matplotlib, yfinance, flask)
├── data/                            # SQLite database
├── logs/                            # Daily log files
├── plots/                           # Performance charts
└── templates/                       # Flask HTML templates
```

## When Modifying

- **Update config.py first** for new parameters
- **Log significant actions** at INFO level
- **Return early** on validation failures
- **Use type hints** for signatures
- **Test with DRY_RUN=True**
- **Check database** after changes

## Common Pitfalls

1. Forgetting to increment `next_valid_order_id` after `placeOrder()`
2. Not waiting for callbacks - use `wait_for_request()`
3. Modifying DataFrames without `.copy()` - causes warnings
4. Hardcoding instead of using config
5. Not checking `if df.empty` before operations
6. Missing error handling in callbacks - many TWS errors aren't fatal

## Multi-Strategy Architecture

### Strategy Selection (`config.TRADING_STRATEGY`)
- **'STOCK'**: Classic technical analysis (MA/RSI/MACD) in `strategy.py`
- **'OPTIONS'**: Contrarian 52-week extrema strategy in `contrarian_options_strategy.py`
  - Long Put @ 52W high (P/E overvaluation + IV Rank 30-80)
  - Long Call @ 52W low (undervaluation + IV Rank 30-80)
  - Auto-close at DTE=7 to avoid theta decay

### Strategy Integration Points
1. `ib_trading_bot.run_trading_cycle()` checks `config.TRADING_STRATEGY`
2. Calls `strategy.check_strategy()` or `contrarian_strategy.check_options_opportunity()`
3. Both return `(signal, confidence, details)` tuple
4. `details` dict contains entry price, stop-loss, strategy-specific metadata

## Data Pipeline & Database

### 8 SQLite Tables (database.py)
1. **historical_data**: OHLCV bars (UNIQUE: symbol, sec_type, date)
2. **trades**: Executed orders with commission tracking
3. **positions**: Current holdings (UNIQUE: symbol, sec_type)
4. **performance**: Equity snapshots for charts
5. **options_data**: Greeks & IV (UNIQUE: symbol, strike, expiry, right)
6. **fundamental_data**: P/E, FCF, sector, earnings dates (UNIQUE: symbol, timestamp)
7. **iv_history**: 52-week IV for IV Rank (UNIQUE: symbol, date)
8. **sector_benchmarks**: Industry median P/E ratios

### Fundamental Data Import Workflows
```powershell
# Option 1: IB Fundamentals API (requires active TWS connection)
python import_fundamentals_ib.py

# Option 2: Yahoo Finance (no TWS needed, less accurate)
python import_fundamentals_simple.py

# After import, check coverage:
python backtest_criteria.py  # Shows filter funnel (why symbols excluded)
```

### Watchlist Management
- **watchlist.csv**: Symbol, enabled, sector, market_cap, avg_volume_20d
- **watchlist_manager.py**: WatchlistManager class for CSV I/O
- **watchlist_cli.py**: CLI for add/remove/enable/disable symbols
- **generate_sp500_watchlist.py**: Downloads S&P 500 constituents with metadata

```powershell
# Add symbol to watchlist
python watchlist_cli.py add NVDA

# Disable symbol temporarily
python watchlist_cli.py disable TSLA

# Regenerate from S&P 500
python generate_sp500_watchlist.py
```

## Deployment & Monitoring

### Windows Service Deployment
```powershell
# Install as service (requires Admin)
.\install_service.ps1

# Start/stop/status
.\start_service.ps1
.\stop_service.ps1
.\status_service.ps1

# Uninstall
.\install_service.ps1 -Uninstall
```

### Linux Systemd Service
```bash
# Install (creates systemd unit)
sudo ./install_service.sh

# Control
./start_service.sh
./stop_service.sh
./status_service.sh
```

### Web Monitoring Dashboard
```powershell
# Start Flask interface on http://localhost:5000
python web_interface.py

# Shows: Equity curve, open positions, recent trades, performance metrics
```

### Trading Journal
```powershell
# View last 7 days of trades
python show_trading_journal.py

# Outputs: Symbol, action, quantity, price, commission, strategy
```

## Trading Costs Architecture

### TradingCostCalculator (`trading_costs.py`)
- **Stock commissions**: Configurable per-order + min/max caps
- **Option commissions**: Per-contract pricing
- **Regulatory fees**: SEC ($27.80/M on sells), FINRA TAF ($0.000166/share)
- **Slippage**: Percentage-based estimate (config.SLIPPAGE_PCT)
- Methods return dict with itemized costs: `{'commission', 'sec_fee', 'finra_taf', 'slippage', 'total_cost', 'cost_pct'}`

### Cost Configuration Pattern (config.py)
```python
# Interactive Brokers Tiered example:
STOCK_COMMISSION_PER_ORDER = 5.00  # Flat fee per order
OPTION_COMMISSION_PER_CONTRACT = 2.50  # Per contract
SLIPPAGE_PCT = 0.001  # 0.1% market impact
```

## Development Workflow

### Setup & Testing
```powershell
# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure for testing
# Edit config.py: IS_PAPER_TRADING=True, DRY_RUN=True
python main.py
```

### Before Committing
1. Test with `DRY_RUN=True` (no real orders)
2. Test with Paper Trading (port 7497)
3. Check logs in `logs/` for errors
4. Validate with multiple symbols from watchlist.csv
5. Run `backtest_criteria.py` to verify strategy filters
6. Update `.github/copilot-instructions.md` for architecture changes

### No Test Suite
- Project has no automated tests (`test*.py` files)
- Manual testing via Paper Trading is the standard
- Use TWS API demo account for validation
- Use `backtest_criteria.py` for filter validation

### Logging Pattern
```python
logger.info("Success message")  # Normal operations
logger.warning("Non-fatal issue") # Recoverable problems
logger.error(f"Context: {e}")    # Failures with context
```

## Advanced Features

### Options Trading Requirements
1. Enable in config: `ENABLE_OPTIONS_TRADING = True`
2. Set strategy: `TRADING_STRATEGY = 'OPTIONS'`
3. Import fundamentals: `python import_fundamentals_ib.py`
4. Configure DTE, IV Rank, delta targets in config.py
5. Greeks tracked in `options_data` table

### Signal Handling
- `main.py` registers SIGINT/SIGTERM handlers
- Graceful shutdown: Disconnects from TWS, generates performance report
- Auto-creates final equity chart on Ctrl+C
