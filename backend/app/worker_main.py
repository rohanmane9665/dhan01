"""
worker_main.py
==============
Entry point for the standalone Trading Worker process.

Initializes:
  - Database tables
  - Broker adapter (LIVE or PAPER)
  - TradingWorker
  - MarketFeed (real DhanHQ SDK, no simulated data)
  - Scheduler
  - Redis command listener
"""

import asyncio
import logging
import math
from datetime import datetime

import pytz

from app.core.config import settings
from app.core.logging import setup_logging
from app.database.database import engine, Base
from app.worker.trading_worker import TradingWorker, set_worker
from app.scheduler.scheduler import start_scheduler
from app.market.feed import MarketFeed
from app.core.redis import redis_client
from app.dhan.client import get_dhan_client

setup_logging()
logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


async def resolve_instruments(worker: TradingWorker) -> list:
    """
    Resolve the real instrument IDs for MarketFeed subscription.

    Subscribes to:
    1. NIFTY 50 Index (IDX_I, security_id=13, Ticker mode)
    """
    instruments = []
    type_map = {}

    try:
        from dhanhq import MarketFeed as DhanMF
    except ImportError:
        logger.error("dhanhq not installed. Cannot resolve instruments for MarketFeed.")
        return instruments, type_map

    NIFTY_SECURITY_ID = "13"

    # 1. Subscribe to NIFTY Index
    instruments.append((DhanMF.IDX, NIFTY_SECURITY_ID, DhanMF.Ticker))
    type_map[NIFTY_SECURITY_ID] = "INDEX"
    logger.info(f"📊 Subscribed to NIFTY Index (ID: {NIFTY_SECURITY_ID})")

    return instruments, type_map


async def command_listener(worker: TradingWorker):
    """Listen for commands from the FastAPI backend via Redis."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("trading_commands")
    logger.info("🎧 Worker listening for trading_commands on Redis")

    async for message in pubsub.listen():
        if message["type"] == "message":
            import json
            try:
                data = json.loads(message["data"])
                cmd = data.get("command")
                if cmd == "kill":
                    await worker.risk_manager.activate_kill_switch(data.get("reason", "Operator triggered"))
                elif cmd == "reset_kill":
                    await worker.risk_manager.reset_kill_switch(data.get("reason", "Operator reset"))
                elif cmd == "close_position":
                    pos_id = data.get("position_id")
                    pos = worker.position_manager.active_positions.get(pos_id)
                    if pos:
                        await worker.execution_engine.close_position(pos_id, pos.current_price)
                        logger.info(f"Closed position {pos_id} via command")
            except Exception as e:
                logger.error(f"Error processing command {message}: {e}")


async def main():
    logger.info("🚀 Dhan Algo Trading Worker starting up...")

    # Ensure DB tables exist (in case worker starts before API)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables created/verified.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

    trading_mode = settings.TRADING_MODE if settings else "PAPER"
    if trading_mode == "LIVE" and settings and settings.ENABLE_LIVE_TRADING:
        from app.dhan.adapter import DhanAdapter
        broker = DhanAdapter()
        logger.info("🔴 LIVE trading mode selected.")
    else:
        from app.dhan.adapter import PaperBrokerAdapter
        broker = PaperBrokerAdapter()
        logger.info("📄 PAPER trading mode selected.")

    worker = TradingWorker(broker)
    set_worker(worker)
    await worker.start()

    # Resolve instruments for the MarketFeed
    instruments, type_map = await resolve_instruments(worker)

    # Start MarketFeed with real DhanHQ SDK
    feed = MarketFeed()
    for sec_id, tick_type in type_map.items():
        feed.set_instrument_type(sec_id, tick_type)
    await feed.start(instruments if instruments else None)
    worker.market_feed = feed

    # Wait for NIFTY price to populate from live market feed before seeding
    if worker._latest_index <= 0:
        logger.info("⏳ Waiting for NIFTY price from market feed before seeding candles...")
        for _ in range(15):
            if worker._latest_index > 0:
                break
            await asyncio.sleep(1)

    # Seed candles from historical intraday data
    await worker.seed_candles()

    # Start scheduler
    start_scheduler(worker)

    # Run command listener concurrently
    await command_listener(worker)


if __name__ == "__main__":
    asyncio.run(main())
