# Copilot Instructions: IB Trading Bot

## Project Overview

Algorithmic trading bot for Interactive Brokers TWS in Python. Trades stocks/options using technical analysis, manages risk automatically, and tracks performance with SQLite.

**Architecture**: `IBTradingBot` inherits from IB's `EClient`/`EWrapper` for async API communication. Modular design with separate concerns for strategy, risk, database, and performance.

## Critical Concepts

### IB API Threading
- `EClient.run()` runs in separate thread (see `ib_trading_bot.py:connect_to_tws()`)
- Callbacks (`nextValidId`, `historicalData`, `error`) execute in API thread
- Use `self.pending_requests` dict to track async requests by ID
- Wait for `self.connected = True` before making requests

### Request ID Management
- Historical/market data requests need unique IDs via `_get_next_request_id()`
- Store metadata in `self.pending_requests[req_id]` for callback matching
- Mark `completed=True` when data arrives in `historicalDataEnd()`

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

- **main.py**: Entry point, signal handling, trading loop
- **config.py**: All tunable parameters
- **ib_trading_bot.py**: Connection (70-110), historical (220-280), orders (340-380)
- **strategy.py**: `check_strategy` signal logic (170-290)
- **risk_management.py**: `calculate_position_size` (40-110)

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
