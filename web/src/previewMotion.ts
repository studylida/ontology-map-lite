import type { Position } from "./graphLayout";

export const preparationDuration = 320;

// 시작 속도를 이어받고 도착 시 속도가 0이 되는 위치 보간이다.
export function samplePreviewMotion(
  start: Position,
  target: Position,
  velocity: Position,
  progress: number,
  duration: number,
) {
  const t = Math.min(1, Math.max(0, progress));
  if (t === 1)
    return { position: { ...target }, velocity: { x: 0, y: 0, z: 0 } };
  const eased = t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2;
  const slope = t < 0.5 ? 12 * t ** 2 : 12 * (1 - t) ** 2;
  const carry = t * (1 - t) ** 2;
  const carrySlope = (1 - t) * (1 - 3 * t);
  const position = { ...start };
  const nextVelocity = { ...velocity };
  for (const axis of ["x", "y", "z"] as const) {
    const distance = target[axis] - start[axis];
    position[axis] =
      start[axis] + distance * eased + velocity[axis] * duration * carry;
    nextVelocity[axis] =
      (distance * slope) / duration + velocity[axis] * carrySlope;
  }
  return { position, velocity: nextVelocity };
}
