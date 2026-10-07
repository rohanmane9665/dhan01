import asyncio
import logging
import json
import time as _time
from datetime import datetime, timedelta, time as dtime
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
    return int(math.ceil(price / 100) * 100)

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
        self.validators = {}          # one validator per security_id
        self._last_put_tick = 0.0
        self._entering = False
        self._entry_task = None

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
                try:
                    parts = str(db_p.symbol).split("_")          # NIFTY_24500_PE
                    strike, opt = float(parts[1]), parts[2]
                    sec_id = self.instrument_manager.get_security_id(
                        base_symbol="NIFTY", strike=strike, option_type=opt,
                        expiry_date=self.calendar.get_expiry_str_yyyy_mm_dd())
                    if not sec_id:
                        raise ValueError(f"security_id not found for {db_p.symbol}")
                    pos = Position(
                        position_id=db_p.id, symbol=db_p.symbol, security_id=sec_id,
                        option_type=opt, quantity=db_p.quantity,
                        entry_price=db_p.entry_price, stop_loss=0.0)
                    pos.finalize_sl(0)       # safe 15-pt SL, marks SL as final
                    self.position_manager.add_position(pos)
                    if opt == "PE":
                        self._active_put_security_id = sec_id
                except Exception as e:
                    logger.error(f"Could not restore position {db_p.id}: {e}")
        
        await self.reconciliation.reconcile()
        await self.seed_candles()
        await self._publish_status("RUNNING")
        logger.info("✅ TradingWorker running.")

        # Start background logger
        asyncio.create_task(self._log_status_periodic())
        asyncio.create_task(self._position_watchdog())

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
                        b2_rsi = c_vals.get('b2_rsi')
                        b2_rsi_str = f"{b2_rsi:.2f}" if b2_rsi is not None else "N/A"
                        
                        live_rsi = c_vals.get('live_rsi')
                        live_rsi_str = f"{live_rsi:.2f}" if live_rsi is not None else "N/A"
                        
                        log_str = (
                            f"[C1 (B1 Cl>Op): {c_vals.get('C1')}] "
                            f"[C2 (B2 Cl<Op): {c_vals.get('C2')}] "
                            f"[C3 (B2 H>B1 H): {c_vals.get('C3')}] "
                            f"[C4 (B1 L<B2 L): {c_vals.get('C4')}] "
                            f"[C5 (Diff<25): {c_vals.get('C5')}] "
                            f"[C12 (B2 RSI {b2_rsi_str} > 40): {c_vals.get('C12')}] "
                            f"[LIVE RSI: {live_rsi_str}] "
                            f"| OVERALL MET: {pattern_formed}"
                        )
                        logger.info(log_str)
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
            
            # Fetch last 21 days (to safely ensure we get exactly 15 trading days like the text explains)
            # Dhan API strictly limits intraday_minute_data to 5 days per request.
            # We need 21 days, so we must chunk the requests into 5-day blocks.
            total_days = 21
            chunk_size = 5
            
            all_opens = []
            all_highs = []
            all_lows = []
            all_closes = []
            all_vols = []
            all_times = []
            
            # Fetch backwards from today
            current_to = datetime.now(IST)
            for _ in range(0, total_days, chunk_size):
                current_from = current_to - timedelta(days=chunk_size)
                
                # Fetch chunk (Try IDX first for NSE)
                res = await dhan.intraday_minute_data(
                    security_id="13",
                    exchange_segment="IDX",
                    instrument_type="INDEX",
                    from_date=current_from.strftime("%Y-%m-%d"),
                    to_date=current_to.strftime("%Y-%m-%d"),
                    interval=5
                )
                
                # Fallback to IDX_I if IDX fails
                if not res or res.get('status') != 'success' or not res.get('data'):
                    err1 = res.get('remarks', res) if res else "None"
                    logger.warning(f"Failed to fetch chunk with IDX (NSE): {err1}. Trying IDX_I...")
                    res = await dhan.intraday_minute_data(
                        security_id="13",
                        exchange_segment="IDX_I",
                        instrument_type="INDEX",
                        from_date=current_from.strftime("%Y-%m-%d"),
                        to_date=current_to.strftime("%Y-%m-%d"),
                        interval=5
                    )
                
                if res and res.get('status') == 'success' and res.get('data'):
                    data = res['data']
                    # API returns chronological order, but since we are fetching backwards, 
                    # we must prepend the new older chunk to the lists.
                    all_opens = data.get('open', []) + all_opens
                    all_highs = data.get('high', []) + all_highs
                    all_lows = data.get('low', []) + all_lows
                    all_closes = data.get('close', []) + all_closes
                    all_vols = data.get('volume', []) + all_vols
                    # Intraday minute API returns 'timestamp', not 'start_Time'
                    all_times = data.get('timestamp', []) + all_times
                else:
                    err2 = res.get('remarks', res) if res else "None"
                    logger.error(f"FATAL: Failed to fetch chunk with IDX_I (BSE) as well: {err2}")
                    
                current_to = current_from - timedelta(days=1)
                await asyncio.sleep(0.5) # Prevent rate limiting
                
            if not all_opens:
                logger.warning("Failed to fetch ANY historical data chunks for NIFTY. Check credentials/limits.")
                return
                
            # Create a mock result object to match the expected format
            result = {
                'status': 'success',
                'data': {
                    'open': all_opens,
                    'high': all_highs,
                    'low': all_lows,
                    'close': all_closes,
                    'volume': all_vols,
                    'timestamp': all_times
                }
            }
            candle_data = result.get("data", {})
            if isinstance(candle_data, dict) and "open" in candle_data:
                opens = candle_data.get("open", [])
                highs = candle_data.get("high", [])
                lows = candle_data.get("low", [])
                closes = candle_data.get("close", [])
                timestamps = candle_data.get("timestamp", [])

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

                # Keep up to 1500 candles to provide the massive history needed to perfectly warm up Wilder's Smoothing
                recent_candles = candles[-1500:]
                
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

            v = self.validators.get(security_id)
            if v is None:
                v = DataValidator(
                    stale_seconds=settings.MARKET_DATA_STALE_SECONDS if settings else 3,
                    max_spike_pct=0.10 if tick_type == "INDEX" else 0.5)
                self.validators[security_id] = v
            is_valid, reason = v.validate(ltp, ts)
            if not is_valid:
                return

            if tick_type == "INDEX" and security_id == "13":
                self._latest_index = ltp
                ts_ist = ts.astimezone(IST) if ts.tzinfo else IST.localize(ts)
                if dtime(9, 15) <= ts_ist.time() < dtime(15, 30):
                    completed = self.index_engine.process_tick(ltp, ts)
                    if completed:
                        self._candle_task = asyncio.create_task(self._safe_candle_close(completed))

                if self._waiting_for_breakout:
                    await self._check_breakout(ltp, ts)

            elif tick_type == "PE" and security_id == self._active_put_security_id:
                self._latest_put_ltp = ltp
                self._last_put_tick = _time.time()
                
            await self._monitor_positions(ltp, tick_type, security_id)
            await self._publish_snapshot()
        except Exception as e:
            logger.error(f"Error processing tick {tick}: {e}", exc_info=True)
    async def _safe_candle_close(self, candle: dict):
        try:
            await self._on_candle_close(candle)
        except Exception as e:
            logger.error(f"Error in candle close handler: {e}", exc_info=True)

    async def _refresh_official_candles(self, completed: dict) -> bool:
        """Replace today's tick-built candles with Dhan's official 5-min candles.
        Returns True only if the just-closed candle is present in the official data."""
        import pandas as pd
        from app.dhan.client import get_dhan_client
        dhan = get_dhan_client()
        target = pd.Timestamp(completed["timestamp"])
        for _ in range(5):                       # official candle can lag a few seconds
            try:
                now = datetime.now(IST)
                r = await dhan.intraday_minute_data(
                    security_id="13", exchange_segment="IDX_I", instrument_type="INDEX",
                    from_date=now.strftime("%Y-%m-%d") + " 09:15:00",
                    to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
                    interval=5)
                d = (r or {}).get("data") or {}
                if (r or {}).get("status") == "success" and d.get("open"):
                    ts = pd.to_datetime(d["timestamp"], unit="s", utc=True).tz_convert(IST)
                    cur = self.index_engine.current_candle
                    cur_ts = pd.Timestamp(cur["timestamp"]) if cur else None
                    official = []
                    for i, t in enumerate(ts):
                        if cur_ts is not None and t >= cur_ts:
                            continue             # skip the candle still forming
                        if not (dtime(9, 15) <= t.time() <= dtime(15, 29)):
                            continue
                        official.append({
                            "timestamp": t.to_pydatetime(),
                            "open": float(d["open"][i]), "high": float(d["high"][i]),
                            "low": float(d["low"][i]), "close": float(d["close"][i]),
                            "volume": 0,
                        })
                    if official and pd.Timestamp(official[-1]["timestamp"]) == target:
                        today = now.date()
                        older = [c for c in self.index_engine.candles
                                 if pd.Timestamp(c["timestamp"]).date() != today]
                        self.index_engine.candles = older + official
                        logger.info(f"✅ Official candles loaded: {len(official)} today, last = {target.strftime('%H:%M')}")
                        return True
            except Exception as e:
                logger.warning(f"Official candle fetch error: {e}")
            await asyncio.sleep(2)
        logger.warning(f"⚠️ Official {target.strftime('%H:%M')} candle not available - using tick-built candles")
        return False
        

    async def _on_candle_close(self, candle: dict):
        logger.info(f"📊 NIFTY 5m Candle closed: O={candle['open']} H={candle['high']} L={candle['low']} C={candle['close']}")
        if not self.calendar.is_market_open():
            return
            
        await self._refresh_official_candles(candle)    # use Dhan's official candles
        df = self.index_engine.get_dataframe()
        pattern_formed, b1_low, _ = self.strategy.evaluate(df)
        
        # If pattern formed, start 15 min countdown
        if pattern_formed and not self.position_manager.get_active_positions() \
                and not self._waiting_for_breakout and not self._entering:
            key = str(df.iloc[-2]['timestamp'])
            if key == getattr(self, "_last_traded_key", None):
                return
            self._pending_key = key
                    
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
            self._last_traded_key = getattr(self, "_pending_key", None)
            self._entering = True
            self._entry_task = asyncio.create_task(self._run_entry(ltp))

    async def _run_entry(self, index_price: float):
        try:
            await self._execute_put_breakout(index_price)
        except Exception as e:
            logger.error(f"Entry failed: {e}", exc_info=True)
        finally:
            self._entering = False

    async def _rest_option_ltp(self, security_id) -> float:
        try:
            from app.dhan.client import get_dhan_client
            r = await get_dhan_client().ticker_data({"NSE_FNO": [int(security_id)]})
            d = (r or {}).get("data", {})
            d = d.get("data", d)
            return float(d["NSE_FNO"][str(security_id)]["last_price"])
        except Exception as e:
            logger.warning(f"REST LTP failed: {e}")
            return 0.0

    async def _execute_put_breakout(self, index_price: float):
        t0 = datetime.now(IST)
        entry_bucket = t0.replace(minute=t0.minute // 5 * 5, second=0, microsecond=0)
        atm_strike = _nifty_atm_strike(index_price)
        logger.info(f"🔍 Looking for PUT option for ATM Strike {atm_strike}")
        
        expiry_date = self.calendar.get_expiry_str_yyyy_mm_dd()
        security_id = self.instrument_manager.get_security_id(
            base_symbol="NIFTY", strike=float(atm_strike),
            option_type="PE", expiry_date=expiry_date)
            
        if not security_id:
            logger.error(f"Could not resolve security ID for NIFTY {atm_strike} PE.")
            return
            
        self._active_put_security_id = security_id
        self._latest_put_ltp = 0.0      # clear the previous trade's price
        self._last_put_tick = 0.0
        
        mf = getattr(self, "market_feed", None)
        if mf:
            try:
                from dhanhq import MarketFeed as DhanMF
                mf.close_extra_feeds()
                mf.add_instruments([(DhanMF.NSE_FNO, str(security_id), DhanMF.Ticker)],
                                   {str(security_id): "PE"})
            except Exception as e:
                logger.error(f"Failed to subscribe to {security_id}: {e}")
                
        # wait up to 3s for the first option tick, else use REST
        for _ in range(30):
            if self._latest_put_ltp > 0:
                break
            await asyncio.sleep(0.1)
            
        ref_price = self._latest_put_ltp or await self._rest_option_ltp(security_id)
        calculated_sl = 0.05   # real SL is set by Position (interim 15pt, then candle low)
        
        signal = Signal(
            symbol=f"NIFTY_{atm_strike}_PE",
            direction="BUY",
            option_type="PE",
            entry_reference=float(ref_price or 0.0),
            stop_loss_reference=calculated_sl,
            signal_reason="NIFTY_5M_BREAKOUT",
            security_id=security_id
        )
        
        logger.info(f"🎯 Firing PUT Signal for {signal.symbol} (Sec ID: {security_id}), ref price {ref_price}")
        
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
        
        result = await self.execution_engine.process_signal(signal, quantity=130)
        if isinstance(result, dict) and result.get("status") == "FILLED":
            pos = self.position_manager.active_positions.get(result.get("position_id"))
            if pos:
                pos.entry_bucket = entry_bucket     # bucket at order time, not after the fill wait
        elif mf and not self.position_manager.get_active_positions():
            mf.close_extra_feeds()
            
    async def _try_finalize_sl(self, pos):
        import time as _t
        import pandas as pd
        now = datetime.now(IST)
        end = pos.entry_bucket + timedelta(minutes=5)
        if now < end + timedelta(seconds=5):
            return
        if _t.time() - getattr(pos, "_last_try", 0) < 2:
            return
        pos._last_try = _t.time()
        low = 0.0
        try:
            from app.dhan.client import get_dhan_client
            dhan = get_dhan_client()
            r = await dhan.intraday_minute_data(
                security_id=str(pos.security_id),
                exchange_segment="NSE_FNO",
                instrument_type="OPTIDX",
                from_date=pos.entry_bucket.strftime("%Y-%m-%d %H:%M:%S"),
                to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
                interval=5,
            )
            d = (r or {}).get("data") or {}
            if r.get("status") == "success" and d.get("low"):
                ts = pd.to_datetime(d["timestamp"], unit="s", utc=True).tz_convert(IST)
                for t, lo in zip(ts, d["low"]):
                    if t == pd.Timestamp(pos.entry_bucket):
                        low = float(lo)
        except Exception as e:
            logger.error(f"candle-low error: {e}")
        if low > 0 or now > end + timedelta(seconds=90):
            pos.finalize_sl(low)
            logger.info(f"✅ SL finalized {pos.stop_loss:.2f} | 1R {pos.risk_diff:.2f} | T1 {pos.target_1:.2f}")
            
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
                
            if pos.security_id == security_id and not pos.sl_finalized:
                await self._try_finalize_sl(pos)

            if pos.security_id == security_id:
                exit_reason = pos.update_price(ltp)
                
                if exit_reason:
                    if exit_reason == "PARTIAL_EXIT":
                        logger.info(f"Selling partial quantity (65) for position {pos.position_id}")
                        try:
                            res = await self.execution_engine.partial_close_position(pos.position_id, 65, ltp)
                            if not isinstance(res, dict) or res.get("status") != "PARTIAL_CLOSED":
                                raise RuntimeError(f"partial exit not filled: {res}")
                        except Exception as e:
                            logger.error(f"Failed to partial exit position {pos.position_id}: {e} - will retry")
                            pos.target_1_hit = False                  # undo the early flag
                            pos.stop_loss = pos.initial_stop_loss     # undo the early breakeven move
                            pos._t1_block_until = _time.time() + 2    # retry in 2s
                    else:
                        logger.info(f"Closing position {pos.position_id}: {exit_reason}")
                        try:
                            await self.execution_engine.close_position(pos.position_id, ltp)
                        except Exception as e:
                            logger.error(f"Failed to full exit position {pos.position_id}: {e}")
    async def _position_watchdog(self):
        """REST price fallback, reconnect on a silent feed, 45s no-price exit, option-feed cleanup."""
        last_ok = _time.time()
        last_resub = 0.0
        while self._running:
            await asyncio.sleep(1)
            try:
                positions = self.position_manager.get_active_positions()
                mf = getattr(self, "market_feed", None)
                if not positions:
                    last_ok = _time.time()
                    if mf and not self._entering and mf.has_extra_feeds():
                        mf.close_extra_feeds()      # free the option websocket after the trade
                    continue
                    
                pos = positions[0]
                sid = str(pos.security_id)
                age = _time.time() - self._last_put_tick if self._last_put_tick else 1e9
                if age < 5:
                    last_ok = _time.time()
                    continue
                    
                # websocket silent for 5s+ -> use REST price
                ltp = await self._rest_option_ltp(sid)
                if ltp > 0:
                    last_ok = _time.time()
                    await self._monitor_positions(ltp, "PE", sid)

                # silent for 15s+ -> reconnect the option feed
                if age > 15 and mf and _time.time() - last_resub > 10:
                    last_resub = _time.time()
                    from dhanhq import MarketFeed as DhanMF
                    mf.close_extra_feeds()
                    mf.add_instruments([(DhanMF.NSE_FNO, sid, DhanMF.Ticker)], {sid: "PE"})
                    logger.warning(f"🔄 Reconnecting option feed {sid}")
                    
                # no price from anywhere for 45s -> close at market
                if _time.time() - last_ok > 45 and self.position_manager.get_active_positions():
                    logger.error("🚨 No option price for 45s - closing at market")
                    await self.execution_engine.close_position(pos.position_id, pos.current_price)
                    last_ok = _time.time()
            except Exception as e:
                logger.error(f"Watchdog error: {e}")

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
