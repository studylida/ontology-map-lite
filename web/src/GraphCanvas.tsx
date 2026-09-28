// web/src/GraphCanvas.tsx
import { useEffect, useImperativeHandle, useRef, type Ref } from "react";
import ForceGraph3D, { type ForceGraph3DInstance } from "3d-force-graph";
import type { GraphEdge, GraphNode } from "./types";
import styles from "./GraphCanvas.module.css";

export interface GraphCanvasHandle {
  zoomIn: () => void;
  zoomOut: () => void;
  fitToView: () => void;
  recenter: () => void;
}

interface GraphCanvasProps {
  ref?: Ref<GraphCanvasHandle | null>;
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedNodeId: number | null;
  onNodeClick: (node: GraphNode) => void;
}

export function GraphCanvas({
  ref,
  nodes,
  edges,
  selectedNodeId,
  onNodeClick,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<ForceGraph3DInstance | null>(null);

  // 리렌더링 시에도 함수 인스턴스를 유지하여 캔버스 재생성을 방지
  const onNodeClickRef = useRef(onNodeClick);
  useEffect(() => {
    onNodeClickRef.current = onNodeClick;
  }, [onNodeClick]);

  const selectedNodeIdRef = useRef(selectedNodeId);
  useEffect(() => {
    selectedNodeIdRef.current = selectedNodeId;
  }, [selectedNodeId]);

  // 플로팅 컨트롤에서 조작할 수 있는 핸들러 제공
  useImperativeHandle(ref, () => ({
    zoomIn: () => {
      if (!graphRef.current) return;
      const cam = graphRef.current.camera();
      graphRef.current.cameraPosition(
        { x: cam.position.x, y: cam.position.y, z: cam.position.z * 0.75 },
        undefined,
        400
      );
    },
    zoomOut: () => {
      if (!graphRef.current) return;
      const cam = graphRef.current.camera();
      graphRef.current.cameraPosition(
        { x: cam.position.x, y: cam.position.y, z: cam.position.z * 1.35 },
        undefined,
        400
      );
    },
    fitToView: () => {
      if (!graphRef.current) return;
      graphRef.current.zoomToFit(600, 60);
    },
    recenter: () => {
      if (!graphRef.current || !selectedNodeIdRef.current) return;
      const currentNodes = (graphRef.current.graphData().nodes || []) as any[];
      const target = currentNodes.find((n) => n.id === selectedNodeIdRef.current);
      if (target && typeof target.x === "number") {
        const cam = graphRef.current.camera();
        graphRef.current.cameraPosition(
          { x: target.x, y: target.y, z: cam.position.z },
          { x: target.x, y: target.y, z: 0 },
          600
        );
      }
    },
  }));

  // 1. 3D 그래프 인스턴스 초기화 (마운트 시 단 1회만 실행 - 5초 소멸 원천 봉쇄)
  useEffect(() => {
    if (!containerRef.current) return;

    const graph = new ForceGraph3D(containerRef.current)
      .backgroundColor("#0a0a0c")
      .numDimensions(2) // [2.5D 평면 레이아웃] Z축 허공 이탈 완전 차단
      .nodeLabel("name")
      .linkLabel((link: any) => link.label)
      .nodeAutoColorBy("classification_id")
      .nodeRelSize(7)
      .linkOpacity(0.4)
      .linkWidth(1.5)
      .linkDirectionalParticles(2)
      .linkDirectionalParticleWidth(1.4)
      .nodeVal((node: any) => (node.id === selectedNodeIdRef.current ? 15 : 8))
      .onNodeClick((nodeObj) => {
        onNodeClickRef.current(nodeObj as GraphNode);
      });

    // OrbitControls: 회전 잠금 및 평면 이동(Pan) & 줌(Zoom) 고정
    const controls = graph.controls();
    if (controls) {
      controls.enableRotate = false; // 평면 지도로서 3D 회전을 잠가 시야 이탈 방지
      controls.enablePan = true;
      controls.enableZoom = true;
      controls.screenSpacePanning = true;
      controls.mouseButtons = {
        LEFT: 2,   // THREE.MOUSE.PAN
        MIDDLE: 1, // THREE.MOUSE.DOLLY
        RIGHT: 2,  // 우클릭도 PAN 지원
      };
    }

    // 카메라 원거리 클리핑 확장
    const camera = graph.camera();
    if (camera && "far" in camera) {
      camera.far = 10000;
      camera.updateProjectionMatrix();
    }

    graphRef.current = graph;

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
        graphRef.current._destructor?.();
        graphRef.current = null;
      }
    };
  }, []); // 의존성 빈 배열 []: 리렌더링 시 절대 파괴되지 않음

  // 2. nodes, edges 변경 시 데이터 반영 (시뮬레이션 좌표 보존)
  useEffect(() => {
    if (!graphRef.current) return;

    const currentNodes = (graphRef.current.graphData().nodes || []) as any[];
    const coordMap = new Map(
      currentNodes.map((n) => [n.id, { x: n.x, y: n.y, z: 0, vx: n.vx, vy: n.vy }])
    );

    const formattedData = {
      nodes: nodes.map((n) => {
        const prev = coordMap.get(n.id);
        return {
          ...n,
          val: n.id === selectedNodeIdRef.current ? 15 : 8,
          ...(prev ? prev : {}),
        };
      }),
      links: edges.map((e) => ({
        source: e.source_node_id,
        target: e.target_node_id,
        label: e.relation_name ?? e.relation_code,
      })),
    };

    graphRef.current.graphData(formattedData);

    // 데이터가 처음 로드되었을 때 전체 화면 맞춤 1회 실행
    if (nodes.length > 0 && currentNodes.length === 0) {
      setTimeout(() => {
        graphRef.current?.zoomToFit(600, 60);
      }, 350);
    }
  }, [nodes, edges]);

  // 3. 선택된 노드 변경 시: 기존 줌 배율 유지한 채 중앙 슬라이드 이동
  useEffect(() => {
    if (!graphRef.current || !selectedNodeId) return;

    graphRef.current.nodeVal((node: any) => (node.id === selectedNodeId ? 15 : 8));

    const currentNodes = (graphRef.current.graphData().nodes || []) as any[];
    const targetNode = currentNodes.find((n) => n.id === selectedNodeId);

    if (targetNode && typeof targetNode.x === "number" && !Number.isNaN(targetNode.x)) {
      const cam = graphRef.current.camera();
      // Z 거리는 기존 줌 유지, X/Y만 부드럽게 슬라이드
      graphRef.current.cameraPosition(
        { x: targetNode.x, y: targetNode.y, z: cam.position.z },
        { x: targetNode.x, y: targetNode.y, z: 0 },
        800
      );
    }
  }, [selectedNodeId]);

  return <div ref={containerRef} className={styles.canvasContainer} />;
}
