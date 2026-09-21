import {
  APIRequestError,
  type ExplorationView,
  fetchExploration,
  type KnowledgeNode,
  type KnowledgeRelation,
  type NodeTier,
  ontologyLabel,
  type TimeRange,
  timeWindowParam,
} from "./data";

export interface TopicReference {
  nodeId: string;
  name: string;
  isActive: boolean;
}

export interface TopicExplorationView extends ExplorationView {
  topic: TopicReference;
  totalPublicMembershipCount: number;
}

type JsonObject = Record<string, unknown>;

function object(value: unknown): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new APIRequestError("INVALID_RESPONSE", 0, true);
  }
  return value as JsonObject;
}

function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) {
    throw new APIRequestError("INVALID_RESPONSE", 0, true);
  }
  return value;
}

function string(value: unknown): string {
  if (typeof value !== "string") {
    throw new APIRequestError("INVALID_RESPONSE", 0, true);
  }
  return value;
}

function number(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new APIRequestError("INVALID_RESPONSE", 0, true);
  }
  return value;
}

function boolean(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new APIRequestError("INVALID_RESPONSE", 0, true);
  }
  return value;
}

function nodeTier(value: unknown): Exclude<NodeTier, "ambient"> {
  if (value === "CENTER") return "center";
  if (value === "DIRECT") return "direct";
  if (value === "TWO_HOP") return "twoHop";
  if (value === "THREE_HOP") return "threeHop";
  throw new APIRequestError("INVALID_RESPONSE", 0, true);
}

function directionality(value: unknown): KnowledgeRelation["directionality"] {
  if (value === "DIRECTED" || value === "SYMMETRIC") return value;
  throw new APIRequestError("INVALID_RESPONSE", 0, true);
}

function relationTier(
  source: KnowledgeNode,
  target: KnowledgeNode,
): Exclude<NodeTier, "center"> {
  if (source.tier === "center" || target.tier === "center") return "direct";
  if (source.tier === "threeHop" || target.tier === "threeHop") {
    return "threeHop";
  }
  return "twoHop";
}

async function fetchJSON(path: string, signal?: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, signal ? { signal } : undefined);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new APIRequestError("NETWORK_ERROR", 0, true);
  }

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const errorBody =
      payload && typeof payload === "object"
        ? (payload as JsonObject).error
        : null;
    const detail =
      errorBody && typeof errorBody === "object"
        ? (errorBody as JsonObject)
        : {};
    throw new APIRequestError(
      typeof detail.code === "string" ? detail.code : "REQUEST_FAILED",
      response.status,
      typeof detail.retryable === "boolean"
        ? detail.retryable
        : response.status >= 500,
    );
  }
  return payload;
}

export function toTopicReferences(payload: unknown): TopicReference[] {
  return array(object(payload).items)
    .map((value) => {
      const item = object(value);
      return {
        nodeId: string(item.node_id),
        name: string(item.canonical_display_name),
        isActive: boolean(item.is_active),
      } satisfies TopicReference;
    })
    .sort((left, right) => left.name.localeCompare(right.name, "ko"));
}

export function toTopicExplorationView(payload: unknown): TopicExplorationView {
  const root = object(payload);
  const topic = object(root.topic);
  const graph = object(root.graph);
  const nodes = array(graph.nodes).map((value) => {
    const item = object(value);
    const type = object(item.node_type);
    return {
      id: string(item.node_id),
      name: string(item.name),
      kind: string(type.display_name),
      kindCode: string(type.code),
      tier: nodeTier(item.tier),
      activityEvidenceGroupCount: number(item.activity_evidence_group_count),
    } satisfies KnowledgeNode;
  });
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const relations = array(graph.relations).map((value) => {
    const item = object(value);
    const source = nodesById.get(string(item.source_node_id));
    const target = nodesById.get(string(item.target_node_id));
    if (!source || !target) {
      throw new APIRequestError("INVALID_RESPONSE", 0, true);
    }
    return {
      id: string(item.relation_id),
      source: source.id,
      target: target.id,
      label: ontologyLabel(string(item.relation_type_display_name)),
      directionality: directionality(item.directionality),
      evidenceGroupCount: number(item.supporting_evidence_group_count),
      conflict: boolean(item.has_conflict),
      tier: relationTier(source, target),
    } satisfies KnowledgeRelation;
  });
  const topicReference: TopicReference = {
    nodeId: string(topic.node_id),
    name: string(topic.canonical_display_name),
    isActive: boolean(topic.is_active),
  };
  return {
    centerId: topicReference.nodeId,
    context: "",
    contextIsCurrent: false,
    periodHighlights: [],
    nodes,
    relations,
    recommendations: [],
    followups: [],
    topic: topicReference,
    totalPublicMembershipCount: number(root.total_public_membership_count),
  };
}

export async function fetchTopicReferences(
  signal?: AbortSignal,
): Promise<TopicReference[]> {
  return toTopicReferences(await fetchJSON("/api/v1/topics", signal));
}

export async function fetchTopicExploration(
  centerId: string,
  range: TimeRange,
  signal?: AbortSignal,
): Promise<TopicExplorationView> {
  const params = new URLSearchParams({
    time_window: timeWindowParam(range),
  });
  return toTopicExplorationView(
    await fetchJSON(
      `/api/v1/topics/${encodeURIComponent(centerId)}/exploration?${params}`,
      signal,
    ),
  );
}

export async function fetchCenterExploration(
  centerId: string,
  range: TimeRange,
  signal?: AbortSignal,
): Promise<ExplorationView> {
  try {
    return await fetchExploration(centerId, range, signal);
  } catch (error) {
    if (
      !(error instanceof APIRequestError) ||
      error.code !== "NODE_NOT_FOUND"
    ) {
      throw error;
    }
    try {
      return await fetchTopicExploration(centerId, range, signal);
    } catch (topicError) {
      if (
        topicError instanceof DOMException &&
        topicError.name === "AbortError"
      ) {
        throw topicError;
      }
      if (
        topicError instanceof APIRequestError &&
        topicError.code === "TOPIC_NOT_FOUND"
      ) {
        throw error;
      }
      throw topicError;
    }
  }
}

export function isTopicExploration(
  view: ExplorationView | null,
): view is TopicExplorationView {
  return Boolean(
    view && "topic" in view && "totalPublicMembershipCount" in view,
  );
}
