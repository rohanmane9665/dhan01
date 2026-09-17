from fastapi import APIRouter
from app.worker.trading_worker import get_worker

router = APIRouter()


@router.get("/daily")
async def get_daily_analytics():
    """Returns today's live trading analytics from the TradingWorker's RiskManager."""
    worker = get_worker()
    if not worker:
        return {"status": "unavailable", "pnl": 0, "trades_today": 0, "open_positions": 0}

    rm = worker.risk_manager
    positions = worker.position_manager.get_active_positions()
    total_unrealized = sum(p.unrealized_pnl for p in positions)

    win_count = sum(1 for p in positions if p.unrealized_pnl > 0)
    win_rate = round((win_count / len(positions)) * 100, 1) if positions else 0.0

    return {
        "status": "ok",
        "daily_pnl": round(rm.current_daily_pnl, 2),
        "unrealized_pnl": round(total_unrealized, 2),
        "total_pnl": round(rm.current_daily_pnl + total_unrealized, 2),
        "trades_today": rm.trades_executed_today,
        "open_positions": len(positions),
        "win_rate": win_rate,
        "kill_switch_active": rm.kill_switch_active,
    }


@router.get("/historical")
async def get_historical_analytics():
    """Returns historical trade analytics (TODO: query PostgreSQL trade log)."""
    return {"status": "ok", "note": "Historical data requires PostgreSQL integration", "history": []}


from app.database.database import AsyncSessionLocal
from sqlalchemy import select
from app.database.models import Trade, Order

@router.get("/local-trades")
async def get_local_trades():
    """Returns executed trades from the local PostgreSQL database (useful for Paper mode)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Trade).order_by(Trade.timestamp.desc()).limit(50))
        trades = list(result.scalars().all())
        return {"status": "ok", "trades": trades, "count": len(trades)}

@router.get("/local-orders")
async def get_local_orders():
    """Returns orders from the local PostgreSQL database (useful for Paper mode)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).order_by(Order.timestamp.desc()).limit(50))
        orders = list(result.scalars().all())
        return {"status": "ok", "orders": orders, "count": len(orders)}

