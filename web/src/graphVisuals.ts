// web/src/graphVisuals.ts
import * as THREE from "three";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import styles from "./GraphCanvas.module.css";
import type { GraphEdge, GraphNode, NodeTier } from "./types";

export type LegacyTier = "center" | "direct" | "twoHop" | "threeHop" | "ambient";

export interface RuntimeNode {
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

export interface RuntimeLink {
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

export interface NodeStyle {
  opacity: number;
  emission: number;
  haloOpacity: number;
  haloFactor: number;
  shellOpacity: number;
  labelOpacity: number;
  colorScale: number;
}

export type NodeVisual = THREE.Group & {
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
    isFilteredOut?: boolean;
  };
};

export type LinkVisual = THREE.Group & {
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
    isFilteredOut?: boolean;
  };
};

// 레거시 8대 온톨로지 고유 컬러 팔레트 (한국어 & 영문 코드 공용 매핑)
export const colors: Record<string, string> = {
  COMPANY: "#b085f5", // 선명하고 풍부한 보라 (기업)
  PERSON: "#fb923c", // 선명한 주황 (인물)
  TECH: "#22d3ee", // 생생한 청록/사이언 (기술)
  PROJECT: "#4ade80", // 비비드 에메랄드 그린 (프로젝트)
  PROGRAM: "#34d399", // 산뜻한 민트 (지원사업)
  TOPIC: "#38bdf8", // 밝은 스카이블루 (주제)
  AGENCY: "#f472b6", // 비비드 로즈 핑크 (기관)
  ORGANIZATION: "#f472b6", // 비비드 로즈 핑크 (주관기관)
  FACILITY: "#60a5fa", // 선명한 코발트 블루 (생산시설/공장)
  METRIC: "#facc15", // 골드 옐로우 (지표)
  GENERAL: "#94a3b8", // 기본 슬레이트
  회사: "#b085f5",
  기업: "#b085f5",
  인물: "#fb923c",
  사람: "#fb923c",
  기술: "#22d3ee",
  프로젝트: "#4ade80",
  사업: "#34d399",
  "지원 사업": "#34d399",
  주제: "#38bdf8",
  기관: "#f472b6",
  "주관 기관": "#f472b6",
  "지원 기관": "#f472b6",
  시설: "#60a5fa",
  "생산 시설": "#60a5fa",
  지표: "#facc15",
  사건: "#f472b6",
};

export function getColorForNode(node: RuntimeNode): string {
  return colors[node.kindCode] ?? colors[node.kind] ?? "#94a3b8";
}

export const nodeStyles: Record<LegacyTier, NodeStyle> = {
  center: {
    opacity: 1,
    emission: 1.15,
    haloOpacity: 0,
    haloFactor: 4,
    shellOpacity: 0.5,
    labelOpacity: 1,
    colorScale: 0.95,
  },
  direct: {
    opacity: 0.98,
    emission: 1.05,
    haloOpacity: 0,
    haloFactor: 3.5,
    shellOpacity: 0.45,
    labelOpacity: 0.98,
    colorScale: 0.95,
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
    opacity: 0.90,
    emission: 1.1,
    haloOpacity: 0,
    haloFactor: 2.5,
    shellOpacity: 0,
    labelOpacity: 0.88, // 외곽 노드도 선명하게 식별
    colorScale: 0.95,
  },
};

export const relationOpacity: Record<LegacyTier, number> = {
  center: 0.98,
  direct: 0.92, // 1-hop 간선: 높은 불투명도로 아주 선명함
  twoHop: 0.38, // 2-hop 간선은 부드럽게 톤다운
  threeHop: 0.22,
  ambient: 0.12,
};

export function getBaseLinkOpacity(link: RuntimeLink): number {
  if (link.isCrossLink) {
    return 0.14; // 1-hop 노드 간 횡단 연결선: 주 간선에 방해되지 않도록 매우 은은하게
  }
  if (link.isCenterBackbone) {
    return 0.98; // 중심 노드 <-> 1-hop 주 간선: 최상위 핵심 연결선으로 압도적 선명도!
  }
  if (link.tier === "direct") {
    return 0.88;
  }
  return relationOpacity[link.tier] ?? 0.38;
}

export function getLinkColor(link: RuntimeLink): string {
  if (link.conflict) return "#f43f5e";
  if (link.isCrossLink) return "#2d3748"; // 횡단 연결: 어두운 슬레이트
  if (link.isCenterBackbone) return "#38bdf8"; // 중심-1hop 주 간선: 강렬한 네온 사이언/스카이블루로 즉시 구별!
  if (link.tier === "direct") return "#60a5fa"; // 1-hop 계층 간선: 선명한 코발트 블루
  if (link.tier === "twoHop") return "#334155"; // 2-hop 간선: 부드러운 다크 그레이
  if (link.tier === "ambient") return "#1e293b";
  return "#334155";
}

