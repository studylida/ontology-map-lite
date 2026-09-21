import { PerspectiveCamera } from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { expect, it, vi } from "vitest";
import { approachesBoundary, watchBoundaryPan } from "./peripheralPan";

const nodes = [
  { x: -100, y: -100, z: 0 },
  { x: 100, y: 100, z: 0 },
];

it("화면 밖 여유 영역이 경계에 닿으면 이동 종료 전에 한 page를 요청한다", () => {
  const camera = new PerspectiveCamera(90, 2);
  camera.position.z = 20;
  const controls = new OrbitControls(camera, document.createElement("div"));
  const load = vi.fn();
  let ready = true;
  const stop = watchBoundaryPan(
    controls,
    () => nodes.map((node) => ({ ...node, x: node.x * 2, y: node.y * 2 })),
    () => ready,
    load,
  );
  const pan = (x: number) => {
    camera.position.x = controls.target.x = x;
    controls.dispatchEvent({ type: "change" });
  };
  pan(10);
  controls.dispatchEvent({ type: "start" });
  pan(15);
  expect(load).not.toHaveBeenCalled();
  pan(75); // 실제 화면 오른쪽 끝은 115, 미리 읽는 범위는 211이다.
  expect(load).toHaveBeenCalledTimes(1);
  pan(90);
  controls.dispatchEvent({ type: "end" });
  expect(load).toHaveBeenCalledTimes(1);
  ready = false;
  controls.dispatchEvent({ type: "start" });
  pan(95);
  controls.dispatchEvent({ type: "end" });
  ready = true;
  pan(96);
  expect(load).toHaveBeenCalledTimes(1);
  controls.dispatchEvent({ type: "start" });
  pan(97);
  expect(load).toHaveBeenCalledTimes(2);
  controls.dispatchEvent({ type: "end" });
  stop();
  controls.dispatchEvent({ type: "start" });
  pan(120);
  expect(load).toHaveBeenCalledTimes(2);
  controls.dispose();
});

it("사용자 축소는 즉시 조회하고 확대와 프로그램 이동은 무시한다", () => {
  const camera = new PerspectiveCamera();
  camera.position.z = 100;
  const controls = new OrbitControls(camera, document.createElement("div"));
  const load = vi.fn();
  const stop = watchBoundaryPan(
    controls,
    () => [],
    () => true,
    load,
  );
  controls.dispatchEvent({ type: "start" });
  camera.position.z = 80;
  controls.dispatchEvent({ type: "change" });
  controls.dispatchEvent({ type: "end" });
  camera.position.z = 120;
  controls.dispatchEvent({ type: "change" });
  expect(load).not.toHaveBeenCalled();
  controls.dispatchEvent({ type: "start" });
  camera.position.z = 150;
  controls.dispatchEvent({ type: "change" });
  expect(load).toHaveBeenCalledTimes(1);
  controls.dispatchEvent({ type: "end" });
  stop();
  controls.dispose();
});

it("많이 확대해도 화면 밖 두 칸의 여유를 유지한다", () => {
  const camera = new PerspectiveCamera(50, 1);
  camera.position.z = 95;
  const controls = new OrbitControls(camera, document.createElement("div"));
  const load = vi.fn();
  const stop = watchBoundaryPan(
    controls,
    () => [
      { x: -200, y: -200, z: 0 },
      { x: 200, y: 200, z: 0 },
    ],
    () => true,
    load,
  );
  controls.dispatchEvent({ type: "start" });
  camera.position.x = controls.target.x = 70;
  controls.dispatchEvent({ type: "change" });
  // 화면 끝은 약 114지만 경계 200까지 미리 준비한다.
  expect(load).toHaveBeenCalledTimes(1);
  controls.dispatchEvent({ type: "end" });
  camera.zoom = 4;
  camera.updateProjectionMatrix();
  controls.dispatchEvent({ type: "start" });
  camera.position.x = controls.target.x = 100;
  controls.dispatchEvent({ type: "change" });
  expect(load).toHaveBeenCalledTimes(2);
  stop();
  controls.dispose();
});

it("양쪽 축의 바깥 이동을 판정하고 안쪽 이동은 조회하지 않는다", () => {
  const margin = { x: 40, y: 40 };
  expect(
    approachesBoundary(
      nodes,
      { x: 90, y: 0, z: 0 },
      { x: 90, y: 65, z: 0 },
      margin,
    ),
  ).toBe(true);
  expect(
    approachesBoundary(
      nodes,
      { x: 90, y: 80, z: 0 },
      { x: 80, y: 70, z: 0 },
      margin,
    ),
  ).toBe(false);
  expect(
    approachesBoundary(
      nodes,
      { x: 90, y: 0, z: 0 },
      { x: -65, y: 0, z: 0 },
      margin,
    ),
  ).toBe(true);
});
