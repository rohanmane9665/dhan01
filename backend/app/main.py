import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, trading, positions, streaming, analytics, risk, logs, auth_routes, dhan_data
from app.api.auth import verify_admin_token
from fastapi import Depends
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

    # Worker is now run in a separate process via worker_main.py
    # API just serves requests and reads from DB/Redis

    yield

    # Shutdown
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
    allow_origins=[origin.strip().rstrip("/") for origin in settings.FRONTEND_URL.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(auth_routes.router, prefix="/api/v1/auth", tags=["auth"])

app.include_router(trading.router, prefix="/api/v1/trading", tags=["trading"], dependencies=[Depends(verify_admin_token)])
app.include_router(positions.router, prefix="/api/v1/positions", tags=["positions"], dependencies=[Depends(verify_admin_token)])
app.include_router(streaming.router, prefix="/api/v1/stream", tags=["streaming"], dependencies=[Depends(verify_admin_token)])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["analytics"], dependencies=[Depends(verify_admin_token)])
app.include_router(risk.router, prefix="/api/v1/risk", tags=["risk"], dependencies=[Depends(verify_admin_token)])
app.include_router(logs.router, prefix="/api/v1/logs", tags=["logs"], dependencies=[Depends(verify_admin_token)])
app.include_router(dhan_data.router, prefix="/api/v1/dhan", tags=["dhan-data"], dependencies=[Depends(verify_admin_token)])
