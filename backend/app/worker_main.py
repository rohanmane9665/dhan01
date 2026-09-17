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
from app.market.feed import MarketFeed, SENSEX_SECURITY_ID
from app.core.redis import redis_client
from app.dhan.client import get_dhan_client

setup_logging()
logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


async def resolve_instruments(worker: TradingWorker) -> list:
    """
    Resolve the real instrument IDs for MarketFeed subscription.

    Subscribes to:
    1. SENSEX Index (IDX_I, security_id=51, Ticker mode)
    2. ATM CE option (BSE_FNO, resolved from instrument master)
    3. ATM PE option (BSE_FNO, resolved from instrument master)
    """
    instruments = []
    type_map = {}

    try:
        from dhanhq import MarketFeed as DhanMF
    except ImportError:
        logger.error("dhanhq not installed. Cannot resolve instruments for MarketFeed.")
        return instruments, type_map

    # 1. Subscribe to SENSEX Index
    instruments.append((DhanMF.IDX, SENSEX_SECURITY_ID, DhanMF.Ticker))
    type_map[SENSEX_SECURITY_ID] = "INDEX"
    logger.info(f"📊 Subscribed to SENSEX Index (ID: {SENSEX_SECURITY_ID})")

    # 2. Get current SENSEX price from Dhan REST API to determine ATM strike
    dhan_client = get_dhan_client()
    dhan_client.initialize()

    sensex_price = 0.0
    try:
        ticker_result = await dhan_client.ohlc_data({"IDX_I": [int(SENSEX_SECURITY_ID)]})
        if isinstance(ticker_result, dict):
            data = ticker_result.get("data", {})
            if isinstance(data, dict):
                # Dhan returns data nested by segment
                for segment_data in data.values():
                    if isinstance(segment_data, dict) and "last_price" in segment_data:
                        sensex_price = float(segment_data["last_price"])
                    elif isinstance(segment_data, list):
                        for item in segment_data:
                            if isinstance(item, dict):
                                ltp = item.get("last_price") or item.get("LTP") or item.get("ltp", 0)
                                if ltp:
                                    sensex_price = float(ltp)
                                    break
        logger.info(f"📈 Current SENSEX price from Dhan: {sensex_price}")
    except Exception as e:
        logger.warning(f"Could not fetch SENSEX price for ATM: {e}. Will subscribe after first tick.")

    # 3. Resolve ATM CE/PE instruments if we have a SENSEX price
    if sensex_price > 0:
        atm_strike = math.ceil(sensex_price / 100) * 100

        ce_sec_id = worker.instrument_manager.get_security_id("SENSEX", float(atm_strike), "CE")
        pe_sec_id = worker.instrument_manager.get_security_id("SENSEX", float(atm_strike), "PE")

        if ce_sec_id:
            instruments.append((DhanMF.BSE_FNO, str(ce_sec_id), DhanMF.Ticker))
            type_map[str(ce_sec_id)] = "CE"
            logger.info(f"📗 Subscribed to SENSEX {atm_strike} CE (ID: {ce_sec_id})")
        else:
            logger.warning(f"Could not resolve CE security ID for SENSEX {atm_strike}")

        if pe_sec_id:
            instruments.append((DhanMF.BSE_FNO, str(pe_sec_id), DhanMF.Ticker))
            type_map[str(pe_sec_id)] = "PE"
            logger.info(f"📕 Subscribed to SENSEX {atm_strike} PE (ID: {pe_sec_id})")
        else:
            logger.warning(f"Could not resolve PE security ID for SENSEX {atm_strike}")
    else:
        logger.warning("SENSEX price unavailable — option instruments will be subscribed dynamically.")

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

    # Seed candles from historical intraday data
    await worker.seed_candles()

    # Resolve instruments for the MarketFeed
    instruments, type_map = await resolve_instruments(worker)

    # Start MarketFeed with real DhanHQ SDK
    feed = MarketFeed()
    for sec_id, tick_type in type_map.items():
        feed.set_instrument_type(sec_id, tick_type)
    await feed.start(instruments if instruments else None)

    # Start scheduler
    start_scheduler(worker)

    # Run command listener concurrently
    await command_listener(worker)


if __name__ == "__main__":
    asyncio.run(main())
