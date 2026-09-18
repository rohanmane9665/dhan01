import asyncio
import logging
import json
from datetime import datetime, timedelta
import pytz
import math

from app.core.config import settings
from app.core.redis import redis_client
from app.execution.engine import ExecutionEngine
from app.risk.manager import RiskManager
from app.execution.position_manager import PositionManager
from app.market.candle_engine import CandleEngine
from app.worker.data_validator import DataValidator
from app.strategies.index_breakout import IndexBreakoutStrategy
from app.strategies.models import Signal, SignalType
from app.reconciliation.service import ReconciliationService
from app.scheduler.market_calendar import MarketCalendar
from app.market.instruments import InstrumentManager

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

def _nifty_atm_strike(price: float) -> int:
    return round(price / 50) * 50

class TradingWorker:
    """
    Single-process, fully async trading worker.
    Instantiated once at startup. Never replicated.
    """

    def __init__(self, broker_adapter):
        self.broker = broker_adapter
        self.risk_manager = RiskManager()
        self.position_manager = PositionManager()
        self.execution_engine = ExecutionEngine(
            broker_adapter=self.broker,
            risk_manager=self.risk_manager,
            position_manager=self.position_manager,
        )
        self.validator = DataValidator(stale_seconds=settings.MARKET_DATA_STALE_SECONDS if settings else 3)

        # Single candle engine for NIFTY 50 (5 minutes)
        self.index_engine = CandleEngine(interval_minutes=5)

        self.strategy = IndexBreakoutStrategy(rsi_period=14)
        self.reconciliation = ReconciliationService(self.broker, self.position_manager)
        self.calendar = MarketCalendar()
        self.instrument_manager = InstrumentManager()

        # Latest market snapshot
        self._latest_index: float = 0.0
        self._latest_put_ltp: float = 0.0
        
        # Breakout state
        self._waiting_for_breakout = False
        self._breakout_b1_low = 0.0
        self._breakout_deadline = None
        
        # Track the active dynamic PUT security ID if any
        self._active_put_security_id = None
        
        self._running = False

    async def start(self):
        """Connect broker and begin the main tick processing loop."""
        logger.info("🚀 TradingWorker starting...")
        self._running = True

        connected = await self.broker.connect()
        if not connected:
            logger.warning("Broker adapter failed to connect. Worker will retry ticks without live execution.")

        from app.database.database import AsyncSessionLocal
        from app.database.repositories import PositionRepository
        from app.execution.position_manager import Position

        # Load latest instruments
        await self.instrument_manager.load_master()

        async with AsyncSessionLocal() as session:
            pos_repo = PositionRepository(session)
            db_positions = await pos_repo.get_open_positions()
            for db_p in db_positions:
                pos = Position(
                    position_id=db_p.id,
                    symbol=db_p.symbol,
                    security_id=db_p.security_id,
                    option_type=db_p.option_type,
                    quantity=db_p.quantity,
                    entry_price=db_p.entry_price,
                    stop_loss=db_p.stop_loss,
                    strategy_id=db_p.strategy_id
                )
                self.position_manager.add_position(pos)
                if pos.option_type == "PE":
                    self._active_put_security_id = pos.security_id
        
        await self.reconciliation.reconcile()
        await self.seed_candles()
        await self._publish_status("RUNNING")
        logger.info("✅ TradingWorker running.")

        # Start background logger
        asyncio.create_task(self._log_status_periodic())

    async def _log_status_periodic(self):
        """Periodically logs status and active strategy states."""
        while self._running:
            try:
                logger.info(f"{'='*60}")
                logger.info(f"CONDITION STATUS - {datetime.now(IST).strftime('%H:%M:%S')}")
                logger.info(f"{'='*60}")
                
                df = self.index_engine.get_dataframe()
                if not df.empty and len(df) >= 3:
                    pattern_formed, b1_low, c_vals = self.strategy.evaluate(df)
                    if c_vals:
                        logger.info(f"B1 Close > B1 Open: C1 = {c_vals.get('C1')}")
                        logger.info(f"B2 Close < B2 Open: C2 = {c_vals.get('C2')}")
                        logger.info(f"B2 High > B1 High: C3 = {c_vals.get('C3')}")
                        logger.info(f"B1 Low < B2 Low: C4 = {c_vals.get('C4')}")
                        logger.info(f"8 < (B2 High - B1 High) < 80: C6 = {c_vals.get('C6')}")
                        logger.info(f"8 < (B2 Low - B1 Low) < 80: C7 = {c_vals.get('C7')}")
                        logger.info(f"5 < (B1 Close - B1 Open) < 100: C8 = {c_vals.get('C8')}")
                        logger.info(f"4 < (B2 Open - B2 Close) < 100: C9 = {c_vals.get('C9')}")
                        logger.info(f"(B1 High - B1 Low) < 150: C10 = {c_vals.get('C10')}")
                        logger.info(f"(B2 High - B2 Low) < 150: C11 = {c_vals.get('C11')}")
                        b2_rsi = c_vals.get('b2_rsi')
                        logger.info(f"B2 RSI(14) ({b2_rsi:.2f} if b2_rsi else 'N/A') > 40: C12 = {c_vals.get('C12')}")
                        logger.info(f"All conditions (C1-C4, C6-C12) met: {pattern_formed}")
                else:
                    logger.info("Index: Not enough data for C1-C12 evaluation")
                
                logger.info(f"NIFTY: {self._latest_index}, PUT Price: {self._latest_put_ltp}")
                
                if self._waiting_for_breakout:
                    time_left = (self._breakout_deadline - datetime.now(IST)).total_seconds()
                    logger.info(f"Breakout Monitor - Current: {self._latest_index}, Target: {self._breakout_b1_low}, Time left: {time_left:.0f}s")
                
                logger.info(f"{'='*60}\n")
            except Exception as e:
                logger.error(f"Error in periodic status logger: {e}")
            await asyncio.sleep(30)

    async def stop(self):
        self._running = False
        await self.broker.disconnect()
        await self._publish_status("STOPPED")
        logger.info("TradingWorker stopped.")

    async def seed_candles(self):
        """
        Fetches today's intraday candle data from Dhan REST API and pre-fills the Index CandleEngine.
        """
        from app.dhan.client import get_dhan_client
        dhan_client = get_dhan_client()
        dhan_client.initialize()

        today = datetime.now(IST).strftime("%Y-%m-%d")

        try:
            # 13 is NIFTY 50 Index on Dhan
            result = await dhan_client.intraday_minute_data(
                security_id="13",
                exchange_segment="IDX_I",
                instrument_type="INDEX",
                from_date=today,
                to_date=today,
                interval=5,
            )
            
            if not isinstance(result, dict):
                logger.warning(f"Unexpected intraday data format for NIFTY: {type(result)}")
                return
            
            candle_data = result.get("data", result)
            if isinstance(candle_data, dict) and "open" in candle_data:
                opens = candle_data.get("open", [])
                highs = candle_data.get("high", [])
                lows = candle_data.get("low", [])
                closes = candle_data.get("close", [])
                timestamps = candle_data.get("start_Time", candle_data.get("timestamp", []))

                count = 0
                for i in range(len(opens)):
                    candle = {
                        "timestamp": datetime.fromisoformat(timestamps[i]) if isinstance(timestamps[i], str) else timestamps[i],
                        "open": float(opens[i]),
                        "high": float(highs[i]),
                        "low": float(lows[i]),
                        "close": float(closes[i]),
                        "volume": 0,
                    }
                    self.index_engine.candles.append(candle)
                    count += 1
                
                if self.index_engine.candles:
                    self.index_engine.current_candle = self.index_engine.candles.pop()
                    self._latest_index = float(closes[-1]) if closes else 0.0

                logger.info(f"🌱 Seeded {count} historical 5-min candles for NIFTY 50")
            else:
                logger.warning("No candle data available for NIFTY seeding.")
        except Exception as e:
            logger.error(f"Error seeding NIFTY candles: {e}")

    async def on_tick(self, tick: dict):
        if not self._running:
            return
        if self.risk_manager.kill_switch_active:
            return

        ts = tick.get("timestamp") or datetime.now(IST)
        ltp = float(tick.get("ltp", 0.0))
        tick_type = tick.get("type", "INDEX")
        security_id = str(tick.get("security_id", ""))

        is_valid, reason = self.validator.validate(ltp, ts)
        if not is_valid:
            return

        if tick_type == "INDEX" and security_id == "13":
            self._latest_index = ltp
            completed = self.index_engine.process_tick(ltp, ts)
            if completed:
                await self._on_candle_close(completed)
            
            # Check Breakout Monitor
            if self._waiting_for_breakout:
                await self._check_breakout(ltp, ts)

        elif tick_type == "PE" and security_id == self._active_put_security_id:
            self._latest_put_ltp = ltp

        await self._monitor_positions(ltp, tick_type, security_id)
        await self._publish_snapshot()

    async def _on_candle_close(self, candle: dict):
        logger.info(f"📊 NIFTY 5m Candle closed: O={candle['open']} H={candle['high']} L={candle['low']} C={candle['close']}")
        if not self.calendar.is_market_open():
            return
            
        df = self.index_engine.get_dataframe()
        pattern_formed, b1_low, _ = self.strategy.evaluate(df)
        
        # If pattern formed, start 15 min countdown
        if pattern_formed:
            # Check if trading is allowed (before 15:10)
            now = datetime.now(IST)
            if now.hour == 15 and now.minute >= 10:
                logger.info("🚫 Time check failed - No new trades after 3:10 PM")
                return
                
            self._waiting_for_breakout = True
            self._breakout_b1_low = b1_low
            self._breakout_deadline = now + timedelta(minutes=15)
            logger.info(f"Pattern formed! Monitoring for breakout below {b1_low} for 15 minutes...")

    async def _check_breakout(self, ltp: float, ts: datetime):
        now = datetime.now(IST)
        
        # Check deadline
        if now > self._breakout_deadline:
            logger.info("⏰ 15-minute breakout window expired. Resetting...")
            self._waiting_for_breakout = False
            return
            
        # Check breakdown
        if ltp < self._breakout_b1_low:
            logger.info(f"🚀 BREAKOUT CONFIRMED! NIFTY {ltp} broke below {self._breakout_b1_low}")
            self._waiting_for_breakout = False
            
            if now.hour == 15 and now.minute >= 10:
                logger.info("🚫 Cannot take new position - Trading time expired")
                return
            
            # Only PUT trades per user request
            await self._execute_put_breakout(ltp)

    async def _execute_put_breakout(self, index_price: float):
        atm_strike = _nifty_atm_strike(index_price)
        logger.info(f"🔍 Looking for PUT option for ATM Strike {atm_strike}")
        
        security_id = self.instrument_manager.get_security_id("NIFTY", float(atm_strike), "PE")
        if not security_id:
            logger.error(f"Could not resolve security ID for NIFTY {atm_strike} PE.")
            return
            
        self._active_put_security_id = security_id
        
        # Generate Signal to execute buy
        signal = Signal(
            symbol=f"NIFTY_{atm_strike}_PE",
            direction=SignalType.BUY,
            option_type="PE",
            entry_reference=0.0, # Market price
            stop_loss_reference=0.0, # Handled by SL tiers
            signal_reason="NIFTY_5M_BREAKOUT",
            metadata={"security_id": security_id}
        )
        
        logger.info(f"🎯 Firing PUT Signal for {signal.symbol} (Sec ID: {security_id})")
        
        await redis_client.set(
            "last_signal",
            json.dumps({
                "symbol": signal.symbol,
                "direction": signal.direction,
                "option_type": signal.option_type,
                "reason": signal.signal_reason,
                "timestamp": signal.timestamp.isoformat(),
            }),
            ex=3600,
        )

        try:
            # Wait a sec for frontend to catch up before order
            await asyncio.sleep(1)
            # Standard params for Friday (Default 60 qty)
            quantity = 60
            await self.execution_engine.process_signal(signal, quantity=quantity)
        except Exception as e:
            logger.error(f"Error processing breakout signal: {e}")

    async def _monitor_positions(self, ltp: float, tick_type: str, security_id: str):
        for pos in self.position_manager.get_active_positions():
            if pos.security_id == security_id:
                exit_reason = pos.update_price(ltp)
                if exit_reason:
                    logger.info(f"Closing position {pos.position_id}: {exit_reason}")
                    try:
                        await self.execution_engine.execute_exit(pos, exit_reason)
                    except Exception as e:
                        logger.error(f"Failed to exit position {pos.position_id}: {e}")

    async def _publish_snapshot(self):
        try:
            snapshot = {
                "sensex": self._latest_index, # Keep named sensex for frontend compatibility if needed
                "nifty": self._latest_index,
                "ce_ltp": 0.0,
                "pe_ltp": self._latest_put_ltp,
                "timestamp": datetime.now(IST).isoformat(),
            }
            await redis_client.set("market_snapshot", json.dumps(snapshot), ex=60)
        except Exception as e:
            logger.error(f"Redis snapshot error: {e}")

    async def _publish_status(self, status: str):
        try:
            await redis_client.set("worker_status", status)
        except:
            pass
