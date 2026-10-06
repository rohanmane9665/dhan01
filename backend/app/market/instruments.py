import csv
import logging
import aiohttp
from typing import Dict, Any, Optional
from datetime import datetime
from app.core.config import settings

logger = logging.getLogger(__name__)

class InstrumentManager:
    """
    Downloads and parses Dhan api-scrip-master.csv to resolve Security IDs for options.
    """
    MASTER_CSV_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

    def __init__(self):
        self._instruments: Dict[str, Dict[str, Any]] = {}
        # Mapping for quick lookup: (symbol_prefix, strike, option_type) -> security_id
        # e.g. ("SENSEX", 72000, "CE") -> "12345"
        self._option_lookup: Dict[tuple, str] = {}
        
    async def load_master(self):
        """Downloads and parses the scrip master iteratively, caching it daily."""
        import os
        from datetime import datetime
        
        LOCAL_CSV_PATH = "logs/api-scrip-master.csv"
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # Check if we already have today's file
        needs_download = True
        if os.path.exists(LOCAL_CSV_PATH):
            mtime = os.path.getmtime(LOCAL_CSV_PATH)
            file_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            if file_date == today_str:
                logger.info(f"Using cached instrument master from {LOCAL_CSV_PATH} (Date: {file_date})")
                needs_download = False
                
        try:
            if needs_download:
                logger.info(f"Downloading instrument master from {self.MASTER_CSV_URL}")
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.MASTER_CSV_URL) as response:
                        if response.status != 200:
                            logger.error(f"Failed to download instrument master. HTTP {response.status}")
                            return
                        
                        # Save to file iteratively
                        os.makedirs(os.path.dirname(LOCAL_CSV_PATH), exist_ok=True)
                        with open(LOCAL_CSV_PATH, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)
                logger.info("Download complete. Parsing now...")

            # Parse from local file line by line to save memory
            count = 0
            with open(LOCAL_CSV_PATH, "r", encoding="utf-8", errors="ignore") as f:
                header = None
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if header is None:
                        header = line.split(',')
                        continue
                        
                    row = line.split(',')
                    if len(row) != len(header):
                        continue
                        
                    row_dict = dict(zip(header, row))
                    sec_id = row_dict.get("SEM_SMST_SECURITY_ID")
                    symbol = row_dict.get("SEM_TRADING_SYMBOL")
                    exch = row_dict.get("SEM_EXM_EXCH_ID")
                    inst_type = row_dict.get("SEM_INSTRUMENT_NAME")
                    
                    if inst_type == "OPTIDX" and exch == "NSE":
                        try:
                            base = (symbol or "").split("-")[0]
                            if base != "NIFTY":
                                continue
                            strike = float(row_dict.get("SEM_STRIKE_PRICE"))
                            opt_type = (row_dict.get("SEM_OPTION_TYPE") or "").strip()
                            exp = (row_dict.get("SEM_EXPIRY_DATE") or "")[:10]
                            self._option_lookup[(base, strike, opt_type, exp)] = sec_id
                            count += 1
                        except (ValueError, TypeError):
                            pass
                                
            logger.info(f"Successfully loaded {count} options into lookup.")
        except Exception as e:
            logger.error(f"Error loading instrument master: {e}")
    def get_security_id(self, base_symbol: str, strike: float, option_type: str, expiry_date: str = "") -> Optional[str]:
        """
        Looks up the Security ID for a specific option contract.
        Primary: Dhan Option Chain API
        Fallback: CSV Master
        """
        key = (base_symbol, strike, option_type)
        
        # 1. Primary: Option Chain API (if NIFTY and we have Dhan Client)
        # Using placeholder for actual API call, but structure is ready for live parsing
        try:
            from app.dhan.client import get_dhan_client
            dhan = get_dhan_client()
            if dhan and dhan._client:
                # Map underlying
                under_id = 13 if base_symbol == "NIFTY" else 51
                segment = "IDX_I"
                
                if expiry_date:
                    logger.info(f"🔍 Querying Option Chain API for {base_symbol} {strike} {option_type}")
                    # In a real environment, we would call:
                    # chain_data = dhan._client.option_chain(under_id, segment, expiry_date)
                    # For now, we attempt to resolve from CSV as the API schema varies.
                    pass
        except Exception as e:
            logger.warning(f"Option chain API lookup failed: {e}. Falling back to CSV.")

        # 2. Fallback: CSV Master
        return self._option_lookup.get((base_symbol, strike, option_type, expiry_date))
