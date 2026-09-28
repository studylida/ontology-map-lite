// web/src/graphLayout.ts

export interface Position {
  x: number;
  y: number;
  z: number;
}

export const depthLimit = 32;

export function depthTargetForNode(node: { id: string | number }): number {
  const strId = String(node.id);
  let hash = 0;
  for (const character of strId) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  }
  hash = Math.imul(hash ^ (hash >>> 16), 0x45d9f3b) >>> 0;
  return ((hash % 2001) / 1000 - 1) * depthLimit;
}

function normalizeAngle(a: number): number {
  let angle = a;
  while (angle > Math.PI) angle -= 2 * Math.PI;
  while (angle < -Math.PI) angle += 2 * Math.PI;
  return angle;
}

const GRID_X = 72;
const GRID_Y = 52;

export function layoutTargets(
  nodes: Array<{ id: string | number; tier?: string }>,
  centerId: string | number,
  anchor: Position,
  relations: Array<{ source: string | number; target: string | number }> = [],
  retained = new Map<string, Position>(),
  depthScale = 0.15,
  currentPositions = new Map<string, Position>(),
): Map<string, Position> {
  const cId = String(centerId);
  const positions = new Map(retained);
  positions.set(cId, { ...anchor });

  const radius = Math.ceil(Math.sqrt(nodes.length)) + 4;
  const slots: Position[] = [];
  for (let y = -radius; y <= radius; y++) {
    for (let x = -radius; x <= radius; x++) {
      if (x === 0 && y === 0) continue;
      slots.push({
        x: anchor.x + x * GRID_X,
        y: anchor.y + y * GRID_Y,
        z: anchor.z,
      });
    }
  }

  const slotKey = (p: Position) =>
    `${Math.round((p.x - anchor.x) / GRID_X)}:${Math.round((p.y - anchor.y) / GRID_Y)}`;
  const occupied = new Set([...positions.values()].map(slotKey));
  const available = slots.filter((p) => !occupied.has(slotKey(p)));

  // 각 부모 노드별로 이미 배정된 자식 노드들의 방사형 각도 목록 추적
  const parentChildAngles = new Map<string, number[]>();

  // 노드 티어 순서대로 배치 (중심 -> 1-hop 직결 -> 2-hop -> 3-hop -> ambient)
  // 안쪽 링부터 바깥쪽 링으로 순차 배정하여 간선이 노드를 가로지르는 교차 방지
  const tierWeight: Record<string, number> = {
    center: 0,
    direct: 1,
    twoHop: 2,
    threeHop: 3,
    ambient: 4,
  };

  const sortedNodes = [...nodes].sort((a, b) => {
    const wa = tierWeight[a.tier ?? ""] ?? 2;
    const wb = tierWeight[b.tier ?? ""] ?? 2;
    return wa - wb;
  });

  for (const node of sortedNodes) {
    const nId = String(node.id);
    if (positions.has(nId)) continue;

    const edge = relations.find(
      (r) =>
        (String(r.source) === nId && positions.has(String(r.target))) ||
        (String(r.target) === nId && positions.has(String(r.source))),
    );

    const parentId = edge
      ? (String(edge.source) === nId ? String(edge.target) : String(edge.source))
      : cId;
    const parent = positions.get(parentId) ?? anchor;

    const existingAngles = parentChildAngles.get(parentId) ?? [];
    const currentPos = currentPositions.get(nId);

    // 이전 화면 위치에서의 방향(각도)을 보존하여 노드가 반대편으로 대각선 횡단(간선 교차)하는 것 방지
    const hasCurrent = Boolean(currentPos);
    const desiredAngle = currentPos
      ? Math.atan2(currentPos.y - parent.y, currentPos.x - parent.x)
      : 0;

    const cost = (p: Position) => {
      const distToParent = Math.hypot(p.x - parent.x, p.y - parent.y);
      const angle = Math.atan2(p.y - parent.y, p.x - parent.x);

      // 이미 같은 부모에 연결된 노드와 동일한 각도 선상(±16도 이내)에 있으면 중복 차단 페널티 부과
      const isCollinear = existingAngles.some(
        (a) => Math.abs(normalizeAngle(a - angle)) < 0.28,
      );

      // 현재 화면 위치가 있다면 그 각도 및 위치와의 오차를 강하게 제약하여 부드러운 최소 이동 유도
      const anglePenalty = hasCurrent
        ? Math.abs(normalizeAngle(angle - desiredAngle)) * 320
        : 0;
      const moveDist = currentPos
        ? Math.hypot(p.x - currentPos.x, p.y - currentPos.y) * 0.75
        : 0;

      // 2-hop 이상 자식 노드가 부모에서 다시 중심(anchor) 쪽으로 파고들어 간선을 찌르는 현상 방지 (외향성 보장)
      let inwardPenalty = 0;
      if (parentId !== cId) {
        const parentToCenter = Math.atan2(anchor.y - parent.y, anchor.x - parent.x);
        if (Math.abs(normalizeAngle(angle - parentToCenter)) < 0.65) {
          inwardPenalty = 15000;
        }
      }

      return (
        distToParent * 1.5 +
        anglePenalty +
        moveDist +
        inwardPenalty +
        (isCollinear ? 50000 : 0)
      );
    };

    available.sort((a, b) => cost(a) - cost(b));
    const slot = available.shift();

    if (slot) {
      const chosenAngle = Math.atan2(slot.y - parent.y, slot.x - parent.x);
      if (!parentChildAngles.has(parentId)) {
        parentChildAngles.set(parentId, []);
      }
      parentChildAngles.get(parentId)?.push(chosenAngle);

      positions.set(nId, {
        ...slot,
        x:
          slot.x +
          (depthTargetForNode({ id: `${nId}:x` }) / depthLimit) * 14,
        y:
          slot.y +
          (depthTargetForNode({ id: `${nId}:y` }) / depthLimit) * 9,
        z: anchor.z + depthTargetForNode({ id: nId }) * depthScale,
      });
      occupied.add(slotKey(slot));
    }
  }

  return positions;
}

export function pinPosition(
  node: {
    x?: number | undefined;
    y?: number | undefined;
    z?: number | undefined;
    fx?: number | undefined;
    fy?: number | undefined;
    fz?: number | undefined;
    vx?: number | undefined;
    vy?: number | undefined;
    vz?: number | undefined;
  },
  position: Position,
): void {
  node.x = node.fx = position.x;
  node.y = node.fy = position.y;
  node.z = node.fz = position.z;
  node.vx = node.vy = node.vz = 0;
}

export function retainGraphItems<T>(items: Map<string, T>, ids: Set<string>) {
  for (const id of items.keys()) {
    if (!ids.has(id)) {
      items.delete(id);
    }
  }
}
