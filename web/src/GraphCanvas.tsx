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
import {
  CSS2DObject,
  CSS2DRenderer,
} from "three/examples/jsm/renderers/CSS2DRenderer.js";
import styles from "./GraphCanvas.module.css";
import {
  layoutTargets,
  pinPosition,
  type Position,
} from "./graphLayout";
import { watchBoundaryPan } from "./peripheralPan";
import { placePreviewLabels } from "./previewLabels";
import { samplePreviewMotion } from "./previewMotion";
import type { GraphEdge, GraphNode, NodeTier } from "./types";

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
  centerNodeId?: number;
  selectedNodeId: number | null;
  onNodeClick: (node: GraphNode) => void;
  onPanBoundary?: () => void;
}

type LegacyTier = "center" | "direct" | "twoHop" | "threeHop" | "ambient";

interface RuntimeNode {
  id: number | string;
  name: string;
  kind: string;
  kindCode: string;
  tier: LegacyTier;
  claimCount: number;
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  vz?: number;
  fx?: number;
  fy?: number;
  fz?: number;
  originalNode: GraphNode;
}

interface RuntimeLink {
  id: number | string;
  source: number | string | RuntimeNode;
  target: number | string | RuntimeNode;
  tier: LegacyTier;
  directionality: "DIRECTED" | "SYMMETRIC";
  evidenceGroupCount: number;
  conflict: boolean;
  label?: string;
  originalEdge: GraphEdge;
  isCenterBackbone?: boolean;
  isCrossLink?: boolean;
}

interface NodeStyle {
  opacity: number;
  emission: number;
  haloOpacity: number;
  haloFactor: number;
  shellOpacity: number;
  labelOpacity: number;
  colorScale: number;
}

type NodeVisual = THREE.Group & {
  userData: {
    nodeId: string;
    velocity: THREE.Vector3;
    reveal: number;
    hoverOpacity: number;
    neighborReveal: number;
    proximityReveal: number;
    surface: THREE.Mesh<THREE.SphereGeometry, THREE.MeshStandardMaterial>;
    occluder: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>;
    core: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>;
    halo: THREE.Sprite;
    shell: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>;
    label: CSS2DObject;
    radius: number;
    style: NodeStyle;
  };
};

type LinkVisual = THREE.Group & {
  userData: {
    linkId: string;
    lines: THREE.Line[];
    opacity: number;
    reveal: number;
    filamentMix: number;
    filamentFrom: number;
    expanded: boolean;
    changedAt: number;
    endpoints?: number[];
    arrow?: THREE.Mesh<THREE.ConeGeometry, THREE.MeshBasicMaterial>;
  };
};

// 레거시 8대 온톨로지 고유 컬러 팔레트 (한국어 & 영문 코드 공용 매핑)
const colors: Record<string, string> = {
  COMPANY: "#b792f4", // 연보라
  PERSON: "#f5a24b", // 주황
  TECH: "#43c6d9", // 청록
  PROJECT: "#65c98b", // 녹색
  TOPIC: "#65c98b", // 녹색
  AGENCY: "#f17c9e", // 핑크
  METRIC: "#facc15", // 골드 노랑
  GENERAL: "#8fa1b8", // 기본 슬레이트
  회사: "#b792f4",
  인물: "#f5a24b",
  사람: "#f5a24b",
  기술: "#43c6d9",
  프로젝트: "#65c98b",
  주제: "#65c98b",
  기관: "#f17c9e",
  지표: "#facc15",
  사건: "#f17c9e",
};

function getColorForNode(node: RuntimeNode): string {
  return colors[node.kindCode] ?? colors[node.kind] ?? "#8fa1b8";
}

