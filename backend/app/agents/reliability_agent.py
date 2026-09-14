import os
import logging
from google import genai
from app.agents.context import AIContextService

logger = logging.getLogger(__name__)

class ReliabilityAgent:
    """
    AI Agent that monitors system health, API limits, and reconciliations.
    Strictly READ-ONLY.
    """
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("GEMINI_API_KEY not set. AI agents will run in mock mode.")

    async def check_system_health(self) -> str:
        context = await AIContextService.get_reliability_context()
        
        if not self.client:
            insight = "Mock Insight: System is healthy. Reconciliations match."
            logger.info(f"[Reliability Agent Mock] {insight}")
            return insight
            
        try:
            prompt = f"""
You are the Reliability Agent for an algorithmic trading system.
Your job is to analyze the following context regarding system errors, kill switches, and API limits.
Provide a brief, professional health summary. If the Kill Switch is active, emphasize it.
Keep the response under 100 words.

CONTEXT:
{context}
"""
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            insight = response.text.strip()
            logger.info(f"[Reliability Agent] {insight}")
            return insight
        except Exception as e:
            logger.error(f"Reliability Agent generation failed: {e}")
            return "Health analysis currently unavailable."
