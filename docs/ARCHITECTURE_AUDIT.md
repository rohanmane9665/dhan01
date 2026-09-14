# DhanHQ Algorithmic Trading Platform - Complete Architecture Audit

## 1. Executive Summary

This document presents a comprehensive, production-grade architectural audit of the existing DhanHQ Python-based algorithmic trading repository. The primary focus of the analysis covers `Option_RSI_strat.py`, `Dhan_Tradehull_V2.py`, `clude_sensex_strat.py`, and supporting utility scripts.

The goal of this audit is to thoroughly evaluate the codebase for transformation into a high-reliability, multi-tier automated trading platform. The current implementation successfully models the core quantitative trading logic—specifically a **SENSEX Options 15-Minute RSI Breakout Strategy with Trailing Stop Loss**. However, the operational baseline suffers from critical architectural liabilities, including plain-text secret exposure, zero thread synchronization, loss of state on process failure, blocking `time.sleep` loops, reliance on local CSV file parsing, and lack of position reconciliation.

**Core Safety Principle:** Deterministic code controls money. Under no circumstances will AI agents or non-deterministic components be granted order execution capabilities. AI agents and MCP tools are strictly scoped to read-only monitoring and analytical roles.

---

## 2. Current Repository Structure

The current codebase is distributed across the root directory and several unstructured subfolders (`My Strategy`, `SAMPLE_CODE_NEED`, `WCL`, `smart_api`):

```
Dhann Rohan/
├── .env                              # Active environment variables (partial)
├── .env.example                      # Template environment variables
├── My Strategy/
│   ├── Option_RSI_strat.py           # Primary production strategy script (~1,178 lines)
│   ├── Dhan_Tradehull_V2.py          # Wrapper library for DhanHQ API operations (~1,880 lines)
│   ├── clude_sensex_strat.py         # Modified variant of the RSI strategy (~1,300 lines)
│   ├── sensexx_2_candle_pattern.py    # Alternative 2-candle pattern strategy draft
│   ├── sensexx_strategy_duplicate.py   # Duplicate copy of sensex strategy script
│   ├── order_placement.py            # Minimal Dhan order placement test script
│   └── maintainance.txt              # Strategy specification and raw operational notes
├── backend/                          # Next-gen platform implementation (FastAPI, Redis, DB)
├── frontend/                         # React monitoring & control terminal
├── docs/
│   ├── ARCHITECTURE.md               # High-level component diagram draft
│   └── ARCHITECTURE_AUDIT.md         # THIS AUDIT DOCUMENTATION FILE
└── master_scrip.csv                  # Downloaded instrument master CSV (~100MB+)
```

---

## 3. Current Runtime Flow

The execution flow of `Option_RSI_strat.py` is procedural and thread-heavy, structured as follows:

```
                  [ Script Execution (__main__) ]
                                │
                      (1) Initialize DhanHQ Client
                     [dhanhq(dhan_context)]
                                │
               (2) Download / Load `master_scrip.csv`
                                │
          ┌─────────────────────┴─────────────────────┐
          ▼                                           ▼
[Thread: SENSEX Feed]                       [Main Thread: Main Logic]
  - Connects to MarketFeed v2                 - Checks Trading Day / Market Hours
  - Polling loop updates                      - Waits for 9:25 AM IST (10m post-open)
    `current_sensex_price`                    - Calls `set_atm_strike_prices()`
                                              - Rounds SENSEX price to ATM (x100)
                                              - Looks up PE & CE Security IDs
                                                        │
                                                        ▼
                                           [Loop: Polling Conditions]
                                              - Updates 15-min Candle DataFrames
                                              - Computes TA-Lib RSI(14)
                                              - Checks Entry Pattern:
                                                iloc[-4] < 60, iloc[-3] < 60, iloc[-2] > 60
                                                        │
                                          ┌─────────────┴─────────────┐
                                          ▼                           ▼
                              (No Condition Hit)              (Condition Hit)
                                  Sleep 15s                   - Set `last_signal_time`
                                                              - Pre-fetch Practical ATM
                                                              - Launch Practical Feed Thread
                                                              - Wait for Breakout (Price > iloc[-2] High)
                                                                          │
                                                                          ▼
                                                              (Breakout Confirmed)
                                                              - `place_option_order()`
                                                              - Set Entry Price & SL (iloc[-2] Low)
                                                              - Stop Trail ATM Feeds
                                                              - Launch `monitor_position()` Thread
                                                                          │
                                                                          ▼
                                                              [Thread: Monitor Position]
                                                              - Polls Practical ATM LTP
                                                              - Checks Index SL Hit
                                                              - Evaluates Trailing SL Trigger
                                                              - Checks Market Close (3:25 PM)
                                                              - Calls `close_option_position()`
                                                              - Restarts Trail Feeds
```

