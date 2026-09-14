import threading
import asyncio
import logging
from typing import List, Tuple
from dhanhq import DhanContext, MarketFeed
from app.core.config import settings

logger = logging.getLogger(__name__)

class MarketFeedClient:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.client_id = settings.DHAN_CLIENT_ID
        self.access_token = settings.DHAN_ACCESS_TOKEN
        self.context = DhanContext(self.client_id, self.access_token)
        self.feed = None
        self.thread = None
        self.is_running = False
        self.loop = loop
        self.tick_queue = asyncio.Queue()

    def _start_feed(self, instruments: List[Tuple[int, str, int]]):
        """Runs in a separate thread."""
        logger.info(f"Starting Dhan Market Feed for instruments: {instruments}")
        self.feed = MarketFeed(self.context, instruments, "v2")
        self.is_running = True
        
        self.feed.run_forever()
        
        import time
        while self.is_running:
            try:
                response = self.feed.get_data()
                if response:
                    # push to async queue safely
                    self.loop.call_soon_threadsafe(self.tick_queue.put_nowait, response)
            except Exception as e:
                logger.error(f"Error in market feed thread: {e}")
            time.sleep(0.01)

    async def connect(self, instruments: List[Tuple[int, str, int]]):
        """
        instruments format: [(exchange_segment, security_id, subscription_type)]
        """
        self.thread = threading.Thread(target=self._start_feed, args=(instruments,), daemon=True)
        self.thread.start()

    async def disconnect(self):
        self.is_running = False
        if self.feed:
            self.feed.close()
        logger.info("Market feed disconnected.")
        
    async def get_next_tick(self):
        return await self.tick_queue.get()
