import {
  useCallback,
  useEffect,
  useEffectEvent,
  useId,
  useRef,
  useState,
} from "react";
import styles from "./App.module.css";
import { relationPathLabel, type TimeRange, timeRangeLabel } from "./data";
import {
  type EvidenceSelection,
  PageNotice,
  TraceContent,
} from "./RelationPanel";
import {
  fetchPanelClaims213,
  fetchPanelTraces213,
  type PanelClaim213,
  type PanelTrace213,
} from "./read213";
import { useCursorPage } from "./useCursorPage";

export function PeriodNote({
  range,
  asOf,
}: {
  range: TimeRange;
  asOf?: string;
}) {
  return (
    <p className={styles.panelMeta}>
      {timeRangeLabel(range)}
      {asOf
        ? ` · ${new Date(asOf).toLocaleDateString("ko-KR")} 기준`
        : " · 출처 게시일 기준"}
    </p>
  );
}

function uniqueTraces(traces: PanelTrace213[]): PanelTrace213[] {
  return [
    ...new Map(
      traces.map((trace) => [`${trace.url}\u0000${trace.quote}`, trace]),
    ).values(),
  ];
}

function ClaimTraces({
  nodeId,
  claim,
  range,
}: {
  nodeId: string;
  claim: PanelClaim213;
  range: TimeRange;
}) {
  const fetchPage = useCallback(
    (_id: string, cursor: string | null, signal: AbortSignal) =>
      fetchPanelTraces213(nodeId, claim, range, cursor, signal),
    [nodeId, claim, range],
  );
  const page = useCursorPage(claim.id, fetchPage);
  const traces = uniqueTraces(page.items);
  return (
    <div className={styles.panelTraceList}>
      {traces.map((trace) => (
        <article key={trace.key} className={styles.evidenceEntry}>
          <small>
            {trace.periodRole === "IN_WINDOW"
              ? range === "all"
                ? "등록된 원문"
                : "이 기간에 게시된 원문"
              : trace.periodRole === "BACKGROUND"
                ? "이 기간 이전의 배경 원문"
                : "게시일을 확인할 수 없는 원문"}
          </small>
          <TraceContent trace={trace} claimText={claim.text} />
        </article>
      ))}
      <PageNotice
        {...page}
        empty={!traces.length}
        additional={traces.length > 0}
        onRetry={page.retry}
      />
      {page.nextCursor && (
        <button
          type="button"
          onClick={page.more}
          disabled={page.loading || !!page.error}
        >
          원문 더 보기
        </button>
      )}
    </div>
  );
}

function modalityLabel(claim: PanelClaim213): string | null {
  if (claim.modality === "PLAN_OR_TARGET") return "계획";
  if (claim.modality === "PREDICTION_OR_ESTIMATE") return "추정";
  if (claim.modality === "OPINION_OR_EVALUATION") return "의견";
  return null;
}

function ClaimDetails({
  nodeId,
  claim,
  range,
  onEvidence,
  onLocate,
}: {
  nodeId: string;
  claim: PanelClaim213;
  range: TimeRange;
  onEvidence: (selection: EvidenceSelection) => void;
  onLocate: (nodeId: string, relationId?: string) => void;
}) {
  const relationConnections = claim.connections.filter(
    (connection) => connection.kind === "RELATION" && connection.relation,
  );
  return (
    <>
      <ClaimTraces nodeId={nodeId} claim={claim} range={range} />
      {relationConnections.map((connection) => {
        const relation = connection.relation;
        if (!relation) return null;
        const label = relationPathLabel(
          relation.sourceNode.name,
          relation.displayName,
          relation.targetNode.name,
          relation.directionality,
        );
        return (
          <div
            key={`${connection.kind}:${relation.id}:${relation.stance}`}
            className={styles.relationCard}
          >
            <div className={styles.relationToggle}>
              <span>
                <small>{label}</small>
                <small>
                  {relation.stance === "SUPPORT"
                    ? "연결을 뒷받침"
                    : "연결과 상충"}
                </small>
              </span>
              <span className={styles.relationActions}>
                <button
                  type="button"
                  onClick={() => onLocate(relation.otherNode.id, relation.id)}
                >
                  지도에서 강조
                </button>
                <button
                  type="button"
                  aria-label={`${relation.otherNode.name} ${relation.displayName} 연결 원문 보기`}
                  onClick={() => onEvidence({ id: relation.id, label })}
                >
                  연결 원문
                </button>
              </span>
            </div>
          </div>
        );
      })}
    </>
  );
}

