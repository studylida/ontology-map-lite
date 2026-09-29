// web/src/useGraphPointerInteraction.ts
import type { ForceGraph3DInstance } from "3d-force-graph";
import type { RefObject } from "react";
import * as THREE from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  applyNodeVisual,
  endpointId,
  getBaseLinkOpacity,
  nodeStyles,
  paintLinkOpacity,
  type LinkVisual,
  type NodeVisual,
  type RuntimeLink,
  type RuntimeNode,
} from "./graphVisuals";
import type { GraphNode } from "./types";

interface PointerInteractionOptions {
  container: HTMLElement;
  controls: OrbitControls;
  graph: ForceGraph3DInstance<RuntimeNode, RuntimeLink>;
  nodeVisualsRef: RefObject<Map<string, NodeVisual>>;
  nodesRef: RefObject<Map<string, RuntimeNode>>;
  cancelIntro: () => void;
  onNodeClick: (node: GraphNode) => void;
}

/**
 * Three.js Raycaster 기반 정밀 노드/라벨 클릭 판정 리스너 바인딩.
 * 미세한 흔들림(5px 이내)은 클릭으로 허용하고 실제 드래그/패닝 조작은 배제합니다.
 */
export function bindPointerInteraction({
  container,
  controls,
  graph,
  nodeVisualsRef,
  nodesRef,
  cancelIntro,
  onNodeClick,
}: PointerInteractionOptions): () => void {
  let pointerStart: { id: number; x: number; y: number } | null = null;
  let pressedNodeId: string | undefined;
  let dragged = false;
  const raycaster = new THREE.Raycaster();

  const nodeIdAt = (event: MouseEvent): string | undefined => {
    const label =
      event.target instanceof Element
        ? event.target.closest<HTMLElement>("button[data-node-id]")
        : null;
    if (label) return label.dataset.nodeId;

    const camera = graph.camera();
    const bounds = graph.renderer().domElement.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;

    camera.updateMatrixWorld();
    graph.scene().updateMatrixWorld(true);
    raycaster.setFromCamera(
      new THREE.Vector2(
        ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
        -((event.clientY - bounds.top) / bounds.height) * 2 + 1,
      ),
      camera,
    );
    const hit = raycaster.intersectObjects(
      [...(nodeVisualsRef.current?.values() ?? [])],
      true,
    )[0];
    return hit?.object.parent?.userData.nodeId;
  };

  const startSelection = (event: PointerEvent) => {
    cancelIntro();
    dragged = !event.isPrimary || event.button !== 0;
    pointerStart = dragged
      ? null
      : {
          id: event.pointerId,
          x: event.clientX,
          y: event.clientY,
        };
    pressedNodeId = dragged ? undefined : nodeIdAt(event);
  };

  const trackSelection = (event: PointerEvent) => {
    if (!pointerStart || pointerStart.id !== event.pointerId) return;
    const distance = Math.hypot(
      event.clientX - pointerStart.x,
      event.clientY - pointerStart.y,
    );
    if (distance > 5) {
      dragged = true;
    }
  };

  const endSelection = (event: PointerEvent) => {
    trackSelection(event);
    pointerStart = null;
  };

  const cancelSelection = () => {
    pointerStart = null;
    pressedNodeId = undefined;
    dragged = true;
  };

  const selectNode = (event: MouseEvent) => {
    if (event.button !== 0 || (event.detail !== 0 && dragged)) return;
    const id = event.detail === 0 ? nodeIdAt(event) : pressedNodeId;
    pressedNodeId = undefined;
    const node = id === undefined ? undefined : nodesRef.current?.get(id);
    if (!node) return;
    cancelIntro();
    onNodeClick(node.originalNode);
  };

  controls.addEventListener("start", cancelIntro);
  container.addEventListener("pointerdown", startSelection, true);
  container.addEventListener("pointermove", trackSelection, true);
  container.addEventListener("pointerup", endSelection, true);
  container.addEventListener("pointercancel", cancelSelection, true);
  container.addEventListener("click", selectNode);
  container.addEventListener("wheel", cancelIntro, { capture: true, passive: true });

  return () => {
    controls.removeEventListener("start", cancelIntro);
    container.removeEventListener("pointerdown", startSelection, true);
    container.removeEventListener("pointermove", trackSelection, true);
    container.removeEventListener("pointerup", endSelection, true);
    container.removeEventListener("pointercancel", cancelSelection, true);
    container.removeEventListener("click", selectNode);
    container.removeEventListener("wheel", cancelIntro, true);
  };
}

