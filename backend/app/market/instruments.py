import csv
import logging
import aiohttp
from typing import Dict, Any, Optional
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
        """Downloads and parses the scrip master."""
        logger.info(f"Downloading instrument master from {self.MASTER_CSV_URL}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.MASTER_CSV_URL) as response:
                    if response.status != 200:
                        logger.error(f"Failed to download instrument master. HTTP {response.status}")
                        return
                    content = await response.text()
                    
            self._parse_csv(content)
            logger.info(f"Successfully loaded {len(self._instruments)} instruments.")
        except Exception as e:
            logger.error(f"Error loading instrument master: {e}")

    def _parse_csv(self, content: str):
        lines = content.splitlines()
        reader = csv.DictReader(lines)
        
        for row in reader:
            sec_id = row.get("SEM_SMST_SECURITY_ID")
            symbol = row.get("SEM_TRADING_SYMBOL")
            exch = row.get("SEM_EXM_EXCH_ID")
            inst_type = row.get("SEM_INSTRUMENT_NAME")
            
            if not sec_id or not symbol:
                continue
                
            self._instruments[sec_id] = row
            
            # Map OPTIDX for fast lookup
            if inst_type == "OPTIDX" and exch == "BSE":
                # Typical format: SENSEX 72000 CE (Highly dependent on Dhan's symbol format)
                # We'll just split assuming standard formatting
                parts = symbol.split()
                if len(parts) >= 3:
                    try:
                        base = parts[0]
                        strike = float(parts[-2])
                        opt_type = parts[-1]
                        self._option_lookup[(base, strike, opt_type)] = sec_id
                    except ValueError:
                        pass
                        
    def get_security_id(self, base_symbol: str, strike: float, option_type: str) -> Optional[str]:
        """Looks up the Security ID for a specific option contract."""
        key = (base_symbol, strike, option_type)
        return self._option_lookup.get(key)
