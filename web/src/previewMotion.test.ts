import { expect, it } from "vitest";
import { samplePreviewMotion } from "./previewMotion";

it("응답 시 시작 속도와 위치를 이어받고 정확한 목표에서 정지한다", () => {
  const start = { x: 12, y: -3, z: 1 };
  const target = { x: -40, y: 18, z: 2 };
  const velocity = { x: 0.01, y: -0.004, z: 0 };
  expect(samplePreviewMotion(start, target, velocity, 0, 1200)).toEqual({
    position: start,
    velocity,
  });
  const next = samplePreviewMotion(start, target, velocity, 0.000001, 1200);
  expect((next.position.x - start.x) / 0.0012).toBeCloseTo(velocity.x, 5);
  expect(samplePreviewMotion(start, target, velocity, 1, 1200)).toEqual({
    position: target,
    velocity: { x: 0, y: 0, z: 0 },
  });
});
