// web/src/App.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { dismissAgentTask, fetchAgentTasks, fetchSubgraph, fetchTopDegreeNode } from "./api";
import { GraphCanvas, type GraphCanvasHandle } from "./GraphCanvas";
import { SidePanel } from "./SidePanel";
import { NodeSearch } from "./NodeSearch";
import { KnowledgePopover } from "./KnowledgePopover";
import { IngestionQueueNotice } from "./IngestionQueueNotice";
import { KnowledgeIngestionModal } from "./KnowledgeIngestionModal";
import { HitlReviewModal } from "./HitlReviewModal";
import { useInitialLoading } from "./useInitialLoading";
import type {
  ExtractionTaskSummary,
  GraphEdge,
  GraphNode,
  NodeSearchItem,
} from "./types";
import styles from "./App.module.css";

export function App() {
  const canvasRef = useRef<GraphCanvasHandle | null>(null);

  // 1. 기본 3D 그래프 상태 (동적으로 최다 관계 노드를 감지하여 설정)
  const [centerNodeId, setCenterNodeId] = useState<number | null>(null);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [hasOmitted, setHasOmitted] = useState<boolean>(false);
  const [omittedCount, setOmittedCount] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // 2. 비동기 대기열 & 모달 상태
  const [tasks, setTasks] = useState<ExtractionTaskSummary[]>([]);
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [modalMode, setModalMode] = useState<"input" | "review">("input");
  const [reviewTaskId, setReviewTaskId] = useState<string | null>(null);

  // 2.1. 1440px 와이드 HITL 검토 모달 상태
  const [isHitlOpen, setIsHitlOpen] = useState<boolean>(false);
  const [hitlProducer, setHitlProducer] = useState<"news" | "gov" | "excel">("news");

  // 2.5. 초기 로딩 화면 & 인트로 애니메이션 상태
  const [graphReady, setGraphReady] = useState(false);
  const LOADING_TIPS = useMemo(
    () => [
      "💡 노드를 클릭하면 해당 주제 중심으로 지식맵을 탐색할 수 있어요.",
      "💡 빈 공간을 드래그해서 지도를 이동하고, 마우스 휠로 확대·축소해 보세요.",
      "💡 간선을 클릭하면 두 개념이 연결된 이유와 근거를 확인할 수 있어요.",
      "💡 노드 위에 마우스를 올리면 연결된 관계들이 하이라이트돼요.",
    ],
    [],
  );
  const [loadingTip] = useState(
    () => LOADING_TIPS[Math.floor(Math.random() * LOADING_TIPS.length)],
  );
  const { progress: loadingProgress, phase: loadingPhase } = useInitialLoading(
    graphReady,
    error !== null,
  );
  const introStarted = graphReady && loadingPhase === "hidden";

  // 3. 중심 노드 변경 시 서브그래프 로드 (기존 탐색 노드는 ambient로 누적 보존)
  const loadGraph = useCallback(
    async (nodeId: number, unbounded: boolean = false) => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchSubgraph(nodeId, unbounded);
        if (res.nodes.length === 0) {
          throw new Error("표시할 그래프 데이터가 없습니다.");
        }

        // 새 서브그래프 데이터와 중심 노드 ID를 원자적으로 동시 갱신
        setCenterNodeId(nodeId);

        setNodes((prevNodes) => {
          const newMap = new Map(res.nodes.map((n) => [n.id, n]));
          const merged: GraphNode[] = [...res.nodes];
          for (const oldNode of prevNodes) {
            if (!newMap.has(oldNode.id)) {
              merged.push({
                ...oldNode,
                tier: "ambient" as any,
              });
            }
          }
          return merged;
        });

        setEdges((prevEdges) => {
          const newMap = new Map(res.edges.map((e) => [e.id, e]));
          const merged: GraphEdge[] = [...res.edges];
          for (const oldEdge of prevEdges) {
            if (!newMap.has(oldEdge.id)) {
              merged.push({
                ...oldEdge,
                tier: "ambient" as any,
              });
            }
          }
          return merged;
        });

        setHasOmitted(Boolean(res.has_omitted));
        setOmittedCount(res.omitted_count ?? 0);

        const center =
          res.nodes.find((n) => n.id === nodeId) ?? res.nodes[0] ?? null;
        if (center) {
          setSelectedNode({ ...center });
        } else {
          setSelectedNode(null);
        }
      } catch {
        setError(
          "지식맵을 불러오지 못했습니다. 서버 연결 상태를 확인한 뒤 다시 시도해 주세요.",
        );
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  // 초기 마운트 시 최다 관계(Top-Degree) 중심 노드 동적 탐색 및 로드
  useEffect(() => {
    const initGraph = async () => {
      try {
        const topNode = await fetchTopDegreeNode();
        await loadGraph(topNode.id);
      } catch {
        setError(
          "지식맵을 불러오지 못했습니다. 서버 연결 상태를 확인한 뒤 다시 시도해 주세요.",
        );
      }
    };

    initGraph();
  }, [loadGraph]);

  // HITL 검토 후 지식그래프 적재 성공 시 (신규 노드 Fly-to + 사이드패널 오픈)
  const handleIngestionSuccess = useCallback(
    async (newNodeId?: number) => {
      const targetId = newNodeId && newNodeId > 0 ? newNodeId : centerNodeId;
      if (targetId) {
        setCenterNodeId(targetId);
        await loadGraph(targetId, false);
        // Three.js 카메라 부드러운 포커싱 & 재배치
        setTimeout(() => {
          canvasRef.current?.recenter();
        }, 150);
      }
    },
    [centerNodeId, loadGraph],
  );

  // 4. 비동기 대기열 폴링 (2.5초 주기)
  useEffect(() => {
    const pollTasks = async () => {
      try {
        const list = await fetchAgentTasks();
        setTasks(list);

        // 사용자가 '검토 없이 완료 즉시 자동 반영'으로 의뢰한 작업이 완료되었을 시 자동 그래프 리프레시
        const autoCommitted = list.find(
          (t) => t.status === "completed" && t.auto_committed && t.primary_node_id,
        );
        if (autoCommitted && autoCommitted.primary_node_id) {
          dismissAgentTask(autoCommitted.id).catch(() => {});
          handleIngestionSuccess(autoCommitted.primary_node_id);
        }
      } catch (err) {
        // 폴링 에러는 조용히 무시
      }
    };
    pollTasks();
    const timer = setInterval(pollTasks, 2500);
    return () => clearInterval(timer);
  }, [handleIngestionSuccess]);

  // 5. 인터랙션 핸들러들
  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      setSelectedNode(node);
      loadGraph(node.id);
    },
    [loadGraph],
  );

  const handlePanBoundary = useCallback(() => {
    if (hasOmitted && !loading && centerNodeId !== null) {
      loadGraph(centerNodeId, true);
    }
  }, [hasOmitted, loading, centerNodeId, loadGraph]);

  const handleSelectSearchedNode = useCallback(
    (item: NodeSearchItem) => {
      canvasRef.current?.cancelIntro();
      loadGraph(item.id);
    },
    [loadGraph],
  );

  // 모달 열기 핸들러들
  const handleOpenInputModal = () => {
    setModalMode("input");
    setReviewTaskId(null);
    setIsModalOpen(true);
  };

  const handleOpenHitlModal = (prod: "news" | "gov" | "excel" = "news") => {
    setHitlProducer(prod);
    setIsHitlOpen(true);
  };

  const handleOpenReviewModal = (taskId: string) => {
    const task = tasks.find((t) => t.id === taskId);
    const src = (task?.source_project || "").toLowerCase();
    if (src.includes("news")) {
      handleOpenHitlModal("news");
    } else if (src.includes("gov")) {
      handleOpenHitlModal("gov");
    } else if (src.includes("excel")) {
      handleOpenHitlModal("excel");
    } else {
      setReviewTaskId(taskId);
      setModalMode("review");
      setIsModalOpen(true);
    }
  };

  const handleDismissTask = async (taskId: string) => {
    try {
      await dismissAgentTask(taskId);
      setTasks((prev) => prev.filter((t) => t.id !== taskId));
    } catch (err) {
      console.error("작업 닫기 실패:", err);
    }
  };

  return (
    <div className={styles.appContainer}>
      {/* 상단 네비게이션 헤더 */}
      <header className={styles.topBar}>
        <div className={styles.brand}>
          <h1>Ontology Map Lite</h1>
          <span className={styles.subText}>8 Core Tables Slim Graph</span>
        </div>

        {/* 검색 컴포넌트 (키보드 방향키 및 Enter 선택 지원) */}
        <NodeSearch onSelectNode={handleSelectSearchedNode} />

        {/* + 지식 추가 버튼 */}
        <button
          type="button"
          className={styles.addKnowledgeBtn}
          onClick={handleOpenInputModal}
        >
          <span>✨</span> + 지식 추가
        </button>

        {/* 전체화면 맞춤 정렬 버튼 */}
        <button
          type="button"
          className={styles.fitBtn}
          onClick={() => canvasRef.current?.fitToView()}
          title="지식맵 전체를 화면에 맞게 정렬합니다"
        >
          <span>⤢</span> 전체화면 맞춤
        </button>

        {/* 백그라운드 대기열 알림 뱃지 */}
        <IngestionQueueNotice
          tasks={tasks}
          onSelectTask={handleOpenReviewModal}
          onDismissTask={handleDismissTask}
        />
      </header>

      {/* 미표시된 이웃 노드가 존재할 때 안내 배너 및 전체 펼치기 버튼 */}
      {hasOmitted && (
        <div className={styles.omittedBanner}>
          <span className={styles.omittedText}>
            ⚠️ 미표시된 이웃 노드가 존재함
          </span>
          <button
            type="button"
            className={styles.expandAllBtn}
            onClick={() => loadGraph(centerNodeId, true)}
            title="생략된 모든 이웃 노드를 캔버스에 추가로 불러옵니다"
          >
            이웃 노드 모두 펼치기
          </button>
        </div>
      )}

      {/* 메인 뷰: 전체 화면 3D 캔버스 */}
      <main className={styles.mainCanvas}>
        {loading && (
          <div className={styles.miniLoadingIndicator}>
            <span className={styles.spinner} />
            <span>지식맵 갱신 중...</span>
          </div>
        )}
        {error && loadingPhase === "hidden" && (
          <div className={styles.errorMessage} role="alert">
            {error}
          </div>
        )}

        <GraphCanvas
          ref={canvasRef}
          nodes={nodes}
          edges={edges}
          centerNodeId={centerNodeId}
          selectedNodeId={selectedNode?.id ?? null}
          onNodeClick={handleNodeClick}
          onPanBoundary={handlePanBoundary}
          onReady={() => setGraphReady(true)}
          introStarted={introStarted}
        />
      </main>

      {/* 우측 슬라이드 사이드 패널 */}
      <SidePanel
        selectedNode={selectedNode}
        onClose={() => setSelectedNode(null)}
      />

      {/* 텍스트 드래그 시 연관 지식 팝오버 */}
      <KnowledgePopover onSelectNode={handleSelectSearchedNode} />

      {/* 지식 인제스트 & HITL 검토 모달 */}
      {isModalOpen && (
        <KnowledgeIngestionModal
          mode={modalMode}
          reviewTaskId={reviewTaskId}
          onClose={() => setIsModalOpen(false)}
          onTaskEnqueued={() => {
            fetchAgentTasks()
              .then(setTasks)
              .catch(() => {});
          }}
          onIngestionSuccess={handleIngestionSuccess}
        />
      )}

      {/* 1440px 와이드 HITL 검토 모달 */}
      <HitlReviewModal
        isOpen={isHitlOpen}
        initialProducer={hitlProducer}
        onClose={() => setIsHitlOpen(false)}
        onIngestionSuccess={(newNodeId) => {
          handleIngestionSuccess(newNodeId);
          setIsHitlOpen(false);
        }}
      />

      {/* 초기 로딩 오버레이 */}
      {loadingPhase !== "hidden" && (
        <div
          className={styles.loadingOverlay}
          data-leaving={loadingPhase === "leaving"}
        >
          <div className={styles.loadingContent}>
            <strong>Ontology Map Lite</strong>
            {loadingPhase === "error" ? (
              <div className={styles.loadingFailure} role="alert">
                <p>{error}</p>
                <button type="button" onClick={() => window.location.reload()}>
                  다시 시도
                </button>
              </div>
            ) : (
              <>
                <span>{loadingProgress}%</span>
                <div
                  className={styles.loadingTrack}
                  role="progressbar"
                  aria-label="지식맵 준비"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={loadingProgress}
                >
                  <i style={{ width: `${loadingProgress}%` }} />
                </div>
                <p className={styles.loadingTip}>{loadingTip}</p>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
