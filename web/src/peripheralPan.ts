import type { PerspectiveCamera } from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Position } from "./graphLayout";

export function approachesBoundary(
  nodes: Position[],
  start: Position,
  end: Position,
  viewport = { x: 0, y: 0 },
): boolean {
  if (!nodes.length) return false;
  let minX = Infinity,
    maxX = -Infinity,
    minY = Infinity,
    maxY = -Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.x);
    maxX = Math.max(maxX, node.x);
    minY = Math.min(minY, node.y);
    maxY = Math.max(maxY, node.y);
  }
  const centerX = (minX + maxX) / 2,
    centerY = (minY + maxY) / 2;
  const halfWidth = Math.max(28, (maxX - minX) / 2);
  const halfHeight = Math.max(28, (maxY - minY) / 2);
  const outward = (
    from: number,
    to: number,
    center: number,
    half: number,
    margin: number,
  ) =>
    Math.abs(to - center) + margin >= half && (to - center) * (to - from) > 0;
  return (
    outward(start.x, end.x, centerX, halfWidth, viewport.x) ||
    outward(start.y, end.y, centerY, halfHeight, viewport.y)
  );
}

export function watchBoundaryPan(
  controls: OrbitControls,
  getNodes: () => Position[],
  canLoad: () => boolean,
  onBoundary: () => void,
): () => void {
  let start: Position | null = null;
  let startDistance = 0;
  const onChange = () => {
    if (!start || !canLoad()) return;
    const camera = controls.object as PerspectiveCamera;
    const distance = camera.position.distanceTo(controls.target);
    const halfHeight =
      (distance * Math.tan((camera.fov * Math.PI) / 360)) / camera.zoom;
    const halfWidth = halfHeight * camera.aspect;
    // 확대해도 화면 밖에 배치 두 칸(48×28) 이상의 여유를 유지한다.
    if (
      approachesBoundary(getNodes(), start, controls.target, {
        x: halfWidth + Math.max(halfWidth, 96),
        y: halfHeight + Math.max(halfHeight, 56),
      }) ||
      distance > startDistance + 0.5
    ) {
      start = null;
      onBoundary();
    }
  };
  const onStart = () => {
    start = controls.target.clone();
    startDistance = controls.object.position.distanceTo(controls.target);
  };
  const onEnd = () => {
    onChange();
    start = null;
  };
  controls.addEventListener("start", onStart);
  controls.addEventListener("end", onEnd);
  controls.addEventListener("change", onChange);
  return () => {
    controls.removeEventListener("start", onStart);
    controls.removeEventListener("end", onEnd);
    controls.removeEventListener("change", onChange);
  };
}
