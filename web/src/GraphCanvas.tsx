// web/src/GraphCanvas.tsx
import ForceGraph3D, { type ForceGraph3DInstance } from "3d-force-graph";
import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type Ref,
} from "react";
import * as THREE from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import styles from "./GraphCanvas.module.css";
import {
  layoutTargets,
  pinPosition,
  type Position,
} from "./graphLayout";
import {
  alignLinks,
  applyNodeVisual,
  endpointId,
  getBaseLinkOpacity,
  makeLinkVisual,
  makeNodeVisual,
  mapTier,
  nodeStyles,
  paintLinkOpacity,
  radiusFor,
  updateLinkPosition,
  type LinkVisual,
  type NodeVisual,
  type RuntimeLink,
  type RuntimeNode,
} from "./graphVisuals";
import { watchBoundaryPan } from "./peripheralPan";
import { placePreviewLabels } from "./previewLabels";
import { samplePreviewMotion } from "./previewMotion";
import type { GraphEdge, GraphNode } from "./types";
import { useGraphIntroAnimation } from "./useGraphIntroAnimation";
import {
  bindPointerInteraction,
  createHoverHighlightManager,
} from "./useGraphPointerInteraction";

export interface GraphCanvasHandle {
  zoomIn: () => void;
  zoomOut: () => void;
  fitToView: () => void;
  recenter: () => void;
  cancelIntro: () => void;
}

interface GraphCanvasProps {
  ref?: Ref<GraphCanvasHandle | null>;
  nodes: GraphNode[];
  edges: GraphEdge[];
  centerNodeId?: number;
  selectedNodeId: number | null;
  onNodeClick: (node: GraphNode) => void;
  onPanBoundary?: () => void;
  onReady?: () => void;
  introStarted?: boolean;
}

