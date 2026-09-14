import os
import logging
from google import genai
from app.agents.context import AIContextService

logger = logging.getLogger(__name__)

class TradingAnalystAgent:
    """
    AI Agent that analyzes real-time strategy performance and market conditions.
    Strictly READ-ONLY. Cannot modify orders or risk limits.
    """
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("GEMINI_API_KEY not set. AI agents will run in mock mode.")

    async def analyze_current_performance(self) -> str:
        context = await AIContextService.get_trading_analyst_context()
        
        if not self.client:
            insight = "Mock Insight: Strategy is stable. No critical anomalies detected."
            logger.info(f"[Trading Analyst Mock] {insight}")
            return insight
            
        try:
            prompt = f"""
You are the Trading Analyst Agent for an algorithmic trading system.
Your job is to analyze the following context and provide a brief, professional summary of the strategy's current performance.
Identify any risks, such as approaching the daily loss limit or holding too many open positions in a sideways market.
Keep the response under 100 words.

CONTEXT:
{context}
"""
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            insight = response.text.strip()
            logger.info(f"[Trading Analyst] {insight}")
            return insight
        except Exception as e:
            logger.error(f"Trading Analyst generation failed: {e}")
            return "Analysis currently unavailable."
