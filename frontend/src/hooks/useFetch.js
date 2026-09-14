import { useState, useEffect, useCallback } from 'react';

/**
 * useFetch — generic hook for polling a fetch function at a given interval.
 * @param {Function} fetchFn  — async function that returns data
 * @param {number}   interval — polling interval in ms (default 3000)
 */
export function useFetch(fetchFn, interval = 3000) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const result = await fetchFn();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e.message || 'Request failed');
    } finally {
      setLoading(false);
    }
  }, [fetchFn]);

  useEffect(() => {
    load();
    const timer = setInterval(load, interval);
    return () => clearInterval(timer);
  }, [load, interval]);

  return { data, loading, error, refetch: load };
}