---

## 4. Current Dhan Integration

### Authentication
- Credentials (`CLIENT_ID`, `ACCESS_TOKEN`) are hardcoded as plain-text constants in `Option_RSI_strat.py` lines 16–17.
- `Dhan_Tradehull_V2.py` accepts parameters but instantiates `DhanContext` synchronously inside class initializers.
- No automated token validity check exists. An expired token causes immediate, unhandled runtime crashes during order execution.

### Market Feed
- Operates via `dhanhq.MarketFeed` (v2 WebSocket wrapper).
- Starts separate background worker threads (`Thread(target=...)`) for:
  1. Underlying Index (`sensex_market_feed_handler`)
  2. ATM PE Feed (`trail_atm_pe_feed_handler`)
  3. ATM CE Feed (`trail_atm_ce_feed_handler`)
  4. Practical ATM Breakout Feed (`practical_atm_feed_handler`)
- Market data callbacks mutate un-synchronized global float variables (`current_sensex_price`, `current_pe_price`, `practical_atm_price`).

### REST APIs
- Historical candles and quote data rely on synchronous HTTP calls wrappers in `dhanhq`.
- Instrument lookup performs raw HTTP downloads of `https://images.dhan.co/api-data/api-scrip-master.csv` via `requests.get()`.

### Instrument Lookup
- Performs string scanning over a loaded Pandas DataFrame (`SEM_CUSTOM_SYMBOL.str.strip() == option_name`).
- Formats instrument name using rigid text generation: `f"SENSEX {expiry_date} {strike_price} {option_type}"`.

### Order APIs & Position APIs
- Calls `dhan.place_order(...)` directly using `MARKET` order type and `INTRADAY` product code.
- Position status checks rely on local boolean variables (`position_active`) rather than querying `dhan.get_positions()`.

---

## 5. Existing Strategy Logic

### Strategy Parameters & Rules

| Parameter | Operational Value | Description / Notes |
| :--- | :--- | :--- |
| **Target Instrument** | SENSEX Options (BSE_FNO) | Index: SENSEX (ID: 51, IDX_I) |
| **Timeframe** | 15-Minute Candles | Aggregated manually on tick stream (900 seconds diff) |
| **RSI Period** | 14 | Computed using `talib.RSI()` on close array |
| **Signal Gap** | 15 Minutes | Minimum enforced duration between consecutive entry signals |
| **Cutoff Time** | 02:40 PM IST | `NO_FRESH_TRADE_HOUR = 14`, `NO_FRESH_TRADE_MINUTE = 40` |
| **Market Close Exit** | 03:25 PM IST | Automatic position square-off before market close |
| **Order Type** | MARKET | Intraday product type (`INTRADAY` / `MIS`) |
| **Default Quantity** | 1 Lot | Hardcoded integer parameter in placement function |

### Entry Conditions
A signal triggers when the 15-minute candle RSI array meets all three sequence criteria:
1. `RSI[iloc[-4]] < 59.99`
2. `RSI[iloc[-3]] < 59.99`
3. `RSI[iloc[-2]] > 59.99`
4. Time is prior to `14:40 IST`.
5. Elapsed time since `last_signal_time >= 15` minutes.

### ATM Strike Calculation
- At 09:25 AM IST (10 minutes post market open), the initial SENSEX price is captured.
- Calculated as `convert_to_strike_price_for_sensex(sensex_price) = ceil(sensex_price / 100) * 100`.
- Expiry date is calculated via `get_next_tuesday()`. Note: SENSEX weekly expiry occurs on Tuesdays.