const nodeStyles: Record<LegacyTier, NodeStyle> = {
  center: {
    opacity: 1,
    emission: 1.2,
    haloOpacity: 0,
    haloFactor: 4,
    shellOpacity: 0.6,
    labelOpacity: 1,
    colorScale: 0.95,
  },
  direct: {
    opacity: 0.95,
    emission: 1.0,
    haloOpacity: 0,
    haloFactor: 3.5,
    shellOpacity: 0,
    labelOpacity: 0.95,
    colorScale: 0.9,
  },
  twoHop: {
    opacity: 0.85,
    emission: 0.9,
    haloOpacity: 0,
    haloFactor: 3.0,
    shellOpacity: 0,
    labelOpacity: 0, // 평상시 숨김 (호버 시 표출)
    colorScale: 0.85,
  },
  threeHop: {
    opacity: 0.65,
    emission: 0.65,
    haloOpacity: 0,
    haloFactor: 2.5,
    shellOpacity: 0,
    labelOpacity: 0, // 평상시 숨김 (호버 시 표출)
    colorScale: 0.75,
  },
  ambient: {
    opacity: 0.35,
    emission: 0.4,
    haloOpacity: 0,
    haloFactor: 2.0,
    shellOpacity: 0,
    labelOpacity: 0, // 평상시 숨김 (호버 시 표출)
    colorScale: 0.6,
  },
};

const relationOpacity: Record<LegacyTier, number> = {
  center: 0.85,
  direct: 0.85,
  twoHop: 0.45,
  threeHop: 0.28,
  ambient: 0.15,
};

function getBaseLinkOpacity(link: RuntimeLink): number {
  if (link.isCrossLink) {
    return 0.22; // 1-hop 노드 간 상호 연결선: 가독성을 위해 은은하게 톤다운
  }
  if (link.isCenterBackbone) {
    return 0.88; // 중심 노드와 1-hop 간 주 간선: 선명하게 강조
  }
  return relationOpacity[link.tier] ?? 0.45;
}

function getLinkColor(link: RuntimeLink): string {
  if (link.conflict) return "#f26d78";
  if (link.isCrossLink) return "#475569"; // 1-hop 상호 간선: 차분한 어두운 슬레이트
  if (link.isCenterBackbone) return "#72a7ff"; // 중심 주 간선: 밝고 선명한 블루
  if (link.tier === "ambient") return "#334155";
  return "#5b8cd6"; // 일반 계층 간선
}

function mapTier(
  tier: NodeTier | string | undefined,
  isCenter: boolean,
): LegacyTier {
  if (isCenter || tier === "CENTER") return "center";
  if (tier === "DIRECT") return "direct";
  if (tier === "TWO_HOP") return "twoHop";
  if (tier === "THREE_HOP") return "threeHop";
  if (tier === "AMBIENT" || tier === "ambient") return "ambient";
  return "ambient";
}

function radiusFor(node: RuntimeNode): number {
  const count = node.claimCount ?? 1;
  const radius = count >= 6 ? 3.5 : count >= 3 ? 2.35 : 1.6;
  return radius * 1.75;
}

function makeLabel(node: RuntimeNode): CSS2DObject {
  const element = document.createElement("span");
  element.className = styles.nodeLabel ?? "";
  element.textContent = node.name;
  element.dataset.nodeId = String(node.id);
  element.dataset.tier = node.tier;
  const obj = new CSS2DObject(element);
  obj.center.set(0, 0.5);
  return obj;
}

function applyNodeVisual(
  visual: NodeVisual,
  node: RuntimeNode,
  radius: number,
  style: NodeStyle,
) {
  const isAmbient = node.tier === "ambient";
  const entityColor = new THREE.Color(getColorForNode(node));
  const proximity = visual.userData.proximityReveal ?? 0;
  const hoverOpacity = visual.userData.hoverOpacity ?? 0;
  const effectiveReveal = Math.max(hoverOpacity, proximity);

  const displayColor = isAmbient
    ? new THREE.Color("#64748b").lerp(
        entityColor,
        Math.max(hoverOpacity, proximity * 0.85),
      )
    : entityColor;

  visual.userData.surface.material.color.set(0x000000);
  visual.userData.surface.material.emissive.copy(displayColor);
  visual.userData.surface.material.emissiveIntensity = isAmbient
    ? 0.45 + effectiveReveal * 0.65
    : style.emission;
  visual.userData.surface.material.opacity = isAmbient
    ? 0.35 + effectiveReveal * 0.6
    : style.opacity;
  visual.userData.surface.scale.setScalar(radius);

  visual.userData.occluder.scale.setScalar(radius * 1.05);
  visual.userData.occluder.visible = visual.userData.surface.material.opacity > 0.05;

  visual.userData.core.visible = false;
  visual.userData.halo.visible = false;

  const shellOpacity = Math.max(
    effectiveReveal * 0.8,
    style.shellOpacity,
  );
  visual.userData.shell.material.opacity = shellOpacity;
  visual.userData.shell.visible = shellOpacity > 0.01;
  visual.userData.shell.scale.setScalar(
    radius * (1.15 + effectiveReveal * 0.2),
  );

  const isPrimary = node.tier === "center" || node.tier === "direct";
  const isHoveredOrActive = effectiveReveal > 0.05;
  const labelOpacity = isPrimary
    ? (isHoveredOrActive ? 1.0 : style.labelOpacity)
    : (isHoveredOrActive ? 0.95 : 0);

  visual.userData.label.element.style.opacity = String(labelOpacity);
  visual.userData.label.element.style.pointerEvents = labelOpacity > 0.05 ? "auto" : "none";
  visual.userData.label.element.style.visibility = labelOpacity > 0.001 ? "visible" : "hidden";
  visual.userData.label.element.dataset.tier = node.tier;
  if (effectiveReveal > 0.2) {
    visual.userData.label.element.dataset.proximity = "true";
  } else {
    delete visual.userData.label.element.dataset.proximity;
  }
  visual.userData.label.position.set(radius + 3.5, 0, 0);

  visual.userData.radius = radius;
  visual.userData.style = { ...style };
}

