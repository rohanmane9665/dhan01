import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import time
from app.core.redis import redis_client

from app.adapter.base import BrokerAdapter
from app.execution.position_manager import PositionManager, Position
from app.risk.manager import RiskManager
from app.strategies.models import Signal
from app.database.database import AsyncSessionLocal
from app.database.repositories import (
    SignalRepository, OrderRepository, TradeRepository, 
    PositionRepository, EventRepository
)

logger = logging.getLogger(__name__)

PRODUCT_TYPE = "MARGIN"
ENTRY_BUFFER = 0.01                # BUY limit = live price + 1%   (154 -> 155.55)
EXIT_BUFFERS = (0.01, 0.02, 0.03)  # SELL limit = live price -1%, then -2%, then -3% on retries
FILL_WAIT_SECONDS = 3              # wait this long for a fill, then cancel
SELL_WAIT_SECONDS = 3              # wait per SELL attempt before cancel + retry
MIN_ENTRY_PRICE = 20               # existing rule: skip trade if fill < 20
ENTRY_LOCK_SECONDS = 30            # blocks duplicate entries for the same symbol
TICK = 0.05                        # NSE option tick size
_TERMINAL_FAIL = ("REJECTED", "CANCELLED", "EXPIRED")

def _tick(price: float) -> float:
    """Round to a valid 0.05 tick."""
    return round(round(price / TICK) * TICK, 2)

