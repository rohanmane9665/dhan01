import React, { useState } from 'react';
import { updateDhanToken } from '../services/api';

const SystemSettings = () => {
  const [token, setToken] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!token.trim()) {
      setError('Token cannot be empty');
      return;
    }

    setLoading(true);
    setMessage('');
    setError('');

    try {
      const response = await updateDhanToken(token);
      setMessage(response.message || 'Token updated successfully');
      setToken('');
    } catch (err) {
      console.error(err);
      setError(err.response?.data?.detail || 'Failed to update token');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      <div>
        <h2 className="text-2xl font-bold bg-gradient-to-r from-blue-400 to-indigo-400 bg-clip-text text-transparent">
          System Settings
        </h2>
        <p className="text-gray-400 mt-1">Configure global platform settings</p>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
        <h3 className="text-lg font-semibold text-gray-200 mb-4">DhanHQ API Configuration</h3>
        <form onSubmit={handleSubmit} className="space-y-4 max-w-2xl">
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              New Access Token (Valid for 24 hours)
            </label>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste your new DhanHQ token here..."
              className="w-full bg-gray-950 border border-gray-800 rounded-lg px-4 py-2 text-gray-200 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </div>

          {message && (
            <div className="p-3 bg-green-500/10 border border-green-500/20 text-green-400 rounded-lg text-sm">
              {message}
            </div>
          )}

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/20 text-red-400 rounded-lg text-sm">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className={`px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition-colors ${loading ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            {loading ? 'Updating...' : 'Update Token'}
          </button>
        </form>
      </div>
    </div>
  );
};

export default SystemSettings;
