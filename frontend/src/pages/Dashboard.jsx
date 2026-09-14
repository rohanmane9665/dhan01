import React from 'react';
import { Activity, TrendingUp, DollarSign, BarChart2, Shield, Zap } from 'lucide-react';
import { useSSE } from '../hooks/useSSE';
import { useFetch } from '../hooks/useFetch';
import { getDailyStats } from '../services/api';
import { StatCard, Badge } from '../components/ui';

function PnLTicker({ value }) {
  const color = value > 0 ? 'text-green-400' : value < 0 ? 'text-red-400' : 'text-gray-400';
  const sign = value > 0 ? '+' : '';
  return <span className={`font-mono font-bold text-3xl ${color}`}>{sign}₹{value?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>;
}

export default function Dashboard() {
  const { data: sse, connected } = useSSE();
  const { data: stats } = useFetch(getDailyStats, 5000);

  const sensex     = sse?.sensex     || 0;
  const ceLtp      = sse?.ce_ltp     || 0;
  const peLtp      = sse?.pe_ltp     || 0;
  const openPos    = sse?.open_positions ?? 0;
  const dailyPnl   = stats?.daily_pnl   ?? sse?.daily_pnl ?? 0;
  const trades     = stats?.trades_today ?? sse?.trades_today ?? 0;
  const winRate    = stats?.win_rate ?? 0;
  const killActive = sse?.kill_switch ?? false;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">Trading Dashboard</h2>
          <p className="text-sm text-gray-500 mt-0.5">SENSEX Options · 15-Min RSI Breakout Strategy</p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500 animate-pulse shadow-[0_0_8px_#22c55e]' : 'bg-gray-600'}`} />
          <span className="text-xs text-gray-500">{connected ? 'Live' : 'Disconnected'}</span>
          {killActive && (
            <Badge variant="red">🚨 KILL SWITCH ACTIVE</Badge>
          )}
        </div>
      </div>

      {/* Main Stats Grid */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard
          label="Daily P&L"
          value={`${dailyPnl >= 0 ? '+' : ''}₹${Math.abs(dailyPnl).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`}
          color={dailyPnl >= 0 ? 'green' : 'red'}
          sub="Realized today"
          icon={DollarSign}
        />
        <StatCard label="Trades Today" value={trades} color="blue" sub="Executed signals" icon={Zap} />
        <StatCard label="Open Positions" value={openPos} color={openPos > 0 ? 'yellow' : 'gray'} sub="Active right now" icon={Activity} />
        <StatCard label="Win Rate" value={`${winRate}%`} color="purple" sub="Today's sessions" icon={BarChart2} />
      </div>

      {/* Market Prices */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'SENSEX Index', value: sensex, color: 'blue' },
          { label: 'CE LTP', value: ceLtp, color: 'green' },
          { label: 'PE LTP', value: peLtp, color: 'red' },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-xl border border-gray-800 bg-gray-900/60 p-5">
            <p className="text-xs text-gray-500 uppercase tracking-widest mb-2">{label}</p>
            <p className={`text-2xl font-mono font-bold ${color === 'blue' ? 'text-blue-300' : color === 'green' ? 'text-green-400' : 'text-red-400'}`}>
              {value > 0 ? value.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '—'}
            </p>
          </div>
        ))}
      </div>

      {/* Strategy Status Card */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-6">
        <div className="flex items-center gap-3 mb-4">
          <Shield size={18} className="text-blue-400" />
          <h3 className="text-sm font-semibold text-gray-300">Strategy Parameters</h3>
          <Badge variant="blue">Active</Badge>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          {[
            ['Instrument', 'SENSEX Options'],
            ['Candle TF', '15 Minutes'],
            ['RSI Period', '14'],
            ['RSI Threshold', '59.99'],
            ['Entry', 'Breakout > iloc[-2] High'],
            ['Initial SL', 'iloc[-2] Low'],
            ['Trailing SL', 'iloc[-3] Low (after range)'],
            ['No-Trade After', '14:40 IST'],
          ].map(([k, v]) => (
            <div key={k}>
              <p className="text-gray-600 text-xs mb-0.5">{k}</p>
              <p className="text-gray-200 font-mono text-xs">{v}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
