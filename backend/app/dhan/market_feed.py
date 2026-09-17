"""
dhan/market_feed.py
===================
Thin compatibility alias — all MarketFeed logic lives in app.market.feed.

This module exists for backward compatibility only.
"""

from app.market.feed import MarketFeed as MarketFeedClient  # noqa: F401

__all__ = ["MarketFeedClient"]
