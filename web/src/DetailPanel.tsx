import { useId, useState } from "react";
import styles from "./App.module.css";
import {
  type ClaimHighlight,
  type ExplorationView,
  type TimeRange,
  timeRangeLabel,
} from "./data";
import { InsightPanel, ReportDialog } from "./InsightPanel";
import { PanelEvidence, RecordPeek } from "./PanelEvidence";
import { QuestionPanel } from "./QuestionPanel";
import type { EvidenceSelection } from "./RelationPanel";
import type { PanelClaim213 } from "./read213";

interface DetailPanelProps {
  view: ExplorationView;
  timeRange: TimeRange;
  onClose: () => void;
  onSelect: (nodeId: string) => void;
  onLocate: (nodeId: string, relationId?: string) => void;
  onLocateMany?: (nodeIds: string[], relationIds: string[]) => void;
  onEvidence?: (selection: EvidenceSelection) => void;
  initialTab?: 0 | 1 | 2;
}

const recommendationStatusLabel = {
  confirmedRelation: "확인된 관계",
  connectedPath: "연결 경로 있음",
  ambient: "새 탐색 출발점",
} as const;

function highlightDate(item: ClaimHighlight): string {
  if (!item.publishedAt || item.precision === "UNKNOWN")
    return "게시 시점 미상";
  if (item.precision === "INSTANT")
    return new Date(item.publishedAt).toLocaleString("ko-KR");
  return item.publishedAt.slice(
    0,
    { DAY: 10, MONTH: 7, YEAR: 4 }[item.precision],
  );
}

function DetailPanelContent({
  view,
  timeRange,
  onClose,
  onSelect,
  onLocate,
  onLocateMany = () => undefined,
  onEvidence = () => undefined,
  initialTab = 0,
}: DetailPanelProps) {
  const [tab, setTab] = useState<0 | 1 | 2>(initialTab);
  const [reportSection, setReportSection] = useState<string | null>(null);
  const [recordPeek, setRecordPeek] = useState<PanelClaim213[] | null>(null);
  const tabsId = useId();
  const center = view.nodes.find((node) => node.id === view.centerId);
  if (!center) return null;

  const selectTab = (next: 0 | 1 | 2) => {
    setRecordPeek(null);
    setTab(next);
  };

  return (
    <>
      <aside
        className={styles.detailPanel}
        aria-label={`${center.name} 상세 정보`}
      >
        <button
          type="button"
          className={styles.closeButton}
          aria-label="상세 패널 닫기"
          onClick={onClose}
        >
          ×
        </button>
        <header className={styles.panelHeader} data-kind={center.kind}>
          <span className={styles.nodeKind}>
            <i className={styles.nodeTypeDot} />
            {center.kind}
          </span>
          <h1>{center.name}</h1>
        </header>

        <div
          role="tablist"
          aria-label="노드 상세 보기"
          className={styles.panelTabs}
        >
          {["개요", "기록", "인사이트"].map((label, index) => (
            <button
              key={label}
              type="button"
              id={`${tabsId}-${index}-tab`}
              role="tab"
              aria-selected={tab === index}
              aria-controls={`${tabsId}-panel`}
              tabIndex={tab === index ? 0 : -1}
              onClick={() => selectTab(index as 0 | 1 | 2)}
              onKeyDown={(event) => {
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight")
                  return;
                event.preventDefault();
                const next = (index + (event.key === "ArrowRight" ? 1 : 2)) % 3;
                selectTab(next as 0 | 1 | 2);
                document.getElementById(`${tabsId}-${next}-tab`)?.focus();
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <div
          id={`${tabsId}-panel`}
          role="tabpanel"
          aria-labelledby={`${tabsId}-${tab}-tab`}
          className={styles.tabPanel}
        >
          {tab === 0 && (
            <>
              <section className={styles.summarySection}>
                <h2>최근 이력 요약</h2>
                <p className={styles.panelContext}>{view.context}</p>
              </section>
              <section className={styles.summarySection}>
                <h2>
                  {timeRange === "all"
                    ? "전체 기간에서 최근 확인된 내용"
                    : `${timeRangeLabel(timeRange)}에 확인된 내용`}
                </h2>
                {view.periodHighlights.length ? (
                  <div className={styles.highlightList}>
                    {view.periodHighlights.map((item) => (
                      <article key={item.id} className={styles.highlightItem}>
                        <p>{item.text}</p>
                        <small>{highlightDate(item)}</small>
                      </article>
                    ))}
                  </div>
                ) : (
                  <p className={styles.empty}>
                    이 기간에 확인된 내용이 없습니다.
                  </p>
                )}
              </section>
              <QuestionPanel
                nodeId={center.id}
                range={timeRange}
                onReport={setReportSection}
                onRecordPeek={setRecordPeek}
              />
              <div className={styles.sectionHeading}>
                <h2>이어서 살펴보기</h2>
                <span>{view.recommendations.length}</span>
              </div>
              {view.recommendations.length ? (
                <div className={styles.recommendations}>
                  {view.recommendations.map((recommendation) => (
                    <button
                      type="button"
                      key={recommendation.node.id}
                      className={styles.recommendationCard}
                      data-kind={recommendation.node.kind}
                      onClick={() => onSelect(recommendation.node.id)}
                    >
                      <span className={styles.cardTitle}>
                        <span>
                          <i className={styles.nodeTypeDot} />
                          {recommendation.node.name}
                        </span>
                        <small>{recommendation.node.kind}</small>
                      </span>
                      <span className={styles.cardReason}>
                        {recommendation.reason}
                      </span>
                      <span className={styles.cardMeta}>
                        {recommendationStatusLabel[recommendation.status]}
                      </span>
                      <span className={styles.cardArrow} aria-hidden="true">
                        ›
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className={styles.empty}>추천할 탐색 대상이 없습니다.</p>
              )}
              {view.recommendations.length > 0 && (
                <button
                  type="button"
                  className={styles.panelAction}
                  onClick={() =>
                    onLocateMany(
                      view.recommendations.map(({ node }) => node.id),
                      view.recommendations.flatMap(({ path }) =>
                        path.map(({ id }) => id),
                      ),
                    )
                  }
                >
                  추천 노드 지도에서 강조
                </button>
              )}
            </>
          )}
          {tab === 1 && (
            <PanelEvidence
              nodeId={center.id}
              range={timeRange}
              onEvidence={onEvidence}
              onLocate={onLocate}
            />
          )}
          {tab === 2 && (
            <InsightPanel
              key={`${center.id}:${timeRange}`}
              nodeId={center.id}
              timeRange={timeRange}
              onReport={setReportSection}
            />
          )}
        </div>
        {reportSection !== null && (
          <ReportDialog
            nodeId={center.id}
            timeRange={timeRange}
            sectionId={reportSection}
            onClose={() => setReportSection(null)}
            onEvidence={onEvidence}
            onLocate={onLocate}
          />
        )}
      </aside>
      {recordPeek && (
        <RecordPeek
          nodeId={center.id}
          claims={recordPeek}
          range={timeRange}
          onClose={() => setRecordPeek(null)}
          onOpenRecords={() => {
            setRecordPeek(null);
            setTab(1);
          }}
          onEvidence={onEvidence}
          onLocate={onLocate}
        />
      )}
    </>
  );
}

export function DetailPanel(props: DetailPanelProps) {
  return (
    <DetailPanelContent
      key={`${props.view.centerId}:${props.timeRange}:${props.initialTab ?? 0}`}
      {...props}
    />
  );
}
