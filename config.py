from data.scanner_universe import NASDAQ_100_TEST_UNIVERSE


# Scanner
DEFAULT_TICKER = "AAPL"
DATA_PERIOD = "2y"
STOCK_UNIVERSE = NASDAQ_100_TEST_UNIVERSE
SCANNER_RESULT_LIMIT = 20

# Strategy defaults
SHORT_MOMENTUM_DAYS = 5
LONG_MOMENTUM_DAYS = 20
MOVING_AVERAGE_DAYS = 20
DEFAULT_STRATEGY_NAME = "Baseline"

# Backtest
BACKTEST_STARTING_CASH = 100_000.00
BACKTEST_START_DATE = "2025-01-01"
BACKTEST_END_DATE = "2025-06-30"
BACKTEST_BENCHMARK = "SPY"
BACKTEST_FEE_RATE = 0.0

# Competition constraints
MIN_STOCK_PRICE = 5.00
MAX_POSITION_VALUE = 20_000
# "initial_allocation" allows appreciation above the cap. Change to
# "rebalance_market_value" if official rules require trimming at rebalance.
POSITION_LIMIT_MODE = "initial_allocation"

# Market data and cache providers
MARKET_DATA_CACHE_DIR = "data_cache"
MANUAL_HISTORICAL_DATA_DIR = "historical_data"
CACHE_COVERAGE_TOLERANCE_DAYS = 7
YFINANCE_REQUEST_TIMEOUT_SECONDS = 8
YFINANCE_HARD_TIMEOUT_SECONDS = 12