interface HoverHighlightOptions {
  container: HTMLElement;
  nodesRef: RefObject<Map<string, RuntimeNode>>;
  nodeVisualsRef: RefObject<Map<string, NodeVisual>>;
  linksRef: RefObject<Map<string, RuntimeLink>>;
  linkVisualsRef: RefObject<Map<string, LinkVisual>>;
  setHoveredRelation: (hint: string | null) => void;
}

export function createHoverHighlightManager({
  container,
  nodesRef,
  nodeVisualsRef,
  linksRef,
  linkVisualsRef,
  setHoveredRelation,
}: HoverHighlightOptions) {
  let hoverAnim: number | null = null;

  const easeInOutCubic = (value: number): number =>
    value < 0.5 ? 4 * value * value * value : 1 - (-2 * value + 2) ** 3 / 2;

  function highlightHover(nodeId: string | null, linkId: string | null) {
    if (hoverAnim !== null) {
      cancelAnimationFrame(hoverAnim);
      hoverAnim = null;
    }

    const activeNodeIds = new Set<string>();
    if (nodeId) activeNodeIds.add(nodeId);
    if (linkId) {
      const link = linksRef.current?.get(linkId);
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
    for (const link of linksRef.current?.values() ?? []) {
      if (!isFocusedLink(link)) continue;
      neighbors.add(endpointId(link.source));
      neighbors.add(endpointId(link.target));
    }

    const hasActive = activeNodeIds.size > 0 || linkId !== null;

    const nodeTargets = [...(nodesRef.current?.values() ?? [])].map((item) => {
      const strId = String(item.id);
      const visual = nodeVisualsRef.current?.get(strId);
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

    const linkTargets = [...(linksRef.current?.values() ?? [])].map((link) => {
      const strId = String(link.id);
      const visual = linkVisualsRef.current?.get(strId);
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
        hoverAnim = requestAnimationFrame(frame);
      } else {
        hoverAnim = null;
      }
    };

    hoverAnim = requestAnimationFrame(frame);
  }

  const handleNodeHover = (node: RuntimeNode | null) => {
    container.style.cursor = node ? "pointer" : "grab";
    highlightHover(node ? String(node.id) : null, null);
  };

  const handleLinkHover = (link: RuntimeLink | null) => {
    container.style.cursor = link ? "pointer" : "grab";
    if (link) {
      const sName =
        typeof link.source === "object"
          ? link.source.name
          : nodesRef.current?.get(String(link.source))?.name ?? "노드";
      const tName =
        typeof link.target === "object"
          ? link.target.name
          : nodesRef.current?.get(String(link.target))?.name ?? "노드";
      const dir = link.directionality === "DIRECTED" ? "➔" : "↔";
      setHoveredRelation(
        `${sName} ${dir} ${tName} · ${link.label ?? "연관"} · 근거 ${link.evidenceGroupCount}건`,
      );
    } else {
      setHoveredRelation(null);
    }
    highlightHover(null, link ? String(link.id) : null);
  };

  const cleanup = () => {
    if (hoverAnim !== null) {
      cancelAnimationFrame(hoverAnim);
      hoverAnim = null;
    }
  };

  return {
    handleNodeHover,
    handleLinkHover,
    cleanup,
  };
}
