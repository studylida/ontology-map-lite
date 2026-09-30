// 1. 온톨로지 분류 (Classification)
export interface Classification {
  id: number;
  code: string;
  display_name: string;
  description?: string | null;
}

// 2. 그래프 노드 & 엣지
export type NodeTier = "CENTER" | "DIRECT" | "TWO_HOP" | "THREE_HOP" | "AMBIENT";

export interface GraphNode {
  id: number;
  name: string;
  classification_id: number;
  classification_code?: string;
  classification_name?: string;
  description?: string | null;
  properties?: Record<string, unknown>;
  tier?: NodeTier;
  created_at?: string | null;
}

export interface GraphEdge {
  id: number;
  source_node_id: number;
  target_node_id: number;
  relation_code?: string;
  relation_name?: string;
  properties?: Record<string, unknown>;
  tier?: NodeTier;
  created_at?: string | null;
}

// 온톨로지 필터 상태 (노드 유형 다중 선택 + 기간 프리셋/커스텀)
export type DatePreset = "ALL" | "1M" | "3M" | "1Y" | "CUSTOM";

export interface GraphFilterState {
  selectedTypes: Set<string>; // empty set means "all types"
  datePreset: DatePreset;
  startDate: string | null; // "YYYY-MM-DD"
  endDate: string | null; // "YYYY-MM-DD"
}

// GET /api/v1/nodes/{id}/graph 응답
export interface SubgraphResponse {
  center_node_id: number | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  has_omitted?: boolean;
  omitted_count?: number;
}

// 3. 노드 인사이트 & Q&A
export interface IssueItem {
  title?: string;
  severity?: string;
  [key: string]: unknown;
}

export interface NodeInsight {
  recent_history_summary?: string | null;
  overall_insight?: string | null;
  issues?: Array<string | IssueItem> | null;
  generated_at?: string | null;
}

export interface NodeQAPair {
  question: string;
  answer: string;
  sequence: number;
}

// GET /api/v1/nodes/{id}/insights 응답
export interface NodeInsightsResponse {
  node_id: number;
  node_name: string;
  insight: NodeInsight | null;
  qa_pairs: NodeQAPair[];
}

export interface NodeClaimItem {
  id: number;
  quote: string;
  statement: string;
  document_title?: string;
}

export interface NodeDetailsResponse {
  node_id: number;
  name: string;
  classification_code: string;
  classification_name: string;
  description?: string | null;
  properties: Record<string, unknown>;
  claims: NodeClaimItem[];
  recent_history_summary?: string | null;
  overall_insight?: string | null;
  issues?: Array<string | IssueItem> | null;
  qa_pairs: NodeQAPair[];
}

// 4. 노드 검색 결과 아이템
export interface NodeSearchItem {
  id: number;
  name: string;
  classification_code: string;
  classification_name: string;
  description?: string | null;
}

// 5. 비동기 인제스트 태스크 & 온톨로지 DTO
export type TaskStatus = "pending" | "processing" | "completed" | "failed";

export interface IntakeNode {
  ref_id?: string;
  name: string;
  classification?: string;
  description?: string;
  properties?: Record<string, unknown>;
  existing_node_id?: number | null;
}

export interface IntakeEdge {
  source_ref?: string;
  target_ref?: string;
  source_name: string;
  target_name: string;
  relation?: string;
  claim_ref?: string;
  claim_refs?: string[];
  properties?: Record<string, unknown>;
}

export interface IntakeClaim {
  ref_id?: string;
  quote: string;
  claim_text?: string;
  confidence?: number;
  start_offset?: number | null;
  end_offset?: number | null;
}

export interface IntakePayload {
  source_project: string;
  document_title?: string;
  document_content?: string;
  document_uri?: string;
  nodes: IntakeNode[];
  edges: IntakeEdge[];
  claims: IntakeClaim[];
  insights?: {
    summary?: string;
    [key: string]: unknown;
  };
  raw_metadata?: Record<string, unknown>;
}

export interface IntakeResponse {
  status: string;
  source_project: string;
  document_id?: number | null;
  primary_node_id?: number | null;
  nodes_created: number;
  edges_created: number;
  claims_created: number;
  duplicate_claims_reused?: number;
  affected_node_ids?: number[];
}

export interface ExtractionTaskSummary {
  id: string;
  source_type: string;
  title: string;
  status: TaskStatus;
  error: string | null;
  auto_commit?: boolean;
  auto_committed?: boolean;
  primary_node_id?: number | null;
  created_at: string;
  completed_at: string | null;
  node_count: number;
  edge_count: number;
}

export interface ExtractionTaskDetail extends ExtractionTaskSummary {
  result: IntakePayload | null;
}
