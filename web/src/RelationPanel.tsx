import { useEffect, useEffectEvent, useId, useRef } from "react";
import styles from "./App.module.css";
import type { APIRequestError, SourceTrace } from "./data";
import { fetchRelationEvidence213 } from "./read213";
import { useCursorPage } from "./useCursorPage";

export interface EvidenceSelection {
  id: string;
  label: string;
}

export function PageNotice({
  loading,
  error,
  empty,
  onRetry,
  emptyMessage = "현재 공개된 자료가 없습니다.",
  additional = false,
  kind = "panel",
  retrySuccess = false,
}: {
  loading: boolean;
  error: APIRequestError | null;
  empty: boolean;
  onRetry: () => void;
  emptyMessage?: string;
  additional?: boolean;
  kind?: "panel" | "relationTrace";
  retrySuccess?: boolean;
}) {
  if (loading) return <p role="status">불러오는 중입니다.</p>;
  if (error) {
    const message = additional
      ? "추가 자료를 불러올 수 없습니다. 이미 불러온 내용은 계속 볼 수 있습니다."
      : error.status === 404
        ? kind === "relationTrace"
          ? "이 연결을 확인한 원문을 현재 불러올 수 없습니다."
          : "요청한 자료를 찾을 수 없습니다."
        : error.status === 422
          ? "요청을 확인할 수 없습니다."
          : error.code === "PANEL_NOT_READY" || error.status === 503
            ? "현재 이 영역의 공개 자료를 불러올 수 없습니다."
            : "자료를 불러오지 못했습니다. 네트워크 연결을 확인한 뒤 다시 시도해 주세요.";
    return (
      <div role="alert">
        <p>{message}</p>
        {error.retryable && (
          <button type="button" onClick={onRetry}>
            다시 조회
          </button>
        )}
      </div>
    );
  }
  return (
    <>
      {retrySuccess && <p role="status">최신 공개 상태로 다시 불러왔습니다.</p>}
      {empty && (
        <p className={styles.empty} role="status">
          {emptyMessage}
        </p>
      )}
    </>
  );
}

export function publicationLabel(trace: SourceTrace): string {
  if (trace.publishedAt === null || trace.precision === "UNKNOWN")
    return "확인되지 않음";
  if (trace.precision === "INSTANT")
    return new Date(trace.publishedAt).toLocaleString("ko-KR");
  const length = { DAY: 10, MONTH: 7, YEAR: 4 }[trace.precision];
  return trace.publishedAt.slice(0, length);
}

function modalityLabel(modality: string): string | null {
  if (modality === "PLAN_OR_TARGET") return "계획·목표";
  if (modality === "PREDICTION_OR_ESTIMATE") return "예측·추정";
  if (modality === "OPINION_OR_EVALUATION") return "의견·평가";
  return null;
}

export function EvidenceDialog({
  selection,
  onClose,
}: {
  selection: EvidenceSelection;
  onClose: () => void;
}) {
  const dialogRef = useModalDialog(onClose);
  const titleId = useId();
  const page = useCursorPage(selection.id, fetchRelationEvidence213);
  return (
    <dialog
      ref={dialogRef}
      className={styles.evidenceDialog}
      aria-labelledby={titleId}
      onCancel={onClose}
    >
      <header>
        <h2 id={titleId}>연결을 확인한 원문</h2>
        <button type="button" onClick={onClose} aria-label="원문 창 닫기">
          ×
        </button>
      </header>
      <p className={styles.panelMeta}>{selection.label}</p>
      <p className={styles.panelMeta}>
        선택한 기간과 관계없이 공개된 연결 자료입니다.
      </p>
      {page.items.map((trace) => {
        const modality = modalityLabel(trace.modality);
        return (
          <article key={trace.key} className={styles.evidenceEntry}>
            <span>
              {trace.stance === "SUPPORT" ? "연결을 뒷받침" : "연결과 상충"}
            </span>
            {modality && <span>{modality}</span>}
            <TraceContent trace={trace} />
          </article>
        );
      })}
      <PageNotice
        {...page}
        empty={!page.items.length}
        additional={page.items.length > 0}
        kind="relationTrace"
        onRetry={page.retry}
      />
      {page.nextCursor && (
        <button
          type="button"
          disabled={page.loading || Boolean(page.error)}
          onClick={page.more}
        >
          원문 더 보기
        </button>
      )}
    </dialog>
  );
}

export function useModalDialog(onClose: () => void) {
  const close = useEffectEvent(onClose);
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const opener = document.activeElement;
    dialog.showModal();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const stops = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], [tabindex="0"]',
        ),
      ).filter((element) => element.getClientRects().length > 0);
      const edge = event.shiftKey ? stops[0] : stops.at(-1);
      if (document.activeElement !== edge) return;
      event.preventDefault();
      (event.shiftKey ? stops.at(-1) : stops[0])?.focus();
    };
    const onClick = (event: MouseEvent) => {
      if (!dialog || event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        close();
    };
    dialog?.addEventListener("click", onClick);
    dialog.addEventListener("keydown", onKeyDown);
    return () => {
      dialog.removeEventListener("keydown", onKeyDown);
      dialog?.removeEventListener("click", onClick);
      dialog?.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);
  return dialogRef;
}

function normalizedText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function TraceContent({
  trace,
  claimText,
}: {
  trace: SourceTrace;
  claimText?: string;
}) {
  const repeatsClaim =
    claimText !== undefined &&
    normalizedText(trace.quote) === normalizedText(claimText);
  return (
    <>
      {!repeatsClaim && <blockquote>{trace.quote}</blockquote>}
      <p className={styles.sourceMeta}>
        <span>출처 · {trace.publisher}</span>
        <span>게시일 · {publicationLabel(trace)}</span>
      </p>
      {trace.url === null ? (
        <p>{trace.title}</p>
      ) : (
        <a href={trace.url} target="_blank" rel="noopener noreferrer">
          원문 기사 열기 · {trace.title}
        </a>
      )}
    </>
  );
}
