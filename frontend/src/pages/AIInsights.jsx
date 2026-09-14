import React from 'react';
import { Cpu } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';
import { getDailyStats, getRisk } from '../services/api';

export default function AIInsights() {
  const { data: stats }  = useFetch(getDailyStats, 10000);
  const { data: risk }   = useFetch(getRisk, 10000);

  const pnl     = stats?.daily_pnl ?? null;
  const trades  = stats?.trades_today ?? 0;
  const winRate = stats?.win_rate ?? 0;
  const open    = stats?.open_positions ?? 0;
  const ksOn    = risk?.kill_switch_active ?? false;
  const safeM   = risk?.reconciliation_safe_mode ?? false;

  const analystInsight = trades === 0
    ? 'No trades executed yet this session. Monitoring market for RSI breakout pattern on SENSEX options.'
    : `Strategy has completed ${trades} trade${trades > 1 ? 's' : ''} today with a ${winRate}% win rate. ` +
      `Current daily P&L: ${pnl !== null ? (pnl >= 0 ? '+' : '') + '₹' + pnl.toFixed(2) : '—'}.`;

  const reliabilityInsight = ksOn
    ? '🚨 Emergency kill switch is currently ACTIVE. All new trades are blocked until manually reset.'
    : safeM
    ? '⚠️ System is in SAFE MODE due to a position reconciliation mismatch. Review Risk Controls.'
    : 'System is healthy. Market data feed is active. No reconciliation issues detected.';

  const postMarketInsight = open > 0
    ? `${open} position${open > 1 ? 's are' : ' is'} currently open. Trailing stop-loss is being monitored on each tick.`
    : trades > 0
    ? `Session closed ${trades} trade${trades > 1 ? 's' : ''}. Daily P&L: ${pnl !== null ? (pnl >= 0 ? '+' : '') + '₹' + pnl.toFixed(2) : '—'}.`
    : 'Session report will be generated after trades are executed.';

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <Cpu size={20} className="text-purple-400" />
        <div>
          <h2 className="text-2xl font-bold text-white">AI Insights</h2>
          <p className="text-xs text-gray-500 mt-0.5">Read-only analytical summaries — AI never controls trading</p>
        </div>
      </div>

      <div className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-3 text-xs text-yellow-400/80">
        ⚠️ AI agents are strictly read-only. They observe system state but can never place, modify, or cancel orders.
      </div>

      <div className="space-y-4">
        <InsightCard
          title="Trading Analyst"
          color="blue"
          badge="Live"
          text={analystInsight}
          meta={`Data source: /api/v1/analytics/daily · Refreshes every 10s`}
        />
        <InsightCard
          title="Reliability Agent"
          color={ksOn || safeM ? 'red' : 'yellow'}
          badge={ksOn ? 'Alert' : safeM ? 'Warning' : 'Monitoring'}
          text={reliabilityInsight}
          meta="Data source: /api/v1/risk/ · Refreshes every 10s"
        />
        <InsightCard
          title="Post-Market Analyst"
          color="purple"
          badge={trades > 0 ? 'Active' : 'Idle'}
          text={postMarketInsight}
          meta="Data source: /api/v1/positions/ + /api/v1/analytics/daily"
        />
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-4 text-xs text-gray-500 space-y-1">
        <p className="font-semibold text-gray-400 mb-2">FastMCP Gateway</p>
        <p>AI agents connect via the FastMCP server at <code className="text-blue-400">backend/app/agents/mcp_server.py</code></p>
        <p>Available tools: <code className="text-blue-400">get_market_status</code>, <code className="text-blue-400">get_positions</code>, <code className="text-blue-400">get_pnl</code></p>
        <p>All tools are read-only. No tool can write state or submit orders.</p>
      </div>
    </div>
  );
}

function InsightCard({ title, color, badge, text, meta }) {
  const colors = {
    blue:   { border: 'border-blue-900/50', bg: 'bg-blue-900/10', title: 'text-blue-400', badge: 'bg-blue-500/20 text-blue-400' },
    yellow: { border: 'border-yellow-900/50', bg: 'bg-yellow-900/10', title: 'text-yellow-400', badge: 'bg-yellow-500/20 text-yellow-400' },
    purple: { border: 'border-purple-900/50', bg: 'bg-purple-900/10', title: 'text-purple-400', badge: 'bg-purple-500/20 text-purple-400' },
    red:    { border: 'border-red-900/50', bg: 'bg-red-900/10', title: 'text-red-400', badge: 'bg-red-500/20 text-red-400 animate-pulse' },
  };
  const c = colors[color] || colors.blue;
  return (
    <div className={`rounded-xl border ${c.border} ${c.bg} p-6`}>
      <div className="flex items-center justify-between mb-3">
        <h3 className={`text-sm font-semibold ${c.title}`}>{title}</h3>
        <span className={`text-xs px-2 py-0.5 rounded-full ${c.badge}`}>{badge}</span>
      </div>
      <p className="text-gray-300 text-sm leading-relaxed">{text}</p>
      <p className="text-gray-600 text-xs mt-3 font-mono">{meta}</p>
    </div>
  );
}
