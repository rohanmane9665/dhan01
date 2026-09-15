import React, { useState, useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Activity, BarChart2, Shield, Settings, List, TrendingUp, AlertTriangle, Cpu, FileText, LogOut } from 'lucide-react';
import { getStatus, setMode, triggerKill, resetKill } from '../services/api';
import { useAuth } from '../context/AuthContext';

const SidebarItem = ({ icon: Icon, label, path, active }) => (
  <Link to={path} className={`flex items-center gap-3 px-4 py-3 cursor-pointer rounded-lg mb-1 transition-colors ${active ? 'bg-blue-600/20 text-blue-400' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'}`}>
    <Icon size={20} />
    <span className="font-medium">{label}</span>
  </Link>
);

const DashboardLayout = ({ children }) => {
  const location = useLocation();
  const currentPath = location.pathname;
  const { logout } = useAuth();
  
  const [tradingMode, setTradingMode] = useState('PAPER');
  const [systemStatus, setSystemStatus] = useState('LOADING');

  useEffect(() => {
    fetchStatus();
    // Poll status every 5 seconds as a fallback to SSE
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchStatus = async () => {
    try {
      const data = await getStatus();
      setTradingMode(data.mode);
      setSystemStatus(data.status);
    } catch (err) {
      console.error("Failed to fetch status:", err);
    }
  };

  const handleToggleMode = async () => {
    const newMode = tradingMode === 'PAPER' ? 'LIVE' : 'PAPER';
    
    if (newMode === 'LIVE') {
      const confirmLive = window.confirm(
        "WARNING: You are about to enable LIVE TRADING.\n\n" +
        "Real money will be used for execution.\nAre you absolutely sure?"
      );
      if (!confirmLive) return;
    }
    
    try {
      await setMode(newMode);
      setTradingMode(newMode);
    } catch (err) {
      alert("Failed to change trading mode.");
    }
  };

  const handleKillSwitch = async () => {
    const confirmKill = window.confirm("🚨 ACTIVATE EMERGENCY KILL SWITCH? 🚨\n\nThis will immediately halt all new trades!");
    if (!confirmKill) return;
    
    try {
      await triggerKill();
      setSystemStatus('KILLED');
      alert("KILL SWITCH ACTIVATED.");
    } catch (err) {
      alert("Failed to activate kill switch.");
    }
  };
  
  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <div className="w-64 bg-gray-900 border-r border-gray-800 flex flex-col">
        <div className="p-6">
          <h1 className="text-xl font-bold bg-gradient-to-r from-blue-400 to-indigo-400 bg-clip-text text-transparent">
            Dhan Algo
          </h1>
        </div>
        
        <div className="flex-1 overflow-y-auto px-4 py-2">
          <SidebarItem icon={Activity}    label="Dashboard"    path="/"             active={currentPath === '/'} />
          <SidebarItem icon={TrendingUp}  label="Live Trading"  path="/live-trading" active={currentPath === '/live-trading'} />
          <SidebarItem icon={BarChart2}   label="P&L Analytics" path="/analytics"    active={currentPath === '/analytics'} />
          <SidebarItem icon={Settings}    label="Strategy"      path="/strategy"     active={currentPath === '/strategy'} />
          <SidebarItem icon={Shield}      label="Risk Controls" path="/risk"         active={currentPath === '/risk'} />
          <SidebarItem icon={Cpu}         label="AI Insights"   path="/ai-insights"  active={currentPath === '/ai-insights'} />
          <SidebarItem icon={FileText}    label="Logs"          path="/logs"         active={currentPath === '/logs'} />
        </div>
        
        <div className="p-4 border-t border-gray-800 space-y-2">
           <button 
             onClick={handleKillSwitch}
             className="w-full py-2 bg-red-500/10 text-red-500 border border-red-500/20 rounded hover:bg-red-500/20 font-bold flex items-center justify-center gap-2 transition-colors">
              <Shield size={18} />
              KILL SWITCH
           </button>
           <button 
             onClick={logout}
             className="w-full py-2 text-gray-400 hover:text-white border border-transparent hover:border-gray-700 rounded hover:bg-gray-800 flex items-center justify-center gap-2 transition-colors">
              <LogOut size={18} />
              Log Out
           </button>
        </div>
      </div>
      
      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden bg-gray-950">
        <header className="h-16 border-b border-gray-800 flex items-center justify-between px-6 bg-gray-900/50 backdrop-blur-sm">
          <div className="flex items-center gap-4">
             <h2 className="text-lg font-semibold text-gray-200">Terminal</h2>
             {systemStatus === 'KILLED' && (
                 <span className="px-3 py-1 bg-red-500/20 text-red-400 text-xs font-bold rounded-full border border-red-500/30 animate-pulse">
                   EMERGENCY STOP ACTIVE
                 </span>
             )}
          </div>
          <div className="flex items-center gap-4">
            <button 
               onClick={handleToggleMode}
               className={`px-4 py-1 text-xs font-bold rounded-full border transition-all ${tradingMode === 'LIVE' ? 'bg-red-500/20 text-red-500 border-red-500/50 animate-pulse shadow-[0_0_10px_rgba(239,68,68,0.5)]' : 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20 hover:bg-yellow-500/20'}`}
            >
               {tradingMode} TRADING
            </button>
            <span className={`w-2 h-2 rounded-full ${systemStatus === 'KILLED' ? 'bg-red-500' : 'bg-green-500 shadow-[0_0_8px_#22c55e] animate-pulse'}`}></span>
          </div>
        </header>
        
        <main className="flex-1 overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  );
};

export default DashboardLayout;
