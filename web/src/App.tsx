// web/src/App.tsx
import { useCallback, useEffect, useState } from "react";
import { dismissAgentTask, fetchAgentTasks, fetchSubgraph } from "./api";
import { GraphCanvas } from "./GraphCanvas";
import { SidePanel } from "./SidePanel";
import { NodeSearch } from "./NodeSearch";
import { KnowledgePopover } from "./KnowledgePopover";
import { IngestionQueueNotice } from "./IngestionQueueNotice";
import { KnowledgeIngestionModal } from "./KnowledgeIngestionModal";
import type { ExtractionTaskSummary, GraphEdge, GraphNode, NodeSearchItem } from "./types";
import styles from "./App.module.css";

export function App() {
  // 1. 기본 3D 그래프 상태
  const [centerNodeId, setCenterNodeId] = useState<number>(5);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // 2. 비동기 대기열 & 모달 상태
  const [tasks, setTasks] = useState<ExtractionTaskSummary[]>([]);
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [modalMode, setModalMode] = useState<"input" | "review">("input");
  const [reviewTaskId, setReviewTaskId] = useState<string | null>(null);

  // 3. 중심 노드 변경 시 서브그래프 로드
  const loadGraph = useCallback(async (nodeId: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchSubgraph(nodeId);
      setNodes(res.nodes);
      setEdges(res.edges);

      const center = res.nodes.find((n) => n.id === nodeId) ?? res.nodes[0] ?? null;
      setSelectedNode(center);
    } catch (err: any) {
      setError(err.message ?? "그래프 데이터를 불러오는 중 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadGraph(centerNodeId);
  }, [centerNodeId, loadGraph]);

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
  const handleNodeClick = (node: GraphNode) => {
    setSelectedNode(node);
  };

  const handleSelectSearchedNode = (item: NodeSearchItem) => {
    setCenterNodeId(item.id);
    const existing = nodes.find((n) => n.id === item.id);
    if (existing) {
      setSelectedNode(existing);
    }
  };

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

  // HITL 검토 후 지식그래프 적재 성공 시
  const handleIngestionSuccess = () => {
    loadGraph(centerNodeId);
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

      {/* 메인 뷰: 전체 화면 3D 캔버스 */}
      <main className={styles.mainCanvas}>
        {loading && <div className={styles.overlayMessage}>그래프 로딩 중...</div>}
        {error && <div className={styles.errorMessage}>오류 발생: {error}</div>}

        <GraphCanvas
          nodes={nodes}
          edges={edges}
          selectedNodeId={selectedNode?.id ?? null}
          onNodeClick={handleNodeClick}
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
            // 즉시 대기열 목록을 새로고침
            fetchAgentTasks().then(setTasks).catch(() => {});
          }}
          onIngestionSuccess={handleIngestionSuccess}
        />
      )}
    </div>
  );
}

export default App;
