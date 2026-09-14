import React from 'react';
import { BarChart2, TrendingUp, TrendingDown, DollarSign } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';
import { getDailyStats } from '../services/api';
import { StatCard, EmptyState, Spinner } from '../components/ui';

export default function Analytics() {
  const { data, loading, error } = useFetch(getDailyStats, 5000);

  if (loading && !data) {
    return (
      <div className="p-6 flex items-center gap-3 text-gray-500">
        <Spinner /> Loading analytics...
      </div>
    );
  }

  if (error) {
    return <div className="p-6 text-red-400">Error: {error}</div>;
  }

  const pnl      = data?.daily_pnl ?? 0;
  const unreal   = data?.unrealized_pnl ?? 0;
  const total    = data?.total_pnl ?? 0;
  const trades   = data?.trades_today ?? 0;
  const open     = data?.open_positions ?? 0;
  const winRate  = data?.win_rate ?? 0;

  return (
    <div className="p-6 space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-white">P&L Analytics</h2>
        <p className="text-xs text-gray-500 mt-0.5">Today's session performance</p>
      </div>

      {/* P&L Cards */}
      <div className="grid grid-cols-2 xl:grid-cols-3 gap-4">
        <StatCard
          label="Realized P&L"
          value={`${pnl >= 0 ? '+' : ''}₹${Math.abs(pnl).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`}
          color={pnl >= 0 ? 'green' : 'red'}
          sub="Closed positions today"
          icon={pnl >= 0 ? TrendingUp : TrendingDown}
        />
        <StatCard
          label="Unrealized P&L"
          value={`${unreal >= 0 ? '+' : ''}₹${Math.abs(unreal).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`}
          color={unreal >= 0 ? 'green' : 'yellow'}
          sub="Open positions MTM"
          icon={BarChart2}
        />
        <StatCard
          label="Total P&L"
          value={`${total >= 0 ? '+' : ''}₹${Math.abs(total).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`}
          color={total >= 0 ? 'green' : 'red'}
          sub="Realized + Unrealized"
          icon={DollarSign}
        />
        <StatCard label="Trades Today"    value={trades}        color="blue"   sub="Executed orders"  icon={BarChart2} />
        <StatCard label="Open Positions"  value={open}          color="yellow" sub="Live right now"    icon={TrendingUp} />
        <StatCard label="Win Rate"        value={`${winRate}%`} color="purple" sub="Today's sessions"  icon={TrendingUp} />
      </div>

      {/* Performance bars */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-6">
        <h3 className="text-sm font-semibold text-gray-300 mb-4">Session Breakdown</h3>
        {trades === 0 ? (
          <EmptyState message="No trades executed in this session yet." icon={BarChart2} />
        ) : (
          <div className="space-y-4">
            <div>
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>Win Rate</span><span>{winRate}%</span>
              </div>
              <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full transition-all duration-700"
                  style={{ width: `${Math.min(winRate, 100)}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>Daily P&L Progress</span>
                <span>{pnl >= 0 ? '+' : ''}₹{Math.abs(pnl).toFixed(0)}</span>
              </div>
              <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-700 ${pnl >= 0 ? 'bg-gradient-to-r from-blue-500 to-cyan-400' : 'bg-gradient-to-r from-red-600 to-red-400'}`}
                  style={{ width: `${Math.min(Math.abs(pnl) / 10000 * 100, 100)}%` }}
                />
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-4 text-xs text-gray-600">
        📌 Historical trade data will be available after PostgreSQL trade-log integration.
      </div>
    </div>
  );
}