export function ClaimCard({
  nodeId,
  claim,
  range,
  expanded,
  onExpanded,
  onEvidence,
  onLocate,
}: {
  nodeId: string;
  claim: PanelClaim213;
  range: TimeRange;
  expanded?: boolean;
  onExpanded?: (open: boolean) => void;
  onEvidence: (selection: EvidenceSelection) => void;
  onLocate: (nodeId: string, relationId?: string) => void;
}) {
  const [localOpen, setOpen] = useState(false);
  const open = expanded ?? localOpen;
  const id = useId();
  const modality = modalityLabel(claim);
  return (
    <article className={styles.panelClaim}>
      <button
        type="button"
        className={styles.panelDisclosure}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setOpen(!open);
          onExpanded?.(!open);
        }}
      >
        <span>
          {modality && <small>{modality}</small>}
          <span>{claim.text}</span>
        </span>
        <span>{open ? "접기" : "원문 보기"}</span>
      </button>
      <div id={id} hidden={!open}>
        {open && (
          <ClaimDetails
            nodeId={nodeId}
            claim={claim}
            range={range}
            onEvidence={onEvidence}
            onLocate={onLocate}
          />
        )}
      </div>
    </article>
  );
}

export function RecordPeek({
  nodeId,
  claims,
  range,
  onClose,
  onOpenRecords,
  onEvidence,
  onLocate,
}: {
  nodeId: string;
  claims: PanelClaim213[];
  range: TimeRange;
  onClose: () => void;
  onOpenRecords: () => void;
  onEvidence: (selection: EvidenceSelection) => void;
  onLocate: (nodeId: string, relationId?: string) => void;
}) {
  const asideRef = useRef<HTMLElement>(null);
  const titleId = useId();
  const close = useEffectEvent(onClose);
  useEffect(() => {
    const opener = document.activeElement;
    const outside = (event: PointerEvent) => {
      if (!asideRef.current?.contains(event.target as Node)) close();
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", closeOnEscape);
    asideRef.current?.querySelector<HTMLElement>("button")?.focus();
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", closeOnEscape);
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);
  return (
    <aside
      ref={asideRef}
      className={styles.recordPeek}
      aria-labelledby={titleId}
    >
      <header>
        <div>
          <small>질문에 사용한 자료</small>
          <h2 id={titleId}>이 답변을 뒷받침한 기록</h2>
        </div>
        <button type="button" onClick={onClose} aria-label="기록 미리보기 닫기">
          ×
        </button>
      </header>
      <div className={styles.recordPeekBody}>
        <div className={styles.recordPeekContent}>
          {claims.map((claim) => (
            <article key={claim.id} className={styles.peekClaim}>
              {modalityLabel(claim) && <small>{modalityLabel(claim)}</small>}
              <p>{claim.text}</p>
              <ClaimDetails
                nodeId={nodeId}
                claim={claim}
                range={range}
                onEvidence={(selection) => {
                  onClose();
                  onEvidence(selection);
                }}
                onLocate={(targetId, relationId) => {
                  onClose();
                  onLocate(targetId, relationId);
                }}
              />
            </article>
          ))}
        </div>
      </div>
      <footer>
        <button type="button" onClick={onOpenRecords}>
          기록 전체 보기
        </button>
      </footer>
    </aside>
  );
}

export function PanelEvidence({
  nodeId,
  range,
  openSource = (url) => window.location.assign(url),
}: {
  nodeId: string;
  range: TimeRange;
  onEvidence: (selection: EvidenceSelection) => void;
  onLocate: (nodeId: string, relationId?: string) => void;
  openSource?: (url: string) => void;
}) {
  const [opening, setOpening] = useState<string | null>(null);
  const [sourceError, setSourceError] = useState(false);
  const fetchPage = useCallback(
    (id: string, cursor: string | null, signal: AbortSignal) =>
      fetchPanelClaims213(id, range, cursor, signal),
    [range],
  );
  const page = useCursorPage(nodeId, fetchPage);
  const openClaimSource = async (claim: PanelClaim213) => {
    setOpening(claim.id);
    setSourceError(false);
    try {
      const traces = await fetchPanelTraces213(
        nodeId,
        claim,
        range,
        null,
        new AbortController().signal,
      );
      if (!traces.items[0]) throw new Error("SOURCE_NOT_FOUND");
      if (traces.items[0].url === null)
        throw new Error("SOURCE_LINK_UNAVAILABLE");
      openSource(traces.items[0].url);
    } catch {
      setSourceError(true);
    } finally {
      setOpening(null);
    }
  };
  return (
    <section aria-label="확인된 기록">
      <h2>확인된 기록</h2>
      <PeriodNote range={range} />
      <p className={styles.panelMeta}>
        기록을 선택하면 원문 기사로 이동합니다.
      </p>
      {page.items.map((claim) => (
        <article key={claim.id} className={styles.recordRow}>
          <button
            type="button"
            disabled={opening !== null}
            onClick={() => void openClaimSource(claim)}
          >
            <span>
              {modalityLabel(claim) && <small>{modalityLabel(claim)}</small>}
              <span>{claim.text}</span>
            </span>
            <span>{opening === claim.id ? "여는 중…" : "원문 보기"}</span>
          </button>
        </article>
      ))}
      {sourceError && (
        <p role="alert">원문 링크를 열지 못했습니다. 다시 시도해 주세요.</p>
      )}
      <PageNotice
        {...page}
        empty={!page.items.length}
        additional={page.items.length > 0}
        onRetry={page.retry}
      />
      {page.nextCursor && (
        <button
          type="button"
          onClick={page.more}
          disabled={page.loading || !!page.error}
        >
          기록 더 보기
        </button>
      )}
    </section>
  );
}
