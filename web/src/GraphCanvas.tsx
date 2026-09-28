// web/src/GraphCanvas.tsx
import { useEffect, useRef } from "react";
import ForceGraph3D, { type ForceGraph3DInstance } from "3d-force-graph";
import type { GraphEdge, GraphNode } from "./types";
import styles from "./GraphCanvas.module.css";

interface GraphCanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedNodeId: number | null;
  onNodeClick: (node: GraphNode) => void;
}

export function GraphCanvas({
  nodes,
  edges,
  selectedNodeId,
  onNodeClick,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<ForceGraph3DInstance | null>(null);

  // 1. 3D 그래프 인스턴스 초기화 (최초 마운트 시 1회)
  useEffect(() => {
    if (!containerRef.current) return;

    const graph = new ForceGraph3D(containerRef.current)
      .backgroundColor("#0a0a0c")
      .nodeLabel("name")
      .linkLabel((link: any) => link.label)
      .nodeAutoColorBy("classification_id")
      .nodeRelSize(6)
      .linkOpacity(0.3)
      .linkWidth(1.5)
      .linkDirectionalParticles(2)
      .linkDirectionalParticleWidth(1.2)
      .onNodeClick((nodeObj) => {
        // [빈칸 1] 3d-force-graph에서 클릭된 객체를 GraphNode 타입으로 부모에 전달
        const clickedNode = nodeObj as GraphNode;
        onNodeClick(clickedNode);
      });

    graphRef.current = graph;

    // 윈도우 리사이즈 대응
    const handleResize = () => {
      if (containerRef.current && graphRef.current) {
        graphRef.current.width(containerRef.current.clientWidth);
        graphRef.current.height(containerRef.current.clientHeight);
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      if (graphRef.current) {
        // 인스턴스 정리
        graphRef.current._destructor?.();
        graphRef.current = null;
      }
    };
  }, [onNodeClick]);

  // 2. nodes, edges 또는 selectedNodeId 변경 시 그래프 데이터 갱신
  useEffect(() => {
    if (!graphRef.current) return;

    // 3d-force-graph 형식에 맞춰 매핑: source/target은 id를 가리킴
    const formattedData = {
      nodes: nodes.map((n) => ({
        ...n,
        // 선택된 노드는 크기를 더 크게 강조
        val: n.id === selectedNodeId ? 14 : 7,
      })),
      links: edges.map((e) => ({
        source: e.source_node_id,
        target: e.target_node_id,
        label: e.relation_name ?? e.relation_code,
      })),
    };

    graphRef.current.graphData(formattedData);

    // 선택된 노드가 있으면 카메라를 부드럽게 이동 (선택적)
    if (selectedNodeId) {
      const targetNode = formattedData.nodes.find((n) => n.id === selectedNodeId);
      if (targetNode && (targetNode as any).x !== undefined) {
        // 카메라 거리 조정
        const distance = 120;
        graphRef.current.cameraPosition(
          { x: (targetNode as any).x, y: (targetNode as any).y, z: (targetNode as any).z + distance },
          targetNode as any,
          1000 // 1초 동안 부드럽게 전환
        );
      }
    }
  }, [nodes, edges, selectedNodeId]);

  return <div ref={containerRef} className={styles.canvasContainer} />;
}
