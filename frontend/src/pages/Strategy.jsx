import React from 'react';
import { Settings, Info } from 'lucide-react';
import { Badge } from '../components/ui';

const STRATEGY_PARAMS = [
  { group: 'Instrument', params: [
    ['Exchange', 'BSE FNO'],
    ['Underlying', 'SENSEX Index'],
    ['Option Type', 'CE and PE'],
    ['Product', 'INTRADAY'],
  ]},
  { group: 'Signal Logic', params: [
    ['Candle Timeframe', '15 Minutes'],
    ['RSI Period', '14'],
    ['RSI Threshold', '59.99'],
    ['RSI Pattern', 'iloc[-4] < 59.99, iloc[-3] < 59.99, iloc[-2] > 59.99'],
    ['Entry Condition', 'LTP > iloc[-2] High (breakout)'],
    ['Strike Selection', 'ATM = ceil(SENSEX / 100) × 100'],
  ]},
  { group: 'Risk Management', params: [
    ['Initial Stop Loss', 'iloc[-2] Low of option candle'],
    ['Trailing SL Trigger', 'Entry + (iloc[-2] High − iloc[-2] Low)'],
    ['Trailing SL Target', 'iloc[-3] Low of option candle'],
    ['Signal Gap', '15 minutes between signals'],
  ]},
  { group: 'Trading Hours', params: [
    ['Market Open', '09:15 IST'],
    ['No Fresh Trade After', '14:40 IST'],
    ['Market Close', '15:30 IST'],
  ]},
];

export default function Strategy() {
  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">Strategy Configuration</h2>
          <p className="text-xs text-gray-500 mt-0.5">SENSEX Options 15-Min RSI Breakout — Read-only view</p>
        </div>
        <Badge variant="blue">v2.0.0</Badge>
      </div>

      <div className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-4 flex items-start gap-3">
        <Info size={16} className="text-yellow-400 flex-shrink-0 mt-0.5" />
        <p className="text-xs text-yellow-400/80">
          Strategy parameters are read-only and set in <code className="font-mono">backend/app/strategies/option_rsi.py</code>.
          Changing them requires a code review and server restart to prevent accidental drift.
        </p>
      </div>

      {STRATEGY_PARAMS.map(({ group, params }) => (
        <div key={group} className="rounded-xl border border-gray-800 bg-gray-900/60 overflow-hidden">
          <div className="px-6 py-3 border-b border-gray-800 flex items-center gap-2">
            <Settings size={14} className="text-gray-500" />
            <span className="text-sm font-semibold text-gray-300">{group}</span>
          </div>
          <div className="divide-y divide-gray-800/50">
            {params.map(([key, val]) => (
              <div key={key} className="px-6 py-3 flex items-center justify-between hover:bg-gray-800/20 transition-colors">
                <span className="text-xs text-gray-500">{key}</span>
                <span className="text-xs font-mono text-gray-200 text-right max-w-xs">{val}</span>
              </div>
            ))}
          </div>
        </div>
      ))}

      <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-4 text-xs text-gray-500 space-y-1">
        <p className="font-semibold text-gray-400 mb-2">Architecture Note</p>
        <p>Strategy logic lives in <code className="text-blue-400">OptionRSIStrategy.evaluate()</code> — fully deterministic and stateless.</p>
        <p>It receives completed 15-min candle DataFrames from the <code className="text-blue-400">CandleEngine</code> and emits a typed <code className="text-blue-400">Signal</code> object.</p>
        <p>Signals are evaluated by <code className="text-blue-400">RiskManager</code> before any order is sent to the broker adapter.</p>
      </div>
    </div>
  );
}
