import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import styles from "./App.module.css";
import { DetailPanel } from "./DetailPanel";
import {
  APIRequestError,
  type ExplorationView,
  type KnowledgeNode,
  type TimeRange,
  timeRangeLabel,
} from "./data";
import {
  GraphCanvas,
  type GraphFocusRequest,
  type GraphOverviewRequest,
} from "./GraphCanvas";
import { NodeSearch } from "./NodeSearch";
import {
  EvidenceDialog,
  type EvidenceSelection,
  PageNotice,
  useModalDialog,
} from "./RelationPanel";
import { TopicPanel } from "./TopicPanel";
import { TopicPicker } from "./TopicPicker";
import { fetchCenterExploration, isTopicExploration } from "./topicData";
import { useInitialLoading } from "./useInitialLoading";
import { usePeripheral } from "./usePeripheral";

interface LocationState {
  centerId: string | null;
  range: TimeRange;
}

interface Navigation {
  trailIndex: number | null;
  historyMode: "push" | "none";
}

interface ExplorationRequest {
  centerId: string;
  range: TimeRange;
  navigation: Navigation | null;
  panelTab?: 0 | 1 | 2;
  retry?: boolean;
}

interface PendingTransition {
  view: ExplorationView;
  request: ExplorationRequest;
}

type LoadStatus = "idle" | "loading" | "start" | "empty" | "error";

const maxTrailLength = 4;
const sourceFileAccept =
  ".txt,.md,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function appendTrail(trail: string[], nodeId: string): string[] {
  if (trail.at(-1) === nodeId) return trail;
  return [...trail, nodeId].slice(-maxTrailLength);
}

function readConfiguredCenter(): string | null {
  return import.meta.env.VITE_DEFAULT_CENTER_NODE_ID?.trim() || null;
}

function readLocation(): LocationState {
  const params = new URLSearchParams(window.location.search);
  return {
    centerId: params.get("center") || readConfiguredCenter(),
    range:
      params.get("range") === "1y" || params.get("range") === "all"
        ? (params.get("range") as "1y" | "all")
        : "90d",
  };
}

function writeLocation(
  centerId: string,
  range: TimeRange,
  mode: "push" | "replace",
) {
  const params = new URLSearchParams({ center: centerId, range });
  window.history[`${mode}State`](
    {},
    "",
    `${window.location.pathname}?${params.toString()}`,
  );
}

function errorCopy(error: APIRequestError | null): {
  title: string;
  detail: string;
} {
  if (
    error?.code === "NODE_NOT_FOUND" ||
    error?.code === "TOPIC_NOT_FOUND" ||
    error?.status === 404
  ) {
    return {
      title: "요청한 대상을 찾을 수 없습니다.",
      detail: "다른 대상을 검색하거나 주제를 선택해 주세요.",
    };
  }
  if (error?.code === "INVALID_REQUEST" || error?.status === 422) {
    return {
      title:
        "요청을 확인할 수 없습니다. 다른 대상을 검색하거나 주제를 선택해 주세요.",
      detail: "",
    };
  }
  if (error?.code === "PUBLICATION_NOT_READY" || error?.status === 503) {
    return {
      title: "현재 이 대상의 공개 탐색 자료를 불러올 수 없습니다.",
      detail: "다른 대상을 검색하거나 주제를 선택할 수 있습니다.",
    };
  }
  return {
    title:
      "탐색 데이터를 불러오지 못했습니다. 네트워크 연결을 확인한 뒤 다시 시도해 주세요.",
    detail: "",
  };
}