class ExecutionEngine:
    """
    Formal Order Lifecycle Execution Engine:
    CREATED -> RISK_PENDING -> RISK_APPROVED -> SUBMITTING -> SUBMITTED -> FILLED / REJECTED
    Captures authoritative fill price from broker. Never assumes HTTP 200 == FILLED.
    Durably persists state transitions to PostgreSQL.
    """

    def __init__(self, broker_adapter: BrokerAdapter, risk_manager: RiskManager, position_manager: PositionManager):
        self.broker = broker_adapter
        self.risk_manager = risk_manager
        self.position_manager = position_manager
        self._exit_locks: Dict[str, asyncio.Lock] = {}
        
        
    async def process_signal(self, signal: Signal, quantity: int = None) -> Dict[str, Any]:
        if quantity:
            signal.quantity = int(quantity)      # 130
        return await self.execute_signal(signal)
        
    async def _submit_and_confirm(self, payload: Dict[str, Any], fallback_price: float, wait: Optional[float] = None) -> Dict[str, Any]:
        """Places ONE order, waits for the broker's final answer. Cancels it if still open."""
        want_qty = int(payload["quantity"])
        resp = await self.broker.place_order(payload)
        if resp.get("status") != "success":
            reason = str(resp.get("remarks", "broker rejected request"))
            logger.error(f"Order {payload['order_id']} not accepted by broker: {reason}")
            return {"ok": False, "filled_qty": 0, "avg_price": 0.0, "oid": None, "reason": reason}
        d = resp.get("data") or {}
        oid = str(d.get("orderId", ""))
        # Paper adapter fills instantly
        if str(d.get("orderStatus", "")).upper() == "FILLED" and d.get("tradedPrice"):
            return {"ok": True, "filled_qty": want_qty, "avg_price": float(d["tradedPrice"]), "oid": oid, "reason": ""}
        # Live Dhan: poll the order status
        last: Dict[str, Any] = {}
        status = ""
        wait = FILL_WAIT_SECONDS if wait is None else wait
        deadline = time.monotonic() + wait
        
        while time.monotonic() < deadline:
            await asyncio.sleep(0.4)
            last = await self.broker.get_order_by_id(oid) or last
            status = str(last.get("orderStatus", "")).upper()
            if status == "TRADED" or status in _TERMINAL_FAIL:
                break
        if status != "TRADED" and status not in _TERMINAL_FAIL:
            logger.warning(f"Order {oid} still {status or 'UNKNOWN'} after {wait}s - cancelling")
            await self.broker.cancel_order(oid)
            await asyncio.sleep(0.6)
            last = await self.broker.get_order_by_id(oid) or last
            status = str(last.get("orderStatus", "")).upper()
            
        if status != "TRADED" and status not in _TERMINAL_FAIL:
            # cancel attempted but still no final status -> check the trade book
            try:
                tb = await self.broker.get_trade_book(oid)
                q = sum(int(float(t.get("tradedQuantity", 0))) for t in tb)
                if q > 0:
                    px = sum(float(t.get("tradedPrice", 0)) * float(t.get("tradedQuantity", 0)) for t in tb) / q
                    return {"ok": True, "filled_qty": q, "avg_price": px, "oid": oid, "reason": "", "unknown": False}
            except Exception:
                pass
            return {"ok": False, "filled_qty": 0, "avg_price": 0.0, "oid": oid,
                    "reason": "status_unknown", "unknown": True}

        filled = int(float(last.get("filledQty") or 0))
  
        if status == "TRADED" and filled <= 0:
            filled = want_qty
        avg = float(last.get("averageTradedPrice") or last.get("tradedPrice") or 0.0) or float(fallback_price or 0.0)
        reason = str(last.get("omsErrorDescription") or status or "no response")
        if filled <= 0:
            logger.error(f"Order {oid} NOT filled: {reason}")
            return {"ok": False, "filled_qty": 0, "avg_price": 0.0, "oid": oid, "reason": reason}
        if filled < want_qty:
            logger.warning(f"Order {oid} PARTIALLY filled {filled}/{want_qty}")
        return {"ok": True, "filled_qty": filled, "avg_price": avg, "oid": oid, "reason": ""}
    async def _live_price_rest(self, security_id: str, exchange_segment: str) -> float:
        """Fresh LTP straight from Dhan REST. Returns 0.0 if unavailable."""
        try:
            from app.dhan.client import get_dhan_client
            r = await get_dhan_client().ticker_data({exchange_segment: [int(security_id)]})
            d = (r or {}).get("data", {})
            d = d.get("data", d)
            return float(d[exchange_segment][str(security_id)]["last_price"])
        except Exception:
            return 0.0
            
    async def _sell_with_retry(self, security_id: str, exchange_segment: str, qty: int,
                               ref_price: float, tag: str) -> Dict[str, Any]:
        """SELL with LIMIT just below live price; cancel + retry wider if not filled."""
        remaining, total_qty, total_val, last_oid, last_reason = int(qty), 0, 0.0, None, ""
        for i, buf in enumerate(EXIT_BUFFERS):
            if remaining <= 0:
                break
            if i > 0:   # retries: re-price off the current market, not the old tick
                fresh = await self._live_price_rest(security_id, exchange_segment)
                if fresh > 0:
                    ref_price = fresh
            limit_price = _tick(max(ref_price * (1 - buf), TICK))
            payload = {
                "order_id": f"{tag}_{i}",
                "correlation_id": f"{tag}_{i}",
                "security_id": security_id,
                "exchange_segment": exchange_segment,
                "transaction_type": "SELL",
                "quantity": remaining,
                "order_type": "LIMIT",
                "product_type": PRODUCT_TYPE,
                "price": limit_price,
                "reference_price": ref_price,
            }
            logger.info(f"SELL attempt {i + 1}/{len(EXIT_BUFFERS)}: {remaining} x {security_id} LIMIT {limit_price} (live {ref_price})")
            r = await self._submit_and_confirm(payload, ref_price, wait=SELL_WAIT_SECONDS)
            last_oid, last_reason = r["oid"] or last_oid, r["reason"]
            if r.get("unknown"):
                logger.critical(f"Order {r['oid']} status unknown - NOT retrying, check Dhan manually")
                break
          
            if r["filled_qty"] > 0:
                total_qty += r["filled_qty"]
                total_val += r["avg_price"] * r["filled_qty"]
                remaining -= r["filled_qty"]
        avg = (total_val / total_qty) if total_qty else 0.0
        return {"filled_qty": total_qty, "avg_price": avg, "oid": last_oid, "reason": last_reason}

    async def _exit_ref_price(self, pos: Position, passed_price: float) -> float:
        """A valid live price for exits: passed price -> position LTP -> REST LTP."""
        for p in (passed_price, getattr(pos, "current_price", 0.0)):
            if p and p > 0:
                return float(p)
        try:
            from app.dhan.client import get_dhan_client
            r = await get_dhan_client().ticker_data({"NSE_FNO": [int(pos.security_id)]})
            d = (r or {}).get("data", {})
            d = d.get("data", d)
            return float(d["NSE_FNO"][str(pos.security_id)]["last_price"])
        except Exception as e:
            logger.error(f"No live price available for exit of {pos.symbol}: {e}")
            return 0.0
            
    async def execute_signal(self, signal: Signal) -> Dict[str, Any]:
        order_id = f"ORD_{uuid.uuid4().hex[:8].upper()}"
        # never trade without a live price
        if not signal.entry_reference or signal.entry_reference <= 0:
            logger.error(f"Order {order_id} aborted: no live price for {signal.symbol}")
            return {"order_id": order_id, "status": "REJECTED", "reason": "no_live_price"}
        if signal.entry_reference < MIN_ENTRY_PRICE:
            return {"order_id": order_id, "status": "REJECTED", "reason": "price_below_min"}
            
        # duplicate guard (two workers / double signals)
        lock_key = f"entry_lock:{signal.symbol}"
        if not await redis_client.set(lock_key, "1", nx=True, ex=ENTRY_LOCK_SECONDS):
            logger.warning(f"Duplicate entry for {signal.symbol} blocked")
            return {"order_id": order_id, "status": "REJECTED", "reason": "duplicate_signal"}
        result: Optional[Dict[str, Any]] = None
        try:
            result = await self._execute_entry(signal, order_id)
            return result
        finally:
            if not result or result.get("status") != "FILLED":
                await redis_client.delete(lock_key)      # allow a legitimate retry

    async def _execute_entry(self, signal: Signal, order_id: str) -> Dict[str, Any]:
        active_count = len(self.position_manager.get_active_positions())
        async with AsyncSessionLocal() as session:
            sig_repo = SignalRepository(session)
            order_repo = OrderRepository(session)
            pos_repo = PositionRepository(session)
            trade_repo = TradeRepository(session)
            event_repo = EventRepository(session)
            sig_model = await sig_repo.create_signal({
                "id": f"SIG_{uuid.uuid4().hex[:8].upper()}",
                "strategy_id": signal.strategy_id,
                "symbol": signal.symbol,
                "option_type": signal.option_type,
                "strike": float(signal.security_id.split('_')[1]) if '_' in signal.security_id else 0.0,
                "side": signal.direction,
                "quantity": signal.quantity,
                "entry_price": signal.entry_reference,
                "stop_loss": signal.stop_loss_reference,
                "reason": signal.signal_reason,
                "timestamp": signal.timestamp,
                "status": "NEW"
            })
            approved, reason = self.risk_manager.evaluate_signal(signal, current_positions_count=active_count)
            if not approved:
                logger.warning(f"Order {order_id} REJECTED by RiskManager: {reason}")
                await sig_repo.update_status(sig_model.id, "REJECTED")
                await event_repo.log_risk_event("SIGNAL_REJECTED", f"Order {order_id} rejected: {reason}")
                return {"order_id": order_id, "status": "REJECTED", "reason": reason}
            await sig_repo.update_status(sig_model.id, "APPROVED")
            limit_price = _tick(signal.entry_reference * (1 + ENTRY_BUFFER))
            exchange_segment = "NSE_FNO" if "NIFTY" in signal.symbol.upper() else "BSE_FNO"
            await order_repo.create_order({
                "id": order_id,
                "signal_id": sig_model.id,
                "symbol": signal.symbol,
                "side": signal.direction,
                "quantity": signal.quantity,
                "order_type": "LIMIT",
                "price": limit_price,
                "status": "PENDING"
            })
            order_payload = {
                "order_id": order_id,
                "correlation_id": order_id,
                "security_id": signal.security_id,
                "exchange_segment": exchange_segment,
                "transaction_type": signal.direction,
                "quantity": signal.quantity,
                "order_type": "LIMIT",
                "product_type": PRODUCT_TYPE,
                "price": limit_price,
                "reference_price": signal.entry_reference,
            }
            logger.info(f"Submitting Order {order_id} ({signal.symbol}) BUY {signal.quantity} LIMIT {limit_price} (live {signal.entry_reference})")
            r = await self._submit_and_confirm(order_payload, signal.entry_reference)
            # Not filled at all -> fail cleanly, NO position
            if r.get("unknown"):
                logger.critical(f"Order {order_id} status unknown - may be filled at Dhan, check manually")
            if r["filled_qty"] <= 0:
                logger.error(f"Order {order_id} failed at broker: {r['reason']}")
                await order_repo.update_status(order_id, "FAILED", r["oid"])
                await sig_repo.update_status(sig_model.id, "REJECTED")
                await event_repo.log_system_event("ORDER_FAILED", "ERROR", f"Order {order_id} failed: {r['reason']}")
                return {"order_id": order_id, "status": "FAILED", "reason": r["reason"]}
            filled_qty, fill_price = r["filled_qty"], r["avg_price"]
            
            # Partial entry fill or price below minimum -> sell back, skip trade
            if filled_qty < signal.quantity or fill_price < MIN_ENTRY_PRICE:
                why = "partial_fill" if filled_qty < signal.quantity else "fill_price_below_20"
                logger.warning(f"Entry {why} (filled {filled_qty} @ {fill_price}) - selling back, trade skipped")
                sell = await self._sell_with_retry(signal.security_id, exchange_segment, filled_qty,
                                                   fill_price, f"{order_id}_X")
                if sell["filled_qty"] < filled_qty:
                    left = filled_qty - sell["filled_qty"]
                    logger.critical(f"!!! {left} qty of {signal.symbol} STILL HELD at broker - square off manually !!!")
                    await event_repo.log_system_event("ORPHAN_QTY", "CRITICAL", f"{left} x {signal.symbol} held after failed sell-back")
                await order_repo.update_status(order_id, "REJECTED", r["oid"])
                return {"order_id": order_id, "status": "REJECTED", "reason": why}
            # Full fill -> open position with the REAL fill price
            await order_repo.update_status(order_id, "FILLED", r["oid"])
            await trade_repo.create_trade({
                "id": f"TRD_{uuid.uuid4().hex[:8].upper()}",
                "order_id": order_id,
                "symbol": signal.symbol,
                "side": signal.direction,
                "quantity": filled_qty,
                "price": fill_price
            })
            pos_id = f"POS_{uuid.uuid4().hex[:8].upper()}"
            position = Position(
                position_id=pos_id,
                symbol=signal.symbol,
                security_id=signal.security_id,
                option_type=signal.option_type,
                quantity=filled_qty,
                entry_price=fill_price,
                stop_loss=signal.stop_loss_reference,
                strategy_id=signal.strategy_id
            )
            self.position_manager.add_position(position)
            await pos_repo.create_position({
                "id": pos_id,
                "symbol": signal.symbol,
                "quantity": filled_qty,
                "entry_price": fill_price,
                "current_sl": signal.stop_loss_reference,
                "status": "OPEN"
            })
            self.risk_manager.record_trade_execution()
            await event_repo.log_system_event("POSITION_OPENED", "INFO", f"Opened {pos_id} for {signal.symbol} at {fill_price}")
            return {
                "order_id": order_id,
                "broker_order_id": r["oid"],
                "status": "FILLED",
                "traded_price": fill_price,
                "position_id": pos_id
            }
    def _lock_for(self, position_id: str) -> asyncio.Lock:
        return self._exit_locks.setdefault(position_id, asyncio.Lock())

    async def close_position(self, position_id: str, exit_price: float):
        lock = self._lock_for(position_id)
        if lock.locked():
            return {"status": "SKIPPED", "reason": "exit_in_progress"}
        async with lock:
            return await self._close_locked(position_id, exit_price)

    async def partial_close_position(self, position_id: str, quantity: int, exit_price: float):
        lock = self._lock_for(position_id)
        if lock.locked():
            return {"status": "SKIPPED", "reason": "exit_in_progress"}
        async with lock:
            return await self._partial_close_locked(position_id, quantity, exit_price)
            
    async def _partial_close_locked(self, position_id: str, quantity: int, exit_price: float) -> Dict[str, Any]:
        pos = self.position_manager.active_positions.get(position_id)
        if not pos or pos.quantity < quantity:
            return {"status": "ERROR", "reason": f"Position {position_id} not found or insufficient quantity"}
        ref = await self._exit_ref_price(pos, exit_price)
        if ref <= 0:
            return {"status": "FAILED", "reason": "no_live_price"}
        order_id = f"EXIT_{uuid.uuid4().hex[:8].upper()}"
        exchange_segment = "NSE_FNO" if "NIFTY" in pos.symbol.upper() else "BSE_FNO"
        logger.info(f"Partial closing {position_id} ({pos.symbol}) - Qty {quantity}, live ₹{ref}")
        async with AsyncSessionLocal() as session:
            order_repo = OrderRepository(session)
            pos_repo = PositionRepository(session)
            trade_repo = TradeRepository(session)
            event_repo = EventRepository(session)
            await order_repo.create_order({
                "id": order_id, "symbol": pos.symbol, "side": "SELL", "quantity": quantity,
                "order_type": "LIMIT", "price": _tick(ref * (1 - EXIT_BUFFERS[0])), "status": "PENDING"
            })
            sell = await self._sell_with_retry(pos.security_id, exchange_segment, quantity, ref, order_id)
            if sell["filled_qty"] <= 0:
                await order_repo.update_status(order_id, "FAILED", sell["oid"])
                await event_repo.log_system_event("ORDER_FAILED", "ERROR", f"Partial exit {order_id} failed: {sell['reason']}")
                return {"order_id": order_id, "status": "FAILED", "reason": sell["reason"]}
            filled, fill_price = sell["filled_qty"], sell["avg_price"]
            await order_repo.update_status(order_id, "FILLED", sell["oid"])
            await trade_repo.create_trade({
                "id": f"TRD_{uuid.uuid4().hex[:8].upper()}", "order_id": order_id, "symbol": pos.symbol,
                "side": "SELL", "quantity": filled, "price": fill_price
            })
            self.position_manager.partial_close(position_id, filled, fill_price)
            pos.t1_sold_qty += filled
            await pos_repo.update_position(position_id, {"quantity": pos.quantity})
            await event_repo.log_system_event("POSITION_PARTIAL_CLOSED", "INFO", f"Sold {filled} of {position_id} at {fill_price}")
            if filled < quantity:
                return {"order_id": order_id, "status": "FAILED", "reason": f"only {filled}/{quantity} sold"}
            return {"order_id": order_id, "status": "PARTIAL_CLOSED", "traded_price": fill_price}
            
    async def _close_locked(self, position_id: str, exit_price: float) -> Dict[str, Any]:
        pos = self.position_manager.active_positions.get(position_id)
        if not pos:
            return {"status": "ERROR", "reason": f"Position {position_id} not found"}
        ref = await self._exit_ref_price(pos, exit_price)
        if ref <= 0:
            return {"status": "FAILED", "reason": "no_live_price"}
        order_id = f"EXIT_{uuid.uuid4().hex[:8].upper()}"
        exchange_segment = "NSE_FNO" if "NIFTY" in pos.symbol.upper() else "BSE_FNO"
        qty = pos.quantity
        logger.info(f"Closing position {position_id} ({pos.symbol}) Qty {qty}, live ₹{ref}")
        async with AsyncSessionLocal() as session:
            order_repo = OrderRepository(session)
            pos_repo = PositionRepository(session)
            trade_repo = TradeRepository(session)
            event_repo = EventRepository(session)
            await order_repo.create_order({
                "id": order_id, "symbol": pos.symbol, "side": "SELL", "quantity": qty,
                "order_type": "LIMIT", "price": _tick(ref * (1 - EXIT_BUFFERS[0])), "status": "PENDING"
            })
            sell = await self._sell_with_retry(pos.security_id, exchange_segment, qty, ref, order_id)
            if sell["filled_qty"] <= 0:
                await order_repo.update_status(order_id, "FAILED", sell["oid"])
                await event_repo.log_system_event("ORDER_FAILED", "ERROR", f"Exit {order_id} failed: {sell['reason']}")
                logger.error(f"Exit {order_id} failed ({sell['reason']}) - position stays open, will retry on next tick")
                return {"order_id": order_id, "status": "FAILED", "reason": sell["reason"]}
            filled, fill_price = sell["filled_qty"], sell["avg_price"]
            await order_repo.update_status(order_id, "FILLED", sell["oid"])
            await trade_repo.create_trade({
                "id": f"TRD_{uuid.uuid4().hex[:8].upper()}", "order_id": order_id, "symbol": pos.symbol,
                "side": "SELL", "quantity": filled, "price": fill_price
            })
            if filled < qty:
                # Only part sold: keep position open with remaining qty; next tick retries
                self.position_manager.partial_close(position_id, filled, fill_price)
                await pos_repo.update_position(position_id, {"quantity": pos.quantity})
                logger.error(f"Exit only {filled}/{qty} sold - {pos.quantity} still open, will retry")
                return {"order_id": order_id, "status": "FAILED", "reason": f"only {filled}/{qty} sold"}
            closed = self.position_manager.close_position(position_id, fill_price)
            if closed:
                self.risk_manager.record_position_closed(closed.realized_pnl)
            await pos_repo.update_position(position_id, {
                "status": "CLOSED",
                "closed_at": datetime.now(timezone.utc),
                "pnl": closed.realized_pnl if closed else 0.0
            })
            await event_repo.log_system_event("POSITION_CLOSED", "INFO", f"Closed {position_id} at {fill_price}")
            return {
                "order_id": order_id,
                "status": "CLOSED",
                "traded_price": fill_price,
                "realized_pnl": closed.realized_pnl if closed else None,
            }
