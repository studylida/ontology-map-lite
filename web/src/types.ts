// 1. 온톨로지 분류 (Classification)
export interface Classification {
  id: number;
  code: string;
  display_name: string;
  description?: string | null;
}

// 2. 그래프 노드 & 엣지
export interface GraphNode {
  id: number;
  name: string;
  classification_id: number;
  classification_code?: string;
  description?: string | null;
  properties?: Record<string, unknown>;
}

export interface GraphEdge {
  id: number;
  source_node_id: number;
  target_node_id: number;
  relation_code?: string;
  relation_name?: string;
  properties?: Record<string, unknown>;
}

// GET /api/v1/nodes/{id}/graph 응답
export interface SubgraphResponse {
  center_node_id: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
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
  name: string;
  classification?: string;
  description?: string;
  properties?: Record<string, unknown>;
}

export interface IntakeEdge {
  source_name: string;
  target_name: string;
  relation?: string;
  properties?: Record<string, unknown>;
}

export interface IntakeClaim {
  quote: string;
  claim_text?: string;
  confidence?: number;
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
  nodes_created: number;
  edges_created: number;
  claims_created: number;
}

export interface ExtractionTaskSummary {
  id: string;
  source_type: string;
  title: string;
  status: TaskStatus;
  error: string | null;
  created_at: string;
  completed_at: string | null;
  node_count: number;
  edge_count: number;
}

export interface ExtractionTaskDetail extends ExtractionTaskSummary {
  result: IntakePayload | null;
}

