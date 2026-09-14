import React from 'react';
import { TrendingUp, AlertTriangle, X } from 'lucide-react';
import { useSSE } from '../hooks/useSSE';
import { useFetch } from '../hooks/useFetch';
import { getPositions, closePosition } from '../services/api';
import { Badge, EmptyState, Spinner } from '../components/ui';

function PnLCell({ value }) {
  if (value == null) return <span className="text-gray-600">—</span>;
  const pos = value >= 0;
  return (
    <span className={`font-mono font-semibold ${pos ? 'text-green-400' : 'text-red-400'}`}>
      {pos ? '+' : ''}₹{Math.abs(value).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
    </span>
  );
}

export default function LiveTrading() {
  const { data: sse, connected } = useSSE();
  const { data: positions, loading, refetch } = useFetch(getPositions, 2000);

  const sensex  = sse?.sensex  || 0;
  const ceLtp   = sse?.ce_ltp  || 0;
  const peLtp   = sse?.pe_ltp  || 0;

  const handleClose = async (id) => {
    if (!window.confirm('Close this position at market price?')) return;
    try {
      await closePosition(id);
      refetch();
    } catch (e) {
      alert('Failed to close position: ' + e.message);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">Live Trading</h2>
          <p className="text-xs text-gray-500 mt-0.5">Real-time positions and market data</p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500 animate-pulse shadow-[0_0_8px_#22c55e]' : 'bg-red-500'}`} />
          <span className="text-xs text-gray-500">{connected ? 'Stream connected' : 'Reconnecting...'}</span>
        </div>
      </div>

      {/* Market Ticker */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'SENSEX', value: sensex, cls: 'text-blue-300' },
          { label: 'CE LTP', value: ceLtp,  cls: 'text-green-400' },
          { label: 'PE LTP', value: peLtp,  cls: 'text-red-400' },
        ].map(({ label, value, cls }) => (
          <div key={label} className="bg-gray-900 border border-gray-800 rounded-xl p-4 flex flex-col gap-1">
            <span className="text-xs text-gray-500 uppercase tracking-wider">{label}</span>
            <span className={`text-xl font-mono font-bold ${cls}`}>
              {value > 0 ? value.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : <span className="text-gray-600">—</span>}
            </span>
          </div>
        ))}
      </div>

      {/* Open Positions Table */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/60 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp size={16} className="text-blue-400" />
            <h3 className="text-sm font-semibold text-gray-200">Open Positions</h3>
            <Badge variant={positions?.length ? 'yellow' : 'gray'}>
              {positions?.length ?? 0} active
            </Badge>
          </div>
          {loading && <Spinner size={16} />}
        </div>

        {(!positions || positions.length === 0) ? (
          <EmptyState message="No open positions — waiting for signal..." icon={TrendingUp} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-500 text-xs uppercase tracking-wider">
                  {['Symbol', 'Type', 'Qty', 'Entry', 'LTP', 'SL', 'Unrealized P&L', 'Trailed', ''].map(h => (
                    <th key={h} className="px-4 py-3 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map(p => (
                  <tr key={p.position_id} className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
                    <td className="px-4 py-3 font-mono text-white text-xs">{p.symbol}</td>
                    <td className="px-4 py-3">
                      <Badge variant={p.option_type === 'CE' ? 'green' : 'red'}>{p.option_type}</Badge>
                    </td>
                    <td className="px-4 py-3 font-mono text-gray-300">{p.quantity}</td>
                    <td className="px-4 py-3 font-mono text-gray-300">₹{p.entry_price?.toFixed(2)}</td>
                    <td className="px-4 py-3 font-mono text-white">₹{p.current_price?.toFixed(2)}</td>
                    <td className="px-4 py-3 font-mono text-orange-400">₹{p.stop_loss?.toFixed(2)}</td>
                    <td className="px-4 py-3"><PnLCell value={p.unrealized_pnl} /></td>
                    <td className="px-4 py-3">
                      {p.trailed_sl ? <Badge variant="blue">Trailed ✓</Badge> : <span className="text-gray-600 text-xs">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => handleClose(p.position_id)}
                        className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300 border border-red-500/20 hover:border-red-500/40 rounded px-2 py-1 transition-colors"
                      >
                        <X size={12} /> Close
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Last Signal Box */}
      {sse?.type === 'MARKET_UPDATE' && (
        <div className="rounded-xl border border-blue-500/20 bg-blue-900/10 p-4">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle size={14} className="text-blue-400" />
            <span className="text-xs font-semibold text-blue-400 uppercase tracking-wider">Last SSE Tick</span>
          </div>
          <p className="text-xs text-gray-400 font-mono">
            Trades: {sse.trades_today} · Realized PnL: {sse.daily_pnl >= 0 ? '+' : ''}₹{sse.daily_pnl?.toFixed(2)} · TS: {sse.timestamp}
          </p>
        </div>
      )}
    </div>
  );
}
