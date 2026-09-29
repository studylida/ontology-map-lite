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
  };
};

// 레거시 8대 온톨로지 고유 컬러 팔레트 (한국어 & 영문 코드 공용 매핑)
export const colors: Record<string, string> = {
  COMPANY: "#b792f4", // 연보라 (기업)
  PERSON: "#f5a24b", // 주황 (인물)
  TECH: "#43c6d9", // 청록 (기술)
  PROJECT: "#65c98b", // 녹색 (프로젝트)
  PROGRAM: "#65c98b", // 녹색 (지원사업)
  TOPIC: "#65c98b", // 녹색 (주제)
  AGENCY: "#f17c9e", // 핑크 (기관)
  ORGANIZATION: "#f17c9e", // 핑크 (주관기관)
  FACILITY: "#38bdf8", // 스카이블루 (생산시설/공장)
  METRIC: "#facc15", // 골드 노랑 (지표)
  GENERAL: "#8fa1b8", // 기본 슬레이트
  회사: "#b792f4",
  기업: "#b792f4",
  인물: "#f5a24b",
  사람: "#f5a24b",
  기술: "#43c6d9",
  프로젝트: "#65c98b",
  사업: "#65c98b",
  "지원 사업": "#65c98b",
  주제: "#65c98b",
  기관: "#f17c9e",
  "주관 기관": "#f17c9e",
  "지원 기관": "#f17c9e",
  시설: "#38bdf8",
  "생산 시설": "#38bdf8",
  지표: "#facc15",
  사건: "#f17c9e",
};

export function getColorForNode(node: RuntimeNode): string {
  return colors[node.kindCode] ?? colors[node.kind] ?? "#8fa1b8";
}

export const nodeStyles: Record<LegacyTier, NodeStyle> = {
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
    opacity: 0.72,
    emission: 0.75,
    haloOpacity: 0,
    haloFactor: 2.2,
    shellOpacity: 0,
    labelOpacity: 0.72, // 평상시에도 외곽 섬노드 이름을 선명하게 식별 가능 (호버 시 1.0)
    colorScale: 0.85,
  },
};

export const relationOpacity: Record<LegacyTier, number> = {
  center: 0.85,
  direct: 0.85,
  twoHop: 0.45,
  threeHop: 0.28,
  ambient: 0.15,
};

export function getBaseLinkOpacity(link: RuntimeLink): number {
  if (link.isCrossLink) {
    return 0.22; // 1-hop 노드 간 상호 연결선: 가독성을 위해 은은하게 톤다운
  }
  if (link.isCenterBackbone) {
    return 0.88; // 중심 노드와 1-hop 간 주 간선: 선명하게 강조
  }
  return relationOpacity[link.tier] ?? 0.45;
}

export function getLinkColor(link: RuntimeLink): string {
  if (link.conflict) return "#f26d78";
  if (link.isCrossLink) return "#475569"; // 1-hop 상호 간선: 차분한 어두운 슬레이트
  if (link.isCenterBackbone) return "#72a7ff"; // 중심 주 간선: 밝고 선명한 블루
  if (link.tier === "ambient") return "#334155";
  return "#5b8cd6"; // 일반 계층 간선
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
  const radius = count >= 6 ? 3.5 : count >= 3 ? 2.35 : 1.6;
  return radius * 1.75;
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

  const isHoveredOrActive = effectiveReveal > 0.05;
  const labelOpacity = isHoveredOrActive ? 1.0 : style.labelOpacity;

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
