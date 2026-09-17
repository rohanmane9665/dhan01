"""
api/routes/dhan_data.py
=======================
REST API routes for fetching dynamic data directly from the Dhan broker API.

These endpoints proxy requests to the DhanHQ SDK and return live broker data
(fund limits, positions, orders, market snapshots, historical candles).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import pytz
from fastapi import APIRouter, HTTPException, Query

from app.dhan.client import get_dhan_client

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

router = APIRouter()


# ── Account & Funds ────────────────────────────────────────────────────
@router.get("/fund-limits")
async def get_fund_limits():
    """Returns live fund limits from Dhan: balance, margins, collateral."""
    client = get_dhan_client()
    try:
        result = await client.get_fund_limits()
        if isinstance(result, dict) and result.get("status") == "failure":
            raise HTTPException(status_code=502, detail=result.get("remarks", "Dhan API error"))
        return {"status": "ok", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching fund limits: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Broker Positions ───────────────────────────────────────────────────
@router.get("/positions")
async def get_broker_positions():
    """Returns live positions directly from the Dhan broker (not local DB)."""
    client = get_dhan_client()
    try:
        result = await client.get_positions()
        positions = []
        if isinstance(result, dict) and result.get("status") == "success":
            positions = result.get("data", [])
        elif isinstance(result, dict):
            positions = result.get("data", [])
        return {"status": "ok", "positions": positions, "count": len(positions)}
    except Exception as e:
        logger.error(f"Error fetching broker positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Broker Orders ──────────────────────────────────────────────────────
@router.get("/orders")
async def get_broker_orders():
    """Returns today's orders from the Dhan broker."""
    client = get_dhan_client()
    try:
        result = await client.get_orders()
        orders = []
        if isinstance(result, dict) and result.get("status") == "success":
            orders = result.get("data", [])
        elif isinstance(result, dict):
            orders = result.get("data", [])
        return {"status": "ok", "orders": orders, "count": len(orders)}
    except Exception as e:
        logger.error(f"Error fetching broker orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Holdings ───────────────────────────────────────────────────────────
@router.get("/holdings")
async def get_holdings():
    """Returns portfolio holdings from Dhan."""
    client = get_dhan_client()
    try:
        result = await client.get_holdings()
        holdings = []
        if isinstance(result, dict) and result.get("status") == "success":
            holdings = result.get("data", [])
        elif isinstance(result, dict):
            holdings = result.get("data", [])
        return {"status": "ok", "holdings": holdings, "count": len(holdings)}
    except Exception as e:
        logger.error(f"Error fetching holdings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Trade Book ─────────────────────────────────────────────────────────
@router.get("/trade-book")
async def get_trade_book(order_id: Optional[str] = Query(None, description="Filter by order ID")):
    """Returns executed trades from Dhan's trade book."""
    client = get_dhan_client()
    try:
        result = await client.get_trade_book(order_id)
        trades = []
        if isinstance(result, dict) and result.get("status") == "success":
            trades = result.get("data", [])
        elif isinstance(result, dict):
            trades = result.get("data", [])
        return {"status": "ok", "trades": trades, "count": len(trades)}
    except Exception as e:
        logger.error(f"Error fetching trade book: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Market Data: Ticker (LTP) ─────────────────────────────────────────
@router.get("/ticker")
async def get_ticker_data(
    exchange_segment: str = Query(..., description="Exchange segment (e.g. BSE_FNO, IDX_I)"),
    security_ids: str = Query(..., description="Comma-separated security IDs (e.g. 12345,67890)"),
):
    """
    Returns latest LTP for the given securities.
    Example: /api/v1/dhan/ticker?exchange_segment=BSE_FNO&security_ids=12345,67890
    """
    client = get_dhan_client()
    try:
        ids = [int(s.strip()) for s in security_ids.split(",") if s.strip()]
        securities = {exchange_segment: ids}
        result = await client.ticker_data(securities)
        return {"status": "ok", "data": result}
    except ValueError:
        raise HTTPException(status_code=400, detail="security_ids must be comma-separated integers")
    except Exception as e:
        logger.error(f"Error fetching ticker data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Market Data: OHLC ─────────────────────────────────────────────────
@router.get("/ohlc")
async def get_ohlc_data(
    exchange_segment: str = Query(..., description="Exchange segment (e.g. BSE_FNO, IDX_I)"),
    security_ids: str = Query(..., description="Comma-separated security IDs"),
):
    """
    Returns OHLC + LTP data for the given securities.
    Example: /api/v1/dhan/ohlc?exchange_segment=IDX_I&security_ids=51
    """
    client = get_dhan_client()
    try:
        ids = [int(s.strip()) for s in security_ids.split(",") if s.strip()]
        securities = {exchange_segment: ids}
        result = await client.ohlc_data(securities)
        return {"status": "ok", "data": result}
    except ValueError:
        raise HTTPException(status_code=400, detail="security_ids must be comma-separated integers")
    except Exception as e:
        logger.error(f"Error fetching OHLC data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Market Data: Full Quotes ──────────────────────────────────────────
@router.get("/quotes")
async def get_quote_data(
    exchange_segment: str = Query(..., description="Exchange segment"),
    security_ids: str = Query(..., description="Comma-separated security IDs"),
):
    """
    Returns full market depth, OHLC, volume, OI, LTP.
    Example: /api/v1/dhan/quotes?exchange_segment=BSE_FNO&security_ids=12345
    """
    client = get_dhan_client()
    try:
        ids = [int(s.strip()) for s in security_ids.split(",") if s.strip()]
        securities = {exchange_segment: ids}
        result = await client.quote_data(securities)
        return {"status": "ok", "data": result}
    except ValueError:
        raise HTTPException(status_code=400, detail="security_ids must be comma-separated integers")
    except Exception as e:
        logger.error(f"Error fetching quote data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Market Data: Intraday Candles ──────────────────────────────────────
@router.get("/intraday-candles")
async def get_intraday_candles(
    security_id: str = Query(..., description="Security ID"),
    exchange_segment: str = Query("BSE_FNO", description="Exchange segment"),
    instrument_type: str = Query("OPTIDX", description="Instrument type"),
    from_date: Optional[str] = Query(None, description="From date YYYY-MM-DD (default: today)"),
    to_date: Optional[str] = Query(None, description="To date YYYY-MM-DD (default: today)"),
    interval: int = Query(1, description="Candle interval in minutes"),
):
    """
    Returns intraday minute-level OHLCV candles from Dhan.
    Example: /api/v1/dhan/intraday-candles?security_id=12345&interval=15
    """
    client = get_dhan_client()
    try:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        from_dt = from_date or today
        to_dt = to_date or today

        result = await client.intraday_minute_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
            from_date=from_dt,
            to_date=to_dt,
            interval=interval,
        )
        return {"status": "ok", "data": result}
    except Exception as e:
        logger.error(f"Error fetching intraday candles: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Market Data: Historical Daily ─────────────────────────────────────
@router.get("/historical-candles")
async def get_historical_candles(
    security_id: str = Query(..., description="Security ID"),
    exchange_segment: str = Query("BSE_FNO", description="Exchange segment"),
    instrument_type: str = Query("OPTIDX", description="Instrument type"),
    from_date: str = Query(..., description="From date YYYY-MM-DD"),
    to_date: str = Query(..., description="To date YYYY-MM-DD"),
):
    """
    Returns daily OHLCV candles from Dhan.
    Example: /api/v1/dhan/historical-candles?security_id=12345&from_date=2025-01-01&to_date=2025-03-01
    """
    client = get_dhan_client()
    try:
        result = await client.historical_daily_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
            from_date=from_date,
            to_date=to_date,
        )
        return {"status": "ok", "data": result}
    except Exception as e:
        logger.error(f"Error fetching historical candles: {e}")
        raise HTTPException(status_code=500, detail=str(e))