function makeNodeVisual(node: RuntimeNode): NodeVisual {
  const group = new THREE.Group() as NodeVisual;
  group.renderOrder = 20;
  group.raycast = () => (group.visible ? undefined : false);

  const geometry = new THREE.SphereGeometry(1, 28, 18);
  const color = new THREE.Color(getColorForNode(node));

  const occluder = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({
      color: "#111416",
      transparent: true,
      opacity: 1,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  occluder.renderOrder = 10;

  const surface = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({
      color: 0x000000,
      emissive: color,
      emissiveIntensity: 1.0,
      roughness: 0.24,
      metalness: 0.04,
      transparent: true,
      depthTest: false,
      depthWrite: false,
    }),
  );
  surface.renderOrder = 11;

  const core = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      visible: false,
    }),
  );
  core.renderOrder = 12;

  const halo = new THREE.Sprite(
    new THREE.SpriteMaterial({
      color,
      transparent: true,
      visible: false,
    }),
  );
  halo.renderOrder = 9;

  const shell = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({
      color: "#e6f0ff",
      transparent: true,
      side: THREE.BackSide,
      blending: THREE.NormalBlending,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  shell.renderOrder = 13;

  for (const decoration of [halo, shell, core, occluder]) {
    decoration.raycast = () => {};
  }

  const label = makeLabel(node);
  const raycast = surface.raycast.bind(surface);
  surface.raycast = (raycaster, hits) => {
    if (group.visible) raycast(raycaster, hits);
  };

  group.add(halo, occluder, surface, core, shell, label);
  group.addEventListener("removed", () => label.element.remove());

  group.userData = {
    nodeId: String(node.id),
    velocity: new THREE.Vector3(),
    reveal: 1,
    hoverOpacity: 0,
    neighborReveal: 0,
    proximityReveal: 0,
    surface,
    occluder,
    core,
    halo,
    shell,
    label,
    radius: radiusFor(node),
    style: { ...nodeStyles[node.tier] },
  };

  applyNodeVisual(group, node, group.userData.radius, group.userData.style);
  return group;
}

function getFilamentOffsets(count: number): number[] {
  const num = Math.max(1, Math.round(count));
  if (num === 1) return [0];
  const spacing = num <= 5 ? 1.4 : 5.2 / (num - 1);
  return Array.from(
    { length: num },
    (_, index) => (index - (num - 1) / 2) * spacing,
  );
}

function makeLinkVisual(link: RuntimeLink): LinkVisual {
  const group = new THREE.Group() as LinkVisual;
  const opacity = getBaseLinkOpacity(link);
  const lineColor = getLinkColor(link);
  const lineCount = Math.max(1, Math.min(5, Math.round(link.evidenceGroupCount)));

  const lines = Array.from({ length: lineCount }, () => {
    const material = link.conflict
      ? new THREE.LineDashedMaterial({
          color: "#f26d78",
          transparent: true,
          opacity,
          dashSize: 3,
          gapSize: 2,
          depthTest: true,
          depthWrite: false,
        })
      : new THREE.LineBasicMaterial({
          color: lineColor,
          transparent: true,
          opacity,
          depthTest: true,
          depthWrite: false,
        });

    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([]),
      material,
    );
    line.renderOrder = 1;
    line.frustumCulled = false;
    const raycast = line.raycast.bind(line);
    line.raycast = (raycaster, hits) => {
      if (group.visible && material.opacity > 0.001) raycast(raycaster, hits);
    };
    group.add(line);
    return line;
  });

  const expanded = link.isCenterBackbone || (!link.isCrossLink && link.tier === "direct");
  group.userData = {
    linkId: String(link.id),
    lines,
    opacity,
    reveal: 1,
    filamentMix: Number(expanded),
    filamentFrom: Number(expanded),
    expanded,
    changedAt: performance.now(),
  };

  if (link.directionality === "DIRECTED") {
    const arrow = new THREE.Mesh(
      new THREE.ConeGeometry(1.2, 4.5, 8),
      new THREE.MeshBasicMaterial({
        color: lineColor,
        transparent: true,
        opacity,
        depthWrite: false,
      }),
    );
    arrow.raycast = () => {};
    group.add(arrow);
    group.userData.arrow = arrow;
  }

  paintLinkOpacity(group, 1, expanded);
  return group;
}

