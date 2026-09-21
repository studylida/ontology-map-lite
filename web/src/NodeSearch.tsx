import { useCallback, useEffect, useId, useRef, useState } from "react";
import styles from "./App.module.css";
import { APIRequestError, fetchNodeSearch, type SearchCandidate } from "./data";

type SearchStatus = "idle" | "loading" | "results" | "empty" | "error";

function SearchOption({
  candidate,
  active,
  optionId,
  onSelect,
}: {
  candidate: SearchCandidate;
  active: boolean;
  optionId: string;
  onSelect: () => void;
}) {
  return (
    <button
      id={optionId}
      type="button"
      role="option"
      aria-selected={active}
      className={styles.searchCandidate}
      onMouseDown={(event) => event.preventDefault()}
      onClick={onSelect}
    >
      <span className={styles.candidateMain}>
        <strong>{candidate.name}</strong>
        <span>{candidate.kind}</span>
      </span>
    </button>
  );
}

export function NodeSearch({
  onSelect,
}: {
  onSelect: (nodeId: string) => void;
}) {
  const listId = useId();
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<SearchCandidate[]>([]);
  const [status, setStatus] = useState<SearchStatus>("idle");
  const [error, setError] = useState<APIRequestError | null>(null);
  const [open, setOpen] = useState(false);
  const [activeCandidate, setActiveCandidate] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const selectedNameRef = useRef<string | null>(null);

  const search = useCallback(async (trimmedQuery: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    const requestId = ++requestIdRef.current;
    abortRef.current = controller;
    setStatus("loading");
    setError(null);
    setOpen(true);

    try {
      const nextCandidates = await fetchNodeSearch(
        trimmedQuery,
        controller.signal,
      );
      if (controller.signal.aborted || requestId !== requestIdRef.current)
        return;
      setCandidates(nextCandidates);
      setActiveCandidate(0);
      setStatus(nextCandidates.length ? "results" : "empty");
    } catch (caught) {
      if (controller.signal.aborted || requestId !== requestIdRef.current)
        return;
      setCandidates([]);
      setError(
        caught instanceof APIRequestError
          ? caught
          : new APIRequestError("NETWORK_ERROR", 0, true),
      );
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    if (selectedNameRef.current !== null) {
      const selectedName = selectedNameRef.current;
      selectedNameRef.current = null;
      if (selectedName === query) return;
    }
    const trimmedQuery = query.trim();
    if (!trimmedQuery) return;
    const timeout = window.setTimeout(() => void search(trimmedQuery), 250);
    return () => window.clearTimeout(timeout);
  }, [query, search]);

  useEffect(
    () => () => {
      requestIdRef.current += 1;
      abortRef.current?.abort();
    },
    [],
  );

  const changeQuery = (nextQuery: string) => {
    requestIdRef.current += 1;
    abortRef.current?.abort();
    setQuery(nextQuery);
    setCandidates([]);
    setError(null);
    setActiveCandidate(0);
    if (nextQuery.trim()) {
      setStatus("loading");
      setOpen(true);
    } else {
      setStatus("idle");
      setOpen(false);
    }
  };

  const chooseCandidate = (candidate: SearchCandidate) => {
    requestIdRef.current += 1;
    abortRef.current?.abort();
    selectedNameRef.current = candidate.name;
    setQuery(candidate.name);
    setCandidates([]);
    setStatus("idle");
    setOpen(false);
    onSelect(candidate.nodeId);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!candidates.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveCandidate((current) => (current + 1) % candidates.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActiveCandidate(
        (current) => (current - 1 + candidates.length) % candidates.length,
      );
    } else if (event.key === "Enter" && open) {
      event.preventDefault();
      const candidate = candidates[activeCandidate];
      if (candidate) chooseCandidate(candidate);
    }
  };

  return (
    <div className={styles.search}>
      <div className={styles.searchBox}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m15.5 15.5 5 5" />
        </svg>
        <input
          id="node-search"
          type="search"
          role="combobox"
          aria-label="대상 검색"
          aria-autocomplete="list"
          aria-controls={listId}
          aria-expanded={open}
          aria-activedescendant={
            open && candidates[activeCandidate]
              ? `${listId}-${candidates[activeCandidate].nodeId}`
              : undefined
          }
          value={query}
          placeholder="회사, 사람 또는 기술 검색"
          onFocus={() => {
            if (status !== "idle") setOpen(true);
          }}
          onBlur={() => setOpen(false)}
          onChange={(event) => changeQuery(event.target.value)}
          onKeyDown={handleKeyDown}
        />
      </div>
      {open && status === "results" && (
        <div id={listId} role="listbox" className={styles.searchResults}>
          {candidates.map((candidate, index) => (
            <SearchOption
              key={candidate.nodeId}
              optionId={`${listId}-${candidate.nodeId}`}
              candidate={candidate}
              active={index === activeCandidate}
              onSelect={() => chooseCandidate(candidate)}
            />
          ))}
        </div>
      )}
      {open && status === "loading" && (
        <div id={listId} className={styles.searchResults} role="status">
          검색 결과를 불러오는 중입니다.
        </div>
      )}
      {open && status === "empty" && (
        <div id={listId} className={styles.searchResults} role="status">
          검색 결과가 없습니다.
        </div>
      )}
      {open && status === "error" && (
        <div id={listId} className={styles.searchResults} role="alert">
          <strong>검색 결과를 불러오지 못했습니다.</strong>
          {error?.retryable && (
            <button
              type="button"
              className={styles.searchCandidate}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => void search(query.trim())}
            >
              다시 시도
            </button>
          )}
        </div>
      )}
    </div>
  );
}
