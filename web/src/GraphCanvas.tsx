import ForceGraph3D, { type ForceGraph3DInstance } from "3d-force-graph";
import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import {
  CSS2DObject,
  CSS2DRenderer,
} from "three/examples/jsm/renderers/CSS2DRenderer.js";
import {
  type ExplorationView,
  getFilamentOffsets,
  type KnowledgeViewNode,
  type KnowledgeViewRelation,
  type NodeTier,
} from "./data";
import styles from "./GraphCanvas.module.css";
import {
  layoutTargets,
  type Position,
  pinPosition,
  retainGraphItems,
} from "./graphLayout";
import { watchBoundaryPan } from "./peripheralPan";
import { placePreviewLabels } from "./previewLabels";
import { preparationDuration, samplePreviewMotion } from "./previewMotion";
import type { EvidenceSelection } from "./RelationPanel";

interface RuntimeNode extends KnowledgeViewNode {
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  vz?: number;
  fx?: number;
  fy?: number;
  fz?: number;
}

interface RuntimeLink extends Omit<KnowledgeViewRelation, "source" | "target"> {
  source: string | RuntimeNode;
  target: string | RuntimeNode;
}

interface CameraSnapshot {
  position: THREE.Vector3;
  target: THREE.Vector3;
  far: number;
  maxDistance: number;
}

export interface GraphFocusRequest {
  key: number;
  nodeIds: string[];
  relationIds?: string[];
}

export interface GraphOverviewRequest {
  key: number;
  action: "show" | "restore";
}

interface GraphCanvasProps {
  designPreview?: boolean;
  theme?: "dark" | "light";
  hiddenKinds?: readonly string[];
  view: ExplorationView;
  introStarted: boolean;
  introCompleted?: boolean;
  pendingNodeId: string | null;
  onSelect: (nodeId: string) => void;
  onTransitionComplete: (nodeId: string) => void;
  onReady: () => void;
  onPanBoundary: () => void;
  panelOpen: boolean;
  focusRequest?: GraphFocusRequest | null;
  overviewRequest?: GraphOverviewRequest | null;
  onOverviewActiveChange?: (active: boolean) => void;
  onIntroComplete: () => void;
  onEvidence: (selection: EvidenceSelection) => void;
}

type GraphControls = OrbitControls;

