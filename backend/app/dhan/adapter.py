import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

try:
    from dhanhq import dhanhq
except ImportError:
    dhanhq = None

from app.adapter.base import BrokerAdapter
from app.core.config import settings

logger = logging.getLogger(__name__)


class DhanAdapter(BrokerAdapter):
    """
    Live DhanHQ Broker Adapter wrapping Dhan REST APIs asynchronously.
    """

    def __init__(self):
        self.client_id = settings.DHAN_CLIENT_ID if settings else ""
        self.access_token = settings.DHAN_ACCESS_TOKEN if settings else ""
        self.dhan = None
        self._connected = False

    async def connect(self) -> bool:
        if not self.client_id or not self.access_token or dhanhq is None:
            logger.warning("Dhan credentials or dhanhq SDK missing. Live adapter connection pending.")
            self._connected = False
            return False
        try:
            loop = asyncio.get_running_loop()
            self.dhan = await loop.run_executor(None, lambda: dhanhq(self.client_id, self.access_token))
            self._connected = True
            logger.info("DhanAdapter connected successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to connect DhanAdapter: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> bool:
        self._connected = False
        self.dhan = None
        logger.info("DhanAdapter disconnected.")
        return True

    async def _run_async(self, func, *args, **kwargs):
        if not self.dhan:
            return {"status": "failure", "remarks": "Dhan client not initialized"}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def place_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Places order with Dhan API. Never retries blindly on uncertainty.
        """
        return await self._run_async(
            self.dhan.place_order,
            security_id=str(order_data.get("security_id")),
            exchange_segment=order_data.get("exchange_segment", "BSE_FNO"),
            transaction_type=order_data.get("transaction_type", "BUY"),
            quantity=int(order_data.get("quantity", 1)),
            order_type=order_data.get("order_type", "MARKET"),
            product_type=order_data.get("product_type", "INTRADAY"),
            price=float(order_data.get("price", 0.0)),
            trigger_price=float(order_data.get("trigger_price", 0.0)),
            validity='DAY'
        )

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return await self._run_async(self.dhan.cancel_order, order_id=str(order_id))

    async def get_positions(self) -> List[Dict[str, Any]]:
        res = await self._run_async(self.dhan.get_positions)
        if isinstance(res, dict) and res.get("status") == "success":
            return res.get("data", [])
        return []

    async def get_orders(self) -> List[Dict[str, Any]]:
        res = await self._run_async(self.dhan.get_order_list)
        if isinstance(res, dict) and res.get("status") == "success":
            return res.get("data", [])
        return []

    async def get_trade_book(self, order_id: Optional[str] = None) -> List[Dict[str, Any]]:
        res = await self._run_async(self.dhan.get_trade_book, order_id) if order_id else await self._run_async(self.dhan.get_trade_book)
        if isinstance(res, dict) and res.get("status") == "success":
            return res.get("data", [])
        return []


class PaperBrokerAdapter(BrokerAdapter):
    """
    Paper Trading Broker Adapter simulating fills, slippage, and position tracking safely in memory.
    """

    def __init__(self, slippage_pct: float = 0.0005):
        self.slippage_pct = slippage_pct
        self._connected = False
        self.orders: List[Dict[str, Any]] = []
        self.positions: Dict[str, Dict[str, Any]] = {}

    async def connect(self) -> bool:
        self._connected = True
        logger.info("PaperBrokerAdapter connected in PAPER mode.")
        return True

    async def disconnect(self) -> bool:
        self._connected = False
        logger.info("PaperBrokerAdapter disconnected.")
        return True

    async def place_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        order_id = f"PAPER_{uuid.uuid4().hex[:8].upper()}"
        side = order_data.get("transaction_type", "BUY")
        quantity = int(order_data.get("quantity", 1))
        security_id = str(order_data.get("security_id", "PAPER_SEC"))
        req_price = float(order_data.get("price", 100.0))
        if req_price <= 0:
            req_price = float(order_data.get("reference_price", 100.0))

        # Apply simulated slippage
        slippage = req_price * self.slippage_pct if side == "BUY" else -req_price * self.slippage_pct
        fill_price = round(req_price + slippage, 2)

        order_record = {
            "orderId": order_id,
            "securityId": security_id,
            "transactionType": side,
            "quantity": quantity,
            "tradedPrice": fill_price,
            "orderStatus": "FILLED",
            "createTime": datetime.now().isoformat()
        }
        self.orders.append(order_record)

        # Update simulated position
        pos = self.positions.get(security_id, {"securityId": security_id, "netQty": 0, "dayBuyValue": 0.0, "daySellValue": 0.0})
        if side == "BUY":
            pos["netQty"] += quantity
            pos["dayBuyValue"] += fill_price * quantity
        else:
            pos["netQty"] -= quantity
            pos["daySellValue"] += fill_price * quantity

        self.positions[security_id] = pos

        logger.info(f"[PAPER] Order Executed: {side} {quantity} x {security_id} @ ₹{fill_price} (Order ID: {order_id})")
        return {
            "status": "success",
            "data": {
                "orderId": order_id,
                "orderStatus": "FILLED",
                "tradedPrice": fill_price
            }
        }

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        for o in self.orders:
            if o["orderId"] == order_id:
                o["orderStatus"] = "CANCELLED"
                return {"status": "success", "data": {"orderId": order_id, "orderStatus": "CANCELLED"}}
        return {"status": "failure", "remarks": "Order not found"}

    async def get_positions(self) -> List[Dict[str, Any]]:
        return list(self.positions.values())

    async def get_orders(self) -> List[Dict[str, Any]]:
        return self.orders
