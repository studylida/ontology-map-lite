import type {
  Classification,
  NodeInsightsResponse,
  NodeDetailsResponse,
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
 * 3. 특정 노드 기준 1~3 hop 계층 서브그래프 조회
 */
export async function fetchSubgraph(
  nodeId: number,
  unbounded: boolean = false,
): Promise<SubgraphResponse> {
  const query = unbounded ? "?unbounded=true" : "";
  const res = await fetch(`${BASE_URL}/nodes/${nodeId}/graph${query}`);
  if (!res.ok) throw new Error(`노드(ID: ${nodeId}) 서브그래프 로드 실패`);
  return res.json();
}

/**
 * 3.5. 연결된 엣지(관계) 수가 가장 많은 대표 핵심 노드 단건 조회
 */
export async function fetchTopDegreeNode(): Promise<{
  id: number;
  name: string;
  edge_count: number;
}> {
  const res = await fetch(`${BASE_URL}/nodes/top-degree`);
  if (!res.ok) throw new Error("대표 중심 노드 로드 실패");
  return res.json();
}

/**
 * 4. 특정 노드의 상세 정보(개요, 원천 근거, AI 분석, Q&A) 종합 조회
 */
export async function fetchNodeDetails(
  nodeId: number,
): Promise<NodeDetailsResponse> {
  const res = await fetch(`${BASE_URL}/nodes/${nodeId}/details`);
  if (!res.ok) throw new Error(`노드(ID: ${nodeId}) 상세 정보 로드 실패`);
  return res.json();
}

/**
 * 5. 특정 노드의 인사이트 및 Q&A 목록 조회 (레거시 호환)
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
export async function searchNodes(
  query: string = "",
): Promise<NodeSearchItem[]> {
  const res = await fetch(
    `${BASE_URL}/nodes/search?q=${encodeURIComponent(query)}`,
  );
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
  auto_commit?: boolean;
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
  autoCommit: boolean = false,
): Promise<{ task_id: string; status: string }> {
  const formData = new FormData();
  formData.append("file", file);

  const query = autoCommit ? "?auto_commit=true" : "";
  const res = await fetch(`${BASE_URL}/agent/extract-async/file${query}`, {
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

/**
 * 12. 네이티브 JSON을 검토 번들로 변환
 */
export async function convertAdapterInput(data: {
  producer: string;
  raw_json: Record<string, any>;
  supplement_json?: Record<string, any>;
}): Promise<any> {
  const res = await fetch(`${BASE_URL}/adapters/convert`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? "어댑터 변환 실패");
  }
  return res.json();
}

/**
 * 13. 승인된 검토 항목 지식그래프 반영
 */
export async function commitAdapterInput(data: {
  bundle: Record<string, any>;
  enabled_ids: string[];
}): Promise<any> {
  const res = await fetch(`${BASE_URL}/adapters/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? "어댑터 반영 실패");
  }
  return res.json();
}

/**
 * 14. 시연 베이스라인 시드 초기화
 */
export async function seedDemoData(): Promise<any> {
  const res = await fetch(`${BASE_URL}/demo/seed`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? "시연 시드 초기화 실패");
  }
  return res.json();
}