function updateLinkPosition(
  object: THREE.Object3D,
  start: { x: number; y: number; z: number },
  end: { x: number; y: number; z: number },
): boolean {
  const group = object as LinkVisual;
  const endpoints = [start.x, start.y, start.z, end.x, end.y, end.z];
  if (group.userData.endpoints?.every((value, i) => value === endpoints[i])) {
    return true;
  }
  group.userData.endpoints = endpoints;

  const startPoint = new THREE.Vector3(start.x, start.y, start.z);
  const endPoint = new THREE.Vector3(end.x, end.y, end.z);
  const direction = endPoint.clone().sub(startPoint);
  let perpendicular = new THREE.Vector3(-direction.y, direction.x, 0);
  if (perpendicular.lengthSq() < 0.001) {
    perpendicular.set(1, 0, 0);
  }
  perpendicular.normalize();

  // 간선 ID 해시 기반 정적 차등 곡률 계산 (동적 좌표 해시 제거하여 애니메이션 중 플리커링 및 간선 요동 방지)
  const idStr = String(group.userData.linkId);
  let edgeHash = 0;
  for (let i = 0; i < idStr.length; i++) {
    edgeHash = (edgeHash * 31 + idStr.charCodeAt(i)) >>> 0;
  }
  const bowSign = edgeHash % 2 === 0 ? 1 : -1;
  const bowAmount = 6 + (edgeHash % 4) * 3.5; // 6 ~ 16.5px 안정적 곡률 변위

  const offsets = getFilamentOffsets(group.userData.lines.length);
  group.userData.lines.forEach((line, index) => {
    const offset = perpendicular.clone().multiplyScalar(offsets[index] ?? 0);
    const midpoint = startPoint
      .clone()
      .add(endPoint)
      .multiplyScalar(0.5)
      .add(perpendicular.clone().multiplyScalar(bowSign * bowAmount))
      .add(offset);

    const curve = new THREE.QuadraticBezierCurve3(
      startPoint,
      midpoint,
      endPoint,
    );

    if (line.geometry.getAttribute("position")?.count === 0) {
      line.geometry.deleteAttribute("position");
    }
    line.geometry.setFromPoints(curve.getPoints(14));

    if (
      index === Math.floor((group.userData.lines.length - 1) / 2) &&
      group.userData.arrow
    ) {
      group.userData.arrow.position.copy(curve.getPoint(0.85));
      group.userData.arrow.quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 1, 0),
        curve.getTangent(0.85).normalize(),
      );
    }

    line.geometry.computeBoundingSphere();
    if (line.material instanceof THREE.LineDashedMaterial) {
      line.computeLineDistances();
    }
  });

  return true;
}

function easeInOutCubic(value: number): number {
  return value < 0.5
    ? 4 * value * value * value
    : 1 - (-2 * value + 2) ** 3 / 2;
}

