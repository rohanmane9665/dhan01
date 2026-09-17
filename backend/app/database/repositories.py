from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime
from app.database.models import (
    SignalModel, Order, Trade, Position, MarketSession, 
    SystemEvent, RiskEvent, AuditLog, AgentInsight
)

class BaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

class SignalRepository(BaseRepository):
    async def create_signal(self, signal_data: Dict[str, Any]) -> SignalModel:
        signal = SignalModel(**signal_data)
        self.session.add(signal)
        await self.session.commit()
        await self.session.refresh(signal)
        return signal

    async def get_by_id(self, signal_id: str) -> Optional[SignalModel]:
        stmt = select(SignalModel).where(SignalModel.id == signal_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(self, signal_id: str, status: str) -> Optional[SignalModel]:
        stmt = update(SignalModel).where(SignalModel.id == signal_id).values(status=status).returning(SignalModel)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.scalar_one_or_none()

class OrderRepository(BaseRepository):
    async def create_order(self, order_data: Dict[str, Any]) -> Order:
        order = Order(**order_data)
        self.session.add(order)
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def update_status(self, order_id: str, status: str, broker_order_id: Optional[str] = None) -> Optional[Order]:
        values = {"status": status}
        if broker_order_id:
            values["broker_order_id"] = broker_order_id
            
        stmt = update(Order).where(Order.id == order_id).values(**values).returning(Order)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.scalar_one_or_none()
        
    async def get_by_id(self, order_id: str) -> Optional[Order]:
        stmt = select(Order).where(Order.id == order_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

class TradeRepository(BaseRepository):
    async def create_trade(self, trade_data: Dict[str, Any]) -> Trade:
        trade = Trade(**trade_data)
        self.session.add(trade)
        await self.session.commit()
        await self.session.refresh(trade)
        return trade

class PositionRepository(BaseRepository):
    async def create_position(self, position_data: Dict[str, Any]) -> Position:
        position = Position(**position_data)
        self.session.add(position)
        await self.session.commit()
        await self.session.refresh(position)
        return position

    async def update_position(self, position_id: str, values: Dict[str, Any]) -> Optional[Position]:
        stmt = update(Position).where(Position.id == position_id).values(**values).returning(Position)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.scalar_one_or_none()

    async def get_open_positions(self) -> List[Position]:
        stmt = select(Position).where(Position.status == "OPEN")
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

class EventRepository(BaseRepository):
    async def get_latest_event_by_type(self, event_type: str) -> Optional[SystemEvent]:
        stmt = select(SystemEvent).where(SystemEvent.event_type == event_type).order_by(SystemEvent.timestamp.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def log_system_event(self, event_type: str, severity: str, message: str, details: Optional[Dict] = None) -> SystemEvent:
        event = SystemEvent(event_type=event_type, severity=severity, message=message, details=details)
        self.session.add(event)
        await self.session.commit()
        return event

    async def log_risk_event(self, event_type: str, message: str) -> RiskEvent:
        event = RiskEvent(event_type=event_type, message=message)
        self.session.add(event)
        await self.session.commit()
        return event

    async def log_audit(self, user: str, action: str, details: Optional[Dict] = None) -> AuditLog:
        audit = AuditLog(user=user, action=action, details=details)
        self.session.add(audit)
        await self.session.commit()
        return audit

class AgentInsightRepository(BaseRepository):
    async def save_insight(self, agent_type: str, insight_data: Dict[str, Any]) -> AgentInsight:
        insight = AgentInsight(agent_type=agent_type, insight_data=insight_data)
        self.session.add(insight)
        await self.session.commit()
        return insight
