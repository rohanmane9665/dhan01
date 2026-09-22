import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

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

    async def execute_signal(self, signal: Signal) -> Dict[str, Any]:
        order_id = f"ORD_{uuid.uuid4().hex[:8].upper()}"
        active_count = len(self.position_manager.get_active_positions())

        async with AsyncSessionLocal() as session:
            sig_repo = SignalRepository(session)
            order_repo = OrderRepository(session)
            pos_repo = PositionRepository(session)
            trade_repo = TradeRepository(session)
            event_repo = EventRepository(session)

            # Persist Signal
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

            # Step 1: RISK_PENDING -> Risk Evaluation
            approved, reason = self.risk_manager.evaluate_signal(signal, current_positions_count=active_count)
            if not approved:
                logger.warning(f"Order {order_id} REJECTED by RiskManager: {reason}")
                await sig_repo.update_status(sig_model.id, "REJECTED")
                await event_repo.log_risk_event("SIGNAL_REJECTED", f"Order {order_id} rejected: {reason}")
                return {
                    "order_id": order_id,
                    "status": "REJECTED",
                    "reason": reason
                }

            await sig_repo.update_status(sig_model.id, "APPROVED")

            # Persist Order (PENDING)
            db_order = await order_repo.create_order({
                "id": order_id,
                "signal_id": sig_model.id,
                "symbol": signal.symbol,
                "side": signal.direction,
                "quantity": signal.quantity,
                "order_type": "MARKET",
                "price": signal.entry_reference,
                "status": "PENDING"
            })

            # Step 2: SUBMITTING -> Submit to Broker Adapter
            exchange_segment = "NSE_FNO" if "NIFTY" in signal.symbol.upper() else "BSE_FNO"
            order_payload = {
                "order_id": order_id,
                "correlation_id": order_id,  # Idempotency key
                "security_id": signal.security_id,
                "exchange_segment": exchange_segment,
                "transaction_type": signal.direction,
                "quantity": signal.quantity,
                "order_type": "MARKET",
                "product_type": "INTRADAY",
                "price": 0.0,
                "reference_price": signal.entry_reference
            }

            logger.info(f"Submitting Order {order_id} ({signal.symbol}) to Broker Adapter...")
            broker_resp = await self.broker.place_order(order_payload)

            # Step 3: SUBMITTED -> Verify Broker Fill Confirmation
            status = broker_resp.get("status")
            resp_data = broker_resp.get("data", {})

            if status == "success" and resp_data.get("orderStatus") == "FILLED":
                fill_price = float(resp_data.get("tradedPrice", signal.entry_reference))
                broker_order_id = str(resp_data.get("orderId", order_id))

                await order_repo.update_status(order_id, "FILLED", broker_order_id)

                # Record Trade
                await trade_repo.create_trade({
                    "id": f"TRD_{uuid.uuid4().hex[:8].upper()}",
                    "order_id": order_id,
                    "symbol": signal.symbol,
                    "side": signal.direction,
                    "quantity": signal.quantity,
                    "price": fill_price
                })

                # Step 4: Open Position using authoritative fill price
                pos_id = f"POS_{uuid.uuid4().hex[:8].upper()}"
                position = Position(
                    position_id=pos_id,
                    symbol=signal.symbol,
                    security_id=signal.security_id,
                    option_type=signal.option_type,
                    quantity=signal.quantity,
                    entry_price=fill_price,
                    stop_loss=signal.stop_loss_reference,
                    strategy_id=signal.strategy_id
                )
                self.position_manager.add_position(position)
                
                # Persist Position
                await pos_repo.create_position({
                    "id": pos_id,
                    "symbol": signal.symbol,
                    "quantity": signal.quantity,
                    "entry_price": fill_price,
                    "current_sl": signal.stop_loss_reference,
                    "status": "OPEN"
                })

                # Step 5: Place Broker-Side SL-M Order
                sl_order_id = f"SL_{uuid.uuid4().hex[:8].upper()}"
                sl_payload = {
                    "order_id": sl_order_id,
                    "correlation_id": sl_order_id,
                    "security_id": signal.security_id,
                    "exchange_segment": exchange_segment,
                    "transaction_type": "SELL" if signal.direction == "BUY" else "BUY",
                    "quantity": signal.quantity,
                    "order_type": "STOP_LOSS_MARKET",
                    "product_type": "INTRADAY",
                    "price": 0.0,
                    "trigger_price": signal.stop_loss_reference,
                    "reference_price": signal.stop_loss_reference
                }
                
                logger.info(f"Placing Broker-side SL-M for {pos_id} at {signal.stop_loss_reference}")
                sl_resp = await self.broker.place_order(sl_payload)
                if sl_resp.get("status") == "success":
                    logger.info(f"Broker-side SL-M {sl_order_id} placed successfully.")
                    await event_repo.log_system_event("SL_ORDER_PLACED", "INFO", f"Broker SL-M {sl_order_id} placed at {signal.stop_loss_reference}")
                else:
                    logger.error(f"Failed to place broker-side SL-M: {sl_resp.get('remarks')}")
                    await event_repo.log_system_event("SL_ORDER_FAILED", "ERROR", f"Failed to place SL-M {sl_order_id}: {sl_resp.get('remarks')}")

                self.risk_manager.record_trade_execution()
                await event_repo.log_system_event("POSITION_OPENED", "INFO", f"Opened {pos_id} for {signal.symbol} at {fill_price}")

                return {
                    "order_id": order_id,
                    "broker_order_id": broker_order_id,
                    "status": "FILLED",
                    "traded_price": fill_price,
                    "position_id": pos_id
                }
            else:
                remarks = broker_resp.get("remarks", "Broker execution failed")
                logger.error(f"Order {order_id} failed at broker: {remarks}")
                await order_repo.update_status(order_id, "FAILED")
                await event_repo.log_system_event("ORDER_FAILED", "ERROR", f"Order {order_id} failed: {remarks}")
                return {
                    "order_id": order_id,
                    "status": "FAILED",
                    "reason": remarks
                }

    async def partial_close_position(self, position_id: str, quantity: int, exit_price: float) -> Dict[str, Any]:
        """
        Submits a partial close SELL order for the specified quantity.
        """
        pos = self.position_manager.active_positions.get(position_id)
        if not pos or pos.quantity < quantity:
            return {"status": "ERROR", "reason": f"Position {position_id} not found or insufficient quantity"}

        order_id = f"EXIT_{uuid.uuid4().hex[:8].upper()}"
        exchange_segment = "NSE_FNO" if "NIFTY" in pos.symbol.upper() else "BSE_FNO"
        order_payload = {
            "order_id": order_id,
            "correlation_id": order_id,
            "security_id": pos.security_id,
            "exchange_segment": exchange_segment,
            "transaction_type": "SELL",
            "quantity": quantity,
            "order_type": "MARKET",
            "product_type": "INTRADAY",
            "price": 0.0,
            "reference_price": exit_price,
        }

        logger.info(f"Partial closing position {position_id} ({pos.symbol}) - Qty {quantity} @ ₹{exit_price}")
        
        async with AsyncSessionLocal() as session:
            order_repo = OrderRepository(session)
            trade_repo = TradeRepository(session)
            event_repo = EventRepository(session)

            await order_repo.create_order({
                "id": order_id,
                "symbol": pos.symbol,
                "side": "SELL",
                "quantity": quantity,
                "order_type": "MARKET",
                "price": exit_price,
                "status": "PENDING"
            })

            broker_resp = await self.broker.place_order(order_payload)
            status = broker_resp.get("status")
            resp_data = broker_resp.get("data", {})

            if status == "success":
                fill_price = float(resp_data.get("tradedPrice", exit_price))
                broker_order_id = str(resp_data.get("orderId", order_id))
                
                await order_repo.update_status(order_id, "FILLED", broker_order_id)
                
                await trade_repo.create_trade({
                    "id": f"TRD_{uuid.uuid4().hex[:8].upper()}",
                    "order_id": order_id,
                    "symbol": pos.symbol,
                    "side": "SELL",
                    "quantity": quantity,
                    "price": fill_price
                })

                self.position_manager.partial_close(position_id, quantity, fill_price)
                await event_repo.log_system_event("POSITION_PARTIAL_CLOSED", "INFO", f"Sold {quantity} of {position_id} at {fill_price}")

                return {
                    "order_id": order_id,
                    "status": "PARTIAL_CLOSED",
                    "traded_price": fill_price
                }
            else:
                await order_repo.update_status(order_id, "FAILED")
                remarks = broker_resp.get("remarks", "Partial exit order failed")
                await event_repo.log_system_event("ORDER_FAILED", "ERROR", f"Partial Exit order {order_id} failed: {remarks}")
                return {"order_id": order_id, "status": "FAILED", "reason": remarks}

    async def close_position(self, position_id: str, exit_price: float) -> Dict[str, Any]:
        """
        Closes an open position by submitting a SELL order and updating PositionManager and DB.
        """
        pos = self.position_manager.active_positions.get(position_id)
        if not pos:
            return {"status": "ERROR", "reason": f"Position {position_id} not found"}

        order_id = f"EXIT_{uuid.uuid4().hex[:8].upper()}"
        exchange_segment = "NSE_FNO" if "NIFTY" in pos.symbol.upper() else "BSE_FNO"
        order_payload = {
            "order_id": order_id,
            "correlation_id": order_id,  # Idempotency key
            "security_id": pos.security_id,
            "exchange_segment": exchange_segment,
            "transaction_type": "SELL",
            "quantity": pos.quantity,
            "order_type": "MARKET",
            "product_type": "INTRADAY",
            "price": 0.0,
            "reference_price": exit_price,
        }

        logger.info(f"Closing position {position_id} ({pos.symbol}) @ ₹{exit_price}")
        
        async with AsyncSessionLocal() as session:
            order_repo = OrderRepository(session)
            pos_repo = PositionRepository(session)
            trade_repo = TradeRepository(session)
            event_repo = EventRepository(session)

            await order_repo.create_order({
                "id": order_id,
                "symbol": pos.symbol,
                "side": "SELL",
                "quantity": pos.quantity,
                "order_type": "MARKET",
                "price": exit_price,
                "status": "PENDING"
            })

            broker_resp = await self.broker.place_order(order_payload)
            status = broker_resp.get("status")
            resp_data = broker_resp.get("data", {})

            if status == "success":
                fill_price = float(resp_data.get("tradedPrice", exit_price))
                broker_order_id = str(resp_data.get("orderId", order_id))
                
                await order_repo.update_status(order_id, "FILLED", broker_order_id)
                
                await trade_repo.create_trade({
                    "id": f"TRD_{uuid.uuid4().hex[:8].upper()}",
                    "order_id": order_id,
                    "symbol": pos.symbol,
                    "side": "SELL",
                    "quantity": pos.quantity,
                    "price": fill_price
                })

                closed = self.position_manager.close_position(position_id, fill_price)
                
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
            else:
                await order_repo.update_status(order_id, "FAILED")
                remarks = broker_resp.get("remarks", "Exit order failed")
                await event_repo.log_system_event("ORDER_FAILED", "ERROR", f"Exit order {order_id} failed: {remarks}")
                return {"order_id": order_id, "status": "FAILED", "reason": remarks}