### Breakout Confirmation & Order Placement
- When RSI conditions are met, the bot pre-fetches the current SENSEX ATM option ("Practical ATM") and opens a WebSocket subscription.
- The strategy waits for the **Breakout Condition**: `Current Option Price > iloc[-2] High` of the current ATM option DataFrame.
- Upon confirmation within 15 minutes, a `MARKET BUY` order is executed for the pre-fetched Practical ATM strike.

### Stop Loss & Trailing Stop Loss
- **Initial Stop Loss:** Set to the `low` of the `iloc[-2]` candle of the option DataFrame.
- **Stop Loss Trigger:** Evaluated against the spot SENSEX index level (`current_sensex_price >= stop_loss` for PE, `<= stop_loss` for CE).
- **Trailing SL Activation:** Triggered when Option LTP rises by the range of `iloc[-2]` (`Entry Price + (iloc[-2] High - iloc[-2] Low)`).
- **Trailing SL Shift:** When activated, SL updates to the `low` of the `iloc[-3]` candle if higher than the current SL.

---

## 6. Current State Management

The entire strategy state relies on raw Python global variables and in-memory DataFrames:

```python
# Unsynchronized Global Mutable State
current_sensex_price = 0.0
position_active = False
current_position = None
entry_price = 0.0
stop_loss = 0.0
trail_sl = False
last_signal_time = None

# Mutable Pandas DataFrames (Truncated to tail 100 rows)
sensex_data_df = pd.DataFrame()
pe_option_data_df = pd.DataFrame()
ce_option_data_df = pd.DataFrame()

# Active Thread References
sensex_feed_thread = None
pe_thread = None
ce_thread = None
monitor_thread = None
```

### Critical Flaws in State Handling:
1. **Zero Locking:** Background WebSocket threads update `current_sensex_price`, `current_pe_price`, and option DataFrames simultaneously while the main loop reads them, causing race conditions.
2. **Volatile In-Memory Storage:** Any process crash, machine reboot, or unhandled exception immediately erases all awareness of open positions, trailing SL state, and candle history.
3. **DataFrame Truncation:** Unbound Pandas concatenation and `tail(100)` reset operations alter DataFrame indexing non-atomically.

---

## 7. Current Order Lifecycle

The current system implements a naive, unverified linear order execution flow:

```
[Signal Generated] ──► [place_option_order()] ──► [Dhan API Response]
                                                          │
                                         ┌────────────────┴────────────────┐
                                         ▼                                 ▼
                                 [status == 'success']            [status != 'success']
                                         │                                 │
                                         ▼                                 ▼
                             Set position_active=True             Print error to stdout
                             Start monitor_position()             Reset flags / Return
```

### Missing States & Lifecycle Gaps:
- **No Order Verification:** Assumes HTTP 200 / `status == 'success'` means the order was completely filled at the broker.
- **Missing Pending / Submitted States:** Ignores pending exchange validation, partial fills, or orders stuck in `PENDING` state.
- **No Fill Price Capture:** Sets `entry_price = practical_atm_price` (last observed tick price) rather than the actual execution price reported in the Dhan trade book.
- **No SL Order at Broker:** Stop loss is monitored via local polling loops. If internet connectivity fails, the position remains exposed with **zero broker-side protection**.

---

## 8. Reliability Risks

| Risk Category | Severity | Description & Root Cause | Impact |
| :--- | :--- | :--- | :--- |
| **Duplicate Orders** | **CRITICAL** | Race conditions between condition checking and order submission. Network retries without idempotency keys. | Multiple orders placed for the same signal, over-leveraging account. |
| **Process Crash State Loss** | **CRITICAL** | All state stored in global variables (`position_active`, `stop_loss`). Zero persistent DB. | Bot restarts blind while live position remains unmanaged at broker. |
| **Local Polling Stop Loss** | **CRITICAL** | Stop Loss is not placed on broker exchange; monitored via local `while True` loop with `time.sleep()`. | Network loss or script freeze leaves live trades without protection. |
| **Stale Market Data** | **HIGH** | `while True` loop reads LTP without timestamp validation. Disconnected WebSocket retains last LTP. | Strategy generates signals or evaluates SL on dead market prices. |
| **Thread Race Conditions** | **HIGH** | Multiple threads write to `pe_option_data_df` and global floats without `threading.Lock()`. | Memory corruption, incomplete candles, corrupted RSI values. |
| **CSV Instrument Parsing** | **HIGH** | Daily 100MB CSV download via `requests.get()` during startup. Unhandled schema/connection shifts. | Bot fails to launch or maps wrong security ID on market open. |
| **Fixed Tuesday Expiry** | **MEDIUM** | Hardcoded `get_next_tuesday()` logic ignores holiday calendar shifts (e.g., expiry moved to Monday). | Lookups return empty security ID or wrong contract. |
| **Unbounded Sleep Loops** | **LOW** | Fixed `time.sleep(15)` and `time.sleep(5)` execution delays. | Signal latency up to 15 seconds after candle closure. |