export function mapTier(
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

export function radiusFor(node: RuntimeNode): number {
  const count = node.claimCount ?? 1;
  // 기본 최소 반지름을 2.8에서 4.8로 약 1.7배 전반적 확대
  let radius = count >= 6 ? 5.1 : count >= 3 ? 3.75 : 2.8;
  radius *= 1.72; // 최소 4.81, 중간 6.45, 대형 8.77
  if (node.tier === "center") {
    radius *= 1.22; // 중심 노드는 더욱 웅장하고 선명하게 강조 (약 10.7)
  }
  return radius;
}

export function makeLabel(node: RuntimeNode): CSS2DObject {
  const element = document.createElement("button");
  element.type = "button";
  element.setAttribute("aria-label", `${node.name} 중심으로 이동`);
  element.className = styles.nodeLabel ?? "";
  element.textContent = node.name;
  element.dataset.nodeId = String(node.id);
  element.dataset.tier = node.tier;
  const obj = new CSS2DObject(element);
  obj.center.set(0, 0.5);
  return obj;
}

export function applyNodeVisual(
  visual: NodeVisual,
  node: RuntimeNode,
  radius: number,
  style: NodeStyle,
  isFilteredOut: boolean = false,
) {
  const isAmbient = node.tier === "ambient";
  const entityColor = new THREE.Color(getColorForNode(node));
  const proximity = visual.userData.proximityReveal ?? 0;
  const hoverOpacity = visual.userData.hoverOpacity ?? 0;
  const effectiveReveal = Math.max(hoverOpacity, proximity);

  const displayColor = entityColor;
  visual.userData.isFilteredOut = isFilteredOut;

  // 표면 색상: 노드 고유 색상으로 풍부하게 채색 (백색 스펙큘러로 바래지 않음)
  visual.userData.surface.material.color.copy(displayColor);
  visual.userData.surface.material.emissive.copy(displayColor);
  visual.userData.surface.material.emissiveIntensity = isFilteredOut
    ? 0.10
    : isAmbient
      ? 0.85 + effectiveReveal * 0.35
      : style.emission * 0.75;
  visual.userData.surface.material.opacity = isFilteredOut
    ? 0.10
    : isAmbient
      ? 0.85 + effectiveReveal * 0.15
      : style.opacity;
  visual.userData.surface.scale.setScalar(radius);

  // 쉘 색상: 노드 고유 색상과 일치 (백색 묻힘 완전 해결)
  visual.userData.shell.material.color.copy(displayColor);
  const shellOpacity = isFilteredOut
    ? 0
    : Math.max(effectiveReveal * 0.6, style.shellOpacity * 0.5);
  visual.userData.shell.material.opacity = shellOpacity;
  visual.userData.shell.visible = shellOpacity > 0.01;
  visual.userData.shell.scale.setScalar(
    radius * (1.15 + effectiveReveal * 0.18),
  );

  visual.userData.occluder.scale.setScalar(radius * 1.05);
  visual.userData.occluder.visible = !isFilteredOut && !isAmbient && visual.userData.surface.material.opacity > 0.05;

  visual.userData.core.visible = false;
  visual.userData.halo.visible = false;

  const isHoveredOrActive = effectiveReveal > 0.05;
  let labelOpacity: number;
  if (isFilteredOut) {
    labelOpacity = isHoveredOrActive ? 0.75 : 0;
  } else {
    labelOpacity = isHoveredOrActive ? 1.0 : style.labelOpacity;
  }

  visual.userData.label.element.style.opacity = String(labelOpacity);
  visual.userData.label.element.style.pointerEvents = labelOpacity > 0.05 ? "auto" : "none";
  visual.userData.label.element.style.visibility = labelOpacity > 0.001 ? "visible" : "hidden";
  visual.userData.label.element.dataset.tier = node.tier;
  if (effectiveReveal > 0.2) {
    visual.userData.label.element.dataset.proximity = "true";
  } else {
    delete visual.userData.label.element.dataset.proximity;
  }
  // 커진 구체 크기에 맞춰 라벨 위치 이격
  visual.userData.label.position.set(radius + 4.8, 0, 0);

  visual.userData.radius = radius;
  visual.userData.style = { ...style };
}

export function makeNodeVisual(node: RuntimeNode): NodeVisual {
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
      color,
      emissive: color,
      emissiveIntensity: 0.75,
      roughness: 0.32,
      metalness: 0.06,
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
      color,
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

export function getFilamentOffsets(count: number): number[] {
  const num = Math.max(1, Math.round(count));
  if (num === 1) return [0];
  const spacing = num <= 5 ? 1.4 : 5.2 / (num - 1);
  return Array.from(
    { length: num },
    (_, index) => (index - (num - 1) / 2) * spacing,
  );
}

export function easeInOutCubic(value: number): number {
  return value < 0.5
    ? 4 * value * value * value
    : 1 - (-2 * value + 2) ** 3 / 2;
}

export function updateFilaments(visual: LinkVisual, expanded: boolean) {
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

export function paintLinkOpacity(
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

export function makeLinkVisual(link: RuntimeLink): LinkVisual {
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
    line.renderOrder = link.isCenterBackbone ? 5 : 1;
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
    const coneRadius = link.isCenterBackbone ? 1.6 : 1.1;
    const coneHeight = link.isCenterBackbone ? 5.5 : 4.2;
    const arrow = new THREE.Mesh(
      new THREE.ConeGeometry(coneRadius, coneHeight, 8),
      new THREE.MeshBasicMaterial({
        color: lineColor,
        transparent: true,
        opacity,
        depthWrite: false,
      }),
    );
    arrow.renderOrder = link.isCenterBackbone ? 6 : 2;
    arrow.raycast = () => {};
    group.add(arrow);
    group.userData.arrow = arrow;
  }

  paintLinkOpacity(group, 1, expanded);
  return group;
}

export function updateLinkPosition(
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

  // 간선 ID 해시 기반 정적 차등 곡률 계산
  const idStr = String(group.userData.linkId);
  let edgeHash = 0;
  for (let i = 0; i < idStr.length; i++) {
    edgeHash = (edgeHash * 31 + idStr.charCodeAt(i)) >>> 0;
  }
  const bowSign = edgeHash % 2 === 0 ? 1 : -1;
  const bowAmount = 6 + (edgeHash % 4) * 3.5;

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

export function endpointId(endpoint: string | number | RuntimeNode): string {
  if (typeof endpoint === "object" && endpoint !== null) {
    return String(endpoint.id);
  }
  return String(endpoint);
}

export function alignLinks(
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
