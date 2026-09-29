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

/**
 * 방사형 동심원(Concentric Circle) & 외곽 호(Arc) 기하학적 레이아웃:
 * 1. 중심 노드: anchor(0, 0, 0)
 * 2. 1-hop 노드: 중심 주위를 감싸는 균일한 동심원(Circle, R1)에 완벽하게 배치
 * 3. 2-hop 노드: 1-hop 영역을 침범하지 않고, 연결된 1-hop 부모 노드의 외곽에서 부채꼴 호(Arc, R2)를 형성
 * 4. 3-hop 및 외곽 노드: 그보다 더 외곽 호(R3)에 계층 배치
 */
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

  // 1. 노드 티어 정규화 및 분류
  const normalizeTier = (tier?: string): "center" | "direct" | "twoHop" | "threeHop" | "ambient" => {
    if (!tier) return "ambient";
    const t = tier.toLowerCase();
    if (t === "center") return "center";
    if (t === "direct") return "direct";
    if (t === "twohop" || t === "two_hop") return "twoHop";
    if (t === "threehop" || t === "three_hop") return "threeHop";
    return "ambient";
  };

  const directNodes: Array<{ id: string | number; tier?: string }> = [];
  const twoHopNodes: Array<{ id: string | number; tier?: string }> = [];
  const outerNodes: Array<{ id: string | number; tier?: string }> = [];

  for (const n of nodes) {
    const strId = String(n.id);
    if (strId === cId) continue;
    const t = normalizeTier(n.tier);
    if (t === "direct") {
      directNodes.push(n);
    } else if (t === "twoHop") {
      twoHopNodes.push(n);
    } else {
      outerNodes.push(n);
    }
  }

  // 각 노드의 방사형 각도 저장
  const nodeAngles = new Map<string, number>();

  // -------------------------------------------------------------
  // STEP 1: 1-hop 동심원 (Concentric Circle) 배치
  // -------------------------------------------------------------
  const N1 = directNodes.length;
  // 1-hop 노드 수에 따른 최적 반경 계산 (노드 간 최소 65px 이상 간격 확보)
  const R1 = Math.max(200, Math.min(340, Math.round((N1 * 68) / (2 * Math.PI))));

  // 이전 위치가 있는 노드는 기존 각도에 따라 정렬하여 애니메이션 교차/꼬임 방지
  directNodes.sort((a, b) => {
    const posA = currentPositions.get(String(a.id));
    const posB = currentPositions.get(String(b.id));
    const angleA = posA ? Math.atan2(posA.y - anchor.y, posA.x - anchor.x) : 0;
    const angleB = posB ? Math.atan2(posB.y - anchor.y, posB.x - anchor.x) : 0;
    return angleA - angleB;
  });

  const deltaTheta1 = N1 > 0 ? (2 * Math.PI) / N1 : 0;
  // 12시 방향(-PI/2)부터 시작하여 균등하게 분할
  const startTheta1 = -Math.PI / 2;

  directNodes.forEach((node, i) => {
    const nId = String(node.id);
    if (positions.has(nId)) {
      const p = positions.get(nId)!;
      nodeAngles.set(nId, Math.atan2(p.y - anchor.y, p.x - anchor.x));
      return;
    }

    const angle = startTheta1 + i * deltaTheta1;
    nodeAngles.set(nId, angle);

    const x = anchor.x + R1 * Math.cos(angle);
    const y = anchor.y + R1 * Math.sin(angle);
    const z = anchor.z + depthTargetForNode({ id: nId }) * depthScale;

    positions.set(nId, { x, y, z });
  });

  // -------------------------------------------------------------
  // STEP 2: 2-hop 외곽 호(Arc) 부채꼴 배치
  // (1-hop 영역을 침범하지 않도록 R2 = R1 + 200px 확보)
  // -------------------------------------------------------------
  const R2 = R1 + 210;

  // 2-hop 노드별 주 부모(1-hop) 노드 탐색 및 그룹화
  const twoHopByParent = new Map<string, Array<{ id: string | number; tier?: string }>>();
  const unassignedTwoHop: Array<{ id: string | number; tier?: string }> = [];

  for (const node of twoHopNodes) {
    const nId = String(node.id);
    if (positions.has(nId)) continue;

    // 이 노드와 연결된 1-hop 노드 찾기
    const connectedParentEdge = relations.find((r) => {
      const s = String(r.source);
      const t = String(r.target);
      if (s === nId && nodeAngles.has(t)) return true;
      if (t === nId && nodeAngles.has(s)) return true;
      return false;
    });

    if (connectedParentEdge) {
      const parentId =
        String(connectedParentEdge.source) === nId
          ? String(connectedParentEdge.target)
          : String(connectedParentEdge.source);

      if (!twoHopByParent.has(parentId)) {
        twoHopByParent.set(parentId, []);
      }
      twoHopByParent.get(parentId)!.push(node);
    } else {
      unassignedTwoHop.push(node);
    }
  }

  // 각 1-hop 부모의 각도를 중심으로 외곽 호(Arc) 펼치기
  for (const [parentId, children] of twoHopByParent.entries()) {
    const parentAngle = nodeAngles.get(parentId) ?? 0;
    const K = children.length;

    // 인접 1-hop과의 겹침을 방지하기 위한 최대 호 폭 (부모 간격의 75% 이내)
    const maxArcSpan = Math.min(deltaTheta1 * 0.75, Math.PI * 0.6);

    if (K === 1) {
      const child = children[0];
      const cIdStr = String(child.id);
      nodeAngles.set(cIdStr, parentAngle);

      const x = anchor.x + R2 * Math.cos(parentAngle);
      const y = anchor.y + R2 * Math.sin(parentAngle);
      const z = anchor.z + depthTargetForNode({ id: cIdStr }) * depthScale;
      positions.set(cIdStr, { x, y, z });
    } else {
      const step = maxArcSpan / (K - 1);
      const startAngle = parentAngle - maxArcSpan / 2;

      children.forEach((child, idx) => {
        const cIdStr = String(child.id);
        const childAngle = startAngle + idx * step;
        nodeAngles.set(cIdStr, childAngle);

        // 자식이 3개 이상이면 지그재그(Stagger)로 반경을 살짝 교대하여 가독성 강화
        const staggerRadius = R2 + (idx % 2 === 1 && K > 2 ? 35 : 0);

        const x = anchor.x + staggerRadius * Math.cos(childAngle);
        const y = anchor.y + staggerRadius * Math.sin(childAngle);
        const z = anchor.z + depthTargetForNode({ id: cIdStr }) * depthScale;
        positions.set(cIdStr, { x, y, z });
      });
    }
  }

  // 부모를 찾지 못한 2-hop 노드는 1-hop 빈 각도에 호를 그려 배치
  if (unassignedTwoHop.length > 0) {
    const unassignedStep = (2 * Math.PI) / unassignedTwoHop.length;
    unassignedTwoHop.forEach((node, idx) => {
      const nId = String(node.id);
      const angle = startTheta1 + (idx + 0.5) * unassignedStep;
      nodeAngles.set(nId, angle);

      const x = anchor.x + R2 * Math.cos(angle);
      const y = anchor.y + R2 * Math.sin(angle);
      const z = anchor.z + depthTargetForNode({ id: nId }) * depthScale;
      positions.set(nId, { x, y, z });
    });
  }

  // -------------------------------------------------------------
  // STEP 3: 3-hop 및 외곽(Ambient) 노드 배치 (R3 = R2 + 180px)
  // -------------------------------------------------------------
  const R3 = R2 + 190;
  const N3 = outerNodes.length;
  if (N3 > 0) {
    const deltaTheta3 = (2 * Math.PI) / N3;
    outerNodes.forEach((node, i) => {
      const nId = String(node.id);
      if (positions.has(nId)) return;

      // 상위 연결된 노드의 각도가 있다면 그 주변을 따름
      let baseAngle = startTheta1 + (i + 0.25) * deltaTheta3;
      const connectedEdge = relations.find((r) => {
        const s = String(r.source);
        const t = String(r.target);
        if (s === nId && nodeAngles.has(t)) return true;
        if (t === nId && nodeAngles.has(s)) return true;
        return false;
      });

      if (connectedEdge) {
        const upId = String(connectedEdge.source) === nId ? String(connectedEdge.target) : String(connectedEdge.source);
        baseAngle = (nodeAngles.get(upId) ?? baseAngle) + (i % 2 === 0 ? 0.15 : -0.15);
      }

      nodeAngles.set(nId, baseAngle);
      const x = anchor.x + R3 * Math.cos(baseAngle);
      const y = anchor.y + R3 * Math.sin(baseAngle);
      const z = anchor.z + depthTargetForNode({ id: nId }) * depthScale;
      positions.set(nId, { x, y, z });
    });
  }

  // -------------------------------------------------------------
  // STEP 4: 미세 충돌 완화 (Collision Relaxation)
  // (같은 반경에서 노드 간 최소 거리가 55px 미만이면 살짝 밀어내기)
  // -------------------------------------------------------------
  const allPosList = Array.from(positions.entries()).filter(([id]) => id !== cId);
  for (let iter = 0; iter < 12; iter++) {
    for (let i = 0; i < allPosList.length; i++) {
      for (let j = i + 1; j < allPosList.length; j++) {
        const [, pA] = allPosList[i];
        const [, pB] = allPosList[j];
        const dx = pB.x - pA.x;
        const dy = pB.y - pA.y;
        const dist = Math.hypot(dx, dy);
        const minDist = 60;
        if (dist < minDist && dist > 0.001) {
          const overlap = (minDist - dist) * 0.5;
          const nx = (dx / dist) * overlap;
          const ny = (dy / dist) * overlap;
          // 중심으로부터의 반지름 보존을 위해 접선 방향 또는 외곽으로 분산
          pA.x -= nx * 0.4;
          pA.y -= ny * 0.4;
          pB.x += nx * 0.4;
          pB.y += ny * 0.4;
        }
      }
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