---

## 9. Security Risks

1. **Hardcoded Credentials:** API Access Tokens and Client IDs are committed directly inside source code (`Option_RSI_strat.py` L16-17).
2. **Missing Token Rotation / Renewal:** Static JWT tokens will expire silently, leading to unexpected execution failure during live hours.
3. **Credentials in Output Logs:** Stack traces printed via `traceback.print_exc()` or `print(e)` output raw request bodies and authentication headers to stdout/log files.
4. **Lack of Role-Based API Authorization:** Existing HTTP endpoints or scripts execute with full account capabilities without distinction between read-only and order execution roles.

---

## 10. Concurrency Risks

- **Global Variable Mutations:** `current_sensex_price` is written continuously by the SENSEX WebSocket thread and read simultaneously by `main_trading_logic()` and `monitor_position()`.
- **DataFrame Multi-Threading:** `update_option_dataframe()` executes inside `trail_atm_pe_feed_handler()` while `check_pe_option_trading_conditions()` calls `.values` and `calculate_rsi()` on the same DataFrame instance. Pandas DataFrames are not thread-safe.
- **Dangling Thread Accumulation:** When market feeds close, threads exit via un-joined daemon loops, risking dangling connections and memory leaks.

---

## 11. Failure Recovery Gaps

| Failure Scenario | Current System Behavior | Production Target Requirement |
| :--- | :--- | :--- |
| **A. WebSocket Disconnects** | Silent socket drop; feed handler loops hang or return stale LTP. | Automatic reconnect with exponential backoff & data staleness alert. |
| **B. Dhan API Timeout** | Exception printed to terminal; loop continues without order verification. | Retries with idempotent request IDs; order status fallback poll. |
| **C. Process Crashes** | All state lost. Global variables reset to default `0.0` / `False`. | DB state restoration on launch; automatic state reconstruction. |
| **D. Server Restarts** | Bot starts fresh; ignores existing open positions. | Startup reconciliation service syncs local DB with broker API. |
| **E. Order Rejected** | Error printed; strategy loop continues operating as if idle. | Log rejection reason, dispatch notification, enter cooldown state. |
| **F. Partial Order Fill** | Unhandled; system assumes full fill or zero fill. | Track filled quantity vs leaves quantity; handle partial exits. |
| **G. Broker Position Exists / Local State Empty** | Bot ignores position; leaves it completely unmonitored. | Sync engine detects untracked position, imports it or triggers exit. |
| **H. Local State Exists / Broker Position Empty** | Bot attempts to monitor/exit non-existent trade; fails repeatedly. | Sync engine flags mismatch, clears local stale position state. |
| **I. Access Token Expires** | Script throws auth error and exits process. | Adapter validates token health pre-market; alerts operator. |
| **J. Stale Data Received** | Bot processes tick regardless of age timestamp. | Data Validator drops ticks with timestamp lag > 3 seconds. |
| **K. Duplicate Signal Occurs** | System places duplicate order if gap check is bypassed. | Idempotency lock in Redis prevents concurrent signals. |
| **L. System Starts Mid-Position** | Bot has no state; cannot manage ongoing trade. | State recovery module reads position parameters from PostgreSQL. |

---

## 12. Proposed Target Architecture

The target architecture enforces complete separation of concerns, decoupling market data ingestion, strategy signal calculation, risk evaluation, and broker order execution.

