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
from app.market.validator import DataValidator
from app.strategies.models import Signal
from app.strategies.nifty_breakout import NiftyBreakoutStrategy
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

        self.strategy = NiftyBreakoutStrategy(rsi_period=14)
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
                        logger.info(f"(B2 High - B1 Low) < 22: C5 = {c_vals.get('C5')}")
                        b2_rsi = c_vals.get('b2_rsi')
                        b2_rsi_str = f"{b2_rsi:.2f}" if b2_rsi is not None else "N/A"
                        logger.info(f"B2 RSI(14) ({b2_rsi_str}) > 40: C12 = {c_vals.get('C12')}")
                        logger.info(f"All conditions (C1-C5, C12) met: {pattern_formed}")
                else:
                    logger.info("Index: Not enough data for C1-C5, C12 evaluation")
                
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
        """Seed the CandleEngine with recent historical data so RSI is warm at 9:15 AM."""
        try:
            from app.dhan.client import get_dhan_client
            dhan = get_dhan_client()
            dhan.initialize()

            logger.info("Seeding CandleEngine with historical NIFTY data for RSI...")
            
            # Fetch last 5 days to ensure we have enough previous-day candles for RSI
            to_date = datetime.now(IST)
            from_date = to_date - timedelta(days=5)
            
            from_date_str = from_date.strftime("%Y-%m-%d")
            to_date_str = to_date.strftime("%Y-%m-%d")

            # 13 is NIFTY 50 Index on Dhan
            # Using intraday_minute_data which accepts from/to date
            result = await dhan.intraday_minute_data(
                security_id="13",
                exchange_segment="IDX",  # NIFTY is on NSE (IDX), not BSE (IDX_I)
                instrument_type="INDEX",
                from_date=from_date_str,
                to_date=to_date_str,
                interval=5
            )
            
            if not isinstance(result, dict) or result.get('status') != 'success' or not result.get('data'):
                logger.warning(f"Failed to fetch historical data for NIFTY (Check credentials or ID): {result}")
                return
            
            candle_data = result.get("data", {})
            if isinstance(candle_data, dict) and "open" in candle_data:
                opens = candle_data.get("open", [])
                highs = candle_data.get("high", [])
                lows = candle_data.get("low", [])
                closes = candle_data.get("close", [])
                timestamps = candle_data.get("start_Time", [])

                count = 0
                candles = []
                for i in range(len(opens)):
                    # Handle integer timestamp or ISO string
                    ts = timestamps[i]
                    if isinstance(ts, (int, float)):
                        ts_obj = datetime.fromtimestamp(ts, IST)
                    elif isinstance(ts, str):
                        try:
                            ts_obj = datetime.fromisoformat(ts)
                        except ValueError:
                            ts_obj = datetime.now(IST)
                    else:
                        ts_obj = datetime.now(IST)

                    candles.append({
                        "timestamp": ts_obj,
                        "open": float(opens[i]),
                        "high": float(highs[i]),
                        "low": float(lows[i]),
                        "close": float(closes[i]),
                        "volume": 0,
                    })

                # Sort by time just in case
                candles.sort(key=lambda x: x["timestamp"])

                # Keep only the last 30 candles for the RSI warmup
                recent_candles = candles[-30:]
                
                for c in recent_candles:
                    self.index_engine.candles.append(c)
                    count += 1
                
                if self.index_engine.candles:
                    self.index_engine.current_candle = self.index_engine.candles.pop()
                    self._latest_index = float(self.index_engine.current_candle['close'])

                logger.info(f"🌱 Seeded {count} historical 5-min candles for NIFTY 50 RSI Calculation")
            else:
                logger.warning("No candle data available for NIFTY seeding.")
        except Exception as e:
            logger.error(f"Error seeding NIFTY candles: {e}", exc_info=True)

    async def on_tick(self, tick: dict):
        if not self._running:
            return
        if self.risk_manager.kill_switch_active:
            return

        try:
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
        except Exception as e:
            logger.error(f"Error processing tick {tick}: {e}", exc_info=True)

    async def _on_candle_close(self, candle: dict):
        logger.info(f"📊 NIFTY 5m Candle closed: O={candle['open']} H={candle['high']} L={candle['low']} C={candle['close']}")
        if not self.calendar.is_market_open():
            return
            
        df = self.index_engine.get_dataframe()
        pattern_formed, b1_low, _ = self.strategy.evaluate(df)
        
        # If pattern formed, start 15 min countdown
        if pattern_formed:
        # Check if trading is allowed (before 14:40 / 2:40 PM)
            now = datetime.now(IST)
            if now.time() >= __import__('datetime').time(14, 40):
                logger.info("🚫 Time check failed - No new trades after 2:40 PM")
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
            
            if now.time() >= __import__('datetime').time(14, 40):
                logger.info("🚫 Cannot take new position - Trading time expired (after 2:40 PM)")
                return
            
            # Only PUT trades per user request
            await self._execute_put_breakout(ltp)

    async def _execute_put_breakout(self, index_price: float):
        atm_strike = _nifty_atm_strike(index_price)
        logger.info(f"🔍 Looking for PUT option for ATM Strike {atm_strike}")
        
        # Get Option Chain Security ID
        expiry_date = self.calendar.get_expiry_str_yyyy_mm_dd()
        security_id = self.instrument_manager.get_security_id(
            base_symbol="NIFTY",
            strike=float(atm_strike),
            option_type="PE",
            expiry_date=expiry_date
        )
        if not security_id:
            logger.error(f"Could not resolve security ID for NIFTY {atm_strike} PE.")
            return
            
        self._active_put_security_id = security_id
        
        if hasattr(self, "market_feed") and self.market_feed:
            try:
                from dhanhq import MarketFeed as DhanMF
                instruments = getattr(self.market_feed, "_instruments", []).copy()
                type_map = getattr(self.market_feed, "_instrument_type_map", {}).copy()
                # NIFTY options use NSE_FNO
                instruments.append((DhanMF.NSE_FNO, str(security_id), DhanMF.Ticker))
                type_map[str(security_id)] = "PE"
                self.market_feed.update_instruments(instruments, type_map)
                logger.info(f"📡 Dynamically subscribed to PUT {security_id} on NSE_FNO")
            except Exception as e:
                logger.error(f"Failed to dynamically subscribe to {security_id}: {e}")
        
        # Fetch Option 5-min candle low for Stop Loss calculation
        option_5m_low = 0.0
        try:
            from app.dhan.client import get_dhan_client
            dhan = get_dhan_client()
            if dhan and dhan._client:
                today_str = datetime.now(IST).strftime("%Y-%m-%d")
                opt_data = await dhan.intraday_minute_data(
                    security_id=str(security_id),
                    exchange_segment="NSE_FNO",
                    instrument_type="OPTIDX",
                    from_date=today_str,
                    to_date=today_str,
                    interval=5
                )
                if isinstance(opt_data, dict) and opt_data.get('status') == 'success':
                    d = opt_data.get('data', {})
                    lows = d.get('low', [])
                    if lows:
                        option_5m_low = float(lows[-1])
                        logger.info(f"📉 Option 5M Candle Low: {option_5m_low}")
        except Exception as e:
            logger.error(f"Failed to fetch option candle low: {e}")

        # Fallback SL if API fails
        if option_5m_low == 0.0 and self._latest_put_ltp > 0:
            option_5m_low = self._latest_put_ltp

        calculated_sl = max(0.05, option_5m_low - 3.0)

        # Generate Signal to execute buy
        signal = Signal(
            symbol=f"NIFTY_{atm_strike}_PE",
            direction="BUY",
            option_type="PE",
            entry_reference=0.0, # Market price
            stop_loss_reference=calculated_sl,
            signal_reason="NIFTY_5M_BREAKOUT",
            security_id=security_id
        )
        
        logger.info(f"🎯 Firing PUT Signal for {signal.symbol} (Sec ID: {security_id}), Initial SL: {calculated_sl}")
        
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
            # Fixed quantity across all days
            quantity = 130
            await self.execution_engine.process_signal(signal, quantity=quantity)
        except Exception as e:
            logger.error(f"Error processing breakout signal: {e}")

    async def _monitor_positions(self, ltp: float, tick_type: str, security_id: str):
        now = datetime.now(IST)
        is_force_close_time = now.time() >= __import__('datetime').time(15, 10)

        for pos in self.position_manager.get_active_positions():
            if is_force_close_time:
                logger.warning(f"⏰ Force closing position {pos.position_id} due to End of Day (15:10)")
                try:
                    await self.execution_engine.close_position(pos.position_id, self._latest_put_ltp)
                except Exception as e:
                    logger.error(f"Failed to force exit position {pos.position_id}: {e}")
                continue

            if pos.security_id == security_id:
                exit_reason = pos.update_price(ltp)
                if exit_reason:
                    if exit_reason == "PARTIAL_EXIT":
                        logger.info(f"Selling partial quantity (65) for position {pos.position_id}")
                        try:
                            # Hardcoded partial exit quantity of 65 as requested
                            await self.execution_engine.partial_close_position(pos.position_id, 65, ltp)
                        except Exception as e:
                            logger.error(f"Failed to partial exit position {pos.position_id}: {e}")
                    else:
                        logger.info(f"Closing position {pos.position_id}: {exit_reason}")
                        try:
                            await self.execution_engine.close_position(pos.position_id, ltp)
                        except Exception as e:
                            logger.error(f"Failed to full exit position {pos.position_id}: {e}")

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
            await redis_client.set("worker_status", status, ex=60)
        except Exception as e:
            logger.error(f"Failed to publish status: {e}")

_worker_instance = None

def set_worker(worker: TradingWorker):
    global _worker_instance
    _worker_instance = worker

def get_worker() -> TradingWorker:
    return _worker_instance
