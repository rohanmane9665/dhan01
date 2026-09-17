import React, { useState, useEffect } from 'react';
import { BarChart2, TrendingUp, TrendingDown, DollarSign, FileText } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';
import { getDailyStats, getTradeBook, getBrokerOrders, getLocalTrades, getLocalOrders, getStatus } from '../services/api';
import { StatCard, EmptyState, Spinner, Badge } from '../components/ui';

export default function Analytics() {
  const [mode, setMode] = useState('PAPER');
  const { data, loading, error } = useFetch(getDailyStats, 5000);
  
  // Fetch both live and local data, then choose based on mode
  const { data: liveTradeBook } = useFetch(getTradeBook, 10000);
  const { data: liveOrders } = useFetch(getBrokerOrders, 10000);
  const { data: localTrades } = useFetch(getLocalTrades, 5000);
  const { data: localOrders } = useFetch(getLocalOrders, 5000);

  useEffect(() => {
    getStatus().then(res => {
      if (res && res.mode) setMode(res.mode);
    });
  }, []);

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

  // Select appropriate list based on mode
  const isPaper = mode === 'PAPER';
  
  // For LIVE mode, Dhan uses different fields than local PostgreSQL DB
  const rawTradeList = isPaper ? (localTrades?.trades ?? []) : (liveTradeBook?.trades ?? []);
  const rawOrderList = isPaper ? (localOrders?.orders ?? []) : (liveOrders?.orders ?? []);

  return (
    <div className="p-6 space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-white">P&L Analytics</h2>
        <p className="text-xs text-gray-500 mt-0.5">Today's session performance ({mode} MODE)</p>
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

      {/* Trade Book */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/60 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FileText size={16} className={isPaper ? "text-yellow-400" : "text-blue-400"} />
            <h3 className="text-sm font-semibold text-gray-200">Trade Book</h3>
            <Badge variant={isPaper ? "yellow" : "blue"}>{isPaper ? "Paper Local DB" : "Dhan Live"}</Badge>
            <Badge variant="gray">{rawTradeList.length} trades</Badge>
          </div>
        </div>

        {rawTradeList.length === 0 ? (
          <EmptyState message={isPaper ? "No paper trades executed locally yet." : "No executed trades from Dhan broker yet."} icon={FileText} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-500 text-xs uppercase tracking-wider">
                  {['Order/Trade ID', 'Symbol', 'Side', 'Qty', 'Price', 'Status', 'Time'].map(h => (
                    <th key={h} className="px-4 py-3 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rawTradeList.slice(0, 50).map((t, idx) => (
                  <tr key={t.orderId || t.id || idx} className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
                    <td className="px-4 py-2.5 font-mono text-gray-400 text-xs">{t.orderId || t.order_id || t.dhanClientOrderId || t.id || '—'}</td>
                    <td className="px-4 py-2.5 font-mono text-white text-xs">{t.customSymbol || t.tradingSymbol || t.symbol || '—'}</td>
                    <td className="px-4 py-2.5">
                      <Badge variant={t.transactionType === 'BUY' || t.side === 'BUY' ? 'green' : 'red'}>
                        {t.transactionType || t.side || '—'}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-gray-300">{t.tradedQuantity ?? t.quantity ?? '—'}</td>
                    <td className="px-4 py-2.5 font-mono text-gray-300">₹{Number(t.tradedPrice ?? t.price ?? 0).toFixed(2)}</td>
                    <td className="px-4 py-2.5">
                      <Badge variant={t.orderStatus === 'TRADED' || t.orderStatus === 'FILLED' || isPaper ? 'green' : 'gray'}>
                        {t.orderStatus || (isPaper ? 'FILLED' : '—')}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5 text-gray-500 text-xs">
                      {t.createTime || t.exchangeTime || (t.timestamp ? new Date(t.timestamp).toLocaleString() : '—')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Orders */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/60 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FileText size={16} className={isPaper ? "text-yellow-400" : "text-purple-400"} />
            <h3 className="text-sm font-semibold text-gray-200">Today's Orders</h3>
            <Badge variant={isPaper ? "yellow" : "purple"}>{isPaper ? "Paper Local DB" : "Dhan Live"}</Badge>
            <Badge variant="gray">{rawOrderList.length} orders</Badge>
          </div>
        </div>

        {rawOrderList.length === 0 ? (
          <EmptyState message={isPaper ? "No paper orders placed locally today." : "No live orders placed today."} icon={FileText} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-500 text-xs uppercase tracking-wider">
                  {['Order ID', 'Symbol', 'Type', 'Side', 'Qty', 'Price', 'Status'].map(h => (
                    <th key={h} className="px-4 py-3 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rawOrderList.slice(0, 50).map((o, idx) => (
                  <tr key={o.orderId || o.id || idx} className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
                    <td className="px-4 py-2.5 font-mono text-gray-400 text-xs">{o.orderId || o.id || '—'}</td>
                    <td className="px-4 py-2.5 font-mono text-white text-xs">{o.customSymbol || o.tradingSymbol || o.symbol || '—'}</td>
                    <td className="px-4 py-2.5 text-gray-400 text-xs">{o.orderType || o.order_type || '—'}</td>
                    <td className="px-4 py-2.5">
                      <Badge variant={o.transactionType === 'BUY' || o.side === 'BUY' ? 'green' : 'red'}>
                        {o.transactionType || o.side || '—'}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-gray-300">{o.quantity || '—'}</td>
                    <td className="px-4 py-2.5 font-mono text-gray-300">₹{Number(o.price ?? 0).toFixed(2)}</td>
                    <td className="px-4 py-2.5">
                      <Badge variant={
                        o.orderStatus === 'TRADED' || o.orderStatus === 'FILLED' || o.status === 'FILLED' ? 'green' :
                        o.orderStatus === 'PENDING' || o.orderStatus === 'TRANSIT' || o.status === 'PENDING' ? 'yellow' :
                        o.orderStatus === 'CANCELLED' || o.orderStatus === 'REJECTED' || o.status === 'REJECTED' || o.status === 'FAILED' ? 'red' : 'gray'
                      }>
                        {o.orderStatus || o.status || '—'}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
