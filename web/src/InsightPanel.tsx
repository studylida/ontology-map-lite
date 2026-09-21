import { useCallback, useEffect, useId, useState } from "react";
import styles from "./App.module.css";
import type { TimeRange } from "./data";
import { ClaimCard, PeriodNote } from "./PanelEvidence";
import {
  type EvidenceSelection,
  PageNotice,
  useModalDialog,
} from "./RelationPanel";
import { fetchPanelReport213 } from "./read213";
import { useCursorPage } from "./useCursorPage";

export function ReportDialog({
  nodeId,
  timeRange,
  sectionId,
  onClose,
  onEvidence,
  onLocate,
}: {
  nodeId: string;
  timeRange: TimeRange;
  sectionId: string;
  onClose: () => void;
  onEvidence: (selection: EvidenceSelection) => void;
  onLocate: (nodeId: string, relationId?: string) => void;
}) {
  const [expanded, setExpanded] = useState(new Set<string>());
  const updateExpanded = (key: string, open: boolean) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (open) next.add(key);
      else next.delete(key);
      return next;
    });
  const dialogRef = useModalDialog(onClose);
  const titleId = useId();
  const fetchPage = useCallback(
    (id: string, _cursor: string | null, signal: AbortSignal) =>
      fetchPanelReport213(id, timeRange, true, signal),
    [timeRange],
  );
  const page = useCursorPage(nodeId, fetchPage);
  const report = page.items[0];
  useEffect(() => {
    if (!report || !sectionId) return;
    const section = document.getElementById(`${titleId}-section-${sectionId}`);
    section?.scrollIntoView?.({ block: "start" });
    section?.focus({ preventScroll: true });
  }, [report, sectionId, titleId]);
  return (
    <dialog
      ref={dialogRef}
      className={styles.insightDialog}
      aria-labelledby={titleId}
      onCancel={onClose}
    >
      <article>
        <button
          type="button"
          className={styles.dialogClose}
          aria-label="분석 닫기"
          onClick={onClose}
        >
          ×
        </button>
        <span className={styles.dialogEyebrow}>종합보고서</span>
        <h2 id={titleId}>{report?.title ?? "분석 불러오기"}</h2>
        <PageNotice
          {...page}
          empty={!report}
          emptyMessage="이 기간에는 분석에 필요한 정보가 아직 충분히 모이지 않았습니다."
          onRetry={page.retry}
        />
        {report && (
          <>
            <PeriodNote range={timeRange} asOf={report.asOf} />
            <h3>핵심 해석</h3>
            <p className={styles.dialogSummary}>{report.summary}</p>
            <p className={styles.panelMeta}>
              서로 다른 근거 {report.evidenceGroupCount}개
            </p>
            <nav aria-label="보고서 목차" className={styles.reportContents}>
              {report.sections.map((section) => (
                <a key={section.id} href={`#${titleId}-section-${section.id}`}>
                  {section.title}
                </a>
              ))}
            </nav>
            {expanded.size >= 2 && (
              <button type="button" onClick={() => setExpanded(new Set())}>
                모두 접기
              </button>
            )}
            {report.sections.map((section) => (
              <section
                key={section.id}
                id={`${titleId}-section-${section.id}`}
                tabIndex={-1}
                className={styles.reportSection}
              >
                <h3>{section.title}</h3>
                <div className={styles.reportInterpretation}>
                  <h4>해석과 판단</h4>
                  <p>{section.synthesis}</p>
                </div>
                <h4>이 분석에 사용한 원문</h4>
                {section.claims.map((claim) => (
                  <ClaimCard
                    key={claim.id}
                    nodeId={nodeId}
                    claim={claim}
                    expanded={expanded.has(`${section.id}:${claim.id}`)}
                    onExpanded={(open) =>
                      updateExpanded(`${section.id}:${claim.id}`, open)
                    }
                    range={timeRange}
                    onEvidence={onEvidence}
                    onLocate={onLocate}
                  />
                ))}
                {section.caveat && (
                  <>
                    <h4>해석의 한계</h4>
                    <p className={styles.panelCaveat}>{section.caveat}</p>
                  </>
                )}
              </section>
            ))}
            {report.conclusion && (
              <section>
                <h3>종합 판단</h3>
                <p>{report.conclusion}</p>
              </section>
            )}
            {report.caveat && (
              <p className={styles.panelCaveat}>{report.caveat}</p>
            )}
          </>
        )}
      </article>
    </dialog>
  );
}

function GeneratedInsightPanel({
  nodeId,
  timeRange,
  onReport,
}: {
  nodeId: string;
  timeRange: TimeRange;
  onReport: (sectionId: string) => void;
}) {
  const fetchPage = useCallback(
    (id: string, _cursor: string | null, signal: AbortSignal) =>
      fetchPanelReport213(id, timeRange, false, signal),
    [timeRange],
  );
  const page = useCursorPage(nodeId, fetchPage);
  const report = page.items[0];
  return (
    <section aria-label="인사이트 보고서">
      <h2>인사이트</h2>
      <PageNotice
        {...page}
        empty={!report}
        emptyMessage="이 기간에는 분석에 필요한 정보가 아직 충분히 모이지 않았습니다."
        onRetry={page.retry}
      />
      {report && (
        <>
          <PeriodNote range={timeRange} asOf={report.asOf} />
          <h3>{report.title}</h3>
          <h4>핵심 해석</h4>
          <p>{report.summary}</p>
          <p className={styles.panelMeta}>
            서로 다른 근거 {report.evidenceGroupCount}개
          </p>
          <div className={styles.reportContents}>
            {report.sections.map((section) => (
              <button
                key={section.id}
                type="button"
                onClick={() => onReport(section.id)}
              >
                {section.title}
              </button>
            ))}
          </div>
          <button
            type="button"
            className={styles.panelAction}
            onClick={() => onReport("")}
          >
            종합보고서 읽기
          </button>
        </>
      )}
    </section>
  );
}

export function InsightPanel(props: {
  nodeId: string;
  timeRange: TimeRange;
  onReport: (sectionId: string) => void;
}) {
  if (props.timeRange === "all")
    return (
      <section aria-label="인사이트 보고서">
        <h2>인사이트</h2>
        <p className={styles.panelMeta}>
          최근 90일 또는 최근 1년을 선택하면 기간별 인사이트를 볼 수 있습니다.
        </p>
      </section>
    );
  return <GeneratedInsightPanel {...props} />;
}
