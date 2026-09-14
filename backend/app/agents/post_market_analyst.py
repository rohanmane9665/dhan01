import os
import logging
from google import genai
from app.agents.context import AIContextService

logger = logging.getLogger(__name__)

class PostMarketAnalyst:
    """
    AI Agent that summarizes the day's trading performance after market close.
    Strictly READ-ONLY.
    """
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("GEMINI_API_KEY not set. AI agents will run in mock mode.")

    async def generate_eod_summary(self) -> str:
        context = await AIContextService.get_post_market_context()
        
        if not self.client:
            insight = "Mock Insight: EOD completed successfully. Day closed in profit."
            logger.info(f"[Post-Market Analyst Mock] {insight}")
            return insight
            
        try:
            prompt = f"""
You are the Post-Market Analyst Agent for an algorithmic trading system.
Your job is to analyze the End of Day summary data.
Provide a concise, professional recap of the day's performance. Highlight the final PnL and total trades.
Keep the response under 100 words.

CONTEXT:
{context}
"""
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            insight = response.text.strip()
            logger.info(f"[Post-Market Analyst] {insight}")
            return insight
        except Exception as e:
            logger.error(f"Post-Market Analyst generation failed: {e}")
            return "EOD summary currently unavailable."
