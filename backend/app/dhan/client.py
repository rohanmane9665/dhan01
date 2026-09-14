import asyncio
import logging
from dhanhq import dhanhq
from app.core.config import settings
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class DhanAdapter:
    def __init__(self):
        self.client_id = settings.DHAN_CLIENT_ID
        self.access_token = settings.DHAN_ACCESS_TOKEN
        
        try:
            self.dhan = dhanhq(self.client_id, self.access_token)
            logger.info("Initialized Dhan Adapter (sync REST client wrapped in async).")
        except Exception as e:
            logger.error(f"Failed to initialize dhanhq: {e}")
            self.dhan = None

    async def _run_async(self, func, *args, **kwargs):
        """Helper to run blocking dhanhq calls in a thread pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def get_fund_limits(self) -> Dict[str, Any]:
        return await self._run_async(self.dhan.get_fund_limits)

    async def get_positions(self) -> Dict[str, Any]:
        return await self._run_async(self.dhan.get_positions)
        
    async def get_orders(self) -> Dict[str, Any]:
        return await self._run_async(self.dhan.get_order_list)

    async def place_order(self, security_id: str, exchange_segment: str, transaction_type: str, quantity: int, order_type: str, product_type: str, price: float = 0.0, trigger_price: float = 0.0) -> Dict[str, Any]:
        return await self._run_async(
            self.dhan.place_order,
            security_id=str(security_id),
            exchange_segment=exchange_segment,
            transaction_type=transaction_type,
            quantity=int(quantity),
            order_type=order_type,
            product_type=product_type,
            price=float(price),
            trigger_price=float(trigger_price),
            validity='DAY'
        )
        
    async def modify_order(self, order_id: str, order_type: str, quantity: int, price: float, trigger_price: float) -> Dict[str, Any]:
        return await self._run_async(
            self.dhan.modify_order,
            order_id=order_id,
            order_type=order_type,
            quantity=int(quantity),
            price=float(price),
            trigger_price=float(trigger_price),
            validity='DAY'
        )

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return await self._run_async(self.dhan.cancel_order, order_id=order_id)
        
    async def historical_daily_data(self, security_id, exchange_segment, instrument_type, from_date, to_date, expiry_code=0):
        return await self._run_async(self.dhan.historical_daily_data, security_id, exchange_segment, instrument_type, from_date, to_date, expiry_code)
        
    async def intraday_minute_data(self, security_id, exchange_segment, instrument_type, from_date, to_date, interval=1):
        return await self._run_async(self.dhan.intraday_minute_data, str(security_id), exchange_segment, instrument_type, from_date, to_date, interval)
