import type {
  Classification,
  NodeInsightsResponse,
  SubgraphResponse,
} from "./types";

const BASE_URL = "/api/v1";

/**
 * 1. 헬스 체크
 */
export async function fetchHealth(): Promise<{ status: string }> {
  const res = await fetch(`${BASE_URL}/health`);
  if (!res.ok) throw new Error("서버 상태 확인 실패");
  return res.json();
}

/**
 * 2. 분류 목록 조회
 */
export async function fetchClassifications(): Promise<Classification[]> {
  const res = await fetch(`${BASE_URL}/classifications`);
  if (!res.ok) throw new Error("분류 목록 로드 실패");
  return res.json();
}

/**
 * 3. 특정 노드 기준 1-hop 서브그래프 조회
 */
export async function fetchSubgraph(nodeId: number): Promise<SubgraphResponse> {
  const res = await fetch(`${BASE_URL}/nodes/${nodeId}/graph`);
  if (!res.ok) throw new Error(`노드(ID: ${nodeId}) 서브그래프 로드 실패`);
  return res.json();
}

/**
 * 4. 특정 노드의 인사이트 및 Q&A 목록 조회
 */
export async function fetchNodeInsights(
  nodeId: number,
): Promise<NodeInsightsResponse> {
  const res = await fetch(`${BASE_URL}/nodes/${nodeId}/insights`);
  if (!res.ok) throw new Error(`노드(ID: ${nodeId}) 인사이트 로드 실패`);
  return res.json();
}
