import React from 'react';
import { Shield, AlertTriangle, CheckCircle, XCircle } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';
import { getRisk, triggerKill, resetKill } from '../services/api';
import { StatCard, Badge, Spinner } from '../components/ui';

export default function RiskControls() {
  const { data, loading, refetch } = useFetch(getRisk, 3000);

  const handleKill = async () => {
    if (!window.confirm('🚨 ACTIVATE EMERGENCY KILL SWITCH?\n\nThis will immediately block ALL new trades.')) return;
    await triggerKill();
    refetch();
  };

  const handleReset = async () => {
    if (!window.confirm('Reset kill switch? Trading will resume on next valid signal.')) return;
    await resetKill();
    refetch();
  };

  if (loading && !data) {
    return <div className="p-6 flex items-center gap-3 text-gray-500"><Spinner /> Loading risk data...</div>;
  }

  const killActive = data?.kill_switch_active ?? false;
  const safeMode   = data?.reconciliation_safe_mode ?? false;

  return (
    <div className="p-6 space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-white">Risk Controls</h2>
        <p className="text-xs text-gray-500 mt-0.5">System safety and risk parameter monitoring</p>
      </div>

      {/* Kill Switch Banner */}
      {killActive && (
        <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-4 flex items-center justify-between animate-pulse">
          <div className="flex items-center gap-3">
            <AlertTriangle size={20} className="text-red-400" />
            <div>
              <p className="text-red-400 font-bold">EMERGENCY KILL SWITCH IS ACTIVE</p>
              <p className="text-xs text-red-400/70 mt-0.5">{data?.kill_switch_reason || 'Operator triggered'}</p>
            </div>
          </div>
          <button
            onClick={handleReset}
            className="px-4 py-2 text-xs font-bold bg-green-500/20 text-green-400 border border-green-500/30 rounded-lg hover:bg-green-500/30 transition-colors"
          >
            RESET KILL SWITCH
          </button>
        </div>
      )}

      {/* Risk Stats */}
      <div className="grid grid-cols-2 xl:grid-cols-3 gap-4">
        <StatCard
          label="Kill Switch"
          value={killActive ? 'ACTIVE' : 'Off'}
          color={killActive ? 'red' : 'green'}
          icon={Shield}
        />
        <StatCard
          label="Daily Loss Limit"
          value={data?.max_daily_loss ? `₹${data.max_daily_loss.toLocaleString()}` : 'Unlimited'}
          color="yellow"
          sub={`Current: ${data?.current_daily_pnl >= 0 ? '+' : ''}₹${data?.current_daily_pnl?.toFixed(2) ?? '0.00'}`}
          icon={AlertTriangle}
        />
        <StatCard
          label="Position Limit"
          value={`${data?.current_open_positions ?? 0} / ${data?.max_open_positions ?? '∞'}`}
          color="blue"
          sub="Open / Max positions"
          icon={Shield}
        />
        <StatCard
          label="Trades Today"
          value={`${data?.trades_today ?? 0}${data?.max_trades_per_day ? ' / ' + data.max_trades_per_day : ''}`}
          color="purple"
          sub="Executed this session"
          icon={Shield}
        />
        <StatCard
          label="Reconciliation"
          value={safeMode ? 'SAFE MODE' : 'OK'}
          color={safeMode ? 'red' : 'green'}
          sub={data?.last_mismatch_reason || 'Position state matches broker'}
          icon={safeMode ? XCircle : CheckCircle}
        />
      </div>

      {/* Manual Controls */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-6">
        <h3 className="text-sm font-semibold text-gray-300 mb-4 flex items-center gap-2">
          <Shield size={16} className="text-gray-400" /> Manual Controls
        </h3>
        <div className="flex flex-wrap gap-3">
          <button
            onClick={handleKill}
            disabled={killActive}
            className="px-6 py-3 bg-red-600/20 text-red-400 border border-red-600/30 rounded-lg hover:bg-red-600/30 disabled:opacity-40 disabled:cursor-not-allowed font-bold text-sm transition-colors flex items-center gap-2"
          >
            <AlertTriangle size={16} /> Activate Kill Switch
          </button>
          <button
            onClick={handleReset}
            disabled={!killActive}
            className="px-6 py-3 bg-green-500/10 text-green-400 border border-green-500/20 rounded-lg hover:bg-green-500/20 disabled:opacity-40 disabled:cursor-not-allowed font-bold text-sm transition-colors flex items-center gap-2"
          >
            <CheckCircle size={16} /> Reset Kill Switch
          </button>
        </div>
      </div>

      {/* Risk rules info */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-6 text-xs text-gray-500 space-y-2">
        <p className="font-semibold text-gray-400 mb-2">System Safety Rules</p>
        <p>• All trade signals MUST pass through RiskManager before execution.</p>
        <p>• AI agents are READ-ONLY — they can never place, modify, or cancel orders.</p>
        <p>• Kill switch is a process-level gate — no order can bypass it.</p>
        <p>• Reconciliation runs on startup and after every fill. Mismatches trigger SAFE_MODE automatically.</p>
        <p>• LIVE trading requires both <code className="text-blue-400">TRADING_MODE=LIVE</code> and <code className="text-blue-400">ENABLE_LIVE_TRADING=true</code> in .env.</p>
      </div>
    </div>
  );
}
