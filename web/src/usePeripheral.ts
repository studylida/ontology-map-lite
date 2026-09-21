import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  APIRequestError,
  type ExplorationView,
  fetchPeripheral,
  mergeById,
  type PeripheralPage,
  type TimeRange,
} from "./data";

interface Accumulated extends PeripheralPage {
  view: ExplorationView;
  range: TimeRange;
}

export function usePeripheral(
  view: ExplorationView | null,
  range: TimeRange,
  enabled: boolean,
  canPresent = true,
) {
  const [result, setResult] = useState<Accumulated | null>(null);
  const [displayed, setDisplayed] = useState<Accumulated | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<APIRequestError | null>(null);
  const [retrySuccess, setRetrySuccess] = useState(false);
  const nextRef = useRef<string | null | undefined>(undefined);
  const seenRef = useRef(new Set<string | null>());
  const pendingRef = useRef(false);
  const errorRef = useRef<APIRequestError | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: 탐색 응답과 기간이 바뀌면 cursor 소유 범위를 초기화한다.
  useEffect(() => {
    setResult(null);
    setDisplayed(null);
    setError(null);
    setRetrySuccess(false);
    setLoading(false);
    nextRef.current = undefined;
    seenRef.current.clear();
    pendingRef.current = false;
    errorRef.current = null;
    return () => {
      controllerRef.current?.abort();
    };
  }, [view, range]);

  useEffect(() => {
    if (enabled) return;
    controllerRef.current?.abort();
    pendingRef.current = false;
    setLoading(false);
  }, [enabled]);

  const load = useCallback(
    async (retry = false) => {
      if (!view || !enabled || pendingRef.current || nextRef.current === null)
        return;
      if (errorRef.current && !retry) return;
      const cursor = nextRef.current ?? null;
      if (seenRef.current.has(cursor)) return;
      pendingRef.current = true;
      setLoading(true);
      setError(null);
      setRetrySuccess(false);
      errorRef.current = null;
      const controller = new AbortController();
      controllerRef.current = controller;
      try {
        const page = await fetchPeripheral(
          {
            ...view,
            nodes: mergeById(
              result?.view === view && result.range === range
                ? result.nodes
                : [],
              view.nodes,
            ),
          },
          range,
          cursor,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        seenRef.current.add(cursor);
        nextRef.current = page.nextCursor;
        if (retry) setRetrySuccess(true);
        setResult((current) => ({
          ...page,
          view,
          range,
          nodes: mergeById(current?.nodes ?? [], page.nodes),
          relations: mergeById(current?.relations ?? [], page.relations),
        }));
      } catch (cause) {
        if (controller.signal.aborted) return;
        const failure =
          cause instanceof APIRequestError
            ? cause
            : new APIRequestError("NETWORK_ERROR", 0, true);
        errorRef.current = failure;
        setError(failure);
      } finally {
        if (!controller.signal.aborted) {
          pendingRef.current = false;
          setLoading(false);
        }
      }
    },
    [view, range, enabled, result],
  );

  useEffect(() => {
    if (enabled && nextRef.current === undefined) void load();
  }, [enabled, load]);
  useEffect(() => {
    if (canPresent) setDisplayed(result);
  }, [canPresent, result]);

  const graphView = useMemo(() => {
    if (!view || displayed?.view !== view || displayed.range !== range)
      return view;
    return {
      ...view,
      nodes: mergeById(displayed.nodes, view.nodes),
      relations: mergeById(displayed.relations, view.relations),
    };
  }, [view, range, displayed]);
  return {
    graphView,
    loading,
    error,
    retrySuccess,
    exhausted:
      result?.view === view &&
      result?.range === range &&
      result.nextCursor === null,
    trigger: () => {
      void load();
    },
    retry: () => {
      void load(true);
    },
  };
}
