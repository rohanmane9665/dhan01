import aiohttp
import pandas as pd
import io
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class InstrumentManager:
    def __init__(self):
        self.instruments_df = pd.DataFrame()
        self.last_fetch_date = None

    async def initialize(self):
        """Fetch and cache the instrument master CSV."""
        today = datetime.now().date()
        if self.last_fetch_date != today or self.instruments_df.empty:
            logger.info("Fetching instrument master CSV from Dhan...")
            async with aiohttp.ClientSession() as session:
                async with session.get("https://images.dhan.co/api-data/api-scrip-master.csv") as response:
                    content = await response.text()
                    self.instruments_df = pd.read_csv(io.StringIO(content), low_memory=False)
                    self.instruments_df['SEM_CUSTOM_SYMBOL'] = self.instruments_df['SEM_CUSTOM_SYMBOL'].str.strip().str.replace(r'\s+', ' ', regex=True)
                    self.last_fetch_date = today
                    logger.info(f"Loaded {len(self.instruments_df)} instruments.")
        
    def find_option_security_id(self, underlying_symbol: str, expiry_date: str, strike: float, option_type: str) -> str:
        """
        expiry_date format: 'DD MMM' (e.g., '14 MAR') or something that matches SEM_CUSTOM_SYMBOL
        Returns security_id
        """
        df = self.instruments_df
        
        condition = (
            (df['SEM_EXCH_INSTRUMENT_TYPE'] == 'OP') &
            (df['SEM_CUSTOM_SYMBOL'].str.contains(underlying_symbol)) &
            (df['SEM_STRIKE_PRICE'] == strike) &
            (df['SEM_OPTION_TYPE'] == option_type)
        )
        
        match = df[condition]
        if not match.empty:
            return str(match.iloc[0]['SEM_SMST_SECURITY_ID'])
        return None
