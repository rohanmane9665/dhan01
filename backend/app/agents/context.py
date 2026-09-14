import json
import logging
from typing import Dict, Any

from app.core.redis import redis_client
from app.database.database import AsyncSessionLocal
from app.database.repositories import EventRepository, PositionRepository, TradeRepository

logger = logging.getLogger(__name__)

class AIContextService:
    """
    Gathers context for AI agents across Redis (real-time) and PostgreSQL (historical).
    Ensures AI agents are strictly read-only and deterministic code handles execution.
    """
    
    @staticmethod
    async def get_trading_analyst_context() -> str:
        """Context tailored for Trading Analyst Agent (Strategy Performance)."""
        snapshot = await AIContextService._get_redis_snapshot()
        
        async with AsyncSessionLocal() as session:
            trade_repo = TradeRepository(session)
            # Fetch today's trades (assuming we'd query by date)
            # For brevity, we'll fetch all trades and just get the count/summary
            trades = await trade_repo.get_recent_trades(limit=50)
            
            pos_repo = PositionRepository(session)
            open_positions = await pos_repo.get_open_positions()

        context = f"""
        Market Snapshot:
        SENSEX: {snapshot.get('sensex')}
        CE LTP: {snapshot.get('ce_ltp')}
        PE LTP: {snapshot.get('pe_ltp')}
        
        Risk Snapshot:
        Daily PNL: {snapshot.get('daily_pnl')}
        Trades Today: {snapshot.get('trades_today')}
        Kill Switch Active: {snapshot.get('kill_switch')}
        
        Recent Trades (DB):
        Total Extracted: {len(trades)}
        
        Open Positions (DB): {len(open_positions)}
        """
        return context

    @staticmethod
    async def get_reliability_context() -> str:
        """Context tailored for Reliability Agent (System health, errors, reconciliation)."""
        snapshot = await AIContextService._get_redis_snapshot()
        
        async with AsyncSessionLocal() as session:
            event_repo = EventRepository(session)
            errors = await event_repo.get_recent_errors(limit=10)
            
            error_list = "\n".join([f"- {e.timestamp}: {e.message}" for e in errors])
            
        context = f"""
        System Health:
        Kill Switch Active: {snapshot.get('kill_switch')}
        
        Recent Critical/Error Events:
        {error_list if error_list else "None recently."}
        """
        return context

    @staticmethod
    async def get_post_market_context() -> str:
        """Context tailored for Post-Market Analyst (EOD summary)."""
        snapshot = await AIContextService._get_redis_snapshot()
        
        context = f"""
        End of Day Summary:
        Final Daily PNL: {snapshot.get('daily_pnl')}
        Total Trades Executed: {snapshot.get('trades_today')}
        """
        return context

    @staticmethod
    async def _get_redis_snapshot() -> Dict[str, Any]:
        try:
            data = await redis_client.get("market_snapshot")
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"Failed to fetch market snapshot for AI context: {e}")
            
        return {}
