import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import DashboardLayout from './layouts/DashboardLayout';
import Dashboard     from './pages/Dashboard';
import LiveTrading   from './pages/LiveTrading';
import Analytics     from './pages/Analytics';
import RiskControls  from './pages/RiskControls';
import Strategy      from './pages/Strategy';
import AIInsights    from './pages/AIInsights';
import Logs          from './pages/Logs';

function App() {
  return (
    <Router>
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <DashboardLayout>
          <Routes>
            <Route path="/"             element={<Dashboard />} />
            <Route path="/live-trading" element={<LiveTrading />} />
            <Route path="/analytics"    element={<Analytics />} />
            <Route path="/risk"         element={<RiskControls />} />
            <Route path="/strategy"     element={<Strategy />} />
            <Route path="/ai-insights"  element={<AIInsights />} />
            <Route path="/logs"         element={<Logs />} />
            <Route path="*"             element={<Navigate to="/" replace />} />
          </Routes>
        </DashboardLayout>
      </div>
    </Router>
  );
}

export default App;