function updateFilaments(visual: LinkVisual, expanded: boolean) {
  const state = visual.userData;
  const now = performance.now();
  if (state.expanded !== expanded) {
    state.filamentFrom = state.filamentMix;
    state.expanded = expanded;
    state.changedAt = now;
  }
  const progress = Math.min(1, (now - state.changedAt) / 400);
  state.filamentMix =
    state.filamentFrom +
    (Number(expanded) - state.filamentFrom) * easeInOutCubic(progress);
}

function paintLinkOpacity(
  visual: LinkVisual,
  reveal: number,
  expanded: boolean,
) {
  updateFilaments(visual, expanded);
  visual.userData.reveal = reveal;
  const opacity = visual.userData.opacity * reveal;
  visual.visible = opacity > 0.001;

  const representative = Math.floor((visual.userData.lines.length - 1) / 2);
  for (const [index, line] of visual.userData.lines.entries()) {
    (line.material as THREE.Material).opacity =
      opacity * (index === representative ? 1 : visual.userData.filamentMix);
  }

  const material = visual.userData.lines[0]?.material as THREE.LineBasicMaterial;
  if (visual.userData.arrow && material) {
    visual.userData.arrow.material.color.copy(material.color);
    visual.userData.arrow.material.opacity = opacity;
  }
}

function endpointId(endpoint: string | number | RuntimeNode): string {
  if (typeof endpoint === "object" && endpoint !== null) {
    return String(endpoint.id);
  }
  return String(endpoint);
}

function alignLinks(
  links: Map<string, RuntimeLink>,
  visuals: Map<string, LinkVisual>,
  nodes: Map<string, NodeVisual>,
  runtimeNodes?: Map<string, RuntimeNode>,
) {
  for (const [id, link] of links) {
    const visual = visuals.get(id);
    if (!visual) continue;
    const sId = endpointId(link.source);
    const tId = endpointId(link.target);
    const sNode = runtimeNodes?.get(sId);
    const tNode = runtimeNodes?.get(tId);
    const sPos = sNode
      ? { x: sNode.x ?? 0, y: sNode.y ?? 0, z: sNode.z ?? 0 }
      : nodes.get(sId)?.position;
    const tPos = tNode
      ? { x: tNode.x ?? 0, y: tNode.y ?? 0, z: tNode.z ?? 0 }
      : nodes.get(tId)?.position;
    if (sPos && tPos) {
      updateLinkPosition(visual, sPos, tPos);
    }
  }
}

