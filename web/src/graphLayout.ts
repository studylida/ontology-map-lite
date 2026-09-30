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
  const positions = new Map<string, Position>();
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
  // -------------------------------------------------------------
  // STEP 1: 1-hop 동심원 (Concentric Circle) 콤팩트 배치
  // -------------------------------------------------------------
  const N1 = directNodes.length;
  // 1-hop 노드가 1~3개일 때도 광활하지 않게 145~180px의 아담하고 콤팩트한 궤도 형성
  const R1 = Math.max(145, Math.min(250, Math.round((N1 * 56) / (2 * Math.PI))));

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
    const angle = startTheta1 + i * deltaTheta1;
    nodeAngles.set(nId, angle);

    const x = anchor.x + R1 * Math.cos(angle);
    const y = anchor.y + R1 * Math.sin(angle);
    const z = anchor.z + depthTargetForNode({ id: nId }) * depthScale;

    positions.set(nId, { x, y, z });
  });

  // -------------------------------------------------------------
  // STEP 2: 2-hop 외곽 호(Arc) 부채꼴 배치
  // (과도한 이격을 줄이고 가시성을 높임: R2 = R1 + 95px)
  // -------------------------------------------------------------
  const hasTwoHop = twoHopNodes.length > 0;
  const R2 = R1 + (hasTwoHop ? 95 : 75);

  // 2-hop 노드별 주 부모(1-hop) 노드 탐색 및 그룹화
  const twoHopByParent = new Map<string, Array<{ id: string | number; tier?: string }>>();
  const unassignedTwoHop: Array<{ id: string | number; tier?: string }> = [];

  for (const node of twoHopNodes) {
    const nId = String(node.id);

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

    // 1-hop 부모가 1~2개로 적을 때 중심 노드를 둥글게 둘러쌀 수 있도록 호를 대폭 확장
    let maxArcSpan: number;
    if (N1 === 1) {
      maxArcSpan = Math.min(Math.PI * 1.45, Math.max(Math.PI * 0.9, K * 0.35));
    } else if (N1 === 2) {
      maxArcSpan = Math.min(Math.PI * 0.95, deltaTheta1 * 0.85);
    } else {
      maxArcSpan = Math.min(deltaTheta1 * 0.75, Math.PI * 0.65);
    }

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
        const staggerRadius = R2 + (idx % 2 === 1 && K > 2 ? 22 : 0);

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
  // STEP 3: 3-hop 및 외곽(Ambient) 노드 스마트 가시 반경 및 빈 공간 우선 채우기
  // (R3 = R2 + 85px로 콤팩트화하여 전체화면 맞춤 시 과도한 축소 방지)
  // -------------------------------------------------------------
  const R3 = hasTwoHop ? R2 + 85 : R1 + 80;
  const N3 = outerNodes.length;

  if (N3 > 0) {
    // 1. 현재 배치된 모든 노드의 각도 수집하여 가장 큰 빈 각도 섹터(Empty Sector) 탐색
    const assignedAngles = Array.from(nodeAngles.values())
      .map(normalizeAngle)
      .sort((a, b) => a - b);

    let maxGap = 0;
    let gapStart = 0;
    if (assignedAngles.length > 0) {
      for (let i = 0; i < assignedAngles.length; i++) {
        const a1 = assignedAngles[i];
        const a2 =
          i === assignedAngles.length - 1
            ? assignedAngles[0] + 2 * Math.PI
            : assignedAngles[i + 1];
        const gap = a2 - a1;
        if (gap > maxGap) {
          maxGap = gap;
          gapStart = a1;
        }
      }
    } else {
      maxGap = 2 * Math.PI;
      gapStart = -Math.PI;
    }

    // 빈 공간의 중심 각도 (예: 상단 12시/10시~2시 방향)
    const emptySectorCenter = normalizeAngle(gapStart + maxGap / 2);
    // 빈 공간 내에서 외곽 노드들이 펼쳐질 각도 범위를 넉넉하게 확장 (최대 260도)
    const sectorSpan = Math.min(maxGap * 0.92, Math.PI * 1.45);
    const sectorStart = emptySectorCenter - sectorSpan / 2;
    const sectorStep = N3 > 1 ? sectorSpan / (N3 - 1) : 0;

    outerNodes.forEach((node, i) => {
      const nId = String(node.id);
      if (nId === cId) return;

      let baseAngle: number;

      // 연결된 상위 노드가 있는지 탐색
      const connectedEdge = relations.find((r) => {
        const s = String(r.source);
        const t = String(r.target);
        if (s === nId && nodeAngles.has(t)) return true;
        if (t === nId && nodeAngles.has(s)) return true;
        return false;
      });

      if (connectedEdge) {
        const upId =
          String(connectedEdge.source) === nId
            ? String(connectedEdge.target)
            : String(connectedEdge.source);
        const upAngle = nodeAngles.get(upId) ?? emptySectorCenter;
        // 상위 노드가 밀집 구역에 있을 때 빈 섹터 방향으로 자연스럽게 외곽 배치
        const angleToEmpty = normalizeAngle(emptySectorCenter - upAngle);
        const pullFactor = maxGap > Math.PI ? 0.35 : 0.15;
        baseAngle =
          upAngle +
          (angleToEmpty > 0 ? pullFactor : -pullFactor) +
          (i % 2 === 0 ? 0.16 : -0.16);
      } else {
        // 독립/앰비언트 노드: 빈 섹터 내에서 한 줄로 뭉치지 않고 지그재그 성단(Constellation) 형태로 자연스럽게 흩뿌림
        const scatterJitter = (i % 2 === 1 ? 0.08 : -0.08) * (N3 > 2 ? 1 : 0);
        baseAngle = N3 > 1 ? sectorStart + i * sectorStep + scatterJitter : emptySectorCenter;
      }

      baseAngle = normalizeAngle(baseAngle);
      nodeAngles.set(nId, baseAngle);

      // 다층 궤도 분산(Multi-tier orbital scattering):
      // 노드들이 동일한 반경에 뭉쳐있지 않도록 3개 궤도(R3, R3+35, R3+70)에 유기적으로 분산
      const depthTier = i % 3; // 0, 1, 2
      const radiusStagger = R3 + depthTier * 36 + ((i * 17) % 21) - 10;

      const x = anchor.x + radiusStagger * Math.cos(baseAngle);
      const y = anchor.y + radiusStagger * Math.sin(baseAngle);
      const z = anchor.z + depthTargetForNode({ id: nId }) * depthScale;
      positions.set(nId, { x, y, z });
    });
  }

  // -------------------------------------------------------------
  // STEP 4: 미세 충돌 완화 (Collision Relaxation)
  // (노드 간 최소 거리가 80px 미만이면 부드럽게 밀어내어 뭉침 원천 방지)
  // -------------------------------------------------------------
  const allPosList = Array.from(positions.entries()).filter(([id]) => id !== cId);
  for (let iter = 0; iter < 16; iter++) {
    for (let i = 0; i < allPosList.length; i++) {
      for (let j = i + 1; j < allPosList.length; j++) {
        const [, pA] = allPosList[i];
        const [, pB] = allPosList[j];
        const dx = pB.x - pA.x;
        const dy = pB.y - pA.y;
        const dist = Math.hypot(dx, dy);
        const minDist = 80;
        if (dist < minDist && dist > 0.001) {
          const overlap = (minDist - dist) * 0.5;
          const nx = (dx / dist) * overlap;
          const ny = (dy / dist) * overlap;
          // 중심으로부터의 반지름 보존을 위해 접선 방향 또는 외곽으로 분산
          pA.x -= nx * 0.45;
          pA.y -= ny * 0.45;
          pB.x += nx * 0.45;
          pB.y += ny * 0.45;
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
