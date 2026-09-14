# Configuration & Security Infrastructure

## Core Principles

> **PAPER is the default mode.**

> **LIVE trading requires explicit dual enablement.**

Under no circumstances will the platform initiate real-money orders unless both `TRADING_MODE=LIVE` and `ENABLE_LIVE_TRADING=true` are explicitly configured alongside valid DhanHQ API credentials.

---

## 1. Environment Configuration Variables

The platform uses Pydantic Settings (`backend/app/core/config.py`) to manage configuration parameters loaded from `.env` or system environment variables:

| Variable Name | Type | Default Value | Description / Requirements |
| :--- | :--- | :--- | :--- |
| `APP_ENV` | string | `development` | Environment environment (`development`, `staging`, `production`) |
| `APP_NAME` | string | `dhan-algo-platform` | System application name |
| `LOG_LEVEL` | string | `INFO` | Console & file logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `TIMEZONE` | string | `Asia/Kolkata` | Standard timezone for market operations |
| `DHAN_CLIENT_ID` | string | `""` | Dhan Client ID (Mandatory for LIVE trading) |
| `DHAN_ACCESS_TOKEN` | string | `""` | Dhan Access Token JWT (Mandatory for LIVE trading) |
| `TRADING_MODE` | string | `PAPER` | Execution mode (`PAPER` or `LIVE`) |
| `ENABLE_LIVE_TRADING` | boolean | `false` | Dual enablement safety switch |
| `DATABASE_URL` | string | `postgresql+asyncpg://...` | PostgreSQL connection string |
| `REDIS_URL` | string | `redis://localhost:6379/0` | Redis connection string |
| `MARKET_DATA_STALE_SECONDS` | integer | `3` | Maximum permitted market data tick lag |
| `MAX_DAILY_LOSS` | float | `None` | Pre-trade risk rule: Max account drawdown |
| `MAX_TRADES_PER_DAY` | integer | `None` | Pre-trade risk rule: Max executed orders |
| `MAX_OPEN_POSITIONS` | integer | `None` | Pre-trade risk rule: Max concurrent trades |
| `MAX_ORDER_QUANTITY` | integer | `None` | Pre-trade risk rule: Max lots/quantity per order |
| `KILL_SWITCH_ENABLED` | boolean | `true` | Emergency manual override kill switch |
| `API_HOST` | string | `0.0.0.0` | FastAPI server listener host |
| `API_PORT` | integer | `8000` | FastAPI server listener port |

---

## 2. Trading Mode Safety Boundaries

### PAPER Mode (Default)
- Configured by default in `.env.example` and standard Settings defaults.
- Permits strategy evaluation and simulated paper executions without broker network access.
- Does NOT require active Dhan credentials to run configuration or paper engine logic.

### LIVE Mode (Dual Enablement Required)
- LIVE execution requires BOTH of the following conditions to be strictly true:
  1. `TRADING_MODE=LIVE`
  2. `ENABLE_LIVE_TRADING=true`
- In addition, non-empty `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` MUST be provided.
- If `TRADING_MODE=LIVE` and `ENABLE_LIVE_TRADING=false`, startup **FAILS FAST** with the following error:
  `LIVE trading is disabled. Set ENABLE_LIVE_TRADING=true explicitly to enable live execution.`

---

## 3. Secret Management & Credential Hygiene

1. **Zero Plaintext Secrets in Code:** All production tokens and Client IDs have been removed from Python source files (`Option_RSI_strat.py`, `clude_sensex_strat.py`, etc.).
2. **Git Exclusion:** `.env` and `.env.*` files are explicitly ignored in `.gitignore`.
3. **Log Masking:** `backend/app/core/logging.py` includes a `SensitiveDataFilter` that automatically redacts access tokens, passwords, secrets, and `Bearer` headers before outputting logs.

---

## 4. Local Development Setup

1. Copy the example environment template:
   ```bash
   cp .env.example .env
   ```
2. Edit `.env` to configure optional database URLs or local overrides.
3. Keep default paper mode settings for safety:
   ```env
   TRADING_MODE=PAPER
   ENABLE_LIVE_TRADING=false
   ```

---

## 5. Docker Environment Preparation

When deploying via Docker, configuration values MUST be injected as environment variables at container launch:

```bash
docker run -e TRADING_MODE=PAPER -e ENABLE_LIVE_TRADING=false dhan-algo-platform
```

> **WARNING:** Never bake secrets or `.env` files into Docker images.

---

## 6. Credential Rotation Procedure

1. If a Dhan access token is suspected of being compromised or expires:
   - Log into the Dhan Developer Console.
   - Revoke the existing token and generate a new Access Token.
   - Update `.env` (or secrets manager) with `DHAN_ACCESS_TOKEN=<new_token>`.
   - Restart the backend worker process.
