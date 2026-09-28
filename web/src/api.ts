import type {
  Classification,
  NodeInsightsResponse,
  SubgraphResponse,
  NodeSearchItem,
  ExtractionTaskSummary,
  ExtractionTaskDetail,
  IntakePayload,
  IntakeResponse,
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

/**
 * 5. 노드 검색 API 호출
 */
export async function searchNodes(query: string = ""): Promise<NodeSearchItem[]> {
  const res = await fetch(`${BASE_URL}/nodes/search?q=${encodeURIComponent(query)}`);
  if (!res.ok) throw new Error("노드 검색 요청 실패");
  return res.json();
}

/**
 * 6. 웹 URL 또는 텍스트 메모 비동기 추출 의뢰 (202 Accepted)
 */
export async function submitExtractAsync(data: {
  source_type: "url" | "text";
  content: string;
  title?: string;
}): Promise<{ task_id: string; status: string }> {
  const res = await fetch(`${BASE_URL}/agent/extract-async`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("비동기 추출 의뢰에 실패했습니다.");
  return res.json();
}

/**
 * 7. 파일(PDF/DOCX/TXT) 업로드 비동기 추출 의뢰 (202 Accepted)
 */
export async function submitExtractFileAsync(
  file: File,
): Promise<{ task_id: string; status: string }> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${BASE_URL}/agent/extract-async/file`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) throw new Error("파일 업로드 비동기 추출 의뢰에 실패했습니다.");
  return res.json();
}

/**
 * 8. 백그라운드 대기열 작업 목록 조회 (폴링용)
 */
export async function fetchAgentTasks(): Promise<ExtractionTaskSummary[]> {
  const res = await fetch(`${BASE_URL}/agent/tasks`);
  if (!res.ok) throw new Error("대기열 작업 목록 조회 실패");
  return res.json();
}

/**
 * 9. 단일 작업 상세 및 추출 결과(IntakePayload) 조회
 */
export async function fetchAgentTaskDetail(
  taskId: string,
): Promise<ExtractionTaskDetail> {
  const res = await fetch(`${BASE_URL}/agent/tasks/${taskId}`);
  if (!res.ok) throw new Error("작업 상세 정보 조회 실패");
  return res.json();
}

/**
 * 10. 완료/실패 작업 대기열에서 제거
 */
export async function dismissAgentTask(taskId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/agent/tasks/${taskId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("작업 삭제 실패");
}

/**
 * 11. HITL 검토 완료된 온톨로지 최종 지식그래프 적재
 */
export async function intakeKnowledge(
  payload: IntakePayload,
): Promise<IntakeResponse> {
  const res = await fetch(`${BASE_URL}/intake`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? "지식그래프 적재에 실패했습니다.");
  }
  return res.json();
}

