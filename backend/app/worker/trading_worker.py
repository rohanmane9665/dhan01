"""
trading_worker.py
=================
The single deterministic trading process.

Flow:
    MarketFeed (tick)
        → DataValidator
        → CandleEngine (15-min OHLCV)
            → OptionRSIStrategy (signal?)
                → RiskManager (approved?)
                    → ExecutionEngine (order → broker)

This worker also:
  - monitors open positions for SL / trailing-SL hits
  - publishes state updates to Redis (for SSE streaming to frontend)
  - runs reconciliation on startup and after each fill

AI agents may READ from Redis. They NEVER write to this worker's state.
"""

import asyncio
import json
import logging
import math
import pytz
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

from app.core.config import settings
from app.core.redis import redis_client
from app.market.validator import DataValidator
from app.market.candle_engine import CandleEngine
from app.strategies.option_rsi import OptionRSIStrategy
from app.risk.manager import RiskManager
from app.execution.engine import ExecutionEngine
from app.execution.position_manager import PositionManager
from app.reconciliation.service import ReconciliationService
from app.scheduler.market_calendar import MarketCalendar
from app.market.instruments import InstrumentManager

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


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

        # Separate candle engines for CE and PE legs
        self.ce_engine = CandleEngine(interval_minutes=15)
        self.pe_engine = CandleEngine(interval_minutes=15)

        self.strategy = OptionRSIStrategy(rsi_period=14, signal_gap_minutes=15)
        self.reconciliation = ReconciliationService(self.broker, self.position_manager)
        self.calendar = MarketCalendar()
        self.instrument_manager = InstrumentManager()

        # Latest market snapshot (published to Redis for frontend)
        self._latest_sensex: float = 0.0
        self._latest_ce_ltp: float = 0.0
        self._latest_pe_ltp: float = 0.0
        self._running = False

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #
    async def start(self):
        """Connect broker and begin the main tick processing loop."""
        logger.info("🚀 TradingWorker starting...")
        self._running = True

        connected = await self.broker.connect()
        if not connected:
            logger.warning("Broker adapter failed to connect. Worker will retry ticks without live execution.")

        # Reconstruct RAM from PostgreSQL
        from app.database.database import AsyncSessionLocal
        from app.database.repositories import PositionRepository, EventRepository
        from app.execution.position_manager import Position

        # Load latest instruments
        await self.instrument_manager.load_master()

        async with AsyncSessionLocal() as session:
            pos_repo = PositionRepository(session)
            db_positions = await pos_repo.get_open_positions()
            for p in db_positions:
                # SENSEX_72000_CE dummy parsing if missing
                option_type = "CE" if "CE" in p.symbol else "PE"
                sec_id = p.symbol.replace(" ", "_") # Fallback mapping
                pos = Position(
                    position_id=p.id,
                    symbol=p.symbol,
                    security_id=sec_id,
                    option_type=option_type,
                    quantity=p.quantity,
                    entry_price=p.entry_price,
                    stop_loss=p.current_sl,
                    strategy_id="option_rsi_sensex"
                )
                self.position_manager.add_position(pos)
                logger.info(f"♻️ Restored position {p.id} ({p.symbol}) from PostgreSQL into memory.")
                
            # Restore Kill Switch state from last event
            event_repo = EventRepository(session)
            last_ks_trigger = await event_repo.get_latest_event_by_type("KILL_SWITCH_TRIGGERED")
            last_ks_reset = await event_repo.get_latest_event_by_type("KILL_SWITCH_RESET")
            
            trigger_time = last_ks_trigger.timestamp if last_ks_trigger else None
            reset_time = last_ks_reset.timestamp if last_ks_reset else None
            
            if trigger_time and (not reset_time or trigger_time > reset_time):
                self.risk_manager.kill_switch.trigger("Restored from database (was active before restart)")
                logger.critical("🚨 Restored KILL SWITCH ACTIVE state from database.")

        # Startup reconciliation
        ok, msg = await self.reconciliation.reconcile()
        if not ok:
            logger.critical(f"Startup reconciliation failed: {msg}. Entering safe mode.")
            await self._publish_status("SAFE_MODE")
            return

        await self._publish_status("RUNNING")
        logger.info("✅ TradingWorker running.")
        
        # Start the periodic status logger
        self._status_logger_task = asyncio.create_task(self._log_status_periodic())

    async def _log_status_periodic(self):
        """Periodically logs the real-time condition status exactly like the original strategy."""
        while self._running:
            try:
                logger.info(f"\n{'='*60}\nCONDITION STATUS - {datetime.now(IST).strftime('%H:%M:%S')}\n{'='*60}")
                
                # Check PE
                pe_df = self.pe_engine.get_dataframe()
                if not pe_df.empty and len(pe_df) >= 4:
                    closes = pe_df['close'].values
                    rsi = self.strategy.calculate_rsi(closes)
                    if rsi is not None and len(rsi) >= 4:
                        c1 = rsi[-4] < 59.99
                        c2 = rsi[-3] < 59.99
                        c3 = rsi[-2] > 59.99
                        
                        logger.info(f"PE RSI[-4] ({rsi[-4]:.2f}) < 59.99: C1 = {c1}")
                        logger.info(f"PE RSI[-3] ({rsi[-3]:.2f}) < 59.99: C2 = {c2}")
                        logger.info(f"PE RSI[-2] ({rsi[-2]:.2f}) > 59.99: C3 = {c3}")
                        logger.info(f"All conditions (C1-C3) met for PE: {c1 and c2 and c3}")
                else:
                    logger.info("PE: Not enough data")
                    
                # Check CE
                ce_df = self.ce_engine.get_dataframe()
                if not ce_df.empty and len(ce_df) >= 4:
                    closes = ce_df['close'].values
                    rsi = self.strategy.calculate_rsi(closes)
                    if rsi is not None and len(rsi) >= 4:
                        c1 = rsi[-4] < 59.99
                        c2 = rsi[-3] < 59.99
                        c3 = rsi[-2] > 59.99
                        
                        logger.info(f"CE RSI[-4] ({rsi[-4]:.2f}) < 59.99: C1 = {c1}")
                        logger.info(f"CE RSI[-3] ({rsi[-3]:.2f}) < 59.99: C2 = {c2}")
                        logger.info(f"CE RSI[-2] ({rsi[-2]:.2f}) > 59.99: C3 = {c3}")
                        logger.info(f"All conditions (C1-C3) met for CE: {c1 and c2 and c3}")
                else:
                    logger.info("CE: Not enough data")
                    
                logger.info(f"SENSEX: {self._latest_sensex}, PE Price: {self._latest_pe_ltp}, CE Price: {self._latest_ce_ltp}")
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
        Fetches today's intraday candle data from Dhan REST API and pre-fills the CandleEngines.
        This allows the RSI strategy to evaluate signals immediately on startup.
        """
        from app.dhan.client import get_dhan_client

        dhan_client = get_dhan_client()
        dhan_client.initialize()

        today = datetime.now(IST).strftime("%Y-%m-%d")

        # We need current SENSEX price to find ATM options
        sensex_price = self._latest_sensex
        if sensex_price <= 0:
            # Try to get from Dhan REST
            try:
                result = await dhan_client.ohlc_data({"IDX_I": [51]})
                if isinstance(result, dict):
                    data = result.get("data", {})
                    if isinstance(data, dict):
                        for segment_data in data.values():
                            if isinstance(segment_data, list):
                                for item in segment_data:
                                    if isinstance(item, dict):
                                        ltp = item.get("last_price") or item.get("LTP") or item.get("ltp", 0)
                                        if ltp:
                                            sensex_price = float(ltp)
                                            self._latest_sensex = sensex_price
                                            break
                            elif isinstance(segment_data, dict):
                                ltp = segment_data.get("last_price") or segment_data.get("LTP", 0)
                                if ltp:
                                    sensex_price = float(ltp)
                                    self._latest_sensex = sensex_price
            except Exception as e:
                logger.warning(f"Could not fetch SENSEX price for candle seeding: {e}")

        if sensex_price <= 0:
            logger.info("Skipping candle seeding — no SENSEX price available yet.")
            return

        atm_strike = _atm_strike(sensex_price)
        logger.info(f"🌱 Seeding candles for SENSEX ATM {atm_strike} (SENSEX @ {sensex_price})")

        for option_type in ["CE", "PE"]:
            sec_id = self.instrument_manager.get_security_id("SENSEX", float(atm_strike), option_type)
            if not sec_id:
                logger.warning(f"Cannot seed {option_type} candles — security ID not resolved for strike {atm_strike}")
                continue

            try:
                result = await dhan_client.intraday_minute_data(
                    security_id=str(sec_id),
                    exchange_segment="BSE_FNO",
                    instrument_type="OPTIDX",
                    from_date=today,
                    to_date=today,
                    interval=15,
                )

                if not isinstance(result, dict):
                    logger.warning(f"Unexpected intraday data format for {option_type}: {type(result)}")
                    continue

                # Dhan returns data with 'open', 'high', 'low', 'close', 'volume', 'start_Time' lists
                candle_data = result.get("data", result)
                if isinstance(candle_data, dict) and "open" in candle_data:
                    opens = candle_data.get("open", [])
                    highs = candle_data.get("high", [])
                    lows = candle_data.get("low", [])
                    closes = candle_data.get("close", [])
                    timestamps = candle_data.get("start_Time", candle_data.get("timestamp", []))

                    engine = self.ce_engine if option_type == "CE" else self.pe_engine
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
                        engine.candles.append(candle)
                        count += 1

                    # Set current candle to the last one
                    if engine.candles:
                        engine.current_candle = engine.candles.pop()

                    logger.info(f"🌱 Seeded {count} historical 15-min candles for {option_type} (sec_id={sec_id})")

                    # Update latest option LTP from the last candle
                    if closes:
                        last_close = float(closes[-1])
                        if option_type == "CE":
                            self._latest_ce_ltp = last_close
                        else:
                            self._latest_pe_ltp = last_close

                else:
                    logger.warning(f"No candle data available for {option_type} seeding: {list(candle_data.keys()) if isinstance(candle_data, dict) else 'non-dict'}")

            except Exception as e:
                logger.error(f"Error seeding {option_type} candles: {e}")

    # ------------------------------------------------------------------ #
    # Tick ingestion (called externally by MarketFeed bridge)             #
    # ------------------------------------------------------------------ #
    async def on_tick(self, tick: dict):
        """
        Called for each incoming market tick.
        tick = {"security_id": str, "ltp": float, "timestamp": datetime, "type": "CE"|"PE"|"INDEX"}
        """
        if not self._running:
            return

        if self.risk_manager.kill_switch_active:
            return

        # Validate tick freshness
        ts = tick.get("timestamp") or datetime.now(IST)
        ltp = float(tick.get("ltp", 0.0))
        tick_type = tick.get("type", "INDEX")

        is_valid, reason = self.validator.validate(ltp, ts)
        if not is_valid:
            logger.debug(f"Tick rejected [{tick_type}]: {reason}")
            return

        # Route tick to correct engine
        if tick_type == "INDEX":
            self._latest_sensex = ltp
        elif tick_type == "CE":
            self._latest_ce_ltp = ltp
            completed = self.ce_engine.process_tick(ltp, ts)
            if completed:
                await self._on_candle_close("CE", completed)
        elif tick_type == "PE":
            self._latest_pe_ltp = ltp
            completed = self.pe_engine.process_tick(ltp, ts)
            if completed:
                await self._on_candle_close("PE", completed)

        # Update open positions for SL monitoring
        await self._monitor_positions(ltp, tick_type)

        # Publish live snapshot to Redis for SSE
        await self._publish_snapshot()

    # ------------------------------------------------------------------ #
    # Candle close handler                                                 #
    # ------------------------------------------------------------------ #
    async def _on_candle_close(self, option_type: str, candle: dict):
        logger.info(f"📊 Candle closed [{option_type}]: O={candle['open']} H={candle['high']} L={candle['low']} C={candle['close']}")

        if not self.calendar.is_market_open():
            return

        engine = self.ce_engine if option_type == "CE" else self.pe_engine
        df = engine.get_dataframe()

        atm_strike = _atm_strike(self._latest_sensex)
        option_ltp = self._latest_ce_ltp if option_type == "CE" else self._latest_pe_ltp
        
        # Resolve real security ID
        security_id = self.instrument_manager.get_security_id("SENSEX", float(atm_strike), option_type)
        if not security_id:
            logger.error(f"Could not resolve security ID for SENSEX {atm_strike} {option_type}. Defaulting to dummy ID.")
            security_id = f"SENSEX_{atm_strike}_{option_type}"

        market_data = {
            "sensex_price": self._latest_sensex,
            "option_type": option_type,
            "option_df": df,
            "option_ltp": option_ltp,
            "security_id": security_id,
            "quantity": 1,
        }

        signal = self.strategy.evaluate(market_data)
        if signal is None:
            return

        logger.info(f"🎯 Signal generated: {signal.symbol} | {signal.signal_reason}")

        # Save signal to Redis for frontend display
        await redis_client.set(
            "last_signal",
            json.dumps({
                "symbol": signal.symbol,
                "direction": signal.direction,
                "option_type": signal.option_type,
                "entry": signal.entry_reference,
                "sl": signal.stop_loss_reference,
                "reason": signal.signal_reason,
                "timestamp": signal.timestamp.isoformat(),
            }),
            ex=3600,
        )

        result = await self.execution_engine.execute_signal(signal)
        logger.info(f"Order result: {result}")

        # Reconcile after each fill
        await self.reconciliation.reconcile()

    # ------------------------------------------------------------------ #
    # Position monitoring (SL / trailing SL)                              #
    # ------------------------------------------------------------------ #
    async def _monitor_positions(self, ltp: float, tick_type: str):
        for pos in self.position_manager.get_active_positions():
            # Only update CE positions on CE ticks, PE on PE ticks
            if pos.option_type.upper() != tick_type.upper():
                continue

            exit_reason = pos.update_price(ltp)
            if exit_reason:
                logger.warning(f"🛑 SL triggered for {pos.symbol}: {exit_reason}")
                result = await self.execution_engine.close_position(pos.position_id, ltp)
                self.risk_manager.record_position_closed(pos.realized_pnl)
                logger.info(f"Exit order result: {result}")
                await self._publish_snapshot()
                continue

            # Trailing SL: check after each candle (engine has latest df)
            engine = self.ce_engine if tick_type == "CE" else self.pe_engine
            df = engine.get_dataframe()
            if len(df) >= 3:
                iloc_2 = df.iloc[-2]
                iloc_3 = df.iloc[-3]
                iloc_2_range = float(iloc_2["high"]) - float(iloc_2["low"])
                iloc_3_low = float(iloc_3["low"])
                pos.trail_stop_loss(iloc_2_range, iloc_3_low)

    # ------------------------------------------------------------------ #
    # Redis publishing                                                     #
    # ------------------------------------------------------------------ #
    async def _publish_snapshot(self):
        """Publishes live market + system snapshot to Redis for SSE."""
        active = self.position_manager.get_active_positions()
        positions_payload = [
            {
                "position_id": p.position_id,
                "symbol": p.symbol,
                "option_type": p.option_type,
                "quantity": p.quantity,
                "entry_price": p.entry_price,
                "current_price": p.current_price,
                "stop_loss": p.stop_loss,
                "unrealized_pnl": round(p.unrealized_pnl, 2),
                "trailed_sl": p.trailed_sl,
            }
            for p in active
        ]

        snapshot = {
            "type": "MARKET_UPDATE",
            "sensex": self._latest_sensex,
            "ce_ltp": self._latest_ce_ltp,
            "pe_ltp": self._latest_pe_ltp,
            "open_positions": len(active),
            "daily_pnl": round(self.risk_manager.current_daily_pnl, 2),
            "trades_today": self.risk_manager.trades_executed_today,
            "kill_switch": self.risk_manager.kill_switch_active,
            "positions": positions_payload,
            "timestamp": datetime.now(IST).isoformat(),
        }

        try:
            await redis_client.set("market_snapshot", json.dumps(snapshot), ex=30)
            await redis_client.publish("trading_events", json.dumps(snapshot))
        except Exception as e:
            logger.debug(f"Redis publish failed: {e}")

    async def _publish_status(self, status: str):
        try:
            await redis_client.set("system_status", status, ex=300)
        except Exception:
            pass


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #
# ------------------------------------------------------------------ #
def _atm_strike(sensex_price: float) -> int:
    """Round SENSEX up to nearest 100 multiple (matches legacy strategy)."""
    return math.ceil(sensex_price / 100) * 100


# Module-level singleton (created in main.py startup)
_worker: Optional[TradingWorker] = None


def get_worker() -> Optional[TradingWorker]:
    return _worker


def set_worker(worker: TradingWorker):
    global _worker
    _worker = worker
