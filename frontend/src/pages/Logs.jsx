import React, { useState } from 'react';
import { FileText, RefreshCw } from 'lucide-react';
import { useFetch } from '../hooks/useFetch';
import { getLogs } from '../services/api';
import { Spinner, EmptyState, Badge } from '../components/ui';

const SEV_COLOR = {
  INFO:     'text-gray-400',
  WARNING:  'text-yellow-400',
  ERROR:    'text-red-400',
  CRITICAL: 'text-red-500 font-bold',
  DEBUG:    'text-gray-600',
};

const SEV_BADGE = {
  INFO:     'gray',
  WARNING:  'yellow',
  ERROR:    'red',
  CRITICAL: 'red',
  DEBUG:    'gray',
};

export default function Logs() {
  const [filter, setFilter] = useState('ALL');
  const { data, loading, refetch } = useFetch(getLogs, 5000);

  const entries = data?.entries ?? [];
  const filtered = filter === 'ALL' ? entries : entries.filter(e => e.severity === filter);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">System Logs</h2>
          <p className="text-xs text-gray-500 mt-0.5">Real-time log stream from trading.log</p>
        </div>
        <button onClick={refetch} className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 border border-gray-700 hover:border-gray-600 rounded-lg px-3 py-2 transition-colors">
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {/* Severity filter */}
      <div className="flex gap-2 flex-wrap">
        {['ALL', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map(s => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`px-3 py-1 text-xs font-medium rounded-full border transition-colors ${
              filter === s
                ? 'bg-blue-500/20 text-blue-400 border-blue-500/40'
                : 'text-gray-500 border-gray-700 hover:text-gray-300 hover:border-gray-600'
            }`}
          >
            {s}
          </button>
        ))}
        <span className="ml-auto text-xs text-gray-600 self-center">{filtered.length} entries</span>
      </div>

      {/* Log table */}
      <div className="rounded-xl border border-gray-800 bg-gray-950 overflow-hidden font-mono text-xs">
        {loading && !data ? (
          <div className="p-6 flex items-center gap-3 text-gray-500"><Spinner /> Loading logs...</div>
        ) : filtered.length === 0 ? (
          <EmptyState message="No log entries found." icon={FileText} />
        ) : (
          <div className="overflow-y-auto max-h-[60vh] divide-y divide-gray-800/40">
            {filtered.map((e) => (
              <div key={e.id} className="flex gap-3 px-4 py-2 hover:bg-gray-800/20">
                <span className="text-gray-600 shrink-0 w-40">{e.timestamp}</span>
                <Badge variant={SEV_BADGE[e.severity] || 'gray'}>{e.severity}</Badge>
                <span className={`flex-1 ${SEV_COLOR[e.severity] || 'text-gray-400'}`}>{e.message}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
