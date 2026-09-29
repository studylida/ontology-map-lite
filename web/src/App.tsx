// web/src/App.tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { dismissAgentTask, fetchAgentTasks, fetchSubgraph, searchNodes } from "./api";
import { GraphCanvas, type GraphCanvasHandle } from "./GraphCanvas";
import { SidePanel } from "./SidePanel";
import { NodeSearch } from "./NodeSearch";
import { KnowledgePopover } from "./KnowledgePopover";
import { IngestionQueueNotice } from "./IngestionQueueNotice";
import { KnowledgeIngestionModal } from "./KnowledgeIngestionModal";
import type {
  ExtractionTaskSummary,
  GraphEdge,
  GraphNode,
  NodeSearchItem,
} from "./types";
import styles from "./App.module.css";

export function App() {
  const canvasRef = useRef<GraphCanvasHandle | null>(null);

  // 1. 기본 3D 그래프 상태 (SK하이닉스 487 기본값)
  const [centerNodeId, setCenterNodeId] = useState<number>(487);
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

  // 3. 중심 노드 변경 시 서브그래프 로드 (기존 탐색 노드는 ambient로 누적 보존)
  const loadGraph = useCallback(
    async (nodeId: number, unbounded: boolean = false) => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchSubgraph(nodeId, unbounded);

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
        setSelectedNode(center);
      } catch (err: any) {
        // 지정된 노드가 없을 경우(404 등), DB에 존재하는 유효 노드로 자동 폴백 복구
        try {
          const fallbackList = await searchNodes("");
          const altNode = fallbackList.find((item) => item.id !== nodeId) || fallbackList[0];
          if (altNode) {
            const fallbackRes = await fetchSubgraph(altNode.id, unbounded);
            setCenterNodeId(altNode.id);
            setNodes(fallbackRes.nodes);
            setEdges(fallbackRes.edges);
            setHasOmitted(Boolean(fallbackRes.has_omitted));
            setOmittedCount(fallbackRes.omitted_count ?? 0);
            const center =
              fallbackRes.nodes.find((n) => n.id === altNode.id) ??
              fallbackRes.nodes[0] ??
              null;
            setSelectedNode(center);
            setError(null);
            return;
          }
        } catch {
          // 폴백도 실패한 경우 아래 setError로 처리
        }

        setError(
          err.message ?? "그래프 데이터를 불러오는 중 오류가 발생했습니다.",
        );
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    loadGraph(centerNodeId);
  }, []); // 초기 마운트 시 1회만 로드

  // 4. 비동기 대기열 폴링 (2.5초 주기)
  useEffect(() => {
    const pollTasks = async () => {
      try {
        const list = await fetchAgentTasks();
        setTasks(list);
      } catch (err) {
        // 폴링 에러는 조용히 무시
      }
    };
    pollTasks();
    const timer = setInterval(pollTasks, 2500);
    return () => clearInterval(timer);
  }, []);

  // 5. 인터랙션 핸들러들
  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      setSelectedNode(node);
      loadGraph(node.id);
    },
    [loadGraph],
  );

  const handlePanBoundary = useCallback(() => {
    if (hasOmitted && !loading) {
      loadGraph(centerNodeId, true);
    }
  }, [hasOmitted, loading, centerNodeId, loadGraph]);

  const handleSelectSearchedNode = useCallback(
    (item: NodeSearchItem) => {
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

  const handleOpenReviewModal = (taskId: string) => {
    setReviewTaskId(taskId);
    setModalMode("review");
    setIsModalOpen(true);
  };

  const handleDismissTask = async (taskId: string) => {
    try {
      await dismissAgentTask(taskId);
      setTasks((prev) => prev.filter((t) => t.id !== taskId));
    } catch (err) {
      console.error("작업 닫기 실패:", err);
    }
  };

  // HITL 검토 후 지식그래프 적재 성공 시 (M4.4: 중심 ID가 같아도 graph와 details를 항상 재조회)
  const handleIngestionSuccess = (newNodeId?: number) => {
    const targetId = newNodeId && newNodeId > 0 ? newNodeId : centerNodeId;
    setCenterNodeId(targetId);
    loadGraph(targetId, false);
    if (selectedNode && selectedNode.id === targetId) {
      setSelectedNode({ ...selectedNode });
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

        {/* 검색 컴포넌트 */}
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

        {/* 노드 ID 직접 입력 점프 컨트롤 */}
        <div className={styles.quickNav}>
          <label htmlFor="node-jump-input">중심 노드: </label>
          <input
            id="node-jump-input"
            type="number"
            min={1}
            value={centerNodeId}
            onChange={(e) => {
              const val = Number.parseInt(e.target.value, 10);
              if (!Number.isNaN(val) && val > 0) {
                setCenterNodeId(val);
              }
            }}
            className={styles.jumpInput}
          />
        </div>
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
          <div className={styles.overlayMessage}>그래프 로딩 중...</div>
        )}
        {error && <div className={styles.errorMessage}>오류 발생: {error}</div>}

        <GraphCanvas
          ref={canvasRef}
          nodes={nodes}
          edges={edges}
          centerNodeId={centerNodeId}
          selectedNodeId={selectedNode?.id ?? null}
          onNodeClick={handleNodeClick}
          onPanBoundary={handlePanBoundary}
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
    </div>
  );
}

export default App;
