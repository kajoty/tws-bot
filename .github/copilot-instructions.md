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
├── strategy.py                      # Technical analysis (original)
├── contrarian_options_strategy.py   # 52-Week extrema options strategy
├── risk_management.py               # Position sizing
├── database.py                      # SQLite persistence (fundamentals + IV)
├── performance.py                   # Analytics & visualization
├── trading_costs.py                 # Commission & fee calculator
├── watchlist.csv                    # S&P 500 symbols with metadata
├── watchlist_manager.py             # CSV watchlist handler
├── watchlist_cli.py                 # CLI for watchlist management
├── generate_sp500_watchlist.py      # Downloads S&P 500 data
├── requirements.txt                 # Dependencies (ibapi, pandas, matplotlib, yfinance)
├── data/                            # SQLite database
├── logs/                            # Daily log files
└── plots/                           # Performance charts
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
4. Validate with multiple symbols from `WATCHLIST_STOCKS`
5. Update `.github/copilot-instructions.md` for architecture changes

### No Test Suite
- Project has no automated tests (`test*.py` files)
- Manual testing via Paper Trading is the standard
- Use TWS API demo account for validation

### Logging Pattern
```python
logger.info("Success message")  # Normal operations
logger.warning("Non-fatal issue") # Recoverable problems
logger.error(f"Context: {e}")    # Failures with context
```
