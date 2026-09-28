// web/src/App.tsx
import { useCallback, useEffect, useState } from "react";
import { fetchSubgraph } from "./api";
import { GraphCanvas } from "./GraphCanvas";
import { SidePanel } from "./SidePanel";
import { NodeSearch } from "./NodeSearch";
import { KnowledgePopover } from "./KnowledgePopover";
import type { GraphEdge, GraphNode, NodeSearchItem } from "./types";
import styles from "./App.module.css";

export function App() {
  // 1. 상태 정의
  const [centerNodeId, setCenterNodeId] = useState<number>(5); // 기본 탐색 시작 노드 ID (현재 시드된 OpenAI=5)
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // 2. 중심 노드 변경 시 서브그래프 로드
  const loadGraph = useCallback(async (nodeId: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchSubgraph(nodeId);
      setNodes(res.nodes);
      setEdges(res.edges);

      // 중심 노드를 기본 선택 상태로 패널에 표시
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

  // 3. 노드 클릭 핸들러: 노드 선택 및 필요 시 중심 노드로 전환
  const handleNodeClick = (node: GraphNode) => {
    setSelectedNode(node);
    // [선택 사항]: 더블클릭이나 탐색 확장 시 중심 노드 전환도 가능:
    // setCenterNodeId(node.id);
  };

  // 4. 검색 결과 항목 선택 시: 중심 노드 전환 및 사이드 패널 동기화
  const handleSelectSearchedNode = (item: NodeSearchItem) => {
    // [빈칸 1]: 중심 노드 ID(centerNodeId)를 선택한 노드의 ID로 변경하세요.
    setCenterNodeId(item.id);

    // 만약 현재 3D 캔버스(nodes)에 이미 렌더링되어 있다면 즉시 패널/카메라 포커스 전환
    const existing = nodes.find((n) => n.id === item.id);
    if (existing) {
      setSelectedNode(existing);
    }
  };

  return (
    <div className={styles.appContainer}>

      {/* 상단 미니멀 네비게이션 헤더 */}
      <header className={styles.topBar}>
        <div className={styles.brand}>
          <h1>Ontology Map Lite</h1>
          <span className={styles.subText}>8 Core Tables Slim Graph</span>
        </div>

        {/* 검색 컴포넌트: 소괄호 없이 함수 참조만 전달 */}
        <NodeSearch onSelectNode={handleSelectSearchedNode} />

        {/* 간단한 노드 ID 입력 점프 컨트롤 */}
        <div className={styles.quickNav}>
          <label htmlFor="node-jump-input">중심 노드 ID: </label>
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

      {/* 우측 슬라이드/플로팅 사이드 패널 */}
      <SidePanel
        selectedNode={selectedNode}
        onClose={() => setSelectedNode(null)}
      />

      {/* 텍스트 드래그/선택 시 연관 지식 팝오버 */}
      <KnowledgePopover onSelectNode={handleSelectSearchedNode} />
    </div>
  );
}

export default App;
