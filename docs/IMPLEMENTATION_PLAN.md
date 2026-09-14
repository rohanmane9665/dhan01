# Master Implementation Plan — End-to-End DhanHQ Algorithmic Trading Platform

## Executive Overview
Transform the legacy single-file Dhan Python trading code (`Option_RSI_strat.py`) into an enterprise-grade, deterministic automated trading platform. The architecture cleanly decouples market data ingestion, candle aggregation, strategy signal generation, pre-trade risk controls, order execution, position tracking, and post-trade reconciliation, backed by PostgreSQL, Redis, FastAPI, React, FastMCP, and read-only AI analytical agents.

**Non-Negotiable Core Principle:** Deterministic code controls money. AI agents are strictly READ-ONLY observers and analysts. They have ZERO authority or technical ability to place, modify, or cancel orders or alter risk parameters.

---

## Target Architecture

```
                    ┌─────────────────────────┐
                    │ DhanHQ REST & WS API    │
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
                    │  15-Min Candle Engine   │
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
                    │    Execution Engine     │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   Position Manager &    │
                    │ Reconciliation Service  │
                    └─────────────────────────┘

 ─── SUPPORTING INFRASTRUCTURE ───────────────────────────────────────────────
 • Trading Worker    : Deterministic Trading Loop Engine Process
 • PostgreSQL        : Persistent Source of Truth (Orders, Positions, Trades, PnL)
 • Redis             : Transient State, Distributed Locks, Tick Pub/Sub, Idempotency
 • FastAPI           : REST API & Real-time WebSocket Control Layer
 • React Terminal    : Observability Dashboard & Manual Risk Controls
 • FastMCP Server    : Read-Only FastMCP Context Gateway
 • AI Agents         : Read-Only Analytics (Trading Analyst, Reliability Agent, Post-Market Analyst)
```

---

## Detailed Implementation Plan (Phases 2 - 17)

### Phase 2: Dhan Adapter Layer (`backend/app/dhan/adapter.py`)
- Implement `DhanAdapter` inheriting from `BrokerAdapter`.
- Provide complete broker encapsulation: authentication, instrument lookup, quote snapshots, order placement, order status, position fetching, trade book fetching.
- Implement `PaperBrokerAdapter` for risk-free simulated fills, slippage, and partial fill simulation.
- Enforce strict idempotency and zero blind retries on order submissions.

### Phase 3: Market Data & Validation (`backend/app/market/data_manager.py`, `validator.py`)
- Implement streaming WebSocket connection with automatic exponential backoff reconnection.
- `DataValidator`: Enforce maximum tick staleness lag (3 seconds), drop out-of-order or corrupt ticks.
- Enter `SAFE_MODE` and stop signal generation when data feed fails or becomes stale.

### Phase 4: 15-Minute Candle Engine (`backend/app/market/candle_engine.py`)
- Build exchange-timestamp-driven OHLCV candle aggregator for 15-minute intervals.
- Eliminate fixed `time.sleep(15)` loops. Trigger candle completion events on exact timestamp boundary crosses.

### Phase 5: Strategy Isolation (`backend/app/strategies/option_rsi.py`)
- Port quantitative SENSEX Option RSI strategy into pure deterministic function methods.
- Preserve 100% of exact strategy math: 15-min candles, RSI(14), pattern `RSI[-4] < 60`, `RSI[-3] < 60`, `RSI[-2] > 60`, ATM strike calculation (`ceil(sensex / 100) * 100`), breakout confirmation (`Option LTP > iloc[-2] High`), initial SL (`iloc[-2] Low`), trailing SL (`iloc[-3] Low`), 14:40 cutoff, 15:25 exit.
- Emit structured `Signal` objects (no direct broker API calls inside strategy).

### Phase 6: Risk Manager & Kill Switch (`backend/app/risk/manager.py`, `kill_switch.py`)
- Pre-trade risk checks: Daily drawdown limit, max daily trades, max lot size, max open positions, market hours validation, data staleness filter, duplicate signal locks.
- Implement persistent `KillSwitch`: `ACTIVE` vs `TRIGGERED`. When triggered, blocks new orders and requires operator reset.

### Phase 7: Execution Engine (`backend/app/execution/engine.py`, `order_manager.py`)
- Formal order state machine: `CREATED` → `RISK_PENDING` → `RISK_APPROVED` → `SUBMITTING` → `SUBMITTED` → `FILLED` / `REJECTED` / `CANCELLED`.
- Capture exact broker execution price for authoritative position entry tracking.

