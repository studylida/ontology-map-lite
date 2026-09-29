// web/src/SidePanel.tsx
import { useEffect, useState } from "react";
import { fetchNodeDetails } from "./api";
import type { GraphNode, NodeDetailsResponse } from "./types";
import styles from "./SidePanel.module.css";

interface SidePanelProps {
  selectedNode: GraphNode | null;
  onClose: () => void;
}

interface InsightPart {
  title?: string;
  icon: string;
  content: string;
}

function parseInsightParts(text: string): InsightPart[] {
  if (!text) return [];

  // 1) 대괄호 [파트제목] 기준 분할
  const bracketRegex = /\[(.*?)\]\s*([\s\S]*?)(?=(?:\[.*?\])|$)/g;
  const matches = [...text.matchAll(bracketRegex)];

  if (matches.length > 0) {
    return matches.map((m) => {
      const rawTitle = m[1].trim();
      let icon = "💡";
      if (
        rawTitle.includes("위상") ||
        rawTitle.includes("전략") ||
        rawTitle.includes("성격") ||
        rawTitle.includes("개요")
      ) {
        icon = "🎯";
      } else if (
        rawTitle.includes("생태계") ||
        rawTitle.includes("기술") ||
        rawTitle.includes("파급") ||
        rawTitle.includes("혁신")
      ) {
        icon = "⚡";
      } else if (
        rawTitle.includes("한계") ||
        rawTitle.includes("불확실") ||
        rawTitle.includes("과제") ||
        rawTitle.includes("리스크")
      ) {
        icon = "⚠️";
      }
      return {
        title: rawTitle,
        icon,
        content: m[2].trim(),
      };
    });
  }

  // 2) 문단 분할 (\n\n)
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);

  if (paragraphs.length > 1) {
    return paragraphs.map((p, idx) => ({
      title: `분석 관점 0${idx + 1}`,
      icon: idx === 0 ? "🎯" : idx === 1 ? "⚡" : "💡",
      content: p,
    }));
  }

  // 3) 단일 문단
  return [
    {
      icon: "💡",
      content: text.trim(),
    },
  ];
}

export function SidePanel({ selectedNode, onClose }: SidePanelProps) {
  const [data, setData] = useState<NodeDetailsResponse | null>(null);
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

    fetchNodeDetails(selectedNode.id)
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

  if (!selectedNode) return null;

  const propEntries = data?.properties
    ? Object.entries(data.properties).filter(
        ([k, v]) =>
          k !== "claim_ids" &&
          !k.startsWith("_") &&
          v !== null &&
          v !== undefined &&
          v !== "",
      )
    : [];

  return (
    <aside className={styles.panel} aria-label="노드 상세 정보">
      {/* 1. 상단 헤더 영역 */}
      <header className={styles.header}>
        <div>
          <span className={styles.badge}>
            {data?.classification_name ??
              selectedNode.classification_code ??
              `ID: ${selectedNode.classification_id}`}
          </span>
          <h2 className={styles.title}>{data?.name ?? selectedNode.name}</h2>
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
        {loading && (
          <div className={styles.stateNotice}>
            노드 상세 정보 불러오는 중...
          </div>
        )}
        {error && <div className={styles.errorNotice}>오류: {error}</div>}

        {!loading && !error && data && (
          <>
            {/* 개요 (Overview) & 주요 속성 */}
            <section className={styles.section}>
              <h3>개요</h3>
              {data.description ? (
                <p className={styles.summaryText}>{data.description}</p>
              ) : (
                <p className={styles.emptyHint}>엔티티 기본 설명이 없습니다.</p>
              )}

              {propEntries.length > 0 && (
                <div className={styles.propertiesGrid}>
                  {propEntries.map(([key, val]) => (
                    <div key={key} className={styles.propItem}>
                      <span className={styles.propKey}>{key}</span>
                      <span className={styles.propVal}>
                        {typeof val === "object"
                          ? JSON.stringify(val)
                          : String(val)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* 개요 근거 (Claims) 아코디언 (기본 접힘) */}
            <section className={styles.section}>
              <details className={styles.claimsAccordion}>
                <summary className={styles.claimsSummary}>
                  <span>📄 개요 근거 ({data.claims.length}건)</span>
                </summary>
                <div className={styles.claimsList}>
                  {data.claims.length > 0 ? (
                    data.claims.map((claim) => (
                      <article key={claim.id} className={styles.claimCard}>
                        <blockquote className={styles.claimQuote}>
                          “{claim.quote}”
                        </blockquote>
                        {claim.statement && claim.statement !== claim.quote && (
                          <p className={styles.claimStatement}>
                            {claim.statement}
                          </p>
                        )}
                        <div className={styles.claimDocTitle}>
                          <span className={styles.sourceTag}>출처</span>
                          <span>{claim.document_title || "확인된 문서"}</span>
                        </div>
                      </article>
                    ))
                  ) : (
                    <div className={styles.emptyHint}>
                      연계된 개요 근거가 없습니다.
                    </div>
                  )}
                </div>
              </details>
            </section>

            {/* AI 분석 요약 (null이어도 기본 골격 항상 노출) */}
            <section className={styles.section}>
              <h3>AI 분석 요약</h3>
              {data.recent_history_summary && (
                <p className={styles.summaryText}>
                  {data.recent_history_summary}
                </p>
              )}
              {data.overall_insight && (
                <div className={styles.insightPartsContainer}>
                  {parseInsightParts(data.overall_insight).map((part, idx) => (
                    <div key={idx} className={styles.insightCard}>
                      {part.title && (
                        <div className={styles.insightCardHeader}>
                          <span className={styles.insightIcon}>{part.icon}</span>
                          <span className={styles.insightTitle}>{part.title}</span>
                        </div>
                      )}
                      <p className={styles.insightContent}>{part.content}</p>
                    </div>
                  ))}
                </div>
              )}
              {data.issues && data.issues.length > 0 && (
                <div className={styles.tags}>
                  {data.issues.map((issue, idx) => {
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
              {!data.recent_history_summary &&
                !data.overall_insight &&
                (!data.issues || data.issues.length === 0) && (
                  <p className={styles.emptyHint}>
                    아직 등록된 AI 분석 요약이 없습니다.
                  </p>
                )}
            </section>

            {/* 핵심 질문 & 답변 (Q&A) (null이어도 기본 골격 항상 노출) */}
            <section className={styles.section}>
              <h3>핵심 질문 & 답변 ({data.qa_pairs ? data.qa_pairs.length : 0})</h3>
              {data.qa_pairs && data.qa_pairs.length > 0 ? (
                <div className={styles.qaList}>
                  {data.qa_pairs.map((qa) => (
                    <article key={qa.sequence} className={styles.qaCard}>
                      <h4 className={styles.question}>Q. {qa.question}</h4>
                      <p className={styles.answer}>{qa.answer}</p>
                    </article>
                  ))}
                </div>
              ) : (
                <p className={styles.emptyHint}>
                  등록된 핵심 질문과 답변이 없습니다.
                </p>
              )}
            </section>
          </>
        )}
      </div>
    </aside>
  );
}
