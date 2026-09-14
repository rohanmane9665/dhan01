# Dhan Algo Trading Platform Architecture

## Components
1. **Trading Engine**: Built on FastAPI. Purely deterministic.
2. **PostgreSQL**: Source of truth for orders, positions, events.
3. **Redis**: In-memory transient state and pub/sub for real-time events.
4. **React Terminal**: The UI for monitoring and risk controls.
5. **AI Agents / MCP**: FastMCP gateway running read-only tools to analyze trading performance, system reliability, and post-market summaries.

## Core Flow
WebSocket/REST -> DataValidator -> CandleEngine -> OptionRSIStrategy -> Signal -> RiskManager -> ExecutionEngine -> DhanAdapter
