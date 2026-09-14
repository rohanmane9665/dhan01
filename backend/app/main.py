import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, trading, positions, streaming, analytics, risk, logs
from app.core.config import settings
from app.core.logging import setup_logging
from app.database.database import engine, Base
from app.worker.trading_worker import TradingWorker, set_worker
from app.scheduler.scheduler import start_scheduler, stop_scheduler

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("🚀 Dhan Algo Trading Platform starting up...")

    # Create DB tables
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables created/verified.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

    # Create the trading worker (uses PaperBrokerAdapter by default)
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

    # Start MarketFeed
    from app.market.feed import MarketFeed
    feed = MarketFeed()
    await feed.start()

    # Start market session scheduler (09:15 open, 15:25 force-close)
    sched = start_scheduler(worker)

    # Attach feed to app state for clean shutdown
    app.state.feed = feed

    yield

    # Shutdown
    stop_scheduler()
    logger.info("Shutting down MarketFeed...")
    await app.state.feed.stop()
    logger.info("Shutting down TradingWorker...")
    await worker.stop()
    logger.info("✅ Shutdown complete.")


from app.api.errors import setup_exception_handlers

app = FastAPI(
    title="Dhan Algo Trading Platform",
    version="2.0.0",
    lifespan=lifespan,
)

# Register custom exception handlers
setup_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(trading.router, prefix="/api/v1/trading", tags=["trading"])
app.include_router(positions.router, prefix="/api/v1/positions", tags=["positions"])
app.include_router(streaming.router, prefix="/api/v1/stream", tags=["streaming"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["analytics"])
app.include_router(risk.router, prefix="/api/v1/risk", tags=["risk"])
app.include_router(logs.router, prefix="/api/v1/logs", tags=["logs"])