function overviewDistance(
  nodes: RuntimeNode[],
  center: RuntimeNode,
  camera: THREE.PerspectiveCamera,
  width: number,
  height: number,
  panelOpen: boolean,
): number {
  const tangent = Math.tan((camera.fov * Math.PI) / 360);
  const sideInset = Math.max(340, panelOpen ? 408 : 24);
  const usableWidth = Math.max(220, width - sideInset * 2);
  const usableHeight = Math.max(220, height - 64);
  const horizontal = (tangent * usableWidth) / usableHeight;
  let distance = 150;
  for (const node of nodes) {
    const depth = (node.z ?? 0) - (center.z ?? 0);
    const radius = radiusFor(node) + 20;
    distance = Math.max(
      distance,
      depth + (Math.abs((node.x ?? 0) - (center.x ?? 0)) + radius) / horizontal,
      depth + (Math.abs((node.y ?? 0) - (center.y ?? 0)) + radius) / tangent,
    );
  }
  return distance * 1.15;
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
    designPreview: boolean;
    lightMode: boolean;
    velocity: THREE.Vector3;
    reveal: number;
    hoverOpacity: number;
    neighborReveal: number;
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

const colors = {
  사람: "#f5a24b",
  회사: "#b792f4",
  기술: "#43c6d9",
  주제: "#65c98b",
  사건: "#f17c9e",
};

const nodeStyles: Record<NodeTier, NodeStyle> = {
  center: {
    opacity: 1,
    emission: 1.7,
    haloOpacity: 0.42,
    haloFactor: 6,
    shellOpacity: 0.1,
    labelOpacity: 0.98,
    colorScale: 0.9,
  },
  direct: {
    opacity: 0.9,
    emission: 1.05,
    haloOpacity: 0.19,
    haloFactor: 4.6,
    shellOpacity: 0,
    labelOpacity: 0.66,
    colorScale: 0.86,
  },
  twoHop: {
    opacity: 0.9,
    emission: 1.05,
    haloOpacity: 0.19,
    haloFactor: 4.6,
    shellOpacity: 0,
    labelOpacity: 0.66,
    colorScale: 0.86,
  },
  threeHop: {
    opacity: 0.6,
    emission: 0.6,
    haloOpacity: 0.08,
    haloFactor: 3.8,
    shellOpacity: 0,
    labelOpacity: 0.35,
    colorScale: 0.7,
  },
  ambient: {
    opacity: 0.28,
    emission: 0.46,
    haloOpacity: 0.055,
    haloFactor: 3.8,
    shellOpacity: 0,
    labelOpacity: 0,
    colorScale: 0.58,
  },
};

const relationOpacity = {
  direct: 0.9,
  twoHop: 0.32,
  threeHop: 0.22,
  ambient: 0.12,
} as const;

function radiusFor(node: RuntimeNode): number {
  const activity = node.activityEvidenceGroupCount;
  const radius = activity >= 6 ? 3.5 : activity >= 3 ? 2.35 : 1.6;
  return radius * 1.75;
}

function makeGlowTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 128;
  const context = canvas.getContext("2d");
  if (context) {
    const gradient = context.createRadialGradient(64, 64, 0, 64, 64, 64);
    gradient.addColorStop(0, "rgba(255,255,255,0.95)");
    gradient.addColorStop(0.12, "rgba(255,255,255,0.55)");
    gradient.addColorStop(0.42, "rgba(255,255,255,0.14)");
    gradient.addColorStop(1, "rgba(255,255,255,0)");
    context.fillStyle = gradient;
    context.fillRect(0, 0, 128, 128);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function makeLabel(node: RuntimeNode): CSS2DObject {
  const element = document.createElement("span");
  element.className = styles.nodeLabel ?? "";
  element.textContent = node.name;
  element.dataset.nodeId = node.id;
  element.dataset.tier = node.tier;
  return new CSS2DObject(element);
}

function applyNodeVisual(
  visual: NodeVisual,
  node: RuntimeNode,
  radius: number,
  style: NodeStyle,
) {
  const color = new THREE.Color(
    colors[node.kind as keyof typeof colors] ?? "#8fa1b8",
  );
  if (visual.userData.lightMode) color.multiplyScalar(0.4);
  visual.userData.surface.material.color
    .copy(color)
    .multiplyScalar(style.colorScale);
  visual.userData.surface.material.emissive.copy(color);
  visual.userData.surface.material.emissiveIntensity = style.emission;
  visual.userData.surface.material.opacity = style.opacity;
  visual.userData.surface.scale.setScalar(radius);
  visual.userData.occluder.scale.setScalar(radius * 1.04);
  visual.userData.occluder.visible = style.opacity > 0;
  visual.userData.core.material.color.copy(color);
  visual.userData.core.material.opacity = style.opacity;
  visual.userData.core.scale.setScalar(radius * 0.22);
  const haloMaterial = visual.userData.halo.material as THREE.SpriteMaterial;
  haloMaterial.color.copy(color);
  haloMaterial.opacity = style.haloOpacity;
  visual.userData.halo.scale.setScalar(radius * style.haloFactor);
  visual.userData.shell.material.opacity = style.shellOpacity;
  visual.userData.shell.scale.setScalar(radius * 1.42);
  visual.userData.label.element.style.opacity = String(style.labelOpacity);
  visual.userData.label.element.dataset.tier = node.tier;
  visual.userData.label.position.y = radius + 9;
  if (visual.userData.designPreview) {
    visual.userData.surface.material.color.set(0x000000);
    visual.userData.surface.material.emissiveIntensity = 1;
    visual.userData.label.center.set(0, 0.5);
    visual.userData.label.position.set(radius + 2, 0, 0);
  }
  visual.userData.radius = radius;
  visual.userData.style = { ...style };
}

function paintPreviewNode(
  visual: NodeVisual,
  node: RuntimeNode,
  reveal: number,
  overview = false,
) {
  const { style, hoverOpacity: focus } = visual.userData;
  const base = styleFor(node.tier, true);
  const presence = Math.min(1, style.opacity / base.opacity);
  const near = node.tier === "center" || node.tier === "direct";
  const distanceOpacity = near ? 1 : reveal;
  const revealFocus = overview
    ? 1
    : Math.max(focus, visual.userData.neighborReveal);
  const opacity = distanceOpacity + (1 - distanceOpacity) * revealFocus;
  const baseLabel = near ? 1 : 0;
  const labelOpacity = baseLabel + (1 - baseLabel) * revealFocus;
  visual.userData.reveal = opacity;
  visual.userData.surface.material.opacity =
    (style.opacity + (1 - base.opacity) * presence * revealFocus) * opacity;
  const { surface, core, lightMode } = visual.userData;
  if (overview || near || node.tier === "twoHop") {
    surface.material.emissive.copy(core.material.color);
  } else {
    surface.material.emissive
      .set(lightMode ? "#707070" : "#808080")
      .lerp(core.material.color, revealFocus);
  }
  visual.userData.occluder.material.opacity = opacity * presence;
  visual.userData.label.visible = opacity * labelOpacity * presence > 0.001;
  visual.userData.label.element.style.opacity = String(
    (style.labelOpacity + (1 - base.labelOpacity) * presence * revealFocus) *
      opacity *
      labelOpacity,
  );
}

function makeNodeVisual(node: RuntimeNode, designPreview: boolean): NodeVisual {
  const group = new THREE.Group() as NodeVisual;
  // three-forcegraph의 link group(10) 뒤에 node 전체를 그린다.
  group.renderOrder = 20;
  group.raycast = () => (group.visible ? undefined : false);
  const geometry = new THREE.SphereGeometry(1, 28, 18);
  const color = new THREE.Color(
    colors[node.kind as keyof typeof colors] ?? "#8fa1b8",
  );
  const occluder = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({
      color: "#070a10",
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
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  core.renderOrder = 12;
  const halo = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: designPreview ? null : makeGlowTexture(),
      color,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  halo.renderOrder = 9;
  const shell = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({
      color: "#d9e7ff",
      transparent: true,
      side: THREE.BackSide,
      blending: THREE.AdditiveBlending,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  shell.renderOrder = 13;
  // 발광과 장식 영역은 주변 간선의 클릭을 가로채지 않는다.
  for (const decoration of [halo, shell, core, occluder])
    decoration.raycast = () => {};
  const label = makeLabel(node);
  if (designPreview) {
    halo.visible = false;
    shell.visible = false;
    core.visible = false;
    surface.material.toneMapped = false;
    surface.material.fog = false;
    occluder.material.color.set("#111416");
  }
  const raycast = surface.raycast.bind(surface);
  surface.raycast = (raycaster, hits) => {
    if (group.visible) raycast(raycaster, hits);
  };
  group.add(halo, occluder, surface, core, shell, label);
  group.addEventListener("removed", () => label.element.remove());
  group.userData = {
    nodeId: node.id,
    designPreview,
    lightMode: false,
    velocity: new THREE.Vector3(),
    reveal: 1,
    hoverOpacity: 0,
    neighborReveal: 0,
    surface,
    occluder,
    core,
    halo,
    shell,
    label,
    radius: radiusFor(node),
    style: { ...styleFor(node.tier, designPreview) },
  };
  applyNodeVisual(group, node, group.userData.radius, group.userData.style);
  return group;
}

function makeLinkVisual(link: RuntimeLink, designPreview: boolean): LinkVisual {
  const group = new THREE.Group() as LinkVisual;
  const opacity = relationOpacity[link.tier];
  const lines = Array.from(
    { length: Math.max(1, Math.round(link.evidenceGroupCount)) },
    () => {
      const material = link.conflict
        ? new THREE.LineDashedMaterial({
            color: designPreview ? "#F26D78" : "#e6a23c",
            transparent: true,
            opacity,
            dashSize: 3,
            gapSize: 2,
            depthTest: true,
            depthWrite: false,
          })
        : new THREE.LineBasicMaterial({
            color: getComputedStyle(document.documentElement)
              .getPropertyValue("--relation")
              .trim(),
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
    },
  );
  const expanded = !designPreview || link.tier === "direct";
  group.userData = {
    linkId: link.id,
    lines,
    opacity,
    reveal: 1,
    filamentMix: Number(expanded),
    filamentFrom: Number(expanded),
    expanded,
    changedAt: performance.now(),
  };
  paintLinkOpacity(group, 1, expanded);
  if (link.directionality === "DIRECTED") {
    const arrow = new THREE.Mesh(
      new THREE.ConeGeometry(1, 4, 8),
      new THREE.MeshBasicMaterial({
        transparent: true,
        opacity: 0,
        depthWrite: false,
        toneMapped: !designPreview,
      }),
    );
    arrow.raycast = () => {};
    group.add(arrow);
    group.userData.arrow = arrow;
  }
  return group;
}

function updateLinkPosition(
  object: THREE.Object3D,
  start: { x: number; y: number; z: number },
  end: { x: number; y: number; z: number },
): boolean {
  const group = object as LinkVisual;
  const endpoints = [start.x, start.y, start.z, end.x, end.y, end.z];
  if (group.userData.endpoints?.every((value, i) => value === endpoints[i]))
    return true;
  group.userData.endpoints = endpoints;
  const startPoint = new THREE.Vector3(start.x, start.y, start.z);
  const endPoint = new THREE.Vector3(end.x, end.y, end.z);
  const direction = endPoint.clone().sub(startPoint);
  const perpendicular = new THREE.Vector3(-direction.y, direction.x, 0);
  if (perpendicular.lengthSq() < 0.001) perpendicular.set(1, 0, 0);
  perpendicular.normalize();
  const offsets = getFilamentOffsets(group.userData.lines.length);
  group.userData.lines.forEach((line, index) => {
    const offset = perpendicular.clone().multiplyScalar(offsets[index] ?? 0);
    const midpoint = startPoint
      .clone()
      .add(endPoint)
      .multiplyScalar(0.5)
      .add(perpendicular.clone().multiplyScalar(4))
      .add(offset);
    const curve = new THREE.QuadraticBezierCurve3(
      startPoint,
      midpoint,
      endPoint,
    );
    if (line.geometry.getAttribute("position")?.count === 0)
      line.geometry.deleteAttribute("position");
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
    if (line.material instanceof THREE.LineDashedMaterial)
      line.computeLineDistances();
  });
  return true;
}

function updateFilaments(visual: LinkVisual, expanded: boolean) {
  const state = visual.userData;
  const now = performance.now();
  if (state.expanded !== expanded) {
    state.filamentFrom = state.filamentMix;
    state.expanded = expanded;
    state.changedAt = now;
  }
  const progress = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ? 1
    : Math.min(1, (now - state.changedAt) / 450);
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
  for (const [index, line] of visual.userData.lines.entries())
    (line.material as THREE.Material).opacity =
      opacity * (index === representative ? 1 : visual.userData.filamentMix);
  const material = visual.userData.lines[0]
    ?.material as THREE.LineBasicMaterial;
  if (visual.userData.arrow && material) {
    visual.userData.arrow.material.color.copy(material.color);
    visual.userData.arrow.material.opacity = opacity;
  }
}

function filterPulse(elapsed: number, reducedMotion: boolean) {
  if (elapsed < 0 || elapsed >= 2500) return 0;
  if (reducedMotion) return 0.55;
  const envelope = Math.min(1, elapsed / 300, (2500 - elapsed) / 500);
  return envelope * (0.35 + 0.3 * (1 - Math.cos((elapsed * Math.PI) / 600)));
}

function easeInOutCubic(value: number): number {
  return value < 0.5
    ? 4 * value * value * value
    : 1 - (-2 * value + 2) ** 3 / 2;
}

function endpointId(endpoint: string | RuntimeNode): string {
  return typeof endpoint === "string" ? endpoint : endpoint.id;
}

function alignLinks(
  links: Map<string, RuntimeLink>,
  visuals: Map<string, LinkVisual>,
  nodes: Map<string, NodeVisual>,
) {
  for (const [id, link] of links) {
    const visual = visuals.get(id);
    const source = nodes.get(endpointId(link.source));
    const target = nodes.get(endpointId(link.target));
    if (visual && source && target)
      updateLinkPosition(visual, source.position, target.position);
  }
}

function previewLinkColor(link: RuntimeLink, focused = false, light = false) {
  if (link.conflict) return light ? "#b52c42" : "#F26D78";
  if (focused) return light ? "#548ce0" : "#bad8ff";
  return link.tier === "direct"
    ? light
      ? "#245ac1"
      : "#72A7FF"
    : light
      ? "#576d83"
      : "#829bb5";
}

const previewNodeStyles = Object.fromEntries(
  Object.entries(nodeStyles).map(([tier, style]) => [
    tier,
    {
      ...style,
      opacity: {
        center: 1,
        direct: 0.98,
        twoHop: 0.55,
        threeHop: 0.28,
        ambient: 0.18,
      }[tier],
      shellOpacity: tier === "direct" ? 0.3 : 0,
      labelOpacity: {
        center: 1,
        direct: 0.98,
        twoHop: 0.9,
        threeHop: 0.55,
        ambient: 0,
      }[tier],
    },
  ]),
) as Record<NodeTier, NodeStyle>;

function styleFor(tier: NodeTier, preview: boolean): NodeStyle {
  return preview ? previewNodeStyles[tier] : nodeStyles[tier];
}

function prepareWhileWaiting(
  nodes: Map<string, NodeVisual>,
  links: Map<string, RuntimeLink>,
  visuals: Map<string, LinkVisual>,
  selectedId: string,
) {
  const selected =
    nodes.get(selectedId)?.position.clone() ?? new THREE.Vector3();
  const starts = [...nodes].map(([id, visual]) => {
    const direction = visual.position.clone().sub(selected).setZ(0).normalize();
    if (id === selectedId) visual.userData.velocity.set(0, 0, 0);
    return {
      visual,
      start: visual.position.clone(),
      target: visual.position.clone().addScaledVector(direction, 2),
    };
  });
  const begun = performance.now();
  let frameId: number;
  const frame = (now: number) => {
    const t = Math.min(1, (now - begun) / preparationDuration);
    const eased = 1 - (1 - t) ** 3;
    for (const { visual, start, target } of starts) {
      visual.position.lerpVectors(start, target, eased);
      visual.userData.velocity
        .copy(target)
        .sub(start)
        .multiplyScalar((3 * (1 - t) ** 2) / preparationDuration);
    }
    alignLinks(links, visuals, nodes);
    if (t < 1) frameId = requestAnimationFrame(frame);
  };
  frameId = requestAnimationFrame(frame);
  return () => cancelAnimationFrame(frameId);
}

function floatWhileWaiting(
  nodes: Map<string, NodeVisual>,
  links: Map<string, RuntimeLink>,
  visuals: Map<string, LinkVisual>,
  selectedId: string,
) {
  const starts = [...nodes].map(([id, visual], index) => ({
    visual,
    position: visual.position.clone(),
    phase: index * 2.4,
    selected: id === selectedId,
  }));
  const begun = performance.now();
  let frameId: number;
  const frame = (now: number) => {
    const elapsed = now - begun;
    const amplitude = Math.min(1, elapsed / 160);
    for (const { visual, position, phase, selected } of starts) {
      if (selected) continue;
      visual.position.set(
        position.x + Math.sin(elapsed / 900 + phase) * amplitude * 0.8,
        position.y + Math.sin(elapsed / 700 + phase) * amplitude * 1.8,
        position.z,
      );
    }
    alignLinks(links, visuals, nodes);
    frameId = requestAnimationFrame(frame);
  };
  frameId = requestAnimationFrame(frame);
  return () => cancelAnimationFrame(frameId);
}

const EMPTY_KINDS: readonly string[] = [];

export function GraphCanvas({
  designPreview = false,
  theme = "dark",
  hiddenKinds = EMPTY_KINDS,
  view,
  introStarted,
  introCompleted = false,
  pendingNodeId,
  onSelect,
  onTransitionComplete,
  onReady,
  onEvidence,
  onPanBoundary,
  panelOpen,
  focusRequest = null,
  overviewRequest = null,
  onOverviewActiveChange = () => undefined,
  onIntroComplete,
}: GraphCanvasProps) {
  const centerId = view.centerId;
  const centerIdRef = useRef(centerId);
  centerIdRef.current = centerId;
  const panelOpenRef = useRef(panelOpen);
  panelOpenRef.current = panelOpen;
  const hiddenKindsRef = useRef(hiddenKinds);
  const filterChangedAtRef = useRef(-Infinity);
  const refreshVisibilityRef = useRef<() => void>(() => {});
  useEffect(() => {
    if (hiddenKindsRef.current === hiddenKinds) return;
    hiddenKindsRef.current = hiddenKinds;
    filterChangedAtRef.current = performance.now();
    focusPathRef.current(null);
    setHoveredRelation(null);
    refreshVisibilityRef.current();
  }, [hiddenKinds]);
  const themeRef = useRef(theme);
  themeRef.current = theme;
  const nearViewRef = useRef<{ distance: number; anchor: Position } | null>(
    null,
  );
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraph3DInstance<
    RuntimeNode,
    RuntimeLink
  > | null>(null);
  const nodesRef = useRef(new Map<string, RuntimeNode>());
  const linksRef = useRef(new Map<string, RuntimeLink>());
  const nodeVisualsRef = useRef(new Map<string, NodeVisual>());
  const linkVisualsRef = useRef(new Map<string, LinkVisual>());
  const linkFocusRef = useRef<(link: RuntimeLink) => boolean>(() => false);
  const previousCenterRef = useRef(centerId);
  const viewNodesRef = useRef(new Set(view.nodes.map((node) => node.id)));
  useEffect(() => {
    viewNodesRef.current = new Set(view.nodes.map((node) => node.id));
  }, [view]);
  const onSelectRef = useRef(onSelect);
  const onTransitionCompleteRef = useRef(onTransitionComplete);
  const onReadyRef = useRef(onReady);
  const focusPathRef = useRef<(nodeId: string | null) => void>(() => {});
  const focusSelectionRef = useRef<
    (nodeIds: string[], relationIds: string[]) => void
  >(() => {});
  const pointerNodeRef = useRef<string | null>(null);
  const pointerRelationRef = useRef<string | null>(null);
  const focusTimeoutRef = useRef<number | null>(null);
  const overviewVisibleRef = useRef(false);
  const overviewActiveRef = useRef(false);
  const overviewSnapshotRef = useRef<CameraSnapshot | null>(null);
  const overviewReturnRef = useRef<number | null>(null);
  const overviewFinishRef = useRef<number | null>(null);
  const restoreOverviewRef = useRef<() => void>(() => {});
  const resetOverviewRef = useRef<() => void>(() => {});
  const dataInitializedRef = useRef(false);
  const readyRef = useRef(false);
  const readyFrameRef = useRef<number | null>(null);
  const introCompletedRef = useRef(false);
  const introRevealRef = useRef<number | null>(null);
  const animationRef = useRef<number | null>(null);
  const preparingRef = useRef(false);
  const resizeDeadlineRef = useRef<number | null>(null);
  const hoverAnimationRef = useRef<number | null>(null);
  const introTimeoutRef = useRef<number | null>(null);
  const [busy, setBusy] = useState(true);
  const [hoveredRelation, setHoveredRelation] = useState<string | null>(null);
  const focusRelationRef = useRef<(id: string | null) => void>(() => {});
  const onEvidenceRef = useRef(onEvidence);
  const onOverviewActiveRef = useRef(onOverviewActiveChange);
  useEffect(() => {
    onOverviewActiveRef.current = onOverviewActiveChange;
  }, [onOverviewActiveChange]);
  const onIntroRef = useRef(onIntroComplete);
  useEffect(() => {
    onIntroRef.current = onIntroComplete;
  }, [onIntroComplete]);
  const onPanRef = useRef(onPanBoundary);
  useEffect(() => {
    onPanRef.current = onPanBoundary;
  }, [onPanBoundary]);
  const relationButtonsRef = useRef(new Map<string, HTMLButtonElement>());
  useEffect(() => {
    onEvidenceRef.current = onEvidence;
  }, [onEvidence]);
  const relationName = useCallback(
    (relation: KnowledgeViewRelation) => {
      const source =
        view.nodes.find((node) => node.id === relation.source)?.name ?? "노드";
      const target =
        view.nodes.find((node) => node.id === relation.target)?.name ?? "노드";
      const direction = relation.directionality === "DIRECTED" ? "→" : "↔";
      return `${source} ${direction} ${target} · ${relation.label} · 서로 다른 근거 ${relation.evidenceGroupCount}개${relation.conflict ? " · 충돌 관계" : ""}`;
    },
    [view.nodes],
  );
  const relationActionsRef = useRef(new Map<string, EvidenceSelection>());
  useEffect(() => {
    relationActionsRef.current = new Map(
      view.relations.map((relation) => [
        relation.id,
        { id: relation.id, label: relationName(relation) },
      ]),
    );
    setHoveredRelation(null);
  }, [view, relationName]);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    onTransitionCompleteRef.current = onTransitionComplete;
  }, [onTransitionComplete]);

  useEffect(() => {
    onReadyRef.current = onReady;
  }, [onReady]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const labels = new CSS2DRenderer();
    labels.domElement.dataset.graphLabels = "true";
    labels.domElement.style.pointerEvents = "none";

    let neighborhoodReveal = 1;
    let closeView = false;
    const nodeIsVisible = (id: string) => {
      const node = nodesRef.current.get(id);
      return !!node && !hiddenKindsRef.current.includes(node.kindCode);
    };
    const linkIsVisible = (link: RuntimeLink) =>
      nodeIsVisible(endpointId(link.source)) &&
      nodeIsVisible(endpointId(link.target)) &&
      (!designPreview ||
        overviewVisibleRef.current ||
        link.tier === "direct" ||
        linkFocusRef.current(link) ||
        (!closeView && link.tier === "twoHop"));
    refreshVisibilityRef.current = () =>
      graphRef.current?.linkVisibility(linkIsVisible);
    const renderLabels = labels.render.bind(labels);
    const updateDisplay = (camera: THREE.Camera) => {
      const near = nearViewRef.current;
      const controls = graphRef.current?.controls() as
        | GraphControls
        | undefined;
      if (designPreview && near && controls) {
        const offset = controls.target.distanceTo(
          new THREE.Vector3(near.anchor.x, near.anchor.y, near.anchor.z),
        );
        const reveal =
          introRevealRef.current ??
          easeInOutCubic(
            Math.min(
              1,
              Math.max(
                0,
                offset /
                  (Math.min(
                    near.distance,
                    camera.position.distanceTo(controls.target),
                  ) *
                    0.25),
                (camera.position.distanceTo(controls.target) / near.distance -
                  1) /
                  0.2,
              ),
            ),
          );
        neighborhoodReveal = reveal;
        const close = reveal <= 0.001;
        if (close !== closeView) {
          closeView = close;
          graphRef.current?.linkVisibility(linkIsVisible);
        }
        for (const [id, visual] of nodeVisualsRef.current) {
          const node = nodesRef.current.get(id);
          if (node)
            paintPreviewNode(visual, node, reveal, overviewVisibleRef.current);
        }
      }
      const pulse = filterPulse(
        performance.now() - filterChangedAtRef.current,
        window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      );
      for (const [id, visual] of nodeVisualsRef.current) {
        visual.visible =
          nodeIsVisible(id) &&
          visual.userData.reveal > 0.001 &&
          visual.userData.style.opacity > 0.001;
        const shell = visual.userData.shell;
        const shellEmphasis = Math.max(
          visual.userData.hoverOpacity,
          pulse,
          visual.userData.style.shellOpacity,
        );
        shell.material.opacity =
          shellEmphasis *
          (visual.userData.lightMode ? 0.22 : 0.5) *
          visual.userData.reveal;
        shell.visible = shell.material.opacity > 0;
        shell.material.blending = THREE.NormalBlending;
        shell.scale.setScalar(
          visual.userData.radius *
            (1.08 + 0.22 * Math.max(visual.userData.hoverOpacity, pulse)),
        );
      }
      for (const [id, visual] of linkVisualsRef.current) {
        const link = linksRef.current.get(id);
        if (!link) continue;
        const source = nodeVisualsRef.current.get(endpointId(link.source));
        const target = nodeVisualsRef.current.get(endpointId(link.target));
        const reveal = Math.min(
          source?.userData.reveal ?? 0,
          target?.userData.reveal ?? 0,
          link.tier === "direct" ||
            linkFocusRef.current(link) ||
            overviewVisibleRef.current
            ? 1
            : neighborhoodReveal,
        );
        paintLinkOpacity(
          visual,
          linkIsVisible(link) ? reveal : 0,
          !designPreview ||
            overviewVisibleRef.current ||
            link.tier === "direct" ||
            linkFocusRef.current(link),
        );
      }
    };
    labels.render = (scene, camera) => {
      updateDisplay(camera);
      renderLabels(scene, camera);
      if (designPreview) placePreviewLabels(container);
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
    // WebGL보다 늦게 실행되는 label renderer에만 맡기면 갱신 직후 기본 밝기가 한 프레임 노출된다.
    graph.scene().onBeforeRender = (_renderer, _scene, camera) =>
      updateDisplay(camera);
    graph
      .backgroundColor(designPreview ? "#111416" : "#070a10")
      .showNavInfo(false)
      .enableNodeDrag(false)
      .enableNavigationControls(true)
      .nodeId("id")
      .nodeLabel(() => "")
      .nodeThreeObject((node) => {
        const visual =
          nodeVisualsRef.current.get(node.id) ??
          makeNodeVisual(node, designPreview);
        visual.visible = !hiddenKindsRef.current.includes(node.kindCode);
        nodeVisualsRef.current.set(node.id, visual);
        return visual;
      })
      .linkThreeObject((link) => {
        const visual =
          linkVisualsRef.current.get(link.id) ??
          makeLinkVisual(link, designPreview);
        // 표시 범위 변화로 paint 이후 다시 생성돼도 첫 raycast 전에 좌표를 채운다.
        const source = nodeVisualsRef.current.get(endpointId(link.source));
        const target = nodeVisualsRef.current.get(endpointId(link.target));
        if (source && target)
          updateLinkPosition(visual, source.position, target.position);
        if (designPreview)
          for (const line of visual.userData.lines) {
            (line.material as THREE.LineBasicMaterial).color.set(
              previewLinkColor(
                link,
                linkFocusRef.current(link),
                themeRef.current === "light",
              ),
            );
            (line.material as THREE.LineBasicMaterial).toneMapped = false;
          }
        linkVisualsRef.current.set(link.id, visual);
        return visual;
      })
      .linkDirectionalArrowLength(0)
      .linkHoverPrecision(6)
      .onLinkHover((link) => {
        pointerRelationRef.current = link?.id ?? null;
        if (link) pointerNodeRef.current = null;
        if (focusTimeoutRef.current === null)
          focusRelationRef.current(link?.id ?? null);
      })
      .onLinkClick((link) => {
        const selection = relationActionsRef.current.get(link.id);
        if (selection && linkIsVisible(link)) {
          relationButtonsRef.current.get(link.id)?.focus();
          onEvidenceRef.current(selection);
        }
      })
      .linkPositionUpdate((object, coordinates) =>
        updateLinkPosition(object, coordinates.start, coordinates.end),
      )
      .onNodeClick((node) => {
        if (viewNodesRef.current.has(node.id) && nodeIsVisible(node.id))
          onSelectRef.current(node.id);
      })
      .onNodeHover((node) => {
        pointerNodeRef.current =
          node && nodeIsVisible(node.id) ? node.id : null;
        if (node) pointerRelationRef.current = null;
        if (focusTimeoutRef.current === null) {
          setHoveredRelation(null);
          focusPathRef.current(pointerNodeRef.current);
        }
        container.style.cursor = node ? "pointer" : "grab";
      })
      .warmupTicks(0)
      .cooldownTicks(0);

    graph.linkVisibility(linkIsVisible);
    const highlight = (nodeIds: Set<string>, relationIds: Set<string>) => {
      container.style.cursor =
        nodeIds.size || relationIds.size ? "pointer" : "grab";
      const reducedMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)",
      ).matches;
      const isFocused = (link: RuntimeLink) =>
        relationIds.size > 0
          ? relationIds.has(link.id)
          : nodeIds.has(endpointId(link.source)) ||
            nodeIds.has(endpointId(link.target));
      linkFocusRef.current = isFocused;
      const neighbors = new Set<string>();
      for (const link of linksRef.current.values()) {
        if (!isFocused(link) || !linkIsVisible(link)) continue;
        neighbors.add(endpointId(link.source));
        neighbors.add(endpointId(link.target));
      }
      const emphasizedNodes = new Set([...nodeIds, ...neighbors]);
      graph.linkVisibility(linkIsVisible);
      if (hoverAnimationRef.current !== null)
        cancelAnimationFrame(hoverAnimationRef.current);
      const nodeTargets = [...nodesRef.current.values()].map((item) => {
        const visual = nodeVisualsRef.current.get(item.id);
        if (visual) {
          if (designPreview) {
            if (!visual.userData.shell.visible)
              visual.userData.shell.material.opacity = 0;
            visual.userData.shell.visible ||= emphasizedNodes.has(item.id);
            visual.userData.shell.material.blending = THREE.NormalBlending;
            visual.userData.shell.material.color.set(
              themeRef.current === "light" ? "#245ac1" : "#e6f0ff",
            );
          }
          visual.userData.label.element.dataset.focused = String(
            emphasizedNodes.has(item.id),
          );
          visual.userData.label.element.style.opacity = String(
            emphasizedNodes.has(item.id)
              ? 0.98
              : styleFor(item.tier, designPreview).labelOpacity,
          );
        }
        return {
          visual,
          shellFrom: visual?.userData.hoverOpacity ?? 0,
          shellTo: emphasizedNodes.has(item.id) ? 1 : 0,
          neighborFrom: visual?.userData.neighborReveal ?? 0,
          neighborTo: neighbors.has(item.id) ? 1 : 0,
          from: visual
            ? (visual.userData.halo.material as THREE.SpriteMaterial).opacity
            : 0,
          to:
            emphasizedNodes.has(item.id) && item.tier !== "center"
              ? Math.min(
                  nodeStyles.center.haloOpacity - 0.01,
                  nodeStyles[item.tier].haloOpacity + 0.14,
                )
              : nodeStyles[item.tier].haloOpacity,
        };
      });
      const linkTargets = [...linksRef.current.values()].map((link) => {
        const visual = linkVisualsRef.current.get(link.id);
        const focused = isFocused(link);
        return {
          visual,
          focused,
          expanded:
            !designPreview ||
            overviewVisibleRef.current ||
            link.tier === "direct" ||
            focused,
          colors:
            visual?.userData.lines.map((line) =>
              (line.material as THREE.LineBasicMaterial).color.clone(),
            ) ?? [],
          color: new THREE.Color(
            previewLinkColor(link, focused, themeRef.current === "light"),
          ),
          from: visual?.userData.opacity ?? 0,
          to: focused
            ? link.tier === "ambient"
              ? 0.66
              : 0.9
            : relationOpacity[link.tier],
        };
      });
      let startedAt: number | null = null;
      const animate = (now: number) => {
        startedAt ??= now;
        const progress = reducedMotion
          ? 1
          : Math.min(1, (now - startedAt) / 400);
        const eased = progress * progress * (3 - 2 * progress);
        for (const target of nodeTargets) {
          if (!target.visual) continue;
          if (designPreview)
            target.visual.userData.hoverOpacity =
              target.shellFrom + (target.shellTo - target.shellFrom) * eased;
          target.visual.userData.neighborReveal =
            target.neighborFrom +
            (target.neighborTo - target.neighborFrom) * eased;
          (
            target.visual.userData.halo.material as THREE.SpriteMaterial
          ).opacity = target.from + (target.to - target.from) * eased;
        }
        for (const target of linkTargets) {
          if (!target.visual) continue;
          const opacity =
            designPreview && target.focused
              ? target.from + (1 - target.from) * eased
              : target.from + (target.to - target.from) * eased;
          for (const [index, line] of target.visual.userData.lines.entries()) {
            const from = target.colors[index];
            if (designPreview && from)
              (line.material as THREE.LineBasicMaterial).color.lerpColors(
                from,
                target.color,
                eased,
              );
          }
          target.visual.userData.opacity = opacity;
          paintLinkOpacity(
            target.visual,
            target.visual.userData.reveal,
            target.expanded,
          );
        }
        if (progress < 1)
          hoverAnimationRef.current = requestAnimationFrame(animate);
        else hoverAnimationRef.current = null;
      };
      hoverAnimationRef.current = requestAnimationFrame(animate);
    };

    focusPathRef.current = (id) =>
      highlight(new Set(id && nodeIsVisible(id) ? [id] : []), new Set());
    focusSelectionRef.current = (nodeIds, relationIds) =>
      highlight(
        new Set(nodeIds.filter(nodeIsVisible)),
        new Set(relationIds.filter((id) => linksRef.current.has(id))),
      );
    focusRelationRef.current = (id) => {
      const relation = id ? linksRef.current.get(id) : undefined;
      setHoveredRelation(id);
      const nodeIds = relation
        ? [endpointId(relation.source), endpointId(relation.target)]
        : [];
      highlight(new Set(nodeIds), new Set(id ? [id] : []));
    };

    graph.d3Force("charge", null);
    graph.d3Force("link", null);
    graph.d3Force("center", null);

    const controls = graph.controls() as GraphControls;
    controls.enableRotate = false;
    controls.enablePan = true;
    controls.mouseButtons.LEFT = THREE.MOUSE.PAN;
    controls.enableZoom = true;
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 95;
    controls.maxDistance = 2400;
    const interruptOverview = () => {
      if (!overviewActiveRef.current) return;
      if (overviewReturnRef.current !== null)
        window.clearTimeout(overviewReturnRef.current);
      if (overviewFinishRef.current !== null)
        window.clearTimeout(overviewFinishRef.current);
      overviewReturnRef.current = null;
      overviewFinishRef.current = null;
      graph.cameraPosition(
        graph.camera().position.clone(),
        controls.target.clone(),
        0,
      );
    };
    controls.addEventListener("start", interruptOverview);
    const stopWatchingPan = watchBoundaryPan(
      controls,
      () =>
        [...nodesRef.current.values()].map((node) => ({
          x: node.x ?? 0,
          y: node.y ?? 0,
          z: node.z ?? 0,
        })),
      () =>
        readyRef.current &&
        introTimeoutRef.current === null &&
        animationRef.current === null,
      () => onPanRef.current(),
    );

    const renderer = graph.renderer();
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    let bloom: UnrealBloomPass | undefined;
    if (!designPreview) {
      graph.scene().fog = new THREE.FogExp2(0x070a10, 0.0017);
      const hemisphere = new THREE.HemisphereLight("#b9d3ff", "#070a10", 0.72);
      const key = new THREE.DirectionalLight("#e8f1ff", 1.4);
      key.position.set(90, 120, 170);
      const rim = new THREE.DirectionalLight("#5a7bff", 0.8);
      rim.position.set(-120, 10, -90);
      graph.lights([hemisphere, key, rim]);
      bloom = new UnrealBloomPass(
        new THREE.Vector2(container.clientWidth, container.clientHeight),
        0.44,
        0.2,
        0.7,
      );
      graph.postProcessingComposer().addPass(bloom);

      const dustPositions: number[] = [];
      for (let index = 0; index < 260; index += 1) {
        const angle = index * 2.399963229728653;
        const radius = 250 + (index % 43) * 6.5;
        const height = ((index * 37) % 180) - 90;
        dustPositions.push(
          Math.cos(angle) * radius,
          height,
          Math.sin(angle) * radius - 120,
        );
      }
      const dustGeometry = new THREE.BufferGeometry();
      dustGeometry.setAttribute(
        "position",
        new THREE.Float32BufferAttribute(dustPositions, 3),
      );
      graph.scene().add(
        new THREE.Points(
          dustGeometry,
          new THREE.PointsMaterial({
            color: "#7892b7",
            size: 0.62,
            transparent: true,
            opacity: 0.18,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
            toneMapped: false,
          }),
        ),
      );
    }
    const resize = () => {
      if (container.clientWidth <= 0 || container.clientHeight <= 0) return;
      graph.width(container.clientWidth).height(container.clientHeight);
      bloom?.resolution.set(container.clientWidth, container.clientHeight);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    graph.cameraPosition({ x: 0, y: 0, z: 360 }, { x: 0, y: 0, z: 0 }, 0);

    return () => {
      stopWatchingPan();
      controls.removeEventListener("start", interruptOverview);
      observer.disconnect();
      if (readyFrameRef.current !== null)
        cancelAnimationFrame(readyFrameRef.current);
      readyFrameRef.current = null;
      if (animationRef.current !== null)
        cancelAnimationFrame(animationRef.current);
      if (hoverAnimationRef.current !== null)
        cancelAnimationFrame(hoverAnimationRef.current);
      if (introTimeoutRef.current !== null)
        window.clearTimeout(introTimeoutRef.current);
      if (focusTimeoutRef.current !== null)
        window.clearTimeout(focusTimeoutRef.current);
      if (overviewReturnRef.current !== null)
        window.clearTimeout(overviewReturnRef.current);
      if (overviewFinishRef.current !== null)
        window.clearTimeout(overviewFinishRef.current);
      for (const visual of nodeVisualsRef.current.values())
        visual.userData.label.element.remove();
      graph._destructor();
      graphRef.current = null;
      dataInitializedRef.current = false;
      readyRef.current = false;
      introCompletedRef.current = false;
      nodesRef.current.clear();
      linksRef.current.clear();
      nodeVisualsRef.current.clear();
      linkVisualsRef.current.clear();
      focusPathRef.current = () => {};
      focusSelectionRef.current = () => {};
      focusRelationRef.current = () => {};
      restoreOverviewRef.current = () => {};
      resetOverviewRef.current = () => {};
    };
  }, [designPreview]);

  useEffect(() => {
    if (!focusRequest) return;
    if (focusTimeoutRef.current !== null)
      window.clearTimeout(focusTimeoutRef.current);
    setHoveredRelation(
      focusRequest.relationIds?.length === 1
        ? (focusRequest.relationIds[0] ?? null)
        : null,
    );
    focusSelectionRef.current(
      focusRequest.nodeIds,
      focusRequest.relationIds ?? [],
    );
    focusTimeoutRef.current = window.setTimeout(() => {
      focusTimeoutRef.current = null;
      if (pointerRelationRef.current) {
        focusRelationRef.current(pointerRelationRef.current);
      } else {
        setHoveredRelation(null);
        focusPathRef.current(pointerNodeRef.current);
      }
    }, 2500);
    return () => {
      if (focusTimeoutRef.current !== null) {
        window.clearTimeout(focusTimeoutRef.current);
        focusTimeoutRef.current = null;
      }
    };
  }, [focusRequest]);

  const overviewViewRef = useRef(view);
  useEffect(() => {
    if (overviewViewRef.current !== view) resetOverviewRef.current();
    overviewViewRef.current = view;
  }, [view]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const controls = graph.controls() as GraphControls;
    const camera = graph.camera() as THREE.PerspectiveCamera;
    const clearTimers = () => {
      if (overviewReturnRef.current !== null)
        window.clearTimeout(overviewReturnRef.current);
      if (overviewFinishRef.current !== null)
        window.clearTimeout(overviewFinishRef.current);
      overviewReturnRef.current = null;
      overviewFinishRef.current = null;
    };
    const finish = () => {
      clearTimers();
      const snapshot = overviewSnapshotRef.current;
      if (snapshot) {
        camera.far = snapshot.far;
        controls.maxDistance = snapshot.maxDistance;
        camera.updateProjectionMatrix();
      }
      overviewSnapshotRef.current = null;
      overviewVisibleRef.current = false;
      overviewActiveRef.current = false;
      refreshVisibilityRef.current();
      onOverviewActiveRef.current(false);
    };
    resetOverviewRef.current = finish;
    restoreOverviewRef.current = () => {
      const snapshot = overviewSnapshotRef.current;
      if (!snapshot) return finish();
      clearTimers();
      const duration = window.matchMedia("(prefers-reduced-motion: reduce)")
        .matches
        ? 0
        : 1000;
      graph.cameraPosition(snapshot.position, snapshot.target, duration);
      if (duration === 0) finish();
      else overviewFinishRef.current = window.setTimeout(finish, duration);
    };
    if (!overviewRequest) return;
    if (overviewRequest.action === "restore") {
      restoreOverviewRef.current();
      return;
    }
    const center = nodesRef.current.get(centerIdRef.current);
    if (!center) return;
    clearTimers();
    overviewSnapshotRef.current ??= {
      position: camera.position.clone(),
      target: controls.target.clone(),
      far: camera.far,
      maxDistance: controls.maxDistance,
    };
    const nodes = [...nodesRef.current.values()].filter(
      (node) => !hiddenKindsRef.current.includes(node.kindCode),
    );
    const distance = overviewDistance(
      nodes,
      center,
      camera,
      graph.width(),
      graph.height(),
      panelOpenRef.current,
    );
    const duration = window.matchMedia("(prefers-reduced-motion: reduce)")
      .matches
      ? 0
      : 1000;
    camera.far = Math.max(camera.far, distance * 2);
    controls.maxDistance = Math.max(controls.maxDistance, distance);
    camera.updateProjectionMatrix();
    overviewVisibleRef.current = true;
    overviewActiveRef.current = true;
    refreshVisibilityRef.current();
    onOverviewActiveRef.current(true);
    const target = new THREE.Vector3(
      center.x ?? 0,
      center.y ?? 0,
      center.z ?? 0,
    );
    graph.cameraPosition(
      { x: target.x, y: target.y, z: target.z + distance },
      target,
      duration,
    );
    overviewReturnRef.current = window.setTimeout(
      () => restoreOverviewRef.current(),
      duration + 4000,
    );
  }, [overviewRequest]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    const controls = graph.controls() as GraphControls;
    const labelLayer = containerRef.current?.querySelector<HTMLElement>(
      "[data-graph-labels]",
    );
    if (pendingNodeId && introCompletedRef.current) {
      setBusy(true);
      if (reducedMotion) return;
      preparingRef.current = designPreview;
      const prepare = designPreview ? prepareWhileWaiting : floatWhileWaiting;
      return prepare(
        nodeVisualsRef.current,
        linksRef.current,
        linkVisualsRef.current,
        pendingNodeId,
      );
    }
    const initial = !dataInitializedRef.current;
    const changed = previousCenterRef.current !== centerId;
    const restoring = designPreview && preparingRef.current && !changed;
    preparingRef.current = false;
    const startVelocities = new Map(
      [...nodeVisualsRef.current].map(([id, visual]) => [
        id,
        visual.userData.velocity.clone(),
      ]),
    );
    const center = restoring
      ? nodesRef.current.get(centerId)
      : (nodeVisualsRef.current.get(centerId)?.position ??
        nodesRef.current.get(centerId));
    const anchor = { x: center?.x ?? 0, y: center?.y ?? 0, z: center?.z ?? 0 };
    const resizeTargetChanged = view.nodes.some((node) => {
      const previous = nodesRef.current.get(node.id);
      return previous && radiusFor(previous) !== radiusFor(node);
    });
    const previousStyles = new Map(
      [...nodeVisualsRef.current].map(([id, visual]) => [
        id,
        { ...visual.userData.style },
      ]),
    );
    const previousRadii = new Map(
      [...nodeVisualsRef.current].map(([id, visual]) => [
        id,
        visual.userData.radius,
      ]),
    );
    const previousOpacities = new Map(
      [...linkVisualsRef.current].map(([id, visual]) => [
        id,
        visual.userData.opacity,
      ]),
    );
    const resizing = view.nodes.some((node) => {
      const previous = previousRadii.get(node.id);
      return (
        previous !== undefined &&
        Math.abs(previous - radiusFor(node)) > 0.000001
      );
    });
    const currentPositions = new Map(
      [...nodesRef.current].map(([id, n]) => [
        id,
        { x: n.x ?? 0, y: n.y ?? 0, z: n.z ?? 0 },
      ]),
    );
    const targets = layoutTargets(
      view.nodes,
      centerId,
      anchor,
      view.relations,
      changed || initial ? new Map() : currentPositions,
      designPreview ? 2 : 0.15,
    );
    const starts = new Map(currentPositions);
    for (const [id, visual] of nodeVisualsRef.current)
      starts.set(id, {
        x: visual.position.x,
        y: visual.position.y,
        z: visual.position.z,
      });
    for (const target of view.nodes) {
      const position = targets.get(target.id);
      if (!position) continue;
      const existing = nodesRef.current.get(target.id);
      const node = existing ?? { ...target, ...position };
      Object.assign(node, target);
      pinPosition(
        node as RuntimeNode & Position,
        (changed || restoring ? starts : currentPositions).get(target.id) ??
          position,
      );
      nodesRef.current.set(target.id, node);
      starts.set(target.id, { x: node.x ?? 0, y: node.y ?? 0, z: node.z ?? 0 });
    }
    for (const target of view.relations) {
      const existing = linksRef.current.get(target.id);
      if (existing) Object.assign(existing, target);
      else linksRef.current.set(target.id, { ...target });
    }
    const publishData = () =>
      graph.graphData({
        nodes: [...nodesRef.current.values()],
        links: [...linksRef.current.values()],
      });
    publishData();
    const fitDistance = (wide: boolean) => {
      const camera = graph.camera() as THREE.PerspectiveCamera;
      const visible = wide
        ? view.nodes
        : view.nodes.filter((n) =>
            designPreview
              ? n.tier === "center" || n.tier === "direct"
              : n.tier !== "ambient",
          );
      const tangent = Math.tan((camera.fov * Math.PI) / 360);
      const horizontal = (tangent * graph.width()) / graph.height();
      let distance = Math.max(150, 40 / horizontal, 40 / tangent);
      let frontDepth = 0;
      for (const node of visible) {
        const position = targets.get(node.id);
        if (!position) continue;
        const depth = designPreview ? position.z - anchor.z : 0;
        frontDepth = Math.max(frontDepth, depth);
        distance = Math.max(
          distance,
          depth + (Math.abs(position.x - anchor.x) + 24) / horizontal,
          depth + (Math.abs(position.y - anchor.y) + 24) / tangent,
        );
      }
      if (wide) return distance * (designPreview ? 100 : 1.55);
      return designPreview
        ? Math.max(95, frontDepth + 32, distance * 0.88)
        : distance;
    };
    nearViewRef.current = { distance: fitDistance(false), anchor };
    const placeCamera = (distance: number) => {
      if (designPreview) {
        controls.maxDistance = Math.max(2400, distance);
        const camera = graph.camera() as THREE.PerspectiveCamera;
        camera.far = Math.max(4000, distance * 2);
        camera.updateProjectionMatrix();
      }
      graph.cameraPosition(
        { x: anchor.x, y: anchor.y, z: anchor.z + distance },
        anchor,
        0,
      );
    };
    const removeOutgoing = () => {
      const nodeIds = new Set(view.nodes.map((n) => n.id));
      const linkIds = new Set(view.relations.map((r) => r.id));
      retainGraphItems(nodesRef.current, nodeIds);
      retainGraphItems(linksRef.current, linkIds);
      for (const [id, visual] of nodeVisualsRef.current)
        if (!nodeIds.has(id)) visual.userData.label.element.remove();
      retainGraphItems(nodeVisualsRef.current, nodeIds);
      retainGraphItems(linkVisualsRef.current, linkIds);
      publishData();
    };
    const paint = (
      progress: number,
      move: boolean,
      time = progress,
      duration = 1200,
    ) => {
      for (const [id, node] of nodesRef.current) {
        const target = targets.get(id);
        const start = starts.get(id);
        if (target && start && move && (changed || restoring)) {
          pinPosition(node as RuntimeNode & Position, {
            x: start.x + (target.x - start.x) * progress,
            y: start.y + (target.y - start.y) * progress,
            z: start.z + (target.z - start.z) * progress,
          });
          if (designPreview) {
            const sample = samplePreviewMotion(
              start,
              target,
              id === centerId
                ? { x: 0, y: 0, z: 0 }
                : (startVelocities.get(id) ?? { x: 0, y: 0, z: 0 }),
              time,
              duration || 1,
            );
            pinPosition(node as RuntimeNode & Position, sample.position);
            nodeVisualsRef.current
              .get(id)
              ?.userData.velocity.set(
                sample.velocity.x,
                sample.velocity.y,
                sample.velocity.z,
              );
          }
        }
        const visual = nodeVisualsRef.current.get(id);
        if (!visual) continue;
        const style = { ...styleFor(node.tier, designPreview) };
        if (!viewNodesRef.current.has(id)) {
          style.opacity = 0;
          style.haloOpacity = 0;
          style.labelOpacity = 0;
          style.shellOpacity = 0;
        }
        let radius = radiusFor(node);
        if (move) {
          const from = previousStyles.get(id) ?? {
            ...style,
            opacity: 0,
            haloOpacity: 0,
            shellOpacity: 0,
            labelOpacity: 0,
          };
          for (const key of Object.keys(style) as (keyof NodeStyle)[]) {
            const end = viewNodesRef.current.has(id)
              ? styleFor(node.tier, designPreview)[key]
              : style[key];
            style[key] = from[key] + (end - from[key]) * progress;
          }
          const oldRadius = previousRadii.get(id) ?? radius;
          radius = oldRadius + (radius - oldRadius) * progress;
        }
        visual.position.set(node.x ?? 0, node.y ?? 0, node.z ?? 0);
        applyNodeVisual(visual, node, radius, style);
        if (
          introRevealRef.current !== null &&
          node.tier !== "center" &&
          node.tier !== "direct"
        ) {
          visual.userData.surface.material.opacity *= introRevealRef.current;
          visual.userData.occluder.material.opacity = introRevealRef.current;
          visual.userData.label.element.style.opacity = String(
            style.labelOpacity * introRevealRef.current,
          );
        }
      }
      alignLinks(
        linksRef.current,
        linkVisualsRef.current,
        nodeVisualsRef.current,
      );
      const ids = new Set(view.relations.map((r) => r.id));
      for (const [id, link] of linksRef.current) {
        const visual = linkVisualsRef.current.get(id);
        if (!visual) continue;
        const targetOpacity = ids.has(id) ? relationOpacity[link.tier] : 0;
        const oldOpacity = previousOpacities.get(id) ?? 0;
        const opacity = move
          ? oldOpacity + (targetOpacity - oldOpacity) * progress
          : targetOpacity;
        for (const line of visual.userData.lines) {
          if (designPreview)
            (line.material as THREE.LineBasicMaterial).color.set(
              previewLinkColor(
                link,
                linkFocusRef.current(link),
                themeRef.current === "light",
              ),
            );
        }
        visual.userData.opacity = opacity;
        paintLinkOpacity(
          visual,
          visual.userData.reveal,
          !designPreview ||
            overviewVisibleRef.current ||
            link.tier === "direct" ||
            linkFocusRef.current(link),
        );
      }
    };
    const animate = (mode: "intro" | "center" | "resize" | "restore") => {
      const intro = mode === "intro";
      const moveCamera = mode === "intro" || mode === "center";
      const startCamera = graph.camera().position.clone();
      const startTarget = controls.target.clone();
      const endTarget = new THREE.Vector3(anchor.x, anchor.y, anchor.z);
      const endCamera = endTarget
        .clone()
        .add(new THREE.Vector3(0, 0, fitDistance(false)));
      const overviewDistance = Math.max(
        fitDistance(false),
        (fitDistance(true) / 100) * 1.15,
      );
      const begun = performance.now();
      if (
        mode === "resize" &&
        (resizeTargetChanged || resizeDeadlineRef.current === null)
      )
        resizeDeadlineRef.current = begun + 320;
      const duration =
        reducedMotion || (intro && introCompleted)
          ? 0
          : intro && designPreview
            ? 3100
            : moveCamera
              ? 1200
              : mode === "restore"
                ? preparationDuration
                : Math.max(0, (resizeDeadlineRef.current ?? begun) - begun);
      paint(0, !intro);
      setBusy(true);
      const frame = (now: number) => {
        const progress = duration ? Math.min(1, (now - begun) / duration) : 1;
        const eased = easeInOutCubic(progress);
        if (moveCamera) {
          const offset = graph.camera().position.clone().sub(controls.target);
          if (intro)
            graph.camera().position.lerpVectors(startCamera, endCamera, eased);
          if (intro && designPreview) {
            const from = startCamera.distanceTo(startTarget);
            const to = endCamera.distanceTo(endTarget);
            const elapsed = progress * 3100;
            const arriving = elapsed < 1200;
            const stage = arriving
              ? elapsed / 1200
              : Math.max(0, (elapsed - 1600) / 1500);
            const stageFrom = arriving ? from : overviewDistance;
            const stageTo = arriving ? overviewDistance : to;
            introRevealRef.current = arriving
              ? 1
              : 1 - easeInOutCubic(Math.min(1, stage / 0.8));
            const distance = Math.exp(
              Math.log(stageFrom) +
                (Math.log(stageTo) - Math.log(stageFrom)) *
                  easeInOutCubic(stage),
            );
            graph
              .camera()
              .position.set(endTarget.x, endTarget.y, endTarget.z + distance);
            if (labelLayer)
              labelLayer.style.opacity = String(
                easeInOutCubic(
                  Math.min(1, Math.max(0, (elapsed - 1200) / 400)),
                ),
              );
          }
          controls.target.lerpVectors(startTarget, endTarget, eased);
          if (!intro) graph.camera().position.copy(controls.target).add(offset);
          controls.update();
        }
        paint(eased, !intro, progress, duration);
        if (progress < 1) {
          animationRef.current = requestAnimationFrame(frame);
          return;
        }
        animationRef.current = null;
        introRevealRef.current = null;
        resizeDeadlineRef.current = null;
        previousCenterRef.current = centerId;
        introCompletedRef.current = true;
        if (intro && !introCompleted) onIntroRef.current();
        if (intro && changed) onTransitionCompleteRef.current(centerId);
        if (intro && designPreview) {
          controls.maxDistance = 2400;
          const camera = graph.camera() as THREE.PerspectiveCamera;
          camera.far = 4000;
          camera.updateProjectionMatrix();
        }
        if (labelLayer) labelLayer.style.opacity = "1";
        if (!intro) {
          removeOutgoing();
          if (mode === "center") onTransitionCompleteRef.current(centerId);
        }
        graph.enableNavigationControls(true).enablePointerInteraction(true);
        setBusy(false);
      };
      if (!duration) frame(begun);
      else animationRef.current = requestAnimationFrame(frame);
    };
    const startIntro = () => {
      introTimeoutRef.current = window.setTimeout(
        () => {
          introTimeoutRef.current = null;
          animate("intro");
        },
        reducedMotion || introCompleted ? 0 : designPreview ? 240 : 720,
      );
    };
    if (initial) {
      dataInitializedRef.current = true;
      paint(1, false);
      placeCamera(fitDistance(!reducedMotion && !introCompleted));
      if (designPreview && labelLayer)
        labelLayer.style.opacity = reducedMotion || introCompleted ? "1" : "0";
      graph.enableNavigationControls(false).enablePointerInteraction(false);
      if (introStarted) startIntro();
    } else if (introStarted && !introCompletedRef.current) {
      startIntro();
    } else if (introStarted && changed) {
      animate("center");
    } else if (introStarted && restoring) {
      animate("restore");
    } else if (introStarted && resizing) {
      animate("resize");
    } else {
      paint(1, false);
      removeOutgoing();
      if (!introStarted)
        placeCamera(fitDistance(!reducedMotion && !introCompleted));
      if (introStarted && introCompletedRef.current) {
        graph.enableNavigationControls(true).enablePointerInteraction(true);
        setBusy(false);
      }
    }
    // 초기 page가 첫 frame보다 먼저 도착해 effect를 교체해도 준비 신호를 잃지 않는다.
    if (!readyRef.current) {
      readyFrameRef.current = requestAnimationFrame(() => {
        readyFrameRef.current = null;
        readyRef.current = true;
        onReadyRef.current();
      });
    }
    return () => {
      if (readyFrameRef.current !== null)
        cancelAnimationFrame(readyFrameRef.current);
      readyFrameRef.current = null;
      if (animationRef.current !== null)
        cancelAnimationFrame(animationRef.current);
      animationRef.current = null;
      introRevealRef.current = null;
      if (introTimeoutRef.current !== null)
        window.clearTimeout(introTimeoutRef.current);
      introTimeoutRef.current = null;
    };
  }, [
    centerId,
    introStarted,
    introCompleted,
    pendingNodeId,
    view,
    designPreview,
  ]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: 새 응답으로 생성된 시각 객체에도 현재 테마를 적용한다.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !designPreview) return;
    const light = theme === "light";
    graph.backgroundColor(light ? "#f5f7fa" : "#111416");
    for (const [id, visual] of nodeVisualsRef.current) {
      visual.userData.lightMode = light;
      const node = nodesRef.current.get(id);
      const shellOpacity = visual.userData.shell.material.opacity;
      const shellScale = visual.userData.shell.scale.clone();
      if (node)
        applyNodeVisual(
          visual,
          node,
          visual.userData.radius,
          visual.userData.style,
        );
      visual.userData.shell.material.opacity = shellOpacity;
      visual.userData.shell.scale.copy(shellScale);
      visual.userData.occluder.material.color.set(
        light ? "#f5f7fa" : "#111416",
      );
      visual.userData.shell.material.color.set(light ? "#245ac1" : "#e6f0ff");
    }
    for (const [id, visual] of linkVisualsRef.current) {
      const link = linksRef.current.get(id);
      if (link)
        for (const line of visual.userData.lines)
          (line.material as THREE.LineBasicMaterial).color.set(
            previewLinkColor(link, linkFocusRef.current(link), light),
          );
    }
  }, [theme, designPreview, view]);

  return (
    <section
      className={styles.map}
      data-panel-open={panelOpen}
      data-design-preview={designPreview || undefined}
      aria-label="동적 지식맵"
      aria-busy={busy}
    >
      <div ref={containerRef} className={styles.canvas} />
      {hoveredRelation && (
        <div className={styles.relationHint} role="status">
          {relationActionsRef.current.get(hoveredRelation)?.label}
        </div>
      )}
      <nav className={styles.accessibleNodes} aria-label="지도 관계 목록">
        {view.relations
          .filter((relation) =>
            [relation.source, relation.target].every((id) =>
              view.nodes.some(
                (node) =>
                  node.id === id && !hiddenKinds.includes(node.kindCode),
              ),
            ),
          )
          .map((relation) => (
            <button
              key={relation.id}
              ref={(element) => {
                if (element)
                  relationButtonsRef.current.set(relation.id, element);
                else relationButtonsRef.current.delete(relation.id);
              }}
              type="button"
              onFocus={() => focusRelationRef.current(relation.id)}
              onBlur={() => focusRelationRef.current(null)}
              onClick={() =>
                onEvidence({ id: relation.id, label: relationName(relation) })
              }
            >
              {relationName(relation)}
            </button>
          ))}
      </nav>
      <div className={styles.depthNote}>
        {designPreview
          ? "드래그로 이동 · 스크롤로 확대 · 관계선을 눌러 연결 원문 확인"
          : "얕은 2.5D · z ±32 · 회전 없음"}
      </div>
      <nav
        className={styles.accessibleNodes}
        aria-label="탐색 가능한 대상 목록"
      >
        {view.nodes
          .filter((node) => !hiddenKinds.includes(node.kindCode))
          .map((node) => (
            <button
              key={node.id}
              type="button"
              onClick={() => onSelect(node.id)}
              onFocus={() => focusPathRef.current(node.id)}
              onBlur={() => focusPathRef.current(null)}
            >
              {node.name} · {node.kind}
            </button>
          ))}
      </nav>
    </section>
  );
}
