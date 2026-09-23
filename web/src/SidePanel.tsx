// web/src/SidePanel.tsx
import { useEffect, useState } from "react";
import { fetchNodeInsights } from "./api";
import type { GraphNode, NodeInsightsResponse } from "./types";
import styles from "./SidePanel.module.css";

interface SidePanelProps {
  selectedNode: GraphNode | null;
  onClose: () => void;
}

export function SidePanel({ selectedNode, onClose }: SidePanelProps) {
  const [data, setData] = useState<NodeInsightsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedNode) {
      setData(null);
      return;
    }

    let isCancelled = false;
    setLoading(true);
    setError(null);

    // [빈칸 1] fetchNodeInsights를 호출하여 데이터를 받아오고
    // 취소되지 않았을 때(isCancelled === false) 상태에 저장해 보세요.
    fetchNodeInsights(selectedNode.id)
      .then((res) => {
        if (!isCancelled) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!isCancelled) {
          setError(err.message);
          setLoading(false);
        }
      });

    return () => {
      isCancelled = true;
    };
  }, [selectedNode]);

  // 노드가 선택되지 않았으면 패널을 렌더링하지 않음
  if (!selectedNode) return null;

  return (
    <aside className={styles.panel} aria-label="노드 상세 정보">
      {/* 1. 상단 헤더 영역 */}
      <header className={styles.header}>
        <div>
          <span className={styles.badge}>
            {selectedNode.classification_code ?? `ID: ${selectedNode.classification_id}`}
          </span>
          <h2 className={styles.title}>{selectedNode.name}</h2>
        </div>
        <button
          type="button"
          className={styles.closeButton}
          onClick={onClose}
          aria-label="닫기"
        >
          ✕
        </button>
      </header>

      {/* 2. 패널 본문 영역 */}
      <div className={styles.content}>
        {loading && <div className={styles.stateNotice}>인사이트 분석 불러오는 중...</div>}
        {error && <div className={styles.errorNotice}>오류: {error}</div>}

        {!loading && !error && data && (
          <>
            {/* AI 종합 인사이트 섹션 */}
            {data.insight ? (
              <section className={styles.section}>
                <h3>AI 분석 요약</h3>
                {data.insight.recent_history_summary && (
                  <p className={styles.summaryText}>
                    {data.insight.recent_history_summary}
                  </p>
                )}
                {data.insight.overall_insight && (
                  <div className={styles.insightBox}>
                    {data.insight.overall_insight}
                  </div>
                )}
                {/* 이슈 태그 목록 */}
                {data.insight.issues && data.insight.issues.length > 0 && (
                  <div className={styles.tags}>
                    {data.insight.issues.map((issue, idx) => {
                      const text =
                        typeof issue === "string"
                          ? issue
                          : (issue.title ?? JSON.stringify(issue));
                      return (
                        <span key={idx} className={styles.tag}>
                          #{text}
                        </span>
                      );
                    })}
                  </div>
                )}
              </section>
            ) : (
              <div className={styles.emptyNotice}>등록된 인사이트가 없습니다.</div>
            )}

            {/* Q&A 카드 섹션 */}
            {data.qa_pairs && data.qa_pairs.length > 0 && (
              <section className={styles.section}>
                <h3>핵심 질문 & 답변 ({data.qa_pairs.length})</h3>
                <div className={styles.qaList}>
                  {data.qa_pairs.map((qa) => (
                    <article key={qa.sequence} className={styles.qaCard}>
                      <h4 className={styles.question}>Q. {qa.question}</h4>
                      <p className={styles.answer}>{qa.answer}</p>
                    </article>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
