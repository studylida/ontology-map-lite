// web/src/KnowledgeIngestionModal.tsx
import { useEffect, useState } from "react";
import {
  dismissAgentTask,
  fetchAgentTaskDetail,
  intakeKnowledge,
  submitExtractAsync,
  submitExtractFileAsync,
} from "./api";
import type { ExtractionTaskDetail, IntakePayload } from "./types";
import styles from "./KnowledgeIngestionModal.module.css";

// 3개 동기 프로젝트의 실제 샘플 온톨로지 페이로드 (M1 검증 데이터 기반)
const PEER_SAMPLE_PAYLOADS: Record<"gov" | "news" | "excel", IntakePayload> = {
  gov: {
    source_project: "gov-insight",
    document_title: "[중소벤처기업부] 2026 AI 바우처 지원사업 공고",
    document_content:
      "총 200억원 규모로 중소기업의 AI 솔루션 도입을 바우처 형태로 지원합니다.",
    nodes: [
      {
        name: "중소벤처기업부",
        classification: "AGENCY",
        description: "중소기업 및 벤처 진흥 중앙행정기관",
      },
      {
        name: "AI 바우처 지원사업",
        classification: "PROJECT",
        description: "AI 도입 수요-공급 매칭 바우처 사업",
      },
      {
        name: "수요기업",
        classification: "COMPANY",
        description: "AI 도입 희망 중소기업",
      },
      {
        name: "공급기업",
        classification: "COMPANY",
        description: "AI 솔루션을 개발 및 제공하는 기술기업",
      },
    ],
    edges: [
      {
        source_name: "중소벤처기업부",
        target_name: "AI 바우처 지원사업",
        relation: "ORGANIZES",
      },
      {
        source_name: "수요기업",
        target_name: "AI 바우처 지원사업",
        relation: "BENEFITS_FROM",
      },
      {
        source_name: "공급기업",
        target_name: "AI 바우처 지원사업",
        relation: "PARTICIPATES_IN",
      },
      {
        source_name: "공급기업",
        target_name: "OpenAI",
        relation: "UTILIZES_API",
      },
    ],
    claims: [
      {
        quote:
          "총 200억원 규모로 중소기업의 AI 솔루션 도입을 바우처 형태로 지원한다.",
        claim_text: "AI 바우처 예산 규모",
      },
    ],
    insights: {
      summary:
        "중기부 주관 200억 규모 AI 솔루션 바우처 사업으로 수요기업과 공급기업을 연결합니다.",
    },
  },
  news: {
    source_project: "news-agent",
    document_title: "[테크 뉴스] 엔비디아-TSMC 차세대 Blackwell Ultra 협력",
    document_content:
      "엔비디아는 차세대 AI 가속기 양산을 위해 TSMC와 차세대 3nm 공정 물량을 계약했습니다.",
    nodes: [
      {
        name: "NVIDIA",
        classification: "COMPANY",
        description: "글로벌 GPU 및 AI 컴퓨팅 하드웨어 리딩 기업",
      },
      {
        name: "TSMC",
        classification: "COMPANY",
        description: "글로벌 최대 파운드리 반도체 제조 기업",
      },
      {
        name: "Blackwell Ultra",
        classification: "TOPIC",
        description: "엔비디아 차세대 아키텍처 AI 가속 칩",
      },
    ],
    edges: [
      { source_name: "NVIDIA", target_name: "TSMC", relation: "PARTNERS_WITH" },
      {
        source_name: "NVIDIA",
        target_name: "Blackwell Ultra",
        relation: "DEVELOPS",
      },
      {
        source_name: "TSMC",
        target_name: "Blackwell Ultra",
        relation: "MANUFACTURES",
      },
      {
        source_name: "NVIDIA",
        target_name: "OpenAI",
        relation: "SUPPLIES_CHIPS",
      },
    ],
    claims: [
      {
        quote:
          "엔비디아는 차세대 AI 가속기 양산을 위해 TSMC 3nm 공정 물량을 대거 확보했다.",
        claim_text: "반도체 공급망 계약",
      },
    ],
    insights: {
      summary:
        "엔비디아와 TSMC의 차세대 칩 양산 파트너십을 통한 AI 인프라 공급망 강화 이슈입니다.",
    },
  },
  excel: {
    source_project: "excel-agent",
    document_title: "2026_전략재무제표_수식분석.xlsx",
    document_content: "워크북 핵심 지표 및 영업이익 계산 군집 분석 결과입니다.",
    nodes: [
      {
        name: "영업이익",
        classification: "METRIC",
        description: "매출총이익에서 판관비를 차감한 영업 성과 지표",
      },
      {
        name: "매출총이익",
        classification: "METRIC",
        description: "매출액에서 매출원가를 차감한 기초 이익",
      },
      {
        name: "판관비",
        classification: "METRIC",
        description: "판매비와 일반관리비 총합",
      },
    ],
    edges: [
      {
        source_name: "영업이익",
        target_name: "매출총이익",
        relation: "CALCULATES",
      },
      {
        source_name: "판관비",
        target_name: "영업이익",
        relation: "SUBTRACTS_FROM",
      },
      {
        source_name: "OpenAI",
        target_name: "영업이익",
        relation: "REPORTS_METRIC",
      },
    ],
    claims: [
      {
        quote: "Sheet1!B12: =B10-B11 (영업이익 = 매출총이익 - 판관비)",
        claim_text: "수식 계산 근거",
      },
    ],
    insights: {
      summary:
        "워크북 시트의 수식 의존 관계 분석을 통해 핵심 수익성 지표 군집을 도출했습니다.",
    },
  },
};

