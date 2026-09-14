"""
scheduler.py
============
APScheduler-based market session manager.

Responsibilities:
  - Reset daily risk stats at market open (09:15 IST)
  - Force-close all open positions at market close (15:25 IST)
  - Update MarketCalendar year if needed
"""

import logging
from typing import Optional, Any
import pytz
from app.scheduler.market_calendar import MarketCalendar

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False
    logger.warning("APScheduler not installed — scheduler disabled. Run: pip install apscheduler")

_scheduler: Optional[Any] = None


async def _on_market_open(worker):
    """Called at 09:15 IST on trading days."""
    if not MarketCalendar().is_trading_day():
        logger.info("Scheduler: market_open skipped — today is not a trading day.")
        return
    logger.info("📅 MARKET OPEN — resetting daily stats.")
    worker.risk_manager.reset_daily_stats()


async def _on_market_close(worker):
    """Called at 15:25 IST to force-exit all open positions."""
    if not MarketCalendar().is_trading_day():
        return
    logger.info("📅 MARKET CLOSE — force-closing all open positions.")
    positions = worker.position_manager.get_active_positions()
    for pos in list(positions):
        logger.warning(f"Force-closing {pos.symbol} at LTP {pos.current_price} (end of day)")
        await worker.execution_engine.close_position(pos.position_id, pos.current_price)
    logger.info("✅ All positions force-closed for end of day.")


def start_scheduler(worker) -> Optional[Any]:
    """
    Creates and starts the APScheduler with IST cron jobs.
    Returns None (no-op) if APScheduler is not installed.
    """
    if not _APSCHEDULER_AVAILABLE:
        logger.warning("Scheduler not started — APScheduler not installed.")
        return None

    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=IST)

    # Market open: 09:15 IST Mon–Fri
    _scheduler.add_job(
        _on_market_open,
        trigger=CronTrigger(hour=9, minute=15, day_of_week="mon-fri", timezone=IST),
        args=[worker],
        id="market_open",
        name="Market Open Reset",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Market close: 15:25 IST Mon–Fri (5 min before hard close)
    _scheduler.add_job(
        _on_market_close,
        trigger=CronTrigger(hour=15, minute=25, day_of_week="mon-fri", timezone=IST),
        args=[worker],
        id="market_close",
        name="Market Close Force Exit",
        replace_existing=True,
        misfire_grace_time=60,
    )

    _scheduler.start()
    logger.info("✅ APScheduler started. Jobs: market_open (09:15), market_close (15:25)")
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
