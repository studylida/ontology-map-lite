import { expect, it } from "vitest";
import type { KnowledgeNode } from "./data";
import { layoutTargets, pinPosition, retainGraphItems } from "./graphLayout";

it("새 중심의 현재 위치를 유지하고 이웃을 새 중심 주위에 결정적으로 배치한다", () => {
  const nodes = ["new", "neighbor", "two-hop"].map(
    (id) => ({ id }) as KnowledgeNode,
  );
  const anchor = { x: 80, y: -50, z: 12 };
  const positions = layoutTargets(nodes, "new", anchor);
  expect(positions.get("new")).toEqual(anchor);
  expect(positions.get("neighbor")).not.toEqual(anchor);
  expect(Math.abs(positions.get("two-hop")?.z ?? 100)).toBeLessThanOrEqual(32);
  expect(layoutTargets(nodes, "new", anchor)).toEqual(positions);
  expect(anchor).toEqual({ x: 80, y: -50, z: 12 });
});

it("전환 완료 뒤 새 응답에 없는 node와 Relation이 남지 않는다", () => {
  const retained = { id: "new" };
  const nodes = new Map([
    ["old", { id: "old" }],
    ["new", retained],
  ]);
  const links = new Map([["old-link", {}]]);
  retainGraphItems(nodes, new Set(["new"]));
  retainGraphItems(links, new Set());
  expect([...nodes.values()]).toEqual([retained]);
  expect(links.size).toBe(0);
});

it("추가 page는 기존 좌표를 유지하고 최종 위치와 속도를 함께 고정한다", () => {
  const nodes = ["a", "b", "c"].map((id) => ({ id }) as KnowledgeNode);
  const anchor = { x: 0, y: 0, z: 0 };
  const first = layoutTargets(nodes.slice(0, 2), "a", anchor);
  const next = layoutTargets(nodes, "a", anchor, [], first);
  expect(next.get("b")).toEqual(first.get("b"));
  const node = { x: 50, y: 60, z: 0, vx: 12, vy: -6, vz: 3 };
  pinPosition(node, anchor);
  expect(node).toEqual({ ...anchor, fx: 0, fy: 0, fz: 0, vx: 0, vy: 0, vz: 0 });
});

it("불규칙한 배치에서도 페이지 추가가 기존 자리를 재사용하거나 좌표를 바꾸지 않는다", () => {
  const nodes = Array.from(
    { length: 100 },
    (_, id) => ({ id: String(id) }) as KnowledgeNode,
  );
  const anchor = { x: 0, y: 0, z: 0 };
  const first = layoutTargets(nodes.slice(0, 60), "0", anchor);
  const next = layoutTargets(nodes, "0", anchor, [], first);
  for (const [id, position] of first) expect(next.get(id)).toEqual(position);
  expect(
    new Set(
      [...next.values()].map(
        (p) => `${Math.round(p.x / 48)}:${Math.round(p.y / 28)}`,
      ),
    ).size,
  ).toBe(100);
  expect([...next.values()].some((p) => p.x % 48 !== 0 && p.y % 28 !== 0)).toBe(
    true,
  );
  expect(layoutTargets(nodes, "0", anchor, [], first)).toEqual(next);
});

it("깊이를 늘려도 평면 배치·중심 기준점과 추가 page의 기존 좌표를 유지한다", () => {
  const nodes = Array.from(
    { length: 100 },
    (_, id) => ({ id: String(id) }) as KnowledgeNode,
  );
  const anchor = { x: 80, y: -40, z: 25 };
  const shallow = layoutTargets(nodes, "0", anchor);
  const deep = layoutTargets(nodes, "0", anchor, [], new Map(), 2);
  expect(deep.get("0")).toEqual(anchor);
  for (const [id, position] of deep) {
    expect(position.x).toBe(shallow.get(id)?.x);
    expect(position.y).toBe(shallow.get(id)?.y);
    expect(Math.abs(position.z - anchor.z)).toBeLessThanOrEqual(64);
  }
  expect([...deep.values()].some((p) => p.z - anchor.z > 50)).toBe(true);
  expect([...deep.values()].some((p) => p.z - anchor.z < -50)).toBe(true);
  const first = layoutTargets(
    nodes.slice(0, 60),
    "0",
    anchor,
    [],
    new Map(),
    2,
  );
  const paged = layoutTargets(nodes, "0", anchor, [], first, 2);
  for (const [id, position] of first) expect(paged.get(id)).toEqual(position);
});
