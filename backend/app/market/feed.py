"""
market/feed.py
==============
Real-time market data feed using the DhanHQ MarketFeed SDK.

Subscribes to SENSEX index + ATM CE/PE option instruments via WebSocket.
Routes parsed ticks to the TradingWorker async pipeline.

No simulated/demo data — all ticks come from the live Dhan feed.
"""

import asyncio
import logging
import time as time_module
import threading
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any

import pytz

from app.core.config import settings

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# SENSEX Index constants
SENSEX_SECURITY_ID = "51"
SENSEX_EXCHANGE_SEGMENT = "IDX_I"


class MarketFeed:
    """
    Bridge connecting the DhanHQ MarketFeed SDK to the async TradingWorker.

    Architecture:
        - The DhanHQ SDK runs in a daemon thread (blocking I/O with run_forever + get_data loop)
        - Parsed ticks are pushed into an asyncio.Queue via call_soon_threadsafe
        - The async _dispatch_loop reads from the queue and calls worker.on_tick()
    """

    def __init__(self):
        self.client_id = settings.DHAN_CLIENT_ID if settings else ""
        self.access_token = settings.DHAN_ACCESS_TOKEN if settings else ""
        self._running = False
        self._feed_thread: Optional[threading.Thread] = None
        self._tick_queue: Optional[asyncio.Queue] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._sdk_feed = None  # DhanHQ MarketFeed instance

        # Track subscribed instruments for dynamic updates
        # Format: [(exchange_segment, security_id, subscription_type), ...]
        self._instruments: List[Tuple] = []

        # Instrument type mapping: security_id -> tick_type ("INDEX", "CE", "PE")
        self._instrument_type_map: Dict[str, str] = {}

    async def start(self, instruments: Optional[List[Tuple]] = None):
        """
        Start the market feed with the given instruments.

        instruments: list of (exchange_segment_int, security_id_str, subscription_type_int)
                     Uses DhanHQ MarketFeed constants (e.g. MarketFeed.BSE_FNO)
        """
        if not self.client_id or not self.access_token:
            logger.error(
                "Cannot start MarketFeed: DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN missing. "
                "Set them in .env for real market data."
            )
            return

        self._loop = asyncio.get_running_loop()
        self._tick_queue = asyncio.Queue()
        self._running = True

        if instruments:
            self._instruments = instruments

        # Start the SDK feed thread
        self._feed_thread = threading.Thread(
            target=self._run_sdk_feed,
            daemon=True,
            name="DhanMarketFeed"
        )
        self._feed_thread.start()

        # Start the async dispatch loop
        asyncio.create_task(self._dispatch_loop())
        logger.info("✅ MarketFeed started with real DhanHQ SDK.")

    async def stop(self):
        """Stop the market feed and clean up."""
        self._running = False
        if self._sdk_feed:
            try:
                self._sdk_feed.close()
            except Exception as e:
                logger.debug(f"Error closing SDK feed: {e}")
            self._sdk_feed = None
        logger.info("MarketFeed stopped.")

    def set_instrument_type(self, security_id: str, tick_type: str):
        """
        Register the tick type for a security ID so ticks are correctly routed.
        tick_type: "INDEX", "CE", or "PE"
        """
        self._instrument_type_map[str(security_id)] = tick_type

    def update_instruments(self, instruments: List[Tuple], type_map: Dict[str, str]):
        """
        Dynamically update the subscribed instruments (e.g. when ATM strike changes).
        This will close the current feed and restart with new instruments.
        """
        self._instruments = instruments
        self._instrument_type_map.update(type_map)

        # Close existing feed — the thread loop will detect and restart
        if self._sdk_feed:
            try:
                self._sdk_feed.close()
            except Exception:
                pass
            self._sdk_feed = None

        logger.info(f"🔄 Instrument subscription updated: {len(instruments)} instruments")

    # ------------------------------------------------------------------ #
    # Internal: SDK Thread                                                 #
    # ------------------------------------------------------------------ #
    def _run_sdk_feed(self):
        """
        Runs in a daemon thread. Connects to DhanHQ WebSocket and polls for data.
        Pushes ticks into the async queue.
        """
        try:
            from dhanhq import DhanContext, MarketFeed as DhanMarketFeed
        except ImportError:
            logger.error(
                "dhanhq package not installed. Cannot start live market feed. "
                "Install with: pip install dhanhq"
            )
            return

        reconnect_delay = 1

        while self._running:
            try:
                if not self._instruments:
                    logger.info("No instruments to subscribe. Waiting...")
                    time_module.sleep(5)
                    continue

                logger.info(f"Connecting DhanHQ MarketFeed for {len(self._instruments)} instruments...")
                dhan_context = DhanContext(self.client_id, self.access_token)
                self._sdk_feed = DhanMarketFeed(dhan_context, self._instruments, "v2")
                self._sdk_feed.run_forever()
                reconnect_delay = 1

                logger.info("✅ DhanHQ MarketFeed connected. Streaming ticks...")

                while self._running and self._sdk_feed:
                    try:
                        response = self._sdk_feed.get_data()
                        if response:
                            self._enqueue_tick(response)
                    except Exception as e:
                        logger.debug(f"Feed get_data error: {e}")
                        break  # Reconnect
                    time_module.sleep(0.01)  # 10ms poll interval

            except Exception as e:
                logger.error(f"MarketFeed connection error: {e}")

            # Reconnect with backoff
            if self._running:
                logger.warning(f"MarketFeed disconnected. Reconnecting in {reconnect_delay}s...")
                time_module.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)

        logger.info("MarketFeed thread exiting.")

    def _enqueue_tick(self, response: Dict[str, Any]):
        """Parse SDK response and push tick into async queue."""
        if not self._loop or not self._tick_queue:
            return

        try:
            ltp = float(response.get("LTP", 0.0))
            if ltp <= 0:
                return

            security_id = str(response.get("security_id", response.get("SecurityId", "")))

            # Determine tick type from our mapping
            tick_type = self._instrument_type_map.get(security_id, "UNKNOWN")
            if tick_type == "UNKNOWN":
                # Try to infer from security ID
                if security_id == SENSEX_SECURITY_ID:
                    tick_type = "INDEX"
                else:
                    logger.debug(f"Unknown security_id in tick: {security_id}")
                    return

            tick = {
                "security_id": security_id,
                "ltp": ltp,
                "timestamp": datetime.now(IST),
                "type": tick_type,
                # Pass through additional fields if available
                "open": response.get("open"),
                "high": response.get("high"),
                "low": response.get("low"),
                "close": response.get("close"),
                "volume": response.get("volume"),
            }

            self._loop.call_soon_threadsafe(self._tick_queue.put_nowait, tick)

        except Exception as e:
            logger.debug(f"Error parsing tick: {e}")

    # ------------------------------------------------------------------ #
    # Internal: Async Dispatch                                             #
    # ------------------------------------------------------------------ #
    async def _dispatch_loop(self):
        """Async loop that reads ticks from queue and dispatches to TradingWorker."""
        from app.worker.trading_worker import get_worker

        while self._running:
            try:
                tick = await asyncio.wait_for(self._tick_queue.get(), timeout=5.0)
                worker = get_worker()
                if worker:
                    await worker.on_tick(tick)
            except asyncio.TimeoutError:
                continue  # Keep looping, just no ticks received
            except Exception as e:
                logger.error(f"Error dispatching tick: {e}")
                await asyncio.sleep(0.1)