interface KnowledgeIngestionModalProps {
  mode: "input" | "review";
  reviewTaskId: string | null;
  onClose: () => void;
  onTaskEnqueued: (taskId: string) => void;
  onIngestionSuccess: (primaryNodeId: number) => void;
}

export function KnowledgeIngestionModal({
  mode,
  reviewTaskId,
  onClose,
  onTaskEnqueued,
  onIngestionSuccess,
}: KnowledgeIngestionModalProps) {
  // 1. 입력 모드 상태 (4종 탭)
  const [activeTab, setActiveTab] = useState<"url" | "file" | "text" | "peer">(
    "url",
  );
  const [urlInput, setUrlInput] = useState<string>("");
  const [textInput, setTextInput] = useState<string>("");
  const [titleInput, setTitleInput] = useState<string>("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [autoCommit, setAutoCommit] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);

  // 2. HITL 검토 모드 상태
  const [currentMode, setCurrentMode] = useState<"input" | "review">(mode);
  const [reviewedPayload, setReviewedPayload] = useState<IntakePayload | null>(
    null,
  );
  const [selectedNodeIndices, setSelectedNodeIndices] = useState<Set<number>>(
    new Set(),
  );
  const [selectedEdgeIndices, setSelectedEdgeIndices] = useState<Set<number>>(
    new Set(),
  );
  const [loadingReview, setLoadingReview] = useState<boolean>(false);

  // HITL 모드 진입 시 태스크 결과 로드
  useEffect(() => {
    if (mode === "review" && reviewTaskId) {
      setCurrentMode("review");
      setLoadingReview(true);
      fetchAgentTaskDetail(reviewTaskId)
        .then((detail) => {
          if (detail.result) {
            loadPayloadForReview(detail.result);
          }
        })
        .finally(() => setLoadingReview(false));
    }
  }, [mode, reviewTaskId]);

  // 엣지 유효성 검사 헬퍼: 양 끝 노드가 모두 선택 목록에 존재하는지 검사
  const isEdgeValid = (
    edge: IntakeEdge,
    nodeIndices: Set<number>,
    nodes: IntakeNode[],
  ): boolean => {
    const isSourceSelected = nodes.some(
      (n, nIdx) =>
        nodeIndices.has(nIdx) &&
        ((edge.source_ref && edge.source_ref === n.ref_id) ||
          edge.source_name.trim().toLowerCase() === n.name.trim().toLowerCase()),
    );
    const isTargetSelected = nodes.some(
      (n, nIdx) =>
        nodeIndices.has(nIdx) &&
        ((edge.target_ref && edge.target_ref === n.ref_id) ||
          edge.target_name.trim().toLowerCase() === n.name.trim().toLowerCase()),
    );
    return isSourceSelected && isTargetSelected;
  };

  // 미해결 끝점이 있는 이유를 구체적인 엔티티명과 함께 반환 (사용자 고지용)
  const getMissingEndpointReason = (
    edge: IntakeEdge,
    nodeIndices: Set<number>,
    nodes: IntakeNode[],
  ): string | null => {
    const hasSource = nodes.some(
      (n, idx) =>
        nodeIndices.has(idx) &&
        ((edge.source_ref && edge.source_ref === n.ref_id) ||
          edge.source_name.trim().toLowerCase() === n.name.trim().toLowerCase()),
    );
    const hasTarget = nodes.some(
      (n, idx) =>
        nodeIndices.has(idx) &&
        ((edge.target_ref && edge.target_ref === n.ref_id) ||
          edge.target_name.trim().toLowerCase() === n.name.trim().toLowerCase()),
    );
    if (!hasSource && !hasTarget) {
      return `출발 노드 '${edge.source_name}', 도착 노드 '${edge.target_name}' 미선택`;
    }
    if (!hasSource) {
      return `출발 노드 '${edge.source_name}' 미선택`;
    }
    if (!hasTarget) {
      return `도착 노드 '${edge.target_name}' 미선택`;
    }
    return null;
  };

  // 온톨로지 DTO를 HITL 검토 화면으로 세팅
  const loadPayloadForReview = (payload: IntakePayload) => {
    const cloned: IntakePayload = JSON.parse(JSON.stringify(payload));
    const nameToRef: Record<string, string> = {};

    cloned.nodes = cloned.nodes.map((n, i) => {
      const ref_id = n.ref_id || `n${i}`;
      nameToRef[n.name.trim().toLowerCase()] = ref_id;
      return { ...n, ref_id };
    });

    cloned.claims = cloned.claims.map((c, i) => ({
      ...c,
      ref_id: c.ref_id || `c${i}`,
    }));

    cloned.edges = cloned.edges.map((e) => {
      const sRef = e.source_ref || nameToRef[e.source_name.trim().toLowerCase()];
      const tRef = e.target_ref || nameToRef[e.target_name.trim().toLowerCase()];
      return { ...e, source_ref: sRef, target_ref: tRef };
    });

    const initialNodeIndices = new Set(cloned.nodes.map((_, i) => i));
    const initialValidEdgeIndices = new Set<number>();
    cloned.edges.forEach((edge, i) => {
      if (isEdgeValid(edge, initialNodeIndices, cloned.nodes)) {
        initialValidEdgeIndices.add(i);
      }
    });

    setReviewedPayload(cloned);
    setSelectedNodeIndices(initialNodeIndices);
    setSelectedEdgeIndices(initialValidEdgeIndices);
    setCurrentMode("review");
  };

  const toggleNode = (idx: number, checked: boolean) => {
    if (!reviewedPayload) return;
    const nextNodes = new Set(selectedNodeIndices);
    if (checked) {
      nextNodes.add(idx);
    } else {
      nextNodes.delete(idx);
    }

    // 새 노드 선택 상태를 기준으로 엣지 유효성을 재계산하여, 유효하지 않은 엣지는 자동 해제
    const nextEdges = new Set<number>();
    selectedEdgeIndices.forEach((edgeIdx) => {
      const edge = reviewedPayload.edges[edgeIdx];
      if (edge && isEdgeValid(edge, nextNodes, reviewedPayload.nodes)) {
        nextEdges.add(edgeIdx);
      }
    });

    setSelectedNodeIndices(nextNodes);
    setSelectedEdgeIndices(nextEdges);
  };

  const updateNodeName = (idx: number, newName: string) => {
    if (!reviewedPayload) return;
    const oldName = reviewedPayload.nodes[idx].name;
    const refId = reviewedPayload.nodes[idx].ref_id;
    const updatedNodes = [...reviewedPayload.nodes];
    updatedNodes[idx] = { ...updatedNodes[idx], name: newName };

    // ref_id를 기반으로 엣지의 표시 이름도 동기화 (참조 무결성 유지)
    const updatedEdges = reviewedPayload.edges.map((edge) => {
      const isSrc = (refId && edge.source_ref === refId) || edge.source_name === oldName;
      const isTgt = (refId && edge.target_ref === refId) || edge.target_name === oldName;
      return {
        ...edge,
        source_name: isSrc ? newName : edge.source_name,
        target_name: isTgt ? newName : edge.target_name,
      };
    });

    setReviewedPayload({
      ...reviewedPayload,
      nodes: updatedNodes,
      edges: updatedEdges,
    });
  };

  // 검토 화면에서 취소 시 모달을 닫지 않고 4종 탭 입력 화면으로 복귀
  const handleCancel = () => {
    if (currentMode === "review") {
      setCurrentMode("input");
    } else {
      onClose();
    }
  };

  // [입력 모드]: 비동기 작업 제출
  const handleEnqueue = async () => {
    setSubmitting(true);
    try {
      let res: { task_id: string; status: string };
      if (activeTab === "file" && selectedFile) {
        res = await submitExtractFileAsync(selectedFile, autoCommit);
      } else if (activeTab === "url") {
        if (!urlInput.trim()) return;
        res = await submitExtractAsync({
          source_type: "url",
          content: urlInput.trim(),
          title: titleInput,
          auto_commit: autoCommit,
        });
      } else {
        if (!textInput.trim()) return;
        res = await submitExtractAsync({
          source_type: "text",
          content: textInput.trim(),
          title: titleInput,
          auto_commit: autoCommit,
        });
      }

      onTaskEnqueued(res.task_id);
      onClose(); // 논블로킹: 즉시 모달을 닫아 자유 탐색 보장
    } catch (err: any) {
      alert(err.message ?? "추출 의뢰 실패");
    } finally {
      setSubmitting(false);
    }
  };

  // [동기 에이전트 탭]: 외부 산출물 JSON 파일 직접 읽기
  const handlePeerJsonFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const parsed = JSON.parse(event.target?.result as string);
        if (parsed.nodes && parsed.edges) {
          loadPayloadForReview(parsed as IntakePayload);
        } else {
          alert("유효한 온톨로지(nodes, edges 포함) JSON 형식이 아닙니다.");
        }
      } catch (err) {
        alert("JSON 파일 파싱 실패");
      }
    };
    reader.readAsText(file);
  };

  // [HITL 검토 모드]: 온톨로지 최종 적재
  const handleCommitToGraph = async () => {
    if (!reviewedPayload) return;
    setSubmitting(true);

    try {
      // 체크된 노드와 유효성이 확인된 엣지만 필터링 (양 끝 노드가 모두 승인된 엣지만 엄격히 보장)
      const approvedNodes = reviewedPayload.nodes.filter((_, idx) =>
        selectedNodeIndices.has(idx),
      );
      const approvedEdges = reviewedPayload.edges.filter(
        (e, idx) =>
          selectedEdgeIndices.has(idx) &&
          isEdgeValid(e, selectedNodeIndices, reviewedPayload.nodes),
      );

      const finalPayload: IntakePayload = {
        ...reviewedPayload,
        nodes: approvedNodes,
        edges: approvedEdges,
        claims: reviewedPayload.claims,
      };

      const res = await intakeKnowledge(finalPayload);
      if (reviewTaskId) {
        try {
          await dismissAgentTask(reviewTaskId);
        } catch {
          // 알림 삭제 실패 시 적재 성공 상태는 유지
        }
      }

      onClose();
      // 생성된 대표 노드 ID를 전달하여 서브그래프 자동 전환 및 이동 유발
      onIngestionSuccess(res.primary_node_id ?? 0);
    } catch (err: any) {
      alert(err.message ?? "지식그래프 적재 실패");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.backdrop}>
      <div
        className={`${styles.modal} ${currentMode === "review" ? styles.modalWide : ""}`}
      >
        <header className={styles.header}>
          <h2>
            <span>✨</span>
            {currentMode === "input"
              ? "지식 인제스트 허브"
              : "1440px 와이드 HITL 온톨로지 검토 & 적재"}
          </h2>
          <button type="button" className={styles.closeBtn} onClick={onClose}>
            ✕
          </button>
        </header>

        <div className={styles.body}>
          {currentMode === "input" ? (
            <>
              {/* 4종 탭 네비게이션 */}
              <div className={styles.tabs}>
                <button
                  type="button"
                  className={`${styles.tabBtn} ${activeTab === "url" ? styles.tabBtnActive : ""}`}
                  onClick={() => setActiveTab("url")}
                >
                  🔗 URL 링크
                </button>
                <button
                  type="button"
                  className={`${styles.tabBtn} ${activeTab === "file" ? styles.tabBtnActive : ""}`}
                  onClick={() => setActiveTab("file")}
                >
                  📄 파일 업로드
                </button>
                <button
                  type="button"
                  className={`${styles.tabBtn} ${activeTab === "text" ? styles.tabBtnActive : ""}`}
                  onClick={() => setActiveTab("text")}
                >
                  📝 텍스트/메모
                </button>
                <button
                  type="button"
                  className={`${styles.tabBtn} ${activeTab === "peer" ? styles.tabBtnActive : ""}`}
                  onClick={() => setActiveTab("peer")}
                >
                  🤝 동기 에이전트 연동
                </button>
              </div>

              {/* 탭 1: URL 입력 */}
              {activeTab === "url" && (
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "10px",
                  }}
                >
                  <input
                    type="url"
                    placeholder="https://example.com/article... (뉴스, 블로그, 공고 URL)"
                    value={urlInput}
                    onChange={(e) => setUrlInput(e.target.value)}
                    className={styles.input}
                  />
                  <input
                    type="text"
                    placeholder="문서 제목 (선택 사항)"
                    value={titleInput}
                    onChange={(e) => setTitleInput(e.target.value)}
                    className={styles.input}
                  />
                </div>
              )}

              {/* 탭 2: 파일 업로드 */}
              {activeTab === "file" && (
                <div
                  className={styles.fileDropzone}
                  onClick={() => document.getElementById("file-input")?.click()}
                >
                  <input
                    id="file-input"
                    type="file"
                    accept=".pdf,.docx,.txt"
                    style={{ display: "none" }}
                    onChange={(e) =>
                      setSelectedFile(e.target.files?.[0] ?? null)
                    }
                  />
                  <div>
                    {selectedFile
                      ? `선택된 파일: ${selectedFile.name}`
                      : "PDF, DOCX, TXT 파일을 클릭하여 선택"}
                  </div>
                </div>
              )}

              {/* 탭 3: 텍스트 직접 입력 */}
              {activeTab === "text" && (
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "10px",
                  }}
                >
                  <input
                    type="text"
                    placeholder="메모/기록 제목 (선택 사항)"
                    value={titleInput}
                    onChange={(e) => setTitleInput(e.target.value)}
                    className={styles.input}
                  />
                  <textarea
                    placeholder="지식으로 변환할 본문 텍스트나 메모를 붙여넣으세요..."
                    value={textInput}
                    onChange={(e) => setTextInput(e.target.value)}
                    className={styles.textarea}
                  />
                </div>
              )}

              {/* 탭 4: 동기 에이전트 연동 */}
              {activeTab === "peer" && (
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "14px",
                  }}
                >
                  <div className={styles.peerGrid}>
                    <div className={styles.peerCard}>
                      <div className={styles.peerHeader}>
                        <span>🏛️</span> GovInsight
                      </div>
                      <div className={styles.peerDesc}>
                        공공 지원사업 공고 및 신청 자격요건 온톨로지
                      </div>
                      <button
                        type="button"
                        className={styles.peerDemoBtn}
                        onClick={() =>
                          loadPayloadForReview(PEER_SAMPLE_PAYLOADS.gov)
                        }
                      >
                        ⚡ 샘플 로드
                      </button>
                    </div>

                    <div className={styles.peerCard}>
                      <div className={styles.peerHeader}>
                        <span>📰</span> News Desk
                      </div>
                      <div className={styles.peerDesc}>
                        글로벌 뉴스 모니터링 및 기업·인물 이슈 클러스터
                      </div>
                      <button
                        type="button"
                        className={styles.peerDemoBtn}
                        onClick={() =>
                          loadPayloadForReview(PEER_SAMPLE_PAYLOADS.news)
                        }
                      >
                        ⚡ 샘플 로드
                      </button>
                    </div>

                    <div className={styles.peerCard}>
                      <div className={styles.peerHeader}>
                        <span>📊</span> Excel Agent
                      </div>
                      <div className={styles.peerDesc}>
                        엑셀 시트 수식 관계망 및 핵심 재무 지표 군집
                      </div>
                      <button
                        type="button"
                        className={styles.peerDemoBtn}
                        onClick={() =>
                          loadPayloadForReview(PEER_SAMPLE_PAYLOADS.excel)
                        }
                      >
                        ⚡ 샘플 로드
                      </button>
                    </div>
                  </div>

                  <div
                    className={styles.fileDropzone}
                    style={{ padding: "16px" }}
                    onClick={() =>
                      document.getElementById("peer-json-input")?.click()
                    }
                  >
                    <input
                      id="peer-json-input"
                      type="file"
                      accept=".json"
                      style={{ display: "none" }}
                      onChange={handlePeerJsonFile}
                    />
                    <div style={{ fontSize: "12px", color: "#38bdf8" }}>
                      📥 동기 에이전트 산출물 (.json) 파일 직접 불러오기
                    </div>
                  </div>

                  <div className={styles.webhookBox}>
                    <strong>🌐 외부 에이전트 Push 엔드포인트:</strong>
                    <div className={styles.webhookCode}>
                      POST /api/v1/intake
                    </div>
                  </div>
                </div>
              )}

              {/* 자동 반영 (HITL 검토 스킵) 체크박스 */}
              {activeTab !== "peer" && (
                <div className={styles.autoCommitRow}>
                  <label className={styles.checkboxLabel}>
                    <input
                      type="checkbox"
                      checked={autoCommit}
                      onChange={(e) => setAutoCommit(e.target.checked)}
                    />
                    <span>
                      검토 없이 완료 즉시 지식맵에 자동 반영 (HITL 검토 건너뛰기)
                    </span>
                  </label>
                </div>
              )}
            </>
          ) : /* 1440px 와이드 HITL 검토 화면 */
          loadingReview ? (
            <div style={{ textAlign: "center", padding: "60px", color: "#38bdf8" }}>
              ⏳ 추출 온톨로지 데이터 로딩 중...
            </div>
          ) : reviewedPayload ? (
            <div className={styles.reviewLayout}>
              {/* Column 1: 원천 문서 메타데이터 & Claims */}
              <div className={styles.reviewColMeta}>
                <div className={styles.metaCard}>
                  <span className={styles.sourceTag}>
                    출처: {reviewedPayload.source_project}
                  </span>
                  <h3 className={styles.metaTitle}>
                    {reviewedPayload.document_title || "문서 제목 없음"}
                  </h3>
                  <div className={styles.metaSummary}>
                    <strong>AI 종합 요약</strong>
                    <p>{reviewedPayload.insights?.summary ?? "요약 정보 없음"}</p>
                  </div>
                </div>

                <div className={styles.claimsCard}>
                  <div className={styles.colHeader}>
                    <span>📄 원천 근거 (Claims)</span>
                    <span className={styles.countBadge}>
                      {reviewedPayload.claims?.length || 0}건
                    </span>
                  </div>
                  <div className={styles.claimsList}>
                    {reviewedPayload.claims && reviewedPayload.claims.length > 0 ? (
                      reviewedPayload.claims.map((claim, cIdx) => (
                        <div key={cIdx} className={styles.claimItem}>
                          <div className={styles.claimQuote}>“{claim.quote}”</div>
                          {claim.claim_text && claim.claim_text !== claim.quote && (
                            <div className={styles.claimStatement}>
                              ➔ {claim.claim_text}
                            </div>
                          )}
                        </div>
                      ))
                    ) : (
                      <div className={styles.emptyNote}>
                        추출된 원천 근거가 없습니다.
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Column 2: 선별된 엔티티 노드 */}
              <div className={styles.reviewColNodes}>
                <div className={styles.colHeader}>
                  <span>선별된 엔티티 노드</span>
                  <span className={styles.countBadge}>
                    {selectedNodeIndices.size} / {reviewedPayload.nodes.length}
                  </span>
                </div>
                <div className={styles.colList}>
                  {reviewedPayload.nodes.map((node, idx) => (
                    <div
                      key={idx}
                      className={`${styles.checkItem} ${selectedNodeIndices.has(idx) ? styles.checkItemActive : ""}`}
                    >
                      <input
                        type="checkbox"
                        checked={selectedNodeIndices.has(idx)}
                        onChange={(e) => toggleNode(idx, e.target.checked)}
                      />
                      <div className={styles.checkItemContent}>
                        <div className={styles.nodeItemTop}>
                          <span className={styles.classBadge}>
                            [{node.classification}]
                          </span>
                          <input
                            type="text"
                            value={node.name}
                            onChange={(e) => updateNodeName(idx, e.target.value)}
                            className={styles.inlineEdit}
                          />
                        </div>
                        {node.description && (
                          <span className={styles.nodeItemDesc}>
                            {node.description}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Column 3: 선별된 관계 엣지 */}
              <div className={styles.reviewColEdges}>
                <div className={styles.colHeader}>
                  <span>선별된 관계 엣지</span>
                  <span className={styles.countBadge}>
                    {selectedEdgeIndices.size} / {reviewedPayload.edges.length}
                  </span>
                </div>
                <div className={styles.colList}>
                  {reviewedPayload.edges.map((edge, idx) => {
                    const missingReason = getMissingEndpointReason(
                      edge,
                      selectedNodeIndices,
                      reviewedPayload.nodes,
                    );
                    const isEdgeValidCondition = missingReason === null;

                    return (
                      <div
                        key={idx}
                        className={`${styles.checkItem} ${
                          selectedEdgeIndices.has(idx) && isEdgeValidCondition
                            ? styles.checkItemActive
                            : !isEdgeValidCondition
                              ? styles.checkItemDisabled
                              : ""
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={
                            selectedEdgeIndices.has(idx) && isEdgeValidCondition
                          }
                          disabled={!isEdgeValidCondition}
                          onChange={(e) => {
                            const next = new Set(selectedEdgeIndices);
                            e.target.checked ? next.add(idx) : next.delete(idx);
                            setSelectedEdgeIndices(next);
                          }}
                        />
                        <div className={styles.checkItemContent}>
                          <div className={styles.edgeRelation}>
                            <strong>{edge.source_name}</strong>
                            <span className={styles.relationArrow}>
                              —[{edge.relation || "RELATES_TO"}]➔
                            </span>
                            <strong>{edge.target_name}</strong>
                          </div>
                          {!isEdgeValidCondition && (
                            <span className={styles.blockedBadge}>
                              ⚠️ {missingReason}
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          ) : null}
        </div>

        <footer className={styles.footer}>
          {currentMode === "review" && (
            <div className={styles.reviewSummaryStats}>
              <span>
                승인 대상: 노드 <strong>{selectedNodeIndices.size}</strong>개 /{" "}
                관계 <strong>{selectedEdgeIndices.size}</strong>개
              </span>
            </div>
          )}
          <button
            type="button"
            className={styles.cancelBtn}
            onClick={handleCancel}
          >
            {currentMode === "review" ? "이전" : "취소"}
          </button>
          {currentMode === "input" && activeTab !== "peer" && (
            <button
              type="button"
              className={styles.submitBtn}
              onClick={handleEnqueue}
              disabled={submitting}
            >
              {submitting ? "등록 중..." : "대기열에 추가"}
            </button>
          )}
          {currentMode === "review" && (
            <button
              type="button"
              className={styles.submitBtn}
              onClick={handleCommitToGraph}
              disabled={submitting || selectedNodeIndices.size === 0}
            >
              {submitting ? "적재 중..." : "지식그래프에 반영"}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
