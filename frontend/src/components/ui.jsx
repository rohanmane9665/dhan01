import React from 'react';

/**
 * Stat card used on Dashboard and Analytics pages.
 */
export function StatCard({ label, value, sub, color = 'blue', icon: Icon }) {
  const colors = {
    blue:   'border-blue-500/30 bg-blue-500/5 text-blue-400',
    green:  'border-green-500/30 bg-green-500/5 text-green-400',
    red:    'border-red-500/30 bg-red-500/5 text-red-400',
    yellow: 'border-yellow-500/30 bg-yellow-500/5 text-yellow-400',
    purple: 'border-purple-500/30 bg-purple-500/5 text-purple-400',
    gray:   'border-gray-700 bg-gray-800/40 text-gray-400',
  };

  return (
    <div className={`rounded-xl border p-5 ${colors[color]}`}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold uppercase tracking-widest opacity-70">{label}</span>
        {Icon && <Icon size={16} className="opacity-50" />}
      </div>
      <div className="text-2xl font-bold font-mono text-white">{value}</div>
      {sub && <div className="mt-1 text-xs opacity-60">{sub}</div>}
    </div>
  );
}

/**
 * Pill / badge component.
 */
export function Badge({ children, variant = 'gray' }) {
  const v = {
    green:  'bg-green-500/20 text-green-400 border-green-500/30',
    red:    'bg-red-500/20 text-red-400 border-red-500/30',
    yellow: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    blue:   'bg-blue-500/20 text-blue-400 border-blue-500/30',
    gray:   'bg-gray-700/50 text-gray-400 border-gray-600/30',
  };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${v[variant]}`}>
      {children}
    </span>
  );
}

/**
 * Loading spinner.
 */
export function Spinner({ size = 20 }) {
  return (
    <div
      style={{ width: size, height: size }}
      className="border-2 border-gray-700 border-t-blue-500 rounded-full animate-spin"
    />
  );
}

/**
 * Empty state placeholder.
 */
export function EmptyState({ message = 'No data available', icon: Icon }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-gray-600">
      {Icon && <Icon size={40} className="mb-3 opacity-40" />}
      <p className="text-sm">{message}</p>
    </div>
  );
}
