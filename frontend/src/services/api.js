import axios from 'axios';

const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const api = axios.create({
  baseURL: BASE,
  headers: { 'Content-Type': 'application/json' },
  timeout: 8000,
});

// Add a request interceptor to attach the auth token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('api_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
}, (error) => {
  return Promise.reject(error);
});

// ── Trading Control ──────────────────────────────────────────────────────────
export const getStatus      = ()         => api.get('/trading/status').then(r => r.data);
export const setMode        = (mode)     => api.post('/trading/mode', { mode }).then(r => r.data);
export const triggerKill    = ()         => api.post('/trading/kill').then(r => r.data);
export const resetKill      = ()         => api.post('/trading/reset-kill').then(r => r.data);
export const closePosition  = (id)       => api.post(`/trading/close-position/${id}`).then(r => r.data);

// ── Positions ────────────────────────────────────────────────────────────────
export const getPositions   = ()         => api.get('/positions/').then(r => r.data);

// ── Analytics ────────────────────────────────────────────────────────────────
export const getDailyStats  = ()         => api.get('/analytics/daily').then(r => r.data);
export const getHistorical  = ()         => api.get('/analytics/historical').then(r => r.data);
export const getLocalTrades = ()         => api.get('/analytics/local-trades').then(r => r.data);
export const getLocalOrders = ()         => api.get('/analytics/local-orders').then(r => r.data);

// ── Risk ─────────────────────────────────────────────────────────────────────
export const getRisk        = ()         => api.get('/risk/').then(r => r.data);

// ── Logs ─────────────────────────────────────────────────────────────────────
export const getLogs        = (n = 200)  => api.get(`/logs/?limit=${n}`).then(r => r.data);

// ── Health ───────────────────────────────────────────────────────────────────
export const getHealth      = ()         => api.get('/ready').then(r => r.data);

// ── Dhan Broker Data (Dynamic) ──────────────────────────────────────
export const getFundLimits     = ()         => api.get('/dhan/fund-limits').then(r => r.data);
export const getBrokerPositions = ()        => api.get('/dhan/positions').then(r => r.data);
export const getBrokerOrders    = ()        => api.get('/dhan/orders').then(r => r.data);
export const getHoldings        = ()        => api.get('/dhan/holdings').then(r => r.data);
export const getTradeBook       = (orderId) => api.get(`/dhan/trade-book${orderId ? `?order_id=${orderId}` : ''}`).then(r => r.data);
export const getTickerData      = (segment, ids) => api.get(`/dhan/ticker?exchange_segment=${segment}&security_ids=${ids}`).then(r => r.data);
export const getOhlcData        = (segment, ids) => api.get(`/dhan/ohlc?exchange_segment=${segment}&security_ids=${ids}`).then(r => r.data);
export const getQuoteData       = (segment, ids) => api.get(`/dhan/quotes?exchange_segment=${segment}&security_ids=${ids}`).then(r => r.data);
export const getIntradayCandles = (secId, opts = {}) => {
  const params = new URLSearchParams({ security_id: secId, ...opts });
  return api.get(`/dhan/intraday-candles?${params}`).then(r => r.data);
};
export const getHistoricalCandles = (secId, from, to, opts = {}) => {
  const params = new URLSearchParams({ security_id: secId, from_date: from, to_date: to, ...opts });
  return api.get(`/dhan/historical-candles?${params}`).then(r => r.data);
};

export default api;