```
                           ┌─────────────────────────┐
                           │   Dhan WebSocket / REST │
                           └────────────┬────────────┘
                                        │
                                        ▼
                           ┌─────────────────────────┐
                           │      DhanAdapter        │
                           └────────────┬────────────┘
                                        │
                                        ▼
                           ┌─────────────────────────┐
                           │      DataValidator      │
                           └────────────┬────────────┘
                                        │
                                        ▼
                           ┌─────────────────────────┐
                           │   15-Min Candle Engine  │
                           └────────────┬────────────┘
                                        │
                                        ▼
                           ┌─────────────────────────┐
                           │   Option RSI Strategy   │
                           └────────────┬────────────┘
                                        │ (Signal)
                                        ▼
                           ┌─────────────────────────┐
                           │      Risk Manager       │
                           └────────────┬────────────┘
                                        │ (Approved Signal)
                                        ▼
                           ┌─────────────────────────┐
                           │     Execution Engine    │
                           └────────────┬────────────┘
                                        │
                                        ▼
                           ┌─────────────────────────┐
                           │     Dhan Broker API     │
                           └─────────────────────────┘

 ─── SUPPORTING INFRASTRUCTURE ───────────────────────────────────────────────
 • PostgreSQL        : Persistent Source of Truth (Orders, Positions, Trades, Logs)
 • Redis             : Transient State, Pub/Sub Tick Bus, Idempotency Locks
 • FastAPI           : REST API & WebSocket Control Layer
 • Trading Worker    : Deterministic Trading Engine Process
 • React Terminal    : Observability Dashboard & Manual Kill-Switch
 • FastMCP Server    : Read-Only Context Gateway for Analytical AI Agents
 • AI Agents         : Read-Only Operational & Strategy Analytics (NO TRADING PERMISSIONS)
```

---

## 13. Migration Plan

A safe 18-phase implementation roadmap to transition from legacy scripts to the production platform without disrupting core strategy math:

- **Phase 0: Audit & Documentation** *(CURRENT PHASE - COMPLETE)*
- **Phase 1: Configuration & Security Infrastructure** (Secure `.env`, Pydantic Settings, Secret Manager)
- **Phase 2: Core Dhan Adapter Layer** (Async client, rate limiters, token validation, paper trading mode)
- **Phase 3: Market Data Engine & Validation** (Async WebSocket manager, Redis Pub/Sub tick broadcasting, staleness filters)
- **Phase 4: Candle Resampling Engine** (Deterministic tick-to-candle engine with strict exchange timestamp alignment)
- **Phase 5: Strategy Logic Isolation** (Pure function state machine wrapping RSI pattern calculation)
- **Phase 6: Centralized Risk Manager** (Account exposure checks, max daily loss limits, duplicate order blocking)
- **Phase 7: Execution Engine & Order Lifecycle** (State machine: PENDING → SUBMITTED → FILLED / REJECTED)
- **Phase 8: Position Tracking & Reconciliation Engine** (Startup broker sync, drift detection)
- **Phase 9: Market Calendar & Scheduler** (Holiday-aware expiry tracking, session start/stop management)
- **Phase 10: PostgreSQL & Redis Persistence Engine** (SQLAlchemy models, migrations, Redis caching)
- **Phase 11: FastAPI Application Layer** (REST endpoints for manual override, kill-switch, status stream)
- **Phase 12: React Terminal Dashboard** (Modern web UI for live P&L, system status, execution logs)
- **Phase 13: FastMCP Protocol Integration** (Expose system state to AI context safely)
- **Phase 14: Read-Only AI Agent Services** (Log analysis, trade post-mortems, performance reports)
- **Phase 15: Containerization & Docker Setup** (Multi-container `docker-compose` orchestration)
- **Phase 16: End-to-End Paper Trading Validation** (Simulation against live market feed)
- **Phase 17: Production Live Deployment & Go-Live Verification**

---

## 14. Recommended New Directory Structure