function TrailHeader({
  trail,
  currentId,
  nodes,
  onHome,
  onSelect,
  children,
}: {
  trail: string[];
  currentId: string | undefined;
  nodes: Map<string, KnowledgeNode>;
  onHome: () => void;
  onSelect: (nodeId: string, trailIndex: number) => void;
  children?: ReactNode;
}) {
  return (
    <header className={styles.header}>
      <button
        type="button"
        className={styles.brand}
        aria-label="비스텔리젼스 홈으로 이동"
        onClick={onHome}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="6" cy="7" r="3" />
          <circle cx="17.5" cy="16.5" r="2" />
          <path d="M8.5 9.2 16 15" />
        </svg>
        <strong>ontology-map</strong>
      </button>
      <nav className={styles.trail} aria-label="최근 탐색 경로">
        <ol>
          {trail.map((trailId, index) => {
            const trailNode = nodes.get(trailId);
            const current = currentId === trailId && index === trail.length - 1;
            return (
              // biome-ignore lint/suspicious/noArrayIndexKey: 같은 node가 경로에 반복될 수 있고 항목 내부 상태가 없습니다.
              <li className={styles.trailItem} key={`${trailId}-${index}`}>
                {index > 0 && <span className={styles.trailSeparator}>/</span>}
                {current ? (
                  <span aria-current="page">{trailNode?.name ?? trailId}</span>
                ) : (
                  <button
                    type="button"
                    aria-label={`탐색 경로에서 ${trailNode?.name ?? trailId} 선택`}
                    onClick={() => onSelect(trailId, index)}
                  >
                    {trailNode?.name ?? trailId}
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      </nav>
      {children}
    </header>
  );
}

function sourceProcessingLabel(status: string, stage?: string): string {
  if (status === "READY") return "자료가 지도에 공개되었습니다.";
  if (status === "FAILED") return "자료 처리에 실패했습니다.";
  if (status === "INTERRUPTED") return "자료 처리가 중단되었습니다.";
  if (status === "EXCLUDED_LANGUAGE")
    return "현재 처리할 수 없는 언어의 자료입니다.";
  if (status === "RUNNING")
    return stage ? `자료 처리 중 · ${stage}` : "자료 처리 중";
  return "자료 처리 대기 중";
}

interface JevValidationResult {
  status: "PASSED" | "BLOCKED";
  reason: string | null;
  model: string;
  policy_version: string;
  body_hash: string;
  drop_threshold: number;
  injection_threshold: number;
  spans_total: number;
  spans_kept: number;
  spans_dropped: number;
  calls: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  elapsed_seconds: number;
  promotion_started: false;
  decisions: {
    source_id: string;
    text: string;
    drop_probability: number;
    decision: "KEEP" | "DROP";
  }[];
}

export function JevValidationDialog({ onClose }: { onClose: () => void }) {
  const dialogRef = useModalDialog(onClose);
  const inputId = useId();
  const titleId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [requestState, setRequestState] = useState<
    | { kind: "idle" }
    | { kind: "submitting" }
    | { kind: "error"; message: string }
    | { kind: "success"; result: JevValidationResult }
  >({ kind: "idle" });

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file) return;
    setRequestState({ kind: "submitting" });
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await fetch("/api/v1/jev-validation", {
        method: "POST",
        body,
      });
      const payload = (await response.json()) as JevValidationResult & {
        error?: { code?: string };
      };
      if (!response.ok) {
        throw new Error(
          payload.error?.code === "JEV_CREDENTIAL_MISSING"
            ? "서버에 JEV API 키가 설정되지 않았습니다."
            : payload.error?.code || "JEV 검증을 완료하지 못했습니다.",
        );
      }
      setRequestState({ kind: "success", result: payload });
    } catch (error) {
      setRequestState({
        kind: "error",
        message:
          error instanceof Error
            ? error.message
            : "JEV 검증을 완료하지 못했습니다.",
      });
    }
  };

  const result = requestState.kind === "success" ? requestState.result : null;

  return (
    <dialog
      ref={dialogRef}
      className={`${styles.sourceDialog} ${styles.jevDialog}`}
      aria-labelledby={titleId}
      onCancel={onClose}
    >
      <form onSubmit={submit}>
        <button
          type="button"
          className={styles.dialogClose}
          aria-label="JEV 검증 창 닫기"
          onClick={onClose}
        >
          ×
        </button>
        <p className={styles.dialogEyebrow}>PRE-PROMOTION SAFETY GATE</p>
        <h2 id={titleId}>JEV 검증</h2>
        <p className={styles.sourceDialogCopy}>
          원문에서 필요한 span만 고르고, prompt injection 공격은 OpenAI에 보내기
          전에 차단합니다.
        </p>
        <ol className={styles.jevFlow} aria-label="JEV 검증 범위">
          <li>본문 추출·정규화</li>
          <li>JEV 관련성·공격 판정</li>
          <li>여기서 중단</li>
        </ol>
        <p className={styles.jevBoundary}>
          DB 저장 · OpenAI 호출 · promotion · publication 없음
        </p>
        <label className={styles.sourceDropzone} htmlFor={inputId}>
          <p>원문 파일을 선택하세요.</p>
          <span className={styles.sourceFileButton}>파일 선택</span>
          <input
            id={inputId}
            className={styles.sourceFileInput}
            aria-label="JEV 검증 파일"
            type="file"
            accept={sourceFileAccept}
            onChange={(event) => {
              const selected = event.target.files?.[0];
              if (selected) {
                setFile(selected);
                setRequestState({ kind: "idle" });
              }
              event.currentTarget.value = "";
            }}
          />
          {file && (
            <p className={styles.sourceFileName} role="status">
              선택한 파일: {file.name}
            </p>
          )}
        </label>
        {requestState.kind === "error" && (
          <p className={styles.sourceRequestError} role="alert">
            {requestState.message}
          </p>
        )}
        {result && (
          <section
            className={styles.jevResult}
            data-status={result.status}
            aria-label="JEV 검증 결과"
          >
            <div className={styles.jevResultHeading}>
              <strong>
                {result.status === "PASSED"
                  ? "검증 통과"
                  : "Prompt injection 차단"}
              </strong>
              <span>promotion 미진입</span>
            </div>
            <p role="status">
              {result.status === "PASSED"
                ? `${result.spans_total}개 span 중 ${result.spans_kept}개만 다음 단계 입력으로 선택했습니다.`
                : `공격 확률이 ${Math.round(result.injection_threshold * 100)}% 이상이라 OpenAI 전송 전에 중단했습니다.`}
            </p>
            <dl className={styles.jevStats}>
              <div>
                <dt>모델</dt>
                <dd>{result.model}</dd>
              </div>
              <div>
                <dt>소요 시간</dt>
                <dd>{result.elapsed_seconds.toFixed(2)}초</dd>
              </div>
              <div>
                <dt>선택 / 제외</dt>
                <dd>
                  {result.spans_kept} / {result.spans_dropped}
                </dd>
              </div>
              <div>
                <dt>JEV 호출</dt>
                <dd>{result.calls ?? "차단"}</dd>
              </div>
              <div>
                <dt>입력 / 출력 토큰</dt>
                <dd>
                  {result.input_tokens ?? "-"} / {result.output_tokens ?? "-"}
                </dd>
              </div>
            </dl>
            {result.decisions.length > 0 && (
              <details className={styles.jevDecisions}>
                <summary>span별 판정 보기</summary>
                <ul>
                  {result.decisions.map((decision) => (
                    <li
                      key={decision.source_id}
                      data-decision={decision.decision}
                    >
                      <span>
                        {decision.decision === "KEEP" ? "선택" : "제외"} · 제외
                        확률 {Math.round(decision.drop_probability * 100)}%
                      </span>
                      <p>{decision.text}</p>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <small>본문 SHA-256 · {result.body_hash}</small>
          </section>
        )}
        <section className={styles.jevBenefits} aria-label="JEV 적용 효과">
          <strong>왜 JEV인가</strong>
          <ul>
            <li>자유 생성이 아닌 정형 확률 판정</li>
            <li>불필요한 문구를 빼 토큰과 호출 비용 절감</li>
            <li>외부 문서의 공격 지시를 생성 모델보다 먼저 차단</li>
          </ul>
        </section>
        <div className={styles.sourceDialogActions}>
          <button type="button" onClick={onClose}>
            닫기
          </button>
          <button
            type="submit"
            disabled={!file || requestState.kind === "submitting"}
          >
            {requestState.kind === "submitting" ? "검증 중…" : "JEV 검증 실행"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

export function SourceAddDialog({ onClose }: { onClose: () => void }) {
  const dialogRef = useModalDialog(onClose);
  const inputId = useId();
  const titleId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [publisherName, setPublisherName] = useState("");
  const [originalLanguage, setOriginalLanguage] = useState("ko");
  const [canonicalUrl, setCanonicalUrl] = useState("");
  const [publishedAt, setPublishedAt] = useState("");
  const [dragging, setDragging] = useState(false);
  const [requestState, setRequestState] = useState<
    | { kind: "idle" }
    | { kind: "submitting" }
    | { kind: "error"; message: string }
    | {
        kind: "success";
        sourceDocumentId: string;
        processingJobId: string;
        bodyHash: string;
        normalizedBody: string;
        status: string;
        stage: string | undefined;
        elapsedSeconds: number | undefined;
        errorCode: string | undefined;
        pollingError: boolean;
        pollingStopped: boolean;
      }
  >({ kind: "idle" });

  const sourceDocumentId =
    requestState.kind === "success" ? requestState.sourceDocumentId : null;
  const processingJobId =
    requestState.kind === "success" ? requestState.processingJobId : null;
  const pollingStopped =
    requestState.kind === "success" && requestState.pollingStopped;

  useEffect(() => {
    if (!sourceDocumentId || !processingJobId || pollingStopped) return;
    let cancelled = false;
    let timer: number | undefined;
    let failures = 0;
    const terminal = new Set([
      "READY",
      "FAILED",
      "INTERRUPTED",
      "EXCLUDED_LANGUAGE",
    ]);

    const poll = async () => {
      let shouldContinue = true;
      try {
        const response = await fetch(
          `/api/v1/source-intake/${sourceDocumentId}?processing_job_id=${processingJobId}`,
        );
        const payload = (await response.json()) as {
          status?: string;
          stage?: string;
          elapsed_seconds?: number;
          error_code?: string;
        };
        if (!response.ok) throw new Error("처리 상태를 확인하지 못했습니다.");
        if (cancelled) return;
        failures = 0;
        const status = payload.status || "QUEUED";
        shouldContinue = !terminal.has(status);
        setRequestState((current) =>
          current.kind === "success" &&
          current.sourceDocumentId === sourceDocumentId
            ? {
                ...current,
                status,
                stage: payload.stage,
                elapsedSeconds: payload.elapsed_seconds,
                errorCode: payload.error_code,
                pollingError: false,
              }
            : current,
        );
      } catch {
        if (cancelled) return;
        failures += 1;
        const stopped = failures >= 3;
        shouldContinue = !stopped;
        setRequestState((current) =>
          current.kind === "success" &&
          current.sourceDocumentId === sourceDocumentId
            ? { ...current, pollingError: true, pollingStopped: stopped }
            : current,
        );
      }
      if (shouldContinue && !cancelled) timer = window.setTimeout(poll, 1000);
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [sourceDocumentId, processingJobId, pollingStopped]);

  const selectFile = (file: File | undefined) => {
    if (!file) return;
    setFile(file);
    setRequestState({ kind: "idle" });
    if (!title) setTitle(file.name.replace(/\.[^.]+$/, ""));
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file) {
      setRequestState({
        kind: "error",
        message: "먼저 자료 파일을 선택해 주세요.",
      });
      return;
    }
    setRequestState({ kind: "submitting" });
    const body = new FormData();
    body.append("file", file);
    if (title.trim()) body.append("title", title.trim());
    if (publisherName.trim())
      body.append("publisher_name", publisherName.trim());
    if (originalLanguage.trim()) {
      body.append("original_language", originalLanguage.trim());
    }
    if (canonicalUrl.trim()) body.append("canonical_url", canonicalUrl.trim());
    if (publishedAt) body.append("published_at", publishedAt);
    try {
      const response = await fetch("/api/v1/source-intake", {
        method: "POST",
        body,
      });
      const payload = (await response.json()) as {
        source_document_id?: string;
        processing_job_id?: string;
        status?: string;
        body_hash?: string;
        normalized_body?: string;
        error?: { code?: string };
      };
      if (!response.ok) {
        throw new Error(payload.error?.code || "자료를 저장하지 못했습니다.");
      }
      if (!payload.source_document_id || !payload.processing_job_id) {
        throw new Error("자료 처리 정보를 받지 못했습니다.");
      }
      setRequestState({
        kind: "success",
        sourceDocumentId: payload.source_document_id,
        processingJobId: payload.processing_job_id,
        bodyHash: payload.body_hash || "",
        normalizedBody: payload.normalized_body || "",
        status: payload.status || "QUEUED",
        stage: undefined,
        elapsedSeconds: undefined,
        errorCode: undefined,
        pollingError: false,
        pollingStopped: false,
      });
    } catch (error) {
      setRequestState({
        kind: "error",
        message:
          error instanceof Error
            ? error.message
            : "자료를 저장하지 못했습니다.",
      });
    }
  };

  return (
    <dialog
      ref={dialogRef}
      className={styles.sourceDialog}
      aria-labelledby={titleId}
      onCancel={onClose}
    >
      <form onSubmit={submit}>
        <button
          type="button"
          className={styles.dialogClose}
          aria-label="자료 추가 창 닫기"
          onClick={onClose}
        >
          ×
        </button>
        <svg
          className={styles.sourceDialogMark}
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <circle cx="6" cy="7" r="3" />
          <circle cx="17.5" cy="16.5" r="2" />
          <path d="M8.5 9.2 16 15" />
        </svg>
        <h2 id={titleId}>자료 추가</h2>
        <p className={styles.sourceDialogCopy}>
          자료를 추가하면 백엔드가 정규화해 저장합니다.
        </p>
        <label
          className={styles.sourceDropzone}
          htmlFor={inputId}
          data-dragging={dragging || undefined}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            selectFile(event.dataTransfer.files[0]);
          }}
        >
          <p>파일을 끌어다 놓거나 선택하세요.</p>
          <span className={styles.sourceFileButton}>파일 선택</span>
          <input
            id={inputId}
            className={styles.sourceFileInput}
            aria-label="자료 파일"
            type="file"
            accept={sourceFileAccept}
            onChange={(event) => {
              selectFile(event.target.files?.[0]);
              event.currentTarget.value = "";
            }}
          />
          {file && (
            <p className={styles.sourceFileName} role="status">
              선택한 파일: {file.name}
            </p>
          )}
        </label>
        <div className={styles.sourceMetadata}>
          <label>
            제목
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label>
            발행처
            <input
              value={publisherName}
              placeholder="사용자 업로드"
              onChange={(event) => setPublisherName(event.target.value)}
            />
          </label>
          <label>
            언어
            <input
              value={originalLanguage}
              onChange={(event) => setOriginalLanguage(event.target.value)}
            />
          </label>
          <label>
            원문 URL (선택)
            <input
              type="url"
              value={canonicalUrl}
              onChange={(event) => setCanonicalUrl(event.target.value)}
            />
          </label>
          <label>
            게시일 (선택)
            <input
              type="date"
              value={publishedAt}
              onChange={(event) => setPublishedAt(event.target.value)}
            />
          </label>
        </div>
        {requestState.kind === "error" && (
          <p className={styles.sourceRequestError} role="alert">
            {requestState.message}
          </p>
        )}
        {requestState.kind === "success" && (
          <section className={styles.sourceResult} aria-label="정규화 결과">
            <strong>정규화 본문을 저장했습니다.</strong>
            <p role="status">
              {sourceProcessingLabel(requestState.status, requestState.stage)}
              {requestState.elapsedSeconds !== undefined &&
                ` · ${requestState.elapsedSeconds}초`}
            </p>
            {requestState.errorCode && <code>{requestState.errorCode}</code>}
            {requestState.pollingError && (
              <small>
                {requestState.pollingStopped
                  ? "처리 상태 확인을 중단했습니다."
                  : "처리 상태를 다시 확인하고 있습니다."}
              </small>
            )}
            {requestState.pollingStopped && (
              <button
                type="button"
                onClick={() =>
                  setRequestState((current) =>
                    current.kind === "success"
                      ? {
                          ...current,
                          pollingError: false,
                          pollingStopped: false,
                        }
                      : current,
                  )
                }
              >
                다시 확인
              </button>
            )}
            <code>{requestState.bodyHash}</code>
            <pre>{requestState.normalizedBody}</pre>
          </section>
        )}
        <div className={styles.sourceDialogActions}>
          <button type="button" onClick={onClose}>
            닫기
          </button>
          <button
            type="submit"
            disabled={!file || requestState.kind === "submitting"}
          >
            {requestState.kind === "submitting" ? "저장 중…" : "자료 저장"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

function MapLegend({
  designPreview,
  open,
  onToggle,
  nodeTypes,
  hiddenKinds,
  onFilter,
}: {
  designPreview: boolean;
  open: boolean;
  onToggle: () => void;
  nodeTypes: Map<string, string>;
  hiddenKinds: readonly string[];
  onFilter: (hidden: string[]) => void;
}) {
  return (
    <aside className={styles.legend} aria-label="지식맵 범례">
      <button
        type="button"
        className={styles.legendToggle}
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className={styles.legendDots} aria-hidden="true">
          {[...nodeTypes].map(([code, name]) => (
            <i
              key={code}
              data-kind={name}
              data-hidden={hiddenKinds.includes(code)}
            />
          ))}
        </span>
        <strong>범례{hiddenKinds.length > 0 && " · 필터 적용 중"}</strong>
        <span aria-hidden="true">{open ? "⌄" : "⌃"}</span>
      </button>
      {open && (
        <div className={styles.legendContent}>
          <h2>노드 유형</h2>
          <div className={styles.nodeTypes}>
            {[...nodeTypes].map(([code, name]) => (
              <button
                type="button"
                key={code}
                data-kind={name}
                aria-pressed={!hiddenKinds.includes(code)}
                onClick={() =>
                  onFilter(
                    hiddenKinds.includes(code)
                      ? hiddenKinds.filter((kind) => kind !== code)
                      : [...hiddenKinds, code],
                  )
                }
              >
                <i aria-hidden="true" />
                {name}
              </button>
            ))}
          </div>
          <div className={styles.legendActions}>
            <button type="button" onClick={() => onFilter([])}>
              전체 표시
            </button>
            <button
              type="button"
              onClick={() => onFilter([...nodeTypes.keys()])}
            >
              전체 해제
            </button>
          </div>
          <div className={styles.legendLine}>
            {designPreview && (
              <span>
                <i className={styles.connectedLine} />
                중심·선택 연결
              </span>
            )}
            <span>
              <i className={styles.line1} />
              근거 1개
            </span>
            <span>
              <i className={styles.line3} />
              근거 3개
            </span>
            <span>
              <i className={styles.line6} />
              근거 6개
            </span>
            <span>
              <i className={styles.conflictLine} />
              충돌 관계
            </span>
          </div>
        </div>
      )}
    </aside>
  );
}

function LoadNotice({
  status,
  hasView,
  error,
  failedRequest,
  currentCenterId,
  currentName,
  currentRange,
  failedTargetName,
  defaultCenterId,
  onRetry,
  onDefault,
}: {
  status: LoadStatus;
  hasView: boolean;
  error: APIRequestError | null;
  failedRequest: ExplorationRequest | null;
  currentCenterId: string | null;
  currentName: string | null;
  currentRange: TimeRange;
  failedTargetName: string | null;
  defaultCenterId: string | null;
  onRetry: () => void;
  onDefault: () => void;
}) {
  const copy = errorCopy(error);
  const canOpenDefault =
    status === "error" &&
    defaultCenterId !== null &&
    failedRequest !== null &&
    failedRequest.centerId !== defaultCenterId;
  const moving =
    hasView &&
    failedRequest !== null &&
    currentCenterId !== null &&
    failedRequest.centerId !== currentCenterId;
  const changingRange =
    hasView &&
    failedRequest !== null &&
    currentCenterId !== null &&
    failedRequest.centerId === currentCenterId &&
    failedRequest.range !== currentRange;
  const actions = (
    <>
      {error?.retryable && (
        <button type="button" onClick={onRetry}>
          다시 조회
        </button>
      )}
      {canOpenDefault && (
        <button type="button" onClick={onDefault}>
          기본 탐색으로 이동
        </button>
      )}
    </>
  );

  if (status === "loading" && hasView) {
    return (
      <div className={styles.requestStatus} role="status">
        선택한 탐색 데이터를 불러오는 중입니다.
      </div>
    );
  }
  if (status === "start" && !hasView) {
    return (
      <div className={styles.fullStatus} role="status">
        <strong>탐색할 대상을 검색하거나 주제를 선택해 주세요.</strong>
      </div>
    );
  }
  if (status === "error" && hasView) {
    return (
      <div className={styles.requestStatus} role="alert">
        <strong>
          {moving
            ? `${failedTargetName ?? "선택한 대상"} 대상을 열 수 없습니다.`
            : copy.title}
        </strong>
        {moving ? (
          <>
            <span>{`현재 ${currentName ?? "열려 있던 대상"} 화면을 계속 표시합니다.`}</span>
            <span>{copy.title}</span>
          </>
        ) : changingRange ? (
          <span>{`현재 ${currentName ?? "열려 있던 대상"}의 ${timeRangeLabel(currentRange)} 화면을 계속 표시합니다.`}</span>
        ) : (
          copy.detail && <span>{copy.detail}</span>
        )}
        {actions}
      </div>
    );
  }
  if (!hasView && (status === "error" || status === "empty")) {
    const empty = status === "empty";
    return (
      <div className={styles.fullStatus} role={empty ? "status" : "alert"}>
        <strong>{empty ? "표시할 탐색 데이터가 없습니다." : copy.title}</strong>
        <span>
          {empty ? "다른 대상을 검색하거나 주제를 선택해 주세요." : copy.detail}
        </span>
        {!empty && actions}
      </div>
    );
  }
  return null;
}

export function App({ designPreview = true }: { designPreview?: boolean }) {
  const initial = useMemo(readLocation, []);
  const defaultCenterId = useMemo(readConfiguredCenter, []);
  const [hiddenKinds, setHiddenKinds] = useState<string[]>([]);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [loadingTip] = useState(() => {
    const tips = [
      "노드를 누르면 그 주제를 중심으로 지도를 탐색할 수 있어요.",
      "빈 공간을 끌어 지도를 움직이고, 휠로 확대하거나 축소해 보세요.",
      "간선을 누르면 두 노드가 연결된 이유와 근거를 볼 수 있어요.",
      "노드에 마우스를 올리면 연결된 관계가 강조돼요.",
    ];
    return tips[Math.floor(Math.random() * tips.length)];
  });
  useEffect(() => {
    if (!designPreview) return;
    document.documentElement.dataset.theme = theme;
    return () => {
      delete document.documentElement.dataset.theme;
    };
  }, [designPreview, theme]);

  const [currentView, setCurrentView] = useState<ExplorationView | null>(null);
  const [graphView, setGraphView] = useState<ExplorationView | null>(null);
  const [timeRange, setTimeRange] = useState(initial.range);
  const [trail, setTrail] = useState<string[]>([]);
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelTab, setPanelTab] = useState<0 | 1 | 2>(0);
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null);
  const [jevValidationOpen, setJevValidationOpen] = useState(false);
  const [sourceAddOpen, setSourceAddOpen] = useState(false);
  const [focusRequest, setFocusRequest] = useState<GraphFocusRequest | null>(
    null,
  );
  const focusSequenceRef = useRef(0);
  const [overviewRequest, setOverviewRequest] =
    useState<GraphOverviewRequest | null>(null);
  const [mapOverviewActive, setMapOverviewActive] = useState(false);
  const overviewSequenceRef = useRef(0);
  const [legendOpen, setLegendOpen] = useState(false);
  const [graphReady, setGraphReady] = useState(false);
  const [introComplete, setIntroComplete] = useState(false);
  const [status, setStatus] = useState<LoadStatus>("loading");
  const [requestError, setRequestError] = useState<APIRequestError | null>(
    null,
  );
  const [announcement, setAnnouncement] = useState(
    "탐색 데이터를 불러오는 중입니다.",
  );
  const abortRef = useRef<AbortController | null>(null);
  const currentViewRef = useRef<ExplorationView | null>(null);
  const lastRequestRef = useRef<ExplorationRequest | null>(null);
  const pendingTransitionRef = useRef<PendingTransition | null>(null);
  const nodeCacheRef = useRef(new Map<string, KnowledgeNode>());

  const cacheView = useCallback((view: ExplorationView) => {
    for (const node of view.nodes) nodeCacheRef.current.set(node.id, node);
    for (const recommendation of view.recommendations) {
      nodeCacheRef.current.set(recommendation.node.id, recommendation.node);
    }
  }, []);

  const commitView = useCallback(
    (view: ExplorationView, request: ExplorationRequest) => {
      currentViewRef.current = view;
      setCurrentView(view);
      setGraphView(view);
      setTimeRange(request.range);
      setPanelTab(request.panelTab ?? 0);
      setPanelOpen(true);
      setEvidence(null);
      setFocusRequest(null);
      setOverviewRequest(null);
      setMapOverviewActive(false);
      const navigation = request.navigation;
      if (navigation) {
        setTrail((current) =>
          navigation.trailIndex === null
            ? appendTrail(current, view.centerId)
            : current.slice(0, navigation.trailIndex + 1),
        );
        if (navigation.historyMode === "push") {
          writeLocation(view.centerId, request.range, "push");
        }
      } else {
        writeLocation(view.centerId, request.range, "replace");
        setTrail((current) => (current.length ? current : [view.centerId]));
      }
      if (request.retry) {
        setAnnouncement("최신 공개 상태로 다시 불러왔습니다.");
      } else if (navigation) {
        setAnnouncement(
          `${nodeCacheRef.current.get(view.centerId)?.name ?? "선택한 대상"} 중심으로 이동했습니다.`,
        );
      } else {
        setAnnouncement(
          `${timeRangeLabel(request.range)} 탐색 데이터를 표시합니다.`,
        );
      }
    },
    [],
  );

  const loadExploration = useCallback(
    async (request: ExplorationRequest) => {
      abortRef.current?.abort();
      pendingTransitionRef.current = null;
      if (currentViewRef.current) setGraphView(currentViewRef.current);
      const controller = new AbortController();
      abortRef.current = controller;
      lastRequestRef.current = request;
      setStatus("loading");
      setRequestError(null);

      try {
        const view = await fetchCenterExploration(
          request.centerId,
          request.range,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        cacheView(view);
        if (!view.nodes.length) {
          setStatus("empty");
          return;
        }
        setStatus("idle");
        const current = currentViewRef.current;
        if (
          request.navigation &&
          current &&
          request.centerId !== current.centerId
        ) {
          pendingTransitionRef.current = { view, request };
          setGraphView(view);
          return;
        }
        commitView(view, request);
      } catch (error) {
        if (controller.signal.aborted) return;
        const normalized =
          error instanceof APIRequestError
            ? error
            : new APIRequestError("NETWORK_ERROR", 0, true);
        setRequestError(normalized);
        setStatus("error");
      }
    },
    [cacheView, commitView],
  );

  useEffect(() => {
    if (initial.centerId) {
      void loadExploration({
        centerId: initial.centerId,
        range: initial.range,
        navigation: null,
      });
    } else {
      setRequestError(null);
      setStatus("start");
      setAnnouncement("탐색할 대상을 검색하거나 주제를 선택해 주세요.");
    }

    const onPopState = () => {
      const location = readLocation();
      if (!location.centerId) {
        abortRef.current?.abort();
        abortRef.current = null;
        pendingTransitionRef.current = null;
        currentViewRef.current = null;
        lastRequestRef.current = null;
        setCurrentView(null);
        setGraphView(null);
        setTimeRange(location.range);
        setTrail([]);
        setEvidence(null);
        setOverviewRequest(null);
        setMapOverviewActive(false);
        setRequestError(null);
        setStatus("start");
        setAnnouncement("탐색할 대상을 검색하거나 주제를 선택해 주세요.");
        return;
      }
      void loadExploration({
        centerId: location.centerId,
        range: location.range,
        navigation: {
          trailIndex: null,
          historyMode: "none",
        },
      });
    };
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("popstate", onPopState);
      abortRef.current?.abort();
    };
  }, [initial, loadExploration]);

  const selectNode = (targetId: string, trailIndex: number | null = null) => {
    void loadExploration({
      centerId: targetId,
      range: timeRange,
      navigation: { trailIndex, historyMode: "push" },
    });
  };

  const selectNodeInsight = (targetId: string) => {
    void loadExploration({
      centerId: targetId,
      range: timeRange,
      navigation: { trailIndex: null, historyMode: "push" },
      panelTab: 2,
    });
  };

  const finishNodeTransition = (completedCenterId: string) => {
    const pending = pendingTransitionRef.current;
    if (!pending || pending.view.centerId !== completedCenterId) return;
    pendingTransitionRef.current = null;
    commitView(pending.view, pending.request);
  };

  const changeRange = (range: TimeRange) => {
    if (!currentView || range === timeRange) return;
    void loadExploration({
      centerId: currentView.centerId,
      range,
      navigation: null,
    });
  };

  const retry = () => {
    const request = lastRequestRef.current;
    if (request) void loadExploration({ ...request, retry: true });
  };

  const openDefault = () => {
    if (!defaultCenterId) return;
    const current = currentViewRef.current;
    if (current?.centerId === defaultCenterId) {
      setRequestError(null);
      setStatus("idle");
      setAnnouncement("기본 탐색 화면을 계속 표시합니다.");
      return;
    }
    void loadExploration({
      centerId: defaultCenterId,
      range: timeRange,
      navigation: current ? { trailIndex: null, historyMode: "push" } : null,
    });
  };

  const currentNode = currentView?.nodes.find(
    (node) => node.id === currentView.centerId,
  );
  const loading = useInitialLoading(
    graphReady,
    status === "error" || status === "empty" || status === "start",
  );
  const initialLoading = loading.phase !== "hidden";
  const peripheral = usePeripheral(
    graphView,
    timeRange,
    status === "idle" &&
      !pendingTransitionRef.current &&
      !isTopicExploration(graphView),
    initialLoading || introComplete,
  );

  const loadedNodes = peripheral.graphView?.nodes ?? [];
  const allNodesFiltered =
    loadedNodes.length > 0 &&
    loadedNodes.every((node) => hiddenKinds.includes(node.kindCode));
  const nodeTypes = new Map([
    ["PERSON", "사람"],
    ["COMPANY", "회사"],
    ["TECHNOLOGY", "기술"],
    ["TOPIC", "주제"],
    ["EVENT", "사건"],
  ]);
  for (const node of peripheral.graphView?.nodes ?? []) {
    nodeTypes.set(node.kindCode, node.kind);
  }
  const failedRequest = status === "error" ? lastRequestRef.current : null;
  const failedTargetName = failedRequest
    ? (nodeCacheRef.current.get(failedRequest.centerId)?.name ?? null)
    : null;

  const locateOnMap = (nodeId: string, relationId?: string) => {
    const map = peripheral.graphView;
    const node = map?.nodes.find((item) => item.id === nodeId);
    const relation = relationId
      ? map?.relations.find((item) => item.id === relationId)
      : undefined;
    if (!map || !node || (relationId && !relation)) {
      setAnnouncement("현재 지도 범위에서는 이 연결을 강조할 수 없습니다.");
      return;
    }
    const visibleNodeIds = relation
      ? [relation.source, relation.target]
      : [nodeId];
    const neededKinds = new Set(
      map.nodes
        .filter((item) => visibleNodeIds.includes(item.id))
        .map((item) => item.kindCode),
    );
    setHiddenKinds((current) =>
      current.filter((kind) => !neededKinds.has(kind)),
    );
    const key = ++focusSequenceRef.current;
    setFocusRequest({
      key,
      nodeIds: visibleNodeIds,
      ...(relationId ? { relationIds: [relationId] } : {}),
    });
    setAnnouncement(
      relation
        ? `지도에서 ${node.name}의 해당 연결을 강조합니다.`
        : `지도에서 ${node.name} 주변 연결을 강조합니다.`,
    );
  };

  const locateManyOnMap = (nodeIds: string[], relationIds: string[]) => {
    const map = peripheral.graphView;
    if (!map) return;
    const validRelations = map.relations.filter((relation) =>
      relationIds.includes(relation.id),
    );
    const visibleNodeIds = [
      ...new Set([
        ...nodeIds.filter((id) => map.nodes.some((node) => node.id === id)),
        ...validRelations.flatMap((relation) => [
          relation.source,
          relation.target,
        ]),
      ]),
    ];
    if (!visibleNodeIds.length) {
      setAnnouncement("현재 지도 범위에서는 추천 대상을 강조할 수 없습니다.");
      return;
    }
    const neededKinds = new Set(
      map.nodes
        .filter((node) => visibleNodeIds.includes(node.id))
        .map((node) => node.kindCode),
    );
    setHiddenKinds((current) =>
      current.filter((kind) => !neededKinds.has(kind)),
    );
    setFocusRequest({
      key: ++focusSequenceRef.current,
      nodeIds: visibleNodeIds,
      relationIds: validRelations.map((relation) => relation.id),
    });
    setAnnouncement(
      `추천 대상 ${nodeIds.length}개와 확인된 연결 경로를 지도에서 강조합니다.`,
    );
  };

  const toggleMapOverview = () => {
    setOverviewRequest({
      key: ++overviewSequenceRef.current,
      action: mapOverviewActive ? "restore" : "show",
    });
  };

  return (
    <>
      <main
        className={styles.app}
        data-design-preview={designPreview || undefined}
        inert={initialLoading ? true : undefined}
      >
        <TrailHeader
          trail={trail}
          currentId={currentView?.centerId}
          nodes={nodeCacheRef.current}
          onHome={openDefault}
          onSelect={selectNode}
        >
          <div className={styles.headerActions}>
            <button
              type="button"
              className={styles.addSourceButton}
              onClick={() => setJevValidationOpen(true)}
            >
              JEV 검증
            </button>
            <button
              type="button"
              className={styles.addSourceButton}
              onClick={() => setSourceAddOpen(true)}
            >
              자료 추가
            </button>
            {designPreview && (
              <div className={styles.themeControl}>
                <button
                  type="button"
                  aria-label="라이트 모드"
                  aria-pressed={theme === "light"}
                  onClick={() =>
                    setTheme((current) =>
                      current === "dark" ? "light" : "dark",
                    )
                  }
                >
                  {theme === "dark" ? "☀ 라이트 모드" : "☾ 다크 모드"}
                </button>
              </div>
            )}
          </div>
        </TrailHeader>
        <section className={styles.workspace}>
          {peripheral.graphView && (
            <GraphCanvas
              designPreview={designPreview}
              theme={theme}
              hiddenKinds={hiddenKinds}
              pendingNodeId={
                status === "loading" && lastRequestRef.current?.navigation
                  ? lastRequestRef.current.centerId
                  : null
              }
              panelOpen={panelOpen}
              focusRequest={focusRequest}
              overviewRequest={overviewRequest}
              onOverviewActiveChange={setMapOverviewActive}
              onIntroComplete={() => setIntroComplete(true)}
              view={peripheral.graphView}
              onPanBoundary={
                isTopicExploration(peripheral.graphView)
                  ? () => undefined
                  : peripheral.trigger
              }
              introStarted={graphReady && !initialLoading}
              introCompleted={introComplete}
              onReady={() => setGraphReady(true)}
              onSelect={selectNode}
              onEvidence={setEvidence}
              onTransitionComplete={finishNodeTransition}
            />
          )}

          {!currentView && status !== "loading" && (
            <div className={styles.controls}>
              <label htmlFor="node-search">대상 검색</label>
              <div className={styles.searchWithTopics}>
                <NodeSearch onSelect={selectNode} />
                <TopicPicker onSelect={selectNode} />
              </div>
            </div>
          )}

          {currentView && currentNode && (
            <>
              <div className={styles.controls}>
                <label htmlFor="node-search">대상 검색</label>
                <div className={styles.searchWithTopics}>
                  <NodeSearch onSelect={selectNode} />
                  <TopicPicker onSelect={selectNode} />
                </div>
                <div className={styles.scopeSummary}>
                  <strong>{currentNode.name} 주변</strong>
                  <button
                    type="button"
                    aria-pressed={mapOverviewActive}
                    onClick={toggleMapOverview}
                  >
                    {mapOverviewActive ? "원래 보기" : "전체 지도 보기"}
                  </button>
                </div>
                <fieldset
                  className={styles.rangeControl}
                  disabled={status === "loading"}
                >
                  <legend>시간 범위</legend>
                  <button
                    type="button"
                    aria-pressed={timeRange === "90d"}
                    onClick={() => changeRange("90d")}
                  >
                    최근 90일
                  </button>
                  <button
                    type="button"
                    aria-pressed={timeRange === "1y"}
                    onClick={() => changeRange("1y")}
                  >
                    최근 1년
                  </button>
                  <button
                    type="button"
                    aria-pressed={timeRange === "all"}
                    onClick={() => changeRange("all")}
                  >
                    전체 기간
                  </button>
                </fieldset>
                {allNodesFiltered && (
                  <div className={styles.filterNotice} role="status">
                    <span>유형 필터로 노드가 숨겨져 있습니다.</span>
                    <button type="button" onClick={() => setHiddenKinds([])}>
                      전체 표시
                    </button>
                  </div>
                )}
              </div>

              <MapLegend
                designPreview={designPreview}
                open={legendOpen}
                nodeTypes={nodeTypes}
                hiddenKinds={hiddenKinds}
                onFilter={setHiddenKinds}
                onToggle={() => setLegendOpen((open) => !open)}
              />

              {panelOpen ? (
                isTopicExploration(currentView) ? (
                  <TopicPanel
                    key={`${currentView.centerId}:${timeRange}`}
                    timeRange={timeRange}
                    view={currentView}
                    onClose={() => setPanelOpen(false)}
                    onSelect={selectNode}
                    onLocate={locateOnMap}
                    onSelectInsight={selectNodeInsight}
                    onEvidence={setEvidence}
                  />
                ) : (
                  <DetailPanel
                    key={`${currentView.centerId}:${timeRange}:${panelTab}`}
                    timeRange={timeRange}
                    view={currentView}
                    initialTab={panelTab}
                    onClose={() => setPanelOpen(false)}
                    onSelect={selectNode}
                    onLocate={locateOnMap}
                    onLocateMany={locateManyOnMap}
                    onEvidence={setEvidence}
                  />
                )
              ) : (
                <button
                  type="button"
                  className={styles.openPanel}
                  onClick={() => setPanelOpen(true)}
                >
                  상세 패널 열기
                </button>
              )}
            </>
          )}

          {!isTopicExploration(peripheral.graphView) &&
            (peripheral.loading ||
              peripheral.error ||
              peripheral.exhausted) && (
              <aside
                className={styles.peripheralStatus}
                aria-label="주변부 조회 상태"
              >
                <PageNotice
                  loading={peripheral.loading}
                  error={peripheral.error}
                  empty={false}
                  additional={Boolean(peripheral.graphView)}
                  retrySuccess={peripheral.retrySuccess}
                  onRetry={peripheral.retry}
                />
                {peripheral.exhausted && !peripheral.error && (
                  <span role="status">추가 주변부 결과가 없습니다.</span>
                )}
              </aside>
            )}
          <LoadNotice
            status={status}
            hasView={Boolean(currentView)}
            error={requestError}
            failedRequest={failedRequest}
            currentCenterId={currentView?.centerId ?? null}
            currentName={currentNode?.name ?? null}
            currentRange={timeRange}
            failedTargetName={failedTargetName}
            defaultCenterId={defaultCenterId}
            onRetry={retry}
            onDefault={openDefault}
          />
        </section>
        <div className={styles.liveRegion} aria-live="polite">
          {announcement}
        </div>
      </main>

      {evidence && (
        <EvidenceDialog
          key={evidence.id}
          selection={evidence}
          onClose={() => setEvidence(null)}
        />
      )}

      {sourceAddOpen && (
        <SourceAddDialog onClose={() => setSourceAddOpen(false)} />
      )}

      {jevValidationOpen && (
        <JevValidationDialog onClose={() => setJevValidationOpen(false)} />
      )}

      {initialLoading && (
        <div
          className={styles.loadingOverlay}
          data-leaving={loading.phase === "leaving"}
          role="status"
          aria-label="탐색 데이터 불러오는 중"
        >
          <div className={styles.loadingContent}>
            <strong>Loading</strong>
            <span>-- {loading.progress}% --</span>
            <div
              className={styles.loadingTrack}
              role="progressbar"
              aria-label="지도 준비"
              aria-valuemin={0}
              aria-valuemax={99}
              aria-valuenow={loading.progress}
            >
              <i style={{ width: `${loading.progress}%` }} />
            </div>
            {designPreview && <p className={styles.loadingTip}>{loadingTip}</p>}
          </div>
        </div>
      )}
    </>
  );
}
