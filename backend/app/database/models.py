from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Text, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.database import Base

class MarketSession(Base):
    __tablename__ = "market_sessions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    trading_date = Column(String, index=True, unique=True) # YYYY-MM-DD
    status = Column(String) # OPEN, CLOSED
    opened_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)

class SignalModel(Base):
    __tablename__ = "signals"
    id = Column(String, primary_key=True, index=True) # E.g., SIGNAL_UUID
    strategy_id = Column(String, index=True)
    symbol = Column(String, index=True)
    option_type = Column(String)
    strike = Column(Float)
    side = Column(String)
    quantity = Column(Integer)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    target_price = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True))
    reason = Column(String)
    status = Column(String, default="NEW") # NEW, APPROVED, REJECTED, EXECUTED
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    orders = relationship("Order", back_populates="signal")

class Order(Base):
    __tablename__ = "orders"
    id = Column(String, primary_key=True, index=True) # E.g. ORD_UUID
    signal_id = Column(String, ForeignKey("signals.id"), nullable=True)
    broker_order_id = Column(String, index=True, nullable=True)
    symbol = Column(String, index=True)
    side = Column(String)
    quantity = Column(Integer)
    order_type = Column(String)
    price = Column(Float, nullable=True)
    status = Column(String) # PENDING, FILLED, REJECTED, CANCELLED
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    signal = relationship("SignalModel", back_populates="orders")
    trades = relationship("Trade", back_populates="order")

class Trade(Base):
    __tablename__ = "trades"
    id = Column(String, primary_key=True, index=True) # TRD_UUID
    order_id = Column(String, ForeignKey("orders.id"), nullable=True)
    symbol = Column(String, index=True)
    side = Column(String)
    quantity = Column(Integer)
    price = Column(Float)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    order = relationship("Order", back_populates="trades")

class Position(Base):
    __tablename__ = "positions"
    id = Column(String, primary_key=True, index=True) # E.g. POS_UUID
    symbol = Column(String, index=True)
    quantity = Column(Integer)
    entry_price = Column(Float)
    current_sl = Column(Float)
    status = Column(String) # OPEN, CLOSED
    pnl = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)

class SystemEvent(Base):
    __tablename__ = "system_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, index=True)
    severity = Column(String, index=True)
    message = Column(String)
    details = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

class RiskEvent(Base):
    __tablename__ = "risk_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, index=True)
    message = Column(String)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user = Column(String, index=True)
    action = Column(String)
    details = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

class AgentInsight(Base):
    __tablename__ = "agent_insights"
    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_type = Column(String, index=True) # TRADING_ANALYST, RELIABILITY_AGENT, POST_MARKET
    insight_data = Column(JSON)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())


