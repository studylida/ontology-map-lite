import { useCallback, useEffect, useRef, useState } from "react";
import { APIRequestError, type CursorPage } from "./data";

type FetchPage<T> = (
  id: string,
  cursor: string | null,
  signal: AbortSignal,
) => Promise<CursorPage<T>>;

export function useCursorPage<T>(id: string, fetchPage: FetchPage<T>) {
  const [items, setItems] = useState<T[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<APIRequestError | null>(null);
  const [retrySuccess, setRetrySuccess] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const pendingRef = useRef(false);
  const lastCursorRef = useRef<string | null>(null);

  const load = useCallback(
    async (cursor: string | null, retryAttempt = false) => {
      if (pendingRef.current) return;
      pendingRef.current = true;
      setRetrySuccess(false);
      const controller = new AbortController();
      controllerRef.current = controller;
      lastCursorRef.current = cursor;
      setLoading(true);
      setError(null);
      try {
        const page = await fetchPage(id, cursor, controller.signal);
        if (controller.signal.aborted) return;
        setItems((current) =>
          cursor === null ? page.items : [...current, ...page.items],
        );
        setNextCursor(page.nextCursor);
        if (retryAttempt) setRetrySuccess(true);
      } catch (cause) {
        if (!controller.signal.aborted)
          setError(
            cause instanceof APIRequestError
              ? cause
              : new APIRequestError("NETWORK_ERROR", 0, true),
          );
      } finally {
        if (!controller.signal.aborted) {
          pendingRef.current = false;
          setLoading(false);
        }
      }
    },
    [id, fetchPage],
  );

  useEffect(() => {
    setItems([]);
    setNextCursor(null);
    setRetrySuccess(false);
    pendingRef.current = false;
    void load(null);
    return () => {
      controllerRef.current?.abort();
    };
  }, [load]);

  return {
    items,
    loading,
    error,
    retrySuccess,
    nextCursor,
    more: () => {
      if (nextCursor !== null && !error) void load(nextCursor);
    },
    retry: () => {
      void load(lastCursorRef.current, true);
    },
  };
}