export function GraphCanvas({
  ref,
  nodes: rawNodes,
  edges: rawEdges,
  centerNodeId,
  selectedNodeId,
  onNodeClick,
  onPanBoundary,
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

  const readyRef = useRef(false);
  const dataInitializedRef = useRef(false);
  const targetCenterRef = useRef<string | null>(null);
  const currentCenterRef = useRef<string | null>(null);
  const animationRef = useRef<number | null>(null);
  const hoverAnimationRef = useRef<number | null>(null);

  const [hoveredRelation, setHoveredRelation] = useState<string | null>(null);

  // 중심 노드 ID 결정
  const activeCenterId =
    centerNodeId ??
    selectedNodeId ??
    rawNodes.find((n) => n.tier === "CENTER")?.id ??
    rawNodes[0]?.id ??
    null;

  // 1. 카메라 핏 & 거리 계산 헬퍼
  // 1. 카메라 핏 & 거리 계산 헬퍼 (항상 원점 0,0,0 중심 포커싱)
  const fitCamera = useCallback((wide = false) => {
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

    controls.target.set(0, 0, 0);
    graph.cameraPosition(
      { x: 0, y: 0, z: targetDistance },
      { x: 0, y: 0, z: 0 },
      800,
    );
  }, []);

  useImperativeHandle(ref, () => ({
    zoomIn: () => {
      const camera = graphRef.current?.camera() as THREE.PerspectiveCamera;
      const controls = graphRef.current?.controls() as OrbitControls;
      if (camera && controls) {
        const offset = camera.position.clone().sub(controls.target).multiplyScalar(0.7);
        camera.position.copy(controls.target).add(offset);
        controls.update();
      }
    },
    zoomOut: () => {
      const camera = graphRef.current?.camera() as THREE.PerspectiveCamera;
      const controls = graphRef.current?.controls() as OrbitControls;
      if (camera && controls) {
        const offset = camera.position.clone().sub(controls.target).multiplyScalar(1.3);
        camera.position.copy(controls.target).add(offset);
        controls.update();
      }
    },
    fitToView: () => fitCamera(true),
    recenter: () => fitCamera(false),
  }), [fitCamera]);

  // 2. Three.js 씬 초기화 & 포스트프로세싱 & OrbitControls 설정
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const labels = new CSS2DRenderer();
    labels.domElement.dataset.graphLabels = "true";
    labels.domElement.style.pointerEvents = "none";
    labels.domElement.style.position = "absolute";
    labels.domElement.style.top = "0";
    labels.domElement.style.left = "0";

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
          if (Math.abs((visual.userData.proximityReveal ?? 0) - proximity) > 0.02) {
            visual.userData.proximityReveal = proximity;
            applyNodeVisual(visual, node, visual.userData.radius, visual.userData.style);
          }
        }
      }

      renderLabels(scene, camera);
      placePreviewLabels(container);
    };

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
      .onNodeClick((node) => {
        onNodeClickRef.current?.(node.originalNode);
      })
      .onNodeHover((node) => {
        container.style.cursor = node ? "pointer" : "grab";
        highlightHover(node ? String(node.id) : null, null);
      })
      .onLinkHover((link) => {
        container.style.cursor = link ? "pointer" : "grab";
        if (link) {
          const sName =
            typeof link.source === "object"
              ? link.source.name
              : nodesRef.current.get(String(link.source))?.name ?? "노드";
          const tName =
            typeof link.target === "object"
              ? link.target.name
              : nodesRef.current.get(String(link.target))?.name ?? "노드";
          const dir = link.directionality === "DIRECTED" ? "➔" : "↔";
          setHoveredRelation(
            `${sName} ${dir} ${tName} · ${link.label ?? "연관"} · 근거 ${link.evidenceGroupCount}건`,
          );
        } else {
          setHoveredRelation(null);
        }
        highlightHover(null, link ? String(link.id) : null);
      })
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

    readyRef.current = true;

    // 400ms 부드러운 감속 호버 인터랙션 (neighborReveal)
    function highlightHover(nodeId: string | null, linkId: string | null) {
      if (hoverAnimationRef.current !== null) {
        cancelAnimationFrame(hoverAnimationRef.current);
      }

      const activeNodeIds = new Set<string>();
      if (nodeId) activeNodeIds.add(nodeId);
      if (linkId) {
        const link = linksRef.current.get(linkId);
        if (link) {
          activeNodeIds.add(endpointId(link.source));
          activeNodeIds.add(endpointId(link.target));
        }
      }

      const isFocusedLink = (l: RuntimeLink) =>
        linkId !== null
          ? String(l.id) === linkId
          : activeNodeIds.has(endpointId(l.source)) ||
            activeNodeIds.has(endpointId(l.target));

      const neighbors = new Set<string>();
      for (const link of linksRef.current.values()) {
        if (!isFocusedLink(link)) continue;
        neighbors.add(endpointId(link.source));
        neighbors.add(endpointId(link.target));
      }

      const hasActive = activeNodeIds.size > 0 || linkId !== null;

      const nodeTargets = [...nodesRef.current.values()].map((item) => {
        const strId = String(item.id);
        const visual = nodeVisualsRef.current.get(strId);
        const isHovered = activeNodeIds.has(strId);
        const isNeighbor = neighbors.has(strId);
        return {
          visual,
          fromHover: visual?.userData.hoverOpacity ?? 0,
          toHover: isHovered ? 1 : isNeighbor ? 0.8 : 0,
          fromNeighbor: visual?.userData.neighborReveal ?? 0,
          toNeighbor: isNeighbor || isHovered ? 1 : 0,
          isHovered,
          item,
        };
      });

      const linkTargets = [...linksRef.current.values()].map((link) => {
        const strId = String(link.id);
        const visual = linkVisualsRef.current.get(strId);
        const focused = isFocusedLink(link);
        return {
          visual,
          focused,
          fromOpacity: visual?.userData.opacity ?? 0,
          toOpacity: hasActive
            ? focused
              ? 0.95
              : getBaseLinkOpacity(link) * 0.25
            : getBaseLinkOpacity(link),
          link,
        };
      });

      const begun = performance.now();
      const duration = 400;

      const frame = (now: number) => {
        const progress = Math.min(1, (now - begun) / duration);
        const eased = easeInOutCubic(progress);

        for (const t of nodeTargets) {
          if (!t.visual) continue;
          t.visual.userData.hoverOpacity =
            t.fromHover + (t.toHover - t.fromHover) * eased;
          t.visual.userData.neighborReveal =
            t.fromNeighbor + (t.toNeighbor - t.fromNeighbor) * eased;

          applyNodeVisual(
            t.visual,
            t.item,
            t.visual.userData.radius,
            nodeStyles[t.item.tier],
          );
          t.visual.userData.label.element.dataset.focused = String(t.isHovered);
        }

        for (const t of linkTargets) {
          if (!t.visual) continue;
          t.visual.userData.opacity =
            t.fromOpacity + (t.toOpacity - t.fromOpacity) * eased;

          const baseLine = t.visual.userData.lines[0]?.material as
            | THREE.LineBasicMaterial
            | THREE.LineDashedMaterial
            | undefined;
          if (baseLine && !t.link.conflict) {
            const targetColor = t.focused
              ? new THREE.Color("#bad8ff")
              : t.link.tier === "ambient"
                ? new THREE.Color("#475569")
                : new THREE.Color("#72a7ff");
            baseLine.color.lerp(targetColor, eased);
          }

          paintLinkOpacity(t.visual, 1, t.focused);
        }

        if (progress < 1) {
          hoverAnimationRef.current = requestAnimationFrame(frame);
        } else {
          hoverAnimationRef.current = null;
        }
      };

      hoverAnimationRef.current = requestAnimationFrame(frame);
    }

    return () => {
      stopWatchingPan();
      observer.disconnect();
      if (animationRef.current !== null) {
        cancelAnimationFrame(animationRef.current);
      }
      if (hoverAnimationRef.current !== null) {
        cancelAnimationFrame(hoverAnimationRef.current);
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
  }, []);

  // 3. 데이터 변환 & 결정론적 슬롯 레이아웃 & 큐빅 보간 모션
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || rawNodes.length === 0) return;

    const initial = !dataInitializedRef.current;
    const centerId = String(centerNodeId ?? activeCenterId ?? rawNodes[0]?.id ?? "");
    const centerChanged = !initial && (targetCenterRef.current !== null && targetCenterRef.current !== centerId);

    // 중심 노드가 실제로 바뀌었을 때만 이전 애니메이션을 취소하고 새로운 전환 시작
    if (centerChanged) {
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
      const isCross = !isBackbone && sNode?.tier === "direct" && tNode?.tier === "direct";

      return {
        id: e.id,
        source: sId,
        target: tId,
        tier: mapTier(e.tier, false),
        directionality:
          (e.properties?.directionality as "DIRECTED" | "SYMMETRIC") ?? "DIRECTED",
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

    // 결정론적 슬롯 타겟 좌표 계산 (현재 화면 좌표를 참조하여 방사형 각도 보존 및 간선 교차 방지)
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
        // 새로 추가된 노드는 연결된 이웃 노드 위치나 중심 앵커에서 부드럽게 펼쳐짐
        const linkedLink = runtimeLinkList.find(
          (l) => endpointId(l.source) === strId || endpointId(l.target) === strId,
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

    const paint = (progress: number, move: boolean, time = progress, duration = 1200) => {
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
        const expanded = link.isCenterBackbone || (!link.isCrossLink && link.tier === "direct");
        paintLinkOpacity(visual, visual.userData.reveal, expanded);
      }
    };

    if (initial) {
      dataInitializedRef.current = true;
      targetCenterRef.current = centerId;
      currentCenterRef.current = centerId;

      paint(1, false);
      requestAnimationFrame(() => {
        paint(1, false);
        alignLinks(
          linksRef.current,
          linkVisualsRef.current,
          nodeVisualsRef.current,
          nodesRef.current,
        );
        fitCamera(false);
      });
      setTimeout(() => {
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
  }, [rawNodes, rawEdges, activeCenterId, centerNodeId, fitCamera]);

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
