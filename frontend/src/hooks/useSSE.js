import { useState, useEffect, useRef } from 'react';

/**
 * useSSE — subscribes to the backend SSE stream and returns the latest parsed payload.
 * Automatically reconnects on error with exponential backoff.
 */
export function useSSE() {
  const [data, setData] = useState(null);
  const [connected, setConnected] = useState(false);
  const retryDelay = useRef(1000);
  const esRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const baseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';
      const es = new EventSource(`${baseUrl}/stream/events`);
      esRef.current = es;

      es.onopen = () => {
        setConnected(true);
        retryDelay.current = 1000;
      };

      es.onmessage = (evt) => {
        try {
          const parsed = JSON.parse(evt.data);
          setData(parsed);
        } catch (_) {}
      };

      es.onerror = () => {
        setConnected(false);
        es.close();
        if (!cancelled) {
          setTimeout(connect, retryDelay.current);
          retryDelay.current = Math.min(retryDelay.current * 2, 15000);
        }
      };
    }

    connect();
    return () => {
      cancelled = true;
      esRef.current?.close();
    };
  }, []);

  return { data, connected };
}
