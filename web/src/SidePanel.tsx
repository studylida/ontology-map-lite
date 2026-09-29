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

  const metrics =
    data?.properties?.metrics && typeof data.properties.metrics === "object"
      ? (data.properties.metrics as Record<string, any>)
      : null;

  const reports =
    data?.properties?.reports && typeof data.properties.reports === "object"
      ? (data.properties.reports as Record<string, any>)
      : null;

  const propEntries = data?.properties
    ? Object.entries(data.properties).filter(
        ([k, v]) =>
          k !== "claim_ids" &&
          k !== "metrics" &&
          k !== "reports" &&
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

              {/* 실적/운영 지표 (Metrics) 카드 */}
              {metrics && (
                <div className={styles.metricsContainer}>
                  {Object.entries(metrics).map(([mKey, mVal]) => {
                    if (typeof mVal === "object" && mVal !== null) {
                      const title =
                        mKey === "revenue"
                          ? "매출 실적"
                          : mKey === "operating_profit"
                          ? "영업이익"
                          : mKey;
                      const valStr =
                        (mVal as any).value !== undefined
                          ? `${(mVal as any).value}${(mVal as any).unit ?? ""}`
                          : JSON.stringify(mVal);
                      const tags = [
                        (mVal as any).period ? `${(mVal as any).period}년` : null,
                        (mVal as any).consolidation === "separate"
                          ? "별도"
                          : (mVal as any).consolidation === "consolidated"
                          ? "연결"
                          : (mVal as any).consolidation,
                        (mVal as any).valueKind === "actual"
                          ? "실적"
                          : (mVal as any).valueKind === "plan"
                          ? "계획"
                          : (mVal as any).valueKind,
                        (mVal as any).currency,
                      ].filter(Boolean);

                      return (
                        <div key={mKey} className={styles.metricCard}>
                          <div className={styles.metricHeader}>
                            <span className={styles.metricTitle}>{title}</span>
                            <div className={styles.metricTags}>
                              {tags.map((t) => (
                                <span key={String(t)} className={styles.metricTag}>
                                  {t}
                                </span>
                              ))}
                            </div>
                          </div>
                          <div className={styles.metricValue}>{valStr}</div>
                        </div>
                      );
                    }
                    return (
                      <div key={mKey} className={styles.metricCard}>
                        <div className={styles.metricHeader}>
                          <span className={styles.metricTitle}>{mKey}</span>
                        </div>
                        <div className={styles.metricValue}>{String(mVal)}</div>
                      </div>
                    );
                  })}
                </div>
              )}

              {propEntries.length > 0 && (
                <div className={styles.propertiesGrid}>
                  {propEntries.map(([key, val]) => (
                    <div key={key} className={styles.propItem}>
                      <span className={styles.propKey}>{key}</span>
                      <span className={styles.propVal}>
                        {(() => {
                          if (val === null || val === undefined) return "";
                          if (typeof val === "boolean") return val ? "예" : "아니오";
                          if (Array.isArray(val)) {
                            return val
                              .map((v) => (typeof v === "object" ? JSON.stringify(v) : String(v)))
                              .join(", ");
                          }
                          if (typeof val === "object") {
                            if (val.name) return String(val.name);
                            if (val.title) return String(val.title);
                            if (val.value !== undefined) return `${val.value}${val.unit ?? ""}`;
                            return JSON.stringify(val);
                          }
                          return String(val);
                        })()}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* 연계 분석 리포트 (심층 인사이트) */}
            {reports && Object.keys(reports).length > 0 && (
              <section className={styles.section}>
                <div className={styles.sectionHeaderRow}>
                  <h3>연계 분석 리포트</h3>
                  <span className={styles.reportCountBadge}>
                    {Object.keys(reports).length}개 출처 연계
                  </span>
                </div>
                <div className={styles.reportsContainer}>
                  {/* 엑셀 재무 분석 리포트 */}
                  {reports.excel && (
                    <article className={styles.reportCard}>
                      <div className={styles.reportCardHeader}>
                        <div className={styles.reportSourceBadge}>
                          <span className={styles.reportIcon}>📊</span>
                          <span>{reports.excel.source || "Excel-Agent"}</span>
                        </div>
                        <span className={styles.reportTitle}>
                          {reports.excel.title || "연간 실적 비교 분석"}
                        </span>
                      </div>
                      {reports.excel.overview && (
                        <p className={styles.reportOverview}>
                          {reports.excel.overview}
                        </p>
                      )}
                      {reports.excel.metrics && (
                        <div className={styles.reportMetricsRow}>
                          {reports.excel.metrics["2024"] && (
                            <div className={styles.reportMetricBox}>
                              <span className={styles.reportMetricLabel}>
                                2024년 실적
                              </span>
                              <span className={styles.reportMetricVal}>
                                {reports.excel.metrics["2024"].value}
                                {reports.excel.metrics["2024"].unit}
                              </span>
                            </div>
                          )}
                          {reports.excel.metrics.growth && (
                            <div className={styles.reportGrowthBox}>
                              <span className={styles.reportGrowthArrow}>▲</span>
                              <span className={styles.reportGrowthVal}>
                                {reports.excel.metrics.growth}
                              </span>
                            </div>
                          )}
                          {reports.excel.metrics["2025"] && (
                            <div
                              className={`${styles.reportMetricBox} ${styles.highlightBox}`}
                            >
                              <span className={styles.reportMetricLabel}>
                                2025년 실적
                              </span>
                              <span className={styles.reportMetricVal}>
                                {reports.excel.metrics["2025"].value}
                                {reports.excel.metrics["2025"].unit}
                              </span>
                            </div>
                          )}
                        </div>
                      )}
                      {reports.excel.insights &&
                        reports.excel.insights.length > 0 && (
                          <div className={styles.reportInsightList}>
                            {reports.excel.insights.map(
                              (ins: any, idx: number) => (
                                <div key={idx} className={styles.reportInsightItem}>
                                  <div className={styles.insightItemFact}>
                                    <strong>
                                      💡 {ins.title || "주요 사실"}:
                                    </strong>{" "}
                                    {ins.fact}
                                  </div>
                                  {ins.cause && (
                                    <div className={styles.insightItemCause}>
                                      <span className={styles.tagPill}>
                                        원인 분석
                                      </span>{" "}
                                      {ins.cause}
                                    </div>
                                  )}
                                  {ins.recommendation && (
                                    <div className={styles.insightItemRec}>
                                      <span className={styles.tagPill}>
                                        권고사항
                                      </span>{" "}
                                      {ins.recommendation}
                                    </div>
                                  )}
                                  {ins.evidence && (
                                    <div className={styles.insightItemEvidence}>
                                      <span>
                                        근거:{" "}
                                        {Array.isArray(ins.evidence)
                                          ? ins.evidence.join(", ")
                                          : ins.evidence}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              ),
                            )}
                          </div>
                        )}
                    </article>
                  )}

                  {/* 정부공고 분석 리포트 */}
                  {reports.gov && (
                    <article className={styles.reportCard}>
                      <div className={styles.reportCardHeader}>
                        <div className={styles.reportSourceBadge}>
                          <span className={styles.reportIcon}>🏛️</span>
                          <span>{reports.gov.source || "GovInsight"}</span>
                        </div>
                        <span className={styles.reportTitle}>
                          {reports.gov.title || "지원사업 참여 검토"}
                        </span>
                      </div>
                      {reports.gov.targetProgram && (
                        <div className={styles.reportTargetProgram}>
                          <span className={styles.tagPillGreen}>대상 사업</span>
                          <span className={styles.targetProgramName}>
                            {reports.gov.targetProgram}
                          </span>
                        </div>
                      )}
                      {reports.gov.recommendedProject && (
                        <div className={styles.reportProjectBox}>
                          <span className={styles.reportSubTitle}>
                            추천 제안 프로젝트
                          </span>
                          <div className={styles.projectName}>
                            {reports.gov.recommendedProject}
                          </div>
                        </div>
                      )}
                      {reports.gov.recommendedParticipation && (
                        <p className={styles.reportRecommendation}>
                          “{reports.gov.recommendedParticipation}”
                        </p>
                      )}
                      {reports.gov.decision && (
                        <div className={styles.decisionRow}>
                          <span className={styles.decisionBadge}>
                            {reports.gov.decision}
                          </span>
                          <span className={styles.decisionReason}>
                            {reports.gov.decisionReason}
                          </span>
                        </div>
                      )}
                      {reports.gov.checklist &&
                        reports.gov.checklist.length > 0 && (
                          <div className={styles.checklistContainer}>
                            <span className={styles.checklistTitle}>
                              📋 신청 요건 사전 체크리스트
                            </span>
                            <ul className={styles.checkList}>
                              {reports.gov.checklist.map(
                                (item: any, idx: number) => (
                                  <li key={idx} className={styles.checkListItem}>
                                    <span
                                      className={
                                        item.level === "필수"
                                          ? styles.levelMandatory
                                          : styles.levelConditional
                                      }
                                    >
                                      [{item.level}]
                                    </span>
                                    <strong className={styles.checkItemTitle}>
                                      {item.title}
                                    </strong>
                                    : {item.detail}
                                  </li>
                                ),
                              )}
                            </ul>
                          </div>
                        )}
                    </article>
                  )}

                  {/* 뉴스 공급망/동향 리포트 */}
                  {reports.news && (
                    <article className={styles.reportCard}>
                      <div className={styles.reportCardHeader}>
                        <div className={styles.reportSourceBadge}>
                          <span className={styles.reportIcon}>📰</span>
                          <span>{reports.news.source || "External-News"}</span>
                        </div>
                        <span className={styles.reportTitle}>
                          {reports.news.title || "공급망 및 동향"}
                        </span>
                      </div>
                      {reports.news.summary && (
                        <p className={styles.reportOverview}>
                          {reports.news.summary}
                        </p>
                      )}
                      <div className={styles.newsDetailsGrid}>
                        {reports.news.category && (
                          <div className={styles.newsItem}>
                            <span className={styles.newsItemKey}>분야:</span>
                            <span className={styles.newsItemVal}>
                              {reports.news.category}
                            </span>
                          </div>
                        )}
                        {reports.news.contractPartner && (
                          <div className={styles.newsItem}>
                            <span className={styles.newsItemKey}>
                              계약 상대:
                            </span>
                            <span className={styles.newsItemVal}>
                              {reports.news.contractPartner}
                            </span>
                          </div>
                        )}
                        {reports.news.eventDate && (
                          <div className={styles.newsItem}>
                            <span className={styles.newsItemKey}>체결일:</span>
                            <span className={styles.newsItemVal}>
                              {reports.news.eventDate}
                            </span>
                          </div>
                        )}
                      </div>
                      {reports.news.plan && (
                        <div className={styles.newsPlanBox}>
                          <span className={styles.tagPillBlue}>향후 계획</span>
                          <span>{reports.news.plan}</span>
                        </div>
                      )}
                    </article>
                  )}
                </div>
              </section>
            )}

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