export function GraphCanvas({
  ref,
  nodes: rawNodes,
  edges: rawEdges,
  centerNodeId,
  selectedNodeId,
  onNodeClick,
  onPanBoundary,
  onReady,
  introStarted = false,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraph3DInstance<
    RuntimeNode,
    RuntimeLink
  > | null>(null);

  const nodesRef = useRef(new Map<string, RuntimeNode>());
  const linksRef = useRef(new Map<string, RuntimeLink>());
  const nodeVisualsRef = useRef(new Map<string, NodeVisual>());
  const linkVisualsRef = useRef(new Map<string, LinkVisual>());

  const onNodeClickRef = useRef(onNodeClick);
  onNodeClickRef.current = onNodeClick;

  const onPanBoundaryRef = useRef(onPanBoundary);
  onPanBoundaryRef.current = onPanBoundary;

  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  const readyRef = useRef(false);
  const dataInitializedRef = useRef(false);
  const targetCenterRef = useRef<string | null>(null);
  const currentCenterRef = useRef<string | null>(null);
  const animationRef = useRef<number | null>(null);
  const initialFrameRef = useRef<number | null>(null);
  const initialPaintTimerRef = useRef<number | null>(null);

  const [hoveredRelation, setHoveredRelation] = useState<string | null>(null);

  // 중심 노드 ID 결정
  const activeCenterId =
    centerNodeId ??
    selectedNodeId ??
    rawNodes.find((n) => n.tier === "CENTER")?.id ??
    rawNodes[0]?.id ??
    null;

  // 시작 연출과 일반 화면 맞춤에 같은 거리 계산을 사용한다.
  const fitDistance = useCallback((wide = false) => {
    const graph = graphRef.current;
    if (!graph) return;
    const camera = graph.camera() as THREE.PerspectiveCamera;
    const controls = graph.controls() as OrbitControls;

    const tangent = Math.tan((camera.fov * Math.PI) / 360);
    const horizontal = (tangent * graph.width()) / graph.height();
    let distance = Math.max(160, 48 / horizontal, 48 / tangent);
    let frontDepth = 0;

    const visibleNodes = [...nodesRef.current.values()].filter((n) =>
      wide ? true : n.tier === "center" || n.tier === "direct",
    );

    for (const node of visibleNodes) {
      const pos = { x: node.x ?? 0, y: node.y ?? 0, z: node.z ?? 0 };
      const depth = pos.z;
      frontDepth = Math.max(frontDepth, depth);
      distance = Math.max(
        distance,
        depth + (Math.abs(pos.x) + 40) / horizontal,
        depth + (Math.abs(pos.y) + 40) / tangent,
      );
    }

    const targetDistance = wide
      ? distance * 1.5
      : Math.max(110, frontDepth + 36, distance * 0.95);

    return THREE.MathUtils.clamp(
      targetDistance,
      controls.minDistance,
      controls.maxDistance,
    );
  }, []);

  // 2단계 시네마틱 줌인 인트로 연출 훅
  const { cancelIntro, resetIntroState } = useGraphIntroAnimation({
    graphRef,
    containerRef,
    introStarted,
    readyRef,
    fitDistance,
  });

  const fitCamera = useCallback(
    (wide = false) => {
      const graph = graphRef.current;
      const distance = fitDistance(wide);
      if (!graph || distance === undefined) return;
      cancelIntro();
      const controls = graph.controls() as OrbitControls;
      controls.target.set(0, 0, 0);
      graph.cameraPosition(
        { x: 0, y: 0, z: distance },
        { x: 0, y: 0, z: 0 },
        800,
      );
    },
    [cancelIntro, fitDistance],
  );

  useImperativeHandle(
    ref,
    () => ({
      zoomIn: () => {
        cancelIntro();
        const camera = graphRef.current?.camera() as THREE.PerspectiveCamera;
        const controls = graphRef.current?.controls() as OrbitControls;
        if (camera && controls) {
          const offset = camera.position
            .clone()
            .sub(controls.target)
            .multiplyScalar(0.7);
          camera.position.copy(controls.target).add(offset);
          controls.update();
        }
      },
      zoomOut: () => {
        cancelIntro();
        const camera = graphRef.current?.camera() as THREE.PerspectiveCamera;
        const controls = graphRef.current?.controls() as OrbitControls;
        if (camera && controls) {
          const offset = camera.position
            .clone()
            .sub(controls.target)
            .multiplyScalar(1.3);
          camera.position.copy(controls.target).add(offset);
          controls.update();
        }
      },
      fitToView: () => fitCamera(true),
      recenter: () => fitCamera(false),
      cancelIntro,
    }),
    [cancelIntro, fitCamera],
  );

  // Three.js 씬 초기화 & 포스트프로세싱 & OrbitControls 설정
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const labels = new CSS2DRenderer();
    labels.domElement.dataset.graphLabels = "true";
    labels.domElement.style.pointerEvents = "none";
    labels.domElement.style.position = "absolute";
    labels.domElement.style.top = "0";
    labels.domElement.style.left = "0";
    labels.domElement.style.opacity = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches
      ? "1"
      : "0";

    const renderLabels = labels.render.bind(labels);
    labels.render = (scene, camera) => {
      // 1. 실시간 링크 위치 정렬 (초기 로드 및 카메라 변경 시 미스얼라인 방지)
      alignLinks(
        linksRef.current,
        linkVisualsRef.current,
        nodeVisualsRef.current,
        nodesRef.current,
      );

      // 2. 카메라 시점 중심(target)과의 거리에 따른 Proximity Reveal 실시간 계산
      const controls = graphRef.current?.controls() as OrbitControls | undefined;
      if (controls) {
        const targetPos = controls.target;
        for (const [id, node] of nodesRef.current) {
          const visual = nodeVisualsRef.current.get(id);
          if (!visual) continue;
          const nx = node.x ?? visual.position.x ?? 0;
          const ny = node.y ?? visual.position.y ?? 0;
          const dist = Math.hypot(nx - targetPos.x, ny - targetPos.y);
          const proximity = dist < 260 ? Math.max(0, 1 - dist / 260) : 0;
          if (
            Math.abs((visual.userData.proximityReveal ?? 0) - proximity) > 0.02
          ) {
            visual.userData.proximityReveal = proximity;
            applyNodeVisual(
              visual,
              node,
              visual.userData.radius,
              visual.userData.style,
            );
          }
        }
      }

      renderLabels(scene, camera);
      placePreviewLabels(container);
    };

    // 호버 하이라이트 매니저 연결
    const hoverManager = createHoverHighlightManager({
      container,
      nodesRef,
      nodeVisualsRef,
      linksRef,
      linkVisualsRef,
      setHoveredRelation,
    });

    const graph = new ForceGraph3D(container, {
      extraRenderers: [labels],
      controlType: "orbit",
      rendererConfig: {
        antialias: true,
        alpha: false,
        preserveDrawingBuffer: true,
        powerPreference: "high-performance",
      },
    }) as unknown as ForceGraph3DInstance<RuntimeNode, RuntimeLink>;

    graphRef.current = graph;

    graph
      .backgroundColor("#111416")
      .showNavInfo(false)
      .enableNodeDrag(false)
      .enableNavigationControls(true)
      .nodeId("id")
      .nodeLabel(() => "")
      .nodeThreeObject((node) => {
        const strId = String(node.id);
        let visual = nodeVisualsRef.current.get(strId);
        // 레이블 DOM이 분리되었을 경우 대비해 갱신
        if (!visual || !visual.userData.label.element.parentElement) {
          visual = makeNodeVisual(node);
          nodeVisualsRef.current.set(strId, visual);
        }
        visual.position.set(node.x ?? 0, node.y ?? 0, node.z ?? 0);

        // 이름표(Label) HTML 버튼에 호버(mouseenter/mouseleave) 및 클릭(click) 완벽 연동
        const labelEl = visual.userData.label.element;
        labelEl.style.pointerEvents = "auto";
        labelEl.style.cursor = "pointer";
        labelEl.onmouseenter = () => {
          hoverManager.handleNodeHover(node);
        };
        labelEl.onmouseleave = () => {
          hoverManager.handleNodeHover(null);
        };
        labelEl.onclick = (e) => {
          e.stopPropagation();
          cancelIntro();
          onNodeClick(node.originalNode);
        };

        return visual;
      })
      .linkThreeObject((link) => {
        const strId = String(link.id);
        const visual =
          linkVisualsRef.current.get(strId) ?? makeLinkVisual(link);
        const sId = endpointId(link.source);
        const tId = endpointId(link.target);
        const sourceNode = nodesRef.current.get(sId);
        const targetNode = nodesRef.current.get(tId);
        const sourcePos = sourceNode
          ? { x: sourceNode.x ?? 0, y: sourceNode.y ?? 0, z: sourceNode.z ?? 0 }
          : nodeVisualsRef.current.get(sId)?.position ?? { x: 0, y: 0, z: 0 };
        const targetPos = targetNode
          ? { x: targetNode.x ?? 0, y: targetNode.y ?? 0, z: targetNode.z ?? 0 }
          : nodeVisualsRef.current.get(tId)?.position ?? { x: 0, y: 0, z: 0 };

        updateLinkPosition(visual, sourcePos, targetPos);
        linkVisualsRef.current.set(strId, visual);
        return visual;
      })
      .linkDirectionalArrowLength(0)
      .linkHoverPrecision(6)
      .linkPositionUpdate((object, coordinates) =>
        updateLinkPosition(object, coordinates.start, coordinates.end),
      )
      .onNodeHover(hoverManager.handleNodeHover)
      .onLinkHover(hoverManager.handleLinkHover)
      .warmupTicks(0)
      .cooldownTicks(0);

    // D3 물리 시뮬레이션 완전 무효화
    graph.d3Force("charge", null);
    graph.d3Force("link", null);
    graph.d3Force("center", null);

    // 피그마/구글맵 스타일 OrbitControls
    const controls = graph.controls() as OrbitControls;
    controls.enableRotate = false;
    controls.enablePan = true;
    controls.mouseButtons.LEFT = THREE.MOUSE.PAN;
    controls.enableZoom = true;
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 95;
    controls.maxDistance = 2400;

    // Raycaster 기반 정밀 노드/라벨 클릭 판정 리스너 바인딩
    const unbindPointer = bindPointerInteraction({
      container,
      controls,
      graph,
      nodeVisualsRef,
      nodesRef,
      cancelIntro,
      onNodeClick: (node) => onNodeClickRef.current(node),
    });

    // 외곽 경계 패닝 감지기 연결
    const stopWatchingPan = watchBoundaryPan(
      controls,
      () =>
        [...nodesRef.current.values()].map((node) => ({
          x: node.x ?? 0,
          y: node.y ?? 0,
          z: node.z ?? 0,
        })),
      () => readyRef.current && animationRef.current === null,
      () => onPanBoundaryRef.current?.(),
    );

    // 스튜디오 3점 조명 설정
    const renderer = graph.renderer();
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;

    const hemisphere = new THREE.HemisphereLight("#b9d3ff", "#070a10", 0.72);
    const key = new THREE.DirectionalLight("#e8f1ff", 1.4);
    key.position.set(90, 120, 170);
    const rim = new THREE.DirectionalLight("#5a7bff", 0.8);
    rim.position.set(-120, 10, -90);
    graph.lights([hemisphere, key, rim]);

    const resize = () => {
      if (container.clientWidth <= 0 || container.clientHeight <= 0) return;
      graph.width(container.clientWidth).height(container.clientHeight);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);

    return () => {
      unbindPointer();
      hoverManager.cleanup();
      if (initialFrameRef.current !== null) {
        cancelAnimationFrame(initialFrameRef.current);
      }
      if (initialPaintTimerRef.current !== null) {
        window.clearTimeout(initialPaintTimerRef.current);
      }
      initialFrameRef.current = null;
      initialPaintTimerRef.current = null;
      resetIntroState();
      stopWatchingPan();
      observer.disconnect();
      if (animationRef.current !== null) {
        cancelAnimationFrame(animationRef.current);
      }
      for (const visual of nodeVisualsRef.current.values()) {
        visual.userData.label.element.remove();
      }
      graph._destructor();
      graphRef.current = null;
      readyRef.current = false;
      dataInitializedRef.current = false;
      nodesRef.current.clear();
      linksRef.current.clear();
      nodeVisualsRef.current.clear();
      linkVisualsRef.current.clear();
    };
  }, [cancelIntro, resetIntroState]);

  // 데이터 변환 & 결정론적 슬롯 레이아웃 & 큐빅 보간 모션
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || rawNodes.length === 0) return;

    const initial = !dataInitializedRef.current;
    const centerId = String(
      centerNodeId ?? activeCenterId ?? rawNodes[0]?.id ?? "",
    );
    const centerChanged =
      !initial &&
      targetCenterRef.current !== null &&
      targetCenterRef.current !== centerId;

    // 중심 노드가 실제로 바뀌었을 때만 이전 애니메이션을 취소하고 새로운 전환 시작
    if (centerChanged) {
      cancelIntro();
      if (animationRef.current !== null) {
        cancelAnimationFrame(animationRef.current);
        animationRef.current = null;
      }
      targetCenterRef.current = centerId;
    }

    // 중심 노드는 항상 화면 중앙인 월드 원점 (0, 0, 0)을 타겟으로 배치
    const anchor: Position = { x: 0, y: 0, z: 0 };

    // RuntimeNode & RuntimeLink 맵 구성
    const runtimeNodeList: RuntimeNode[] = rawNodes.map((n) => ({
      id: n.id,
      name: n.name,
      kind: n.classification_name ?? n.classification_code ?? "개념",
      kindCode: (n.classification_code ?? "GENERAL").toUpperCase(),
      tier: mapTier(n.tier, n.id === activeCenterId),
      claimCount:
        typeof n.properties?.claim_count === "number"
          ? n.properties.claim_count
          : 1,
      originalNode: n,
    }));

    const runtimeLinkList: RuntimeLink[] = rawEdges.map((e) => {
      const sId = e.source_node_id;
      const tId = e.target_node_id;
      const isBackbone = sId === activeCenterId || tId === activeCenterId;
      const sNode = runtimeNodeList.find((n) => n.id === sId);
      const tNode = runtimeNodeList.find((n) => n.id === tId);
      const isCross =
        !isBackbone && sNode?.tier === "direct" && tNode?.tier === "direct";

      return {
        id: e.id,
        source: sId,
        target: tId,
        tier: mapTier(e.tier, false),
        directionality:
          (e.properties?.directionality as "DIRECTED" | "SYMMETRIC") ??
          "DIRECTED",
        evidenceGroupCount:
          typeof e.properties?.claim_count === "number"
            ? e.properties.claim_count
            : 1,
        conflict: Boolean(e.properties?.conflict),
        label: e.relation_name ?? e.relation_code ?? "연관",
        originalEdge: e,
        isCenterBackbone: isBackbone,
        isCrossLink: isCross,
      };
    });

    const currentPositions = new Map<string, Position>(
      [...nodesRef.current].map(([id, n]) => [
        id,
        { x: n.x ?? 0, y: n.y ?? 0, z: n.z ?? 0 },
      ]),
    );

    // 결정론적 슬롯 타겟 좌표 계산
    const targets = layoutTargets(
      runtimeNodeList,
      centerId,
      anchor,
      runtimeLinkList.map((l) => ({
        source: String(l.source),
        target: String(l.target),
      })),
      centerChanged || initial ? new Map() : currentPositions,
      0.15,
      currentPositions,
    );

    const starts = new Map<string, Position>(currentPositions);
    for (const [id, visual] of nodeVisualsRef.current) {
      starts.set(id, {
        x: visual.position.x,
        y: visual.position.y,
        z: visual.position.z,
      });
    }

    const startVelocities = new Map(
      [...nodeVisualsRef.current].map(([id, visual]) => [
        id,
        visual.userData.velocity.clone(),
      ]),
    );

    for (const target of runtimeNodeList) {
      const strId = String(target.id);
      const position = targets.get(strId);
      if (!position) continue;

      if (!starts.has(strId)) {
        const linkedLink = runtimeLinkList.find(
          (l) =>
            endpointId(l.source) === strId || endpointId(l.target) === strId,
        );
        const neighborId = linkedLink
          ? endpointId(linkedLink.source) === strId
            ? endpointId(linkedLink.target)
            : endpointId(linkedLink.source)
          : null;
        const neighborPos = neighborId ? starts.get(neighborId) : null;
        starts.set(strId, neighborPos ? { ...neighborPos } : { ...anchor });
      }

      const existing = nodesRef.current.get(strId);
      const node = existing ?? { ...target, ...position };
      Object.assign(node, target);
      pinPosition(
        node,
        (centerChanged ? starts : currentPositions).get(strId) ?? position,
      );
      nodesRef.current.set(strId, node);
      starts.set(strId, { x: node.x ?? 0, y: node.y ?? 0, z: node.z ?? 0 });
    }

    for (const target of runtimeLinkList) {
      const strId = String(target.id);
      const existing = linksRef.current.get(strId);
      if (existing) Object.assign(existing, target);
      else linksRef.current.set(strId, { ...target });
    }

    const publishData = () => {
      graph.graphData({
        nodes: [...nodesRef.current.values()],
        links: [...linksRef.current.values()],
      });
    };
    publishData();

    const paint = (
      progress: number,
      move: boolean,
      time = progress,
      duration = 1200,
    ) => {
      for (const [id, node] of nodesRef.current) {
        const target = targets.get(id);
        const start = starts.get(id);
        if (target && start && move && centerChanged) {
          const sample = samplePreviewMotion(
            start,
            target,
            id === centerId
              ? { x: 0, y: 0, z: 0 }
              : (startVelocities.get(id) ?? { x: 0, y: 0, z: 0 }),
            time,
            duration,
          );
          pinPosition(node, sample.position);
          nodeVisualsRef.current
            .get(id)
            ?.userData.velocity.set(
              sample.velocity.x,
              sample.velocity.y,
              sample.velocity.z,
            );
        }

        const visual = nodeVisualsRef.current.get(id);
        if (!visual) continue;
        visual.position.set(node.x ?? 0, node.y ?? 0, node.z ?? 0);
        applyNodeVisual(visual, node, radiusFor(node), nodeStyles[node.tier]);
      }

      alignLinks(
        linksRef.current,
        linkVisualsRef.current,
        nodeVisualsRef.current,
        nodesRef.current,
      );

      for (const [id, link] of linksRef.current) {
        const visual = linkVisualsRef.current.get(id);
        if (!visual) continue;
        const transitionDampen =
          move && centerChanged
            ? 0.35 + 0.65 * (progress > 0.8 ? (progress - 0.8) * 5 : 0)
            : 1;
        visual.userData.opacity = getBaseLinkOpacity(link) * transitionDampen;
        const expanded =
          link.isCenterBackbone ||
          (!link.isCrossLink && link.tier === "direct");
        paintLinkOpacity(visual, visual.userData.reveal, expanded);
      }
    };

    if (initial) {
      dataInitializedRef.current = true;
      targetCenterRef.current = centerId;
      currentCenterRef.current = centerId;

      paint(1, false);
      initialFrameRef.current = requestAnimationFrame(() => {
        initialFrameRef.current = null;
        paint(1, false);
        alignLinks(
          linksRef.current,
          linkVisualsRef.current,
          nodeVisualsRef.current,
          nodesRef.current,
        );
        const distance = fitDistance();
        if (distance === undefined) return;
        const camera = graph.camera() as THREE.PerspectiveCamera;
        const controls = graph.controls() as OrbitControls;
        const reducedMotion = window.matchMedia(
          "(prefers-reduced-motion: reduce)",
        ).matches;
        const startDistance = reducedMotion
          ? distance
          : Math.min(controls.maxDistance, (distance * 3) / 0.95);
        controls.target.set(0, 0, 0);
        camera.position.set(0, 0, startDistance);
        controls.update();
        if (!readyRef.current) {
          readyRef.current = true;
          onReadyRef.current?.();
        }
      });
      initialPaintTimerRef.current = window.setTimeout(() => {
        initialPaintTimerRef.current = null;
        paint(1, false);
        alignLinks(
          linksRef.current,
          linkVisualsRef.current,
          nodeVisualsRef.current,
          nodesRef.current,
        );
      }, 100);
    } else if (centerChanged) {
      const controls = graph.controls() as OrbitControls;
      const startTarget = controls.target.clone();
      const endTarget = new THREE.Vector3(anchor.x, anchor.y, anchor.z);
      const begun = performance.now();
      const duration = 1200;

      const easeInOutCubic = (value: number): number =>
        value < 0.5 ? 4 * value * value * value : 1 - (-2 * value + 2) ** 3 / 2;

      const frame = (now: number) => {
        const progress = Math.min(1, (now - begun) / duration);
        const eased = easeInOutCubic(progress);

        const offset = graph.camera().position.clone().sub(controls.target);
        controls.target.lerpVectors(startTarget, endTarget, eased);
        graph.camera().position.copy(controls.target).add(offset);
        controls.update();

        paint(eased, true, progress, duration);

        if (progress < 1) {
          animationRef.current = requestAnimationFrame(frame);
        } else {
          animationRef.current = null;
          currentCenterRef.current = centerId;
          publishData();
          alignLinks(
            linksRef.current,
            linkVisualsRef.current,
            nodeVisualsRef.current,
            nodesRef.current,
          );
          fitCamera(false);
        }
      };
      animationRef.current = requestAnimationFrame(frame);
    } else if (animationRef.current === null) {
      paint(1, false);
      alignLinks(
        linksRef.current,
        linkVisualsRef.current,
        nodeVisualsRef.current,
        nodesRef.current,
      );
    }
  }, [
    rawNodes,
    rawEdges,
    activeCenterId,
    centerNodeId,
    fitCamera,
    fitDistance,
    cancelIntro,
  ]);

  return (
    <section className={styles.map} aria-label="3D 온톨로지 지식맵">
      <div ref={containerRef} className={styles.canvas} />

      {hoveredRelation && (
        <div className={styles.relationHint} role="status">
          {hoveredRelation}
        </div>
      )}

      <div className={styles.depthNote}>
        좌클릭 드래그로 이동 · 휠로 확대/축소 · 2.5D 실크 보간
      </div>
    </section>
  );
}

export default GraphCanvas;
