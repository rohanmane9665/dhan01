"""
dhan/client.py
==============
Async wrapper around the dhanhq REST SDK.

Provides async methods for all Dhan API operations:
- Market Data: ticker, OHLC, quotes, intraday, historical
- Trading: orders, positions, fund limits, trade book, holdings
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class DhanClient:
    """
    Async DhanHQ REST client wrapping the synchronous dhanhq SDK.
    All blocking SDK calls are run in a thread pool via run_in_executor.
    """

    def __init__(self):
        self.client_id = settings.DHAN_CLIENT_ID if settings else ""
        self.access_token = settings.DHAN_ACCESS_TOKEN if settings else ""
        self.dhan = None
        self._initialized = False

    def initialize(self):
        """Initialize the underlying dhanhq client (call once at startup)."""
        if self._initialized:
            return

        if not self.client_id or not self.access_token:
            logger.warning("Dhan credentials not set. DhanClient will not be functional.")
            return

        try:
            from dhanhq import dhanhq
            self.dhan = dhanhq(self.client_id, self.access_token)
            self._initialized = True
            logger.info("✅ DhanClient initialized (sync REST client with async wrapper).")
        except ImportError:
            logger.error("dhanhq package not installed. Install with: pip install dhanhq")
        except Exception as e:
            logger.error(f"Failed to initialize dhanhq: {e}")

    async def _run_async(self, method_name: str, *args, **kwargs):
        """Helper to run blocking dhanhq calls in a thread pool."""
        if not self.dhan:
            self.initialize()
        if not self.dhan:
            return {"status": "failure", "remarks": "Dhan client not initialized"}
        
        func = getattr(self.dhan, method_name)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    # ── Account & Funds ────────────────────────────────────────────────
    async def get_fund_limits(self) -> Dict[str, Any]:
        """Get account fund limits: balance, margin, collateral."""
        return await self._run_async("get_fund_limits")

    # ── Positions & Orders ─────────────────────────────────────────────
    async def get_positions(self) -> Dict[str, Any]:
        """Get all open positions for the day."""
        return await self._run_async("get_positions")

    async def get_orders(self) -> Dict[str, Any]:
        """Get all orders placed today."""
        return await self._run_async("get_order_list")

    async def get_holdings(self) -> Dict[str, Any]:
        """Get portfolio holdings (equity delivery)."""
        return await self._run_async("get_holdings")

    async def get_trade_book(self, order_id: Optional[str] = None) -> Dict[str, Any]:
        """Get trade book (executed trades). Optionally filter by order_id."""
        if order_id:
            return await self._run_async("get_trade_book", order_id)
        return await self._run_async("get_trade_book")

    # ── Order Management ───────────────────────────────────────────────
    async def place_order(
        self,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,
        quantity: int,
        order_type: str,
        product_type: str,
        price: float = 0.0,
        trigger_price: float = 0.0,
    ) -> Dict[str, Any]:
        """Place a new order."""
        return await self._run_async(
            "place_order",
            security_id=str(security_id),
            exchange_segment=exchange_segment,
            transaction_type=transaction_type,
            quantity=int(quantity),
            order_type=order_type,
            product_type=product_type,
            price=float(price),
            trigger_price=float(trigger_price),
            validity="DAY",
        )

    async def modify_order(
        self,
        order_id: str,
        order_type: str,
        quantity: int,
        price: float,
        trigger_price: float,
    ) -> Dict[str, Any]:
        """Modify an existing order."""
        return await self._run_async(
            "modify_order",
            order_id=order_id,
            order_type=order_type,
            quantity=int(quantity),
            price=float(price),
            trigger_price=float(trigger_price),
            validity="DAY",
        )

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an existing order."""
        return await self._run_async("cancel_order", order_id=order_id)

    # ── Market Data: REST Snapshots ────────────────────────────────────
    async def ticker_data(self, securities: Dict[str, List[int]]) -> Dict[str, Any]:
        """
        Get latest LTP (Last Traded Price) for given securities.
        securities format: {"BSE_FNO": [12345, 67890], "NSE_EQ": [1333]}
        """
        return await self._run_async("ticker_data", securities=securities)

    async def ohlc_data(self, securities: Dict[str, List[int]]) -> Dict[str, Any]:
        """
        Get OHLC + LTP data for given securities.
        securities format: {"BSE_FNO": [12345], "IDX_I": [51]}
        """
        return await self._run_async("ohlc_data", securities=securities)

    async def quote_data(self, securities: Dict[str, List[int]]) -> Dict[str, Any]:
        """
        Get full market depth, OHLC, volume, OI, LTP.
        securities format: {"BSE_FNO": [12345]}
        """
        return await self._run_async("quote_data", securities=securities)

    # ── Market Data: Historical ────────────────────────────────────────
    async def historical_daily_data(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str,
        from_date: str,
        to_date: str,
        expiry_code: int = 0,
    ) -> Dict[str, Any]:
        """
        Fetch daily OHLCV candles.
        from_date, to_date format: "YYYY-MM-DD"
        """
        return await self._run_async(
            "historical_daily_data",
            security_id,
            exchange_segment,
            instrument_type,
            from_date,
            to_date,
            expiry_code,
        )

    async def intraday_minute_data(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str,
        from_date: str,
        to_date: str,
        interval: int = 1,
    ) -> Dict[str, Any]:
        """
        Fetch intraday minute-level OHLCV candles.
        from_date, to_date format: "YYYY-MM-DD"
        interval: candle interval in minutes (default 1)
        """
        return await self._run_async(
            "intraday_minute_data",
            str(security_id),
            exchange_segment,
            instrument_type,
            from_date,
            to_date,
            interval,
        )


# Module-level singleton
_dhan_client: Optional[DhanClient] = None


def get_dhan_client() -> DhanClient:
    """Get or create the module-level DhanClient singleton."""
    global _dhan_client
    if _dhan_client is None:
        _dhan_client = DhanClient()
    return _dhan_client
