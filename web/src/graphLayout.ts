import type { KnowledgeNode, KnowledgeRelation } from "./data";

export interface Position {
  x: number;
  y: number;
  z: number;
}
export const depthLimit = 32;
export function depthTargetForNode(node: { id: string }): number {
  let hash = 0;
  for (const character of node.id)
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  hash = Math.imul(hash ^ (hash >>> 16), 0x45d9f3b) >>> 0;
  return ((hash % 2001) / 1000 - 1) * depthLimit;
}

export function layoutTargets(
  nodes: KnowledgeNode[],
  centerId: string,
  anchor: Position,
  relations: KnowledgeRelation[] = [],
  retained = new Map<string, Position>(),
  depthScale = 0.15,
): Map<string, Position> {
  const positions = new Map(retained);
  positions.set(centerId, { ...anchor });
  const radius = Math.ceil(Math.sqrt(nodes.length)) + 2;
  const slots: Position[] = [];
  for (let y = -radius; y <= radius; y++) {
    for (let x = -radius; x <= radius; x++) {
      slots.push({ x: anchor.x + x * 48, y: anchor.y + y * 28, z: anchor.z });
    }
  }
  const slotKey = (p: Position) =>
    `${Math.round((p.x - anchor.x) / 48)}:${Math.round((p.y - anchor.y) / 28)}`;
  const occupied = new Set([...positions.values()].map(slotKey));
  const available = slots.filter((p) => !occupied.has(slotKey(p)));
  for (const node of nodes) {
    if (positions.has(node.id)) continue;
    const edge = relations.find(
      (r) =>
        (r.source === node.id && positions.has(r.target)) ||
        (r.target === node.id && positions.has(r.source)),
    );
    const parent = edge
      ? (positions.get(edge.source === node.id ? edge.target : edge.source) ??
        anchor)
      : anchor;
    const distance = (p: Position) =>
      (p.x - parent.x) ** 2 +
      (p.y - parent.y) ** 2 +
      ((p.x - anchor.x) ** 2 + (p.y - anchor.y) ** 2) * 0.2;
    available.sort((a, b) => distance(a) - distance(b));
    const slot = available.shift();
    if (slot)
      positions.set(node.id, {
        ...slot,
        x:
          slot.x +
          (depthTargetForNode({ id: `${node.id}:x` }) / depthLimit) * 12,
        y:
          slot.y +
          (depthTargetForNode({ id: `${node.id}:y` }) / depthLimit) * 7,
        z: anchor.z + depthTargetForNode(node) * depthScale,
      });
  }
  return positions;
}

export function pinPosition(
  node: Position & {
    fx?: number;
    fy?: number;
    fz?: number;
    vx?: number;
    vy?: number;
    vz?: number;
  },
  position: Position,
): void {
  node.x = node.fx = position.x;
  node.y = node.fy = position.y;
  node.z = node.fz = position.z;
  node.vx = node.vy = node.vz = 0;
}

export function retainGraphItems<T>(items: Map<string, T>, ids: Set<string>) {
  for (const id of items.keys()) if (!ids.has(id)) items.delete(id);
}
