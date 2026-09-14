# Dhan Algo Platform Deployment Guide

## 1. Environment Setup
Create a `.env` file from `.env.example`:
```
DHAN_CLIENT_ID=your_client_id
DHAN_ACCESS_TOKEN=your_jwt_token
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/dhan_algo
REDIS_URL=redis://redis:6379/0
TRADING_MODE=PAPER
ENABLE_LIVE_TRADING=false
```

## 2. Docker Deployment
Run the following to start the platform:
`docker-compose up -d --build`

## 3. Paper Trading (Default)
By default, `TRADING_MODE=PAPER`. The system will connect to the live market feed, calculate RSI, check risk conditions, and simulate the order execution without calling the Dhan `place_order` API.

## 4. Live Trading Activation
To enable real money trading:
1. Set `TRADING_MODE=LIVE`
2. Set `ENABLE_LIVE_TRADING=true`
3. Restart the backend container: `docker-compose restart backend`

> [!WARNING]
> DO NOT enable live trading until you have verified the strategy logic in Paper mode. Live trading executes real market orders.
