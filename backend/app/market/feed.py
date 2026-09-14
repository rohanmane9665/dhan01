import asyncio
import logging
import json
import traceback
from datetime import datetime
from typing import Optional
import websockets

from app.core.config import settings
from app.worker.trading_worker import get_worker

logger = logging.getLogger(__name__)

class MarketFeed:
    """
    Bridge connecting Broker WebSocket data to the TradingWorker.
    Handles reconnection and routing ticks.
    """
    def __init__(self):
        self.client_id = settings.DHAN_CLIENT_ID
        self.access_token = settings.DHAN_ACCESS_TOKEN
        self._running = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._reconnect_delay = 1
        
        # NOTE: Dhan actually uses a custom binary protocol for their python SDK, 
        # but for this architecture we simulate the websocket connection parsing 
        # or use a generic WSS endpoint if available.
        # Since dhanhq provides `marketfeed`, a real production app would wrap their SDK.
        # We will build an asyncio standard loop that feeds ticks to the worker.

    async def start(self):
        self._running = True
        logger.info("Starting MarketFeed...")
        asyncio.create_task(self._feed_loop())

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info("MarketFeed stopped.")

    async def _feed_loop(self):
        # In a real Dhan implementation, you would connect to: wss://api-feed.dhan.co
        # We will wrap it in a robust reconnect loop.
        uri = "wss://api-feed.dhan.co"
        
        while self._running:
            try:
                # If credentials are not set (e.g. Paper mode), we can simulate ticks
                if not self.client_id or self.client_id == "dummy":
                    logger.info("Running MarketFeed in simulated mode (no Dhan credentials).")
                    await self._simulated_feed_loop()
                    break

                logger.info(f"Connecting to Market Feed at {uri}")
                async with websockets.connect(uri) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1
                    
                    # Send authentication (hypothetical JSON for standard WSS)
                    auth_payload = {
                        "RequestCode": 11,
                        "ClientCode": self.client_id,
                        "Token": self.access_token
                    }
                    await ws.send(json.dumps(auth_payload))
                    
                    while self._running:
                        message = await ws.recv()
                        await self._process_message(message)
                        
            except websockets.exceptions.ConnectionClosed:
                logger.warning("MarketFeed WebSocket closed. Reconnecting...")
            except Exception as e:
                logger.error(f"MarketFeed error: {e}")
                logger.debug(traceback.format_exc())
                
            if self._running:
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    async def _process_message(self, message: str):
        worker = get_worker()
        if not worker:
            return
            
        try:
            # Parse tick data (Assuming JSON for simplicity; Dhan SDK uses binary struct)
            data = json.loads(message)
            
            # Extract fields
            ltp = float(data.get("LTP", 0.0))
            security_id = data.get("SecurityId")
            
            # Map security to type
            tick_type = "INDEX"
            if "CE" in str(security_id):
                tick_type = "CE"
            elif "PE" in str(security_id):
                tick_type = "PE"
                
            tick = {
                "security_id": security_id,
                "ltp": ltp,
                "timestamp": datetime.now(),
                "type": tick_type
            }
            
            await worker.on_tick(tick)
            
        except json.JSONDecodeError:
            pass # Ignore non-JSON (or binary) messages for now
        except Exception as e:
            logger.error(f"Error processing tick: {e}")

    async def _simulated_feed_loop(self):
        """Simulate market ticks for PAPER testing without broker connection."""
        import random
        worker = get_worker()
        sensex_base = 72000.0
        
        while self._running:
            if worker:
                sensex_base += random.uniform(-10, 10)
                ce_price = max(0.05, 300.0 + random.uniform(-5, 5))
                pe_price = max(0.05, 300.0 + random.uniform(-5, 5))
                
                await worker.on_tick({"security_id": "SENSEX", "ltp": sensex_base, "timestamp": datetime.now(), "type": "INDEX"})
                await worker.on_tick({"security_id": "SENSEX_72000_CE", "ltp": ce_price, "timestamp": datetime.now(), "type": "CE"})
                await worker.on_tick({"security_id": "SENSEX_72000_PE", "ltp": pe_price, "timestamp": datetime.now(), "type": "PE"})
                
            await asyncio.sleep(1) # 1 tick per second