### Phase 8: Position Tracking & Reconciliation (`backend/app/execution/position_manager.py`, `backend/app/reconciliation/service.py`)
- `PositionManager`: Track real-time P&L, protective SL, and trailing SL adjustments.
- `ReconciliationService`: Compare PostgreSQL local DB state vs. Dhan broker live position/order book on startup, reconnect, and market close. Enter `SAFE_MODE` on mismatch.

### Phase 9: Market Calendar & Scheduler (`backend/app/scheduler/market_calendar.py`, `scheduler.py`)
- Holiday-aware calendar for BSE/NSE 2025/2026 market sessions.
- Automated state transitions: `PRE_MARKET` → `READY` → `TRADING` → `MARKET_CLOSED` → `POST_MARKET`.

### Phase 10: PostgreSQL & Redis Persistence (`backend/app/database/`, `backend/app/core/redis.py`)
- SQLAlchemy models and Alembic migrations for strategies, signals, orders, positions, trades, risk events, audit logs, and agent insights.
- Redis distributed locks and idempotency keys to prevent duplicate signal execution.

### Phase 11: FastAPI Layer (`backend/app/main.py`, `backend/app/api/`)
- REST control endpoints (`/api/v1/trading/status`, `/start`, `/pause`, `/resume`, `/kill`, `/positions`, `/orders`, `/pnl`, `/strategies`, `/health`).
- Real-time WebSocket streaming feed (`/api/v1/ws`) broadcasting tick data, candle updates, orders, and system health to React.

### Phase 12: React Terminal Dashboard (`frontend/src/`)
- Professional dark-mode trading operations dashboard built with React, Vite, Tailwind CSS, Recharts, and Framer Motion.
- Distinct visual badges for `PAPER` vs `🔴 LIVE` trading mode.
- Real-time P&L cards, live RSI chart, active orders table, system health indicators, and prominent manual Kill-Switch button.

### Phase 13: FastMCP Protocol Gateway (`backend/app/mcp/server.py`)
- FastMCP server exposing 14 READ-ONLY analytical tools (`get_market_status`, `get_positions`, `get_orders`, `get_pnl`, `get_risk_status`, `get_system_health`, etc.).
- ZERO write/order tools exposed to MCP.

### Phase 14: Read-Only AI Agents (`backend/app/agents/`)
- `TradingAnalyst`: Analyzes execution quality, win rate, drawdown, and strategy behavior.
- `ReliabilityAgent`: Monitors WebSocket health, latency, reconnect counts, DB sync, and error logs.
- `PostMarketAnalyst`: Runs after session close to produce structured daily trading reports in DB & UI.

### Phase 15: Docker Containerization (`docker-compose.yml`, `Dockerfile`)
- Multi-container architecture: `postgres`, `redis`, `backend` (FastAPI), `trading-worker`, `frontend`.
- Environment variable injection with zero baked secrets.

### Phase 16: Safety Test Suite & Paper Trading Validation (`backend/tests/`)
- Unit tests: Strategy math, RSI accuracy, ATM calculation, candle engine, risk manager, kill switch, order state machine.
- Integration & Failure tests: WebSocket disconnect/reconnect, stale tick rejection, order rejection, partial fill, restart recovery, position mismatch.
- Paper trading end-to-end test execution.

### Phase 17: Validation & Final Implementation Report (`docs/IMPLEMENTATION_REPORT.md`)
- Complete implementation report summarizing all 17 phases, test results, Docker setup, and final system status rating (**READY FOR PAPER**).

---

## Verification Plan

### Automated Verification
1. Run complete pytest test suite:
   ```bash
   python -m pytest backend/tests -v
   ```
2. Verify clean compilation across backend codebase:
   ```bash
   python -m compileall backend
   ```
3. Test FastMCP protocol tools:
   ```bash
   python -m backend.app.mcp.server --test
   ```

### End-to-End System Verification
1. Launch backend services and TradingWorker in `PAPER` mode.
2. Verify FastAPI health check endpoint (`GET http://localhost:8000/health`).
3. Verify React terminal dashboard (`http://localhost:5173`) connects to WebSocket stream.
4. Run simulated paper execution end-to-end (Signal → Risk → Paper Fill → Position → Trailing SL → Exit → DB record → React UI update → AI report).