```
dhan-algo-platform/
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── README.md
├── docs/
│   ├── ARCHITECTURE_AUDIT.md
│   ├── DEPLOYMENT.md
│   └── API_SPECIFICATION.md
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI Entrypoint
│   │   ├── config.py                  # Pydantic BaseSettings Config
│   │   ├── adapter/                   # Dhan Integration Layer
│   │   │   ├── dhan_adapter.py
│   │   │   ├── mock_adapter.py        # Paper Trading Broker Mock
│   │   │   └── instrument_lookup.py
│   │   ├── core/                      # Trading Engine Core
│   │   │   ├── worker.py              # Main Trading Loop Process
│   │   │   ├── candle_engine.py       # OHLCV Resampling
│   │   │   ├── validator.py           # Data Freshness & Sanity Filters
│   │   │   ├── risk_manager.py        # Pre-trade Risk Constraints
│   │   │   └── execution_engine.py    # Order Execution State Machine
│   │   ├── strategy/                  # Isolated Strategy Logic
│   │   │   ├── base.py
│   │   │   └── option_rsi_strategy.py
│   │   ├── db/                        # Database Layer
│   │   │   ├── session.py
│   │   │   ├── models.py
│   │   │   └── repository.py
│   │   ├── mcp/                       # Read-Only FastMCP Gateway
│   │   │   └── server.py
│   │   └── api/                       # REST & WebSocket Routes
│   │       ├── routes_control.py
│   │       ├── routes_positions.py
│   │       └── websocket_feed.py
├── frontend/                          # React Dashboard
│   ├── src/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── services/
│   │   └── App.jsx
└── tests/                             # Test Suite
    ├── unit/
    ├── integration/
    └── simulation/
```

---

## 15. Testing Strategy

1. **Unit Tests:**
   - Strategy Math: Test RSI calculation against standard TA-Lib reference datasets.
   - Entry/Exit Rules: Verify signal generation under exact OHLCV candle arrays.
   - Strike Selection: Verify round-up ATM calculation logic and holiday-shifted expiry dates.
2. **Integration Tests:**
   - Dhan Adapter Mocking: Test REST & WebSocket response handling using recorded mock API payloads.
   - Database Persistence: Verify order lifecycle state transitions in PostgreSQL.
3. **Failure Recovery Tests:**
   - WebSocket Disconnect Simulation: Interrupt network connectivity and verify auto-reconnect and staleness detection.
   - Sudden Process Kill: Terminate worker process while position is active; verify state recovery on reboot.
4. **Paper Trading Simulation:**
   - Run system against real-time Dhan market feed in `PAPER` execution mode for at least 5 consecutive trading days.

---

## 16. Critical Decisions Required

1. **Broker Stop-Loss Mechanism:** Should Stop Loss orders be placed directly on the Dhan exchange (e.g., `STOPLIMIT` / `SL-M` orders) or monitored server-side via ultra-low-latency Redis ticks? *(Recommendation: Server-side trigger with fallback broker-side SL order).*
2. **Paper Trading Engine Mode:** Should paper trading simulate slippage and partial fills based on order book depth?
3. **Database Migration Strategy:** Establish automated Alembic migrations for DB schema updates.

---

## 17. Files That Can Be Deleted

*Note: Do NOT delete any files during this audit phase.*

The following files are identified as obsolete or redundant copies that should be archived or deleted in future phases:

1. `My Strategy/sensexx_strategy_duplicate.py` (Exact redundant copy of strategy script).
2. `My Strategy/order_placement.py` (Unused basic test script).
3. `master_scrip.csv` (Large dynamic CSV; should be managed in gitignore and stored in cache directory).

---

## 18. Files That Must Be Preserved

1. `My Strategy/Option_RSI_strat.py`: Contains the exact production quantitative rules, RSI patterns, entry timing, and trailing SL parameters.
2. `My Strategy/Dhan_Tradehull_V2.py`: Contains reference mapping codes, step sizes (`stock_step_df`, `index_step_dict`), and Dhan API parameters required for backward compatibility verification.

---

## 19. Final Risk Assessment

### System Readiness Rating: **NOT READY FOR LIVE TRADING**

**Rationale:**
- **Security:** Access tokens are exposed in plain text in client script files.
- **Reliability:** All state is in-memory; any disconnect or process crash creates an unmonitored position.
- **Concurrency:** Thread race conditions on DataFrames and global variables risk memory corruption or false trade triggers.
- **Safety:** Zero pre-trade risk checks or broker position reconciliation exist.

**Conclusion:** The codebase must undergo the full phased refactoring outlined in Section 13 before live capital is deployed.

---
*Audit Document Complete. Awaiting User Approval to Proceed to Phase 1.*
