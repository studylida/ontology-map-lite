export type TimeRange = "90d" | "1y" | "all";
export type NodeTier = "center" | "direct" | "twoHop" | "threeHop" | "ambient";

export function timeWindowParam(
  range: TimeRange,
): "RECENT_90_DAYS" | "RECENT_1_YEAR" | "ALL_TIME" {
  if (range === "90d") return "RECENT_90_DAYS";
  if (range === "1y") return "RECENT_1_YEAR";
  return "ALL_TIME";
}

export function timeRangeLabel(range: TimeRange): string {
  if (range === "90d") return "최근 90일";
  if (range === "1y") return "최근 1년";
  return "전체 기간";
}

const ontologyLabels: Record<string, string> = {
  AFFILIATED_WITH: "소속",
  DEVELOPS: "개발",
  COLLABORATES_WITH: "협력",
  ANNOUNCES: "발표",
  INVESTS_IN: "투자",
  ADOPTS: "도입",
  TESTS: "시험",
  SUPPLIES: "공급",
  SUPPLIES_TO: "공급 관계",
  INCLUDES: "포함",
  PARTICIPATES_IN: "참여",
  MENTIONS: "언급",
  RELATED_TO: "관련",
  HAS_TOPIC: "관련 주제",
  ROLE_TITLE: "직책",
  TECHNOLOGY_VERSION: "기술 버전",
  COMMERCIALIZATION_STATUS: "상용화 상태",
  COMMERCIALIZATION_SCHEDULE: "상용화 일정",
  CORE_COUNT: "코어 수",
  MAX_MEMORY_BANDWIDTH: "최대 메모리 대역폭",
};

export function ontologyLabel(value: string): string {
  return ontologyLabels[value] ?? value;
}

export interface KnowledgeNode {
  id: string;
  name: string;
  kind: string;
  kindCode: string;
  tier: NodeTier;
  activityEvidenceGroupCount: number;
}

export interface KnowledgeRelation {
  id: string;
  source: string;
  target: string;
  label: string;
  directionality: "DIRECTED" | "SYMMETRIC";
  evidenceGroupCount: number;
  conflict?: boolean;
  tier: Exclude<NodeTier, "center">;
}

export interface ExplorationRecommendation {
  node: KnowledgeNode;
  reason: string;
  status: "confirmedRelation" | "connectedPath" | "ambient";
  path: { id: string; source: string; target: string }[];
  evidenceGroupCount?: number;
}

export interface FollowupQuestion {
  id: string;
  text: string;
  targetNodeId: string;
}

export interface ExplorationView {
  centerId: string;
  context: string;
  contextIsCurrent: boolean;
  periodHighlights: ClaimHighlight[];
  nodes: KnowledgeNode[];
  relations: KnowledgeRelation[];
  recommendations: ExplorationRecommendation[];
  followups: FollowupQuestion[];
}

export interface ClaimHighlight {
  id: string;
  text: string;
  modality: string;
  evidenceGroupCount: number;
  publishedAt: string | null;
  precision: "INSTANT" | "DAY" | "MONTH" | "YEAR" | "UNKNOWN";
}

export interface SearchCandidate {
  nodeId: string;
  name: string;
  kind: string;
  kindCode: string;
}

export type KnowledgeViewNode = KnowledgeNode;
export type KnowledgeViewRelation = KnowledgeRelation;

export class APIRequestError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    readonly retryable: boolean,
  ) {
    super(code);
  }
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

function relationTier(
  source: KnowledgeNode,
  target: KnowledgeNode,
): Exclude<NodeTier, "center"> {
  if (source.tier === "center" || target.tier === "center") return "direct";
  if (source.tier === "ambient" || target.tier === "ambient") return "ambient";
  if (source.tier === "threeHop" || target.tier === "threeHop")
    return "threeHop";
  return "twoHop";
}

export function relationPathLabel(
  source: string,
  label: string,
  target: string,
  direction: KnowledgeRelation["directionality"],
): string {
  return `${source} ${direction === "DIRECTED" ? "→" : "↔"} ${target} · ${ontologyLabel(label)}`;
}

function recommendationDetails(item: JsonObject) {
  const code = member(item.reason_code, [
    "DIRECT",
    "TWO_HOP",
    "AMBIENT",
  ] as const);
  const rawPath = array(item.path);
  if (rawPath.length !== { DIRECT: 1, TWO_HOP: 2, AMBIENT: 0 }[code])
    throw new APIRequestError("INVALID_RESPONSE", 0, true);
  const path = rawPath.map((value) => {
    const edge = object(value);
    return {
      id: string(edge.relation_id),
      source: string(edge.source_node_id),
      target: string(edge.target_node_id),
      label: relationPathLabel(
        string(edge.source_node_name),
        ontologyLabel(string(edge.relation_type_display_name)),
        string(edge.target_node_name),
        directionality(edge.directionality),
      ),
    };
  });
  return {
    code,
    path,
    reason:
      code === "AMBIENT"
        ? "현재 중심과의 관계가 확인되지 않은 새 탐색 출발점입니다."
        : path.map(({ label }) => label).join(" · "),
  };
}

export function toExplorationView(payload: unknown): ExplorationView {
  const root = object(payload);
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
  const recommendations = array(root.recommendations).map((value) => {
    const item = object(value);
    const target = object(item.target_node);
    const targetType = object(target.node_type);
    const id = string(target.node_id);
    const details = recommendationDetails(item);
    const graphNode = nodesById.get(id);
    const recommendationNode: KnowledgeNode = graphNode ?? {
      id,
      name: string(target.name),
      kind: string(targetType.display_name),
      kindCode: string(targetType.code),
      tier: "ambient",
      activityEvidenceGroupCount: 0,
    };
    const evidenceCount =
      item.supporting_evidence_group_count === null
        ? undefined
        : number(item.supporting_evidence_group_count);
    return {
      node: recommendationNode,
      reason: details.reason,
      path: details.path.map(({ id, source, target }) => ({
        id,
        source,
        target,
      })),
      status:
        details.code === "DIRECT"
          ? "confirmedRelation"
          : details.code === "TWO_HOP"
            ? "connectedPath"
            : "ambient",
      ...(evidenceCount === undefined
        ? {}
        : { evidenceGroupCount: evidenceCount }),
    } satisfies ExplorationRecommendation;
  });
  const followups = array(root.followup_questions).map((value) => {
    const item = object(value);
    const slot = number(item.slot);
    return {
      id: `followup-${slot}`,
      text: string(item.question_text),
      targetNodeId: string(item.target_node_id),
    } satisfies FollowupQuestion;
  });
  const periodHighlights = (
    root.period_highlights === undefined ? [] : array(root.period_highlights)
  ).map((value) => {
    const item = object(value);
    return {
      id: string(item.claim_id),
      text: string(item.claim_text),
      modality: string(item.modality),
      evidenceGroupCount: number(item.evidence_group_count),
      publishedAt: nullableString(item.latest_published_at),
      precision: member(item.latest_published_precision, [
        "INSTANT",
        "DAY",
        "MONTH",
        "YEAR",
        "UNKNOWN",
      ] as const),
    } satisfies ClaimHighlight;
  });

  const displayNodes = [
    ...new Map(
      [...nodes, ...recommendations.map(({ node }) => node)].map((node) => [
        node.id,
        node,
      ]),
    ).values(),
  ];
  return {
    centerId: string(root.center_node_id),
    context: string(root.context_text),
    contextIsCurrent:
      root.context_is_current === undefined
        ? false
        : boolean(root.context_is_current),
    periodHighlights,
    nodes: displayNodes,
    relations,
    recommendations,
    followups,
  };
}

export function toSearchCandidates(payload: unknown): SearchCandidate[] {
  return array(object(payload).items).map((value) => {
    const item = object(value);
    const type = object(item.node_type);
    return {
      nodeId: string(item.node_id),
      name: string(item.name),
      kind: string(type.display_name),
      kindCode: string(type.code),
    };
  });
}

async function fetchAPI(path: string, signal?: AbortSignal): Promise<unknown> {
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

export async function fetchExploration(
  centerId: string,
  timeRange: TimeRange,
  signal?: AbortSignal,
): Promise<ExplorationView> {
  const path = `/api/v1/exploration/${encodeURIComponent(
    centerId,
  )}?time_window=${timeWindowParam(timeRange)}`;
  return toExplorationView(await fetchAPI(path, signal));
}

export async function fetchNodeSearch(
  query: string,
  signal?: AbortSignal,
): Promise<SearchCandidate[]> {
  const params = new URLSearchParams({ q: query, limit: "5" });
  return toSearchCandidates(
    await fetchAPI(`/api/v1/nodes/search?${params.toString()}`, signal),
  );
}

export function getFilamentOffsets(evidenceGroupCount: number): number[] {
  const count = Math.max(1, Math.round(evidenceGroupCount));
  if (count === 1) return [0];
  const spacing = count <= 5 ? 1.2 : 4.8 / (count - 1);
  return Array.from(
    { length: count },
    (_, index) => (index - (count - 1) / 2) * spacing,
  );
}

function member<T extends string>(value: unknown, choices: readonly T[]): T {
  const matched = choices.find((choice) => choice === value);
  if (matched === undefined)
    throw new APIRequestError("INVALID_RESPONSE", 0, true);
  return matched;
}

function directionality(value: unknown): KnowledgeRelation["directionality"] {
  return member(value, ["DIRECTED", "SYMMETRIC"] as const);
}

export interface CursorPage<T> {
  items: T[];
  nextCursor: string | null;
}
export interface NodeRelation {
  sourceId: string;
  targetId: string;
  directionality: KnowledgeRelation["directionality"];
  id: string;
  label: string;
  otherName: string;
  otherKind: string;
  evidenceGroupCount: number;
  conflict: boolean;
}
export interface EvidenceTrace extends SourceTrace {
  claimText: string;
  stance: "SUPPORT" | "DISPUTE";
}
export interface SourceTrace {
  key: string;
  title: string;
  publisher: string;
  publishedAt: string | null;
  precision: "INSTANT" | "DAY" | "MONTH" | "YEAR" | "UNKNOWN";
  url: string | null;
  quote: string;
  paragraph: number | null;
  start: number;
  end: number;
}

function nullableString(value: unknown): string | null {
  return value === null ? null : string(value);
}

function pagePath(path: string, cursor: string | null): string {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor !== null) params.set("cursor", cursor);
  return `${path}?${params}`;
}

export async function fetchNodeRelations(
  id: string,
  cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<NodeRelation>> {
  const payload = object(
    await fetchAPI(
      pagePath(`/api/v1/nodes/${encodeURIComponent(id)}/relations`, cursor),
      signal,
    ),
  );
  return {
    items: array(payload.items).map((value) => {
      const item = object(value);
      const other = object(item.other_node);
      return {
        id: string(item.relation_id),
        sourceId: string(item.source_node_id),
        targetId: string(item.target_node_id),
        directionality: directionality(item.directionality),
        label: ontologyLabel(string(item.relation_type_display_name)),
        otherName: string(other.name),
        otherKind: string(object(other.node_type).display_name),
        evidenceGroupCount: number(item.supporting_evidence_group_count),
        conflict: boolean(item.has_conflict),
      };
    }),
    nextCursor: nullableString(payload.next_cursor),
  };
}

function toSourceTrace(value: unknown): SourceTrace {
  const item = object(value);
  const source = object(item.source);
  const locator = object(item.locator);
  const precision = member(source.published_precision, [
    "INSTANT",
    "DAY",
    "MONTH",
    "YEAR",
    "UNKNOWN",
  ] as const);
  const url = nullableString(source.canonical_url);
  if (url !== null && !/^https?:\/\//i.test(url))
    throw new APIRequestError("INVALID_RESPONSE", 0, true);
  const trace: Omit<SourceTrace, "key"> = {
    title: string(source.title),
    publisher: string(source.publisher_name),
    publishedAt: nullableString(source.published_at),
    precision,
    url,
    quote: string(item.quote_text),
    paragraph:
      locator.paragraph_number === null
        ? null
        : number(locator.paragraph_number),
    start: number(locator.start_char),
    end: number(locator.end_char),
  };
  return { ...trace, key: JSON.stringify(trace) };
}

function toEvidenceTrace(value: unknown): EvidenceTrace {
  const item = object(value);
  return {
    ...toSourceTrace(item),
    claimText: string(item.claim_text),
    stance: member(item.stance, ["SUPPORT", "DISPUTE"] as const),
  };
}

export async function fetchRelationEvidence(
  id: string,
  cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<EvidenceTrace>> {
  const payload = object(
    await fetchAPI(
      pagePath(`/api/v1/relations/${encodeURIComponent(id)}/evidence`, cursor),
      signal,
    ),
  );
  return {
    items: array(payload.items).map(toEvidenceTrace),
    nextCursor: nullableString(payload.next_cursor),
  };
}

export interface PeripheralPage {
  nodes: KnowledgeNode[];
  relations: KnowledgeRelation[];
  nextCursor: string | null;
}

export function mergeById<T extends { id: string }>(
  current: T[],
  additions: T[],
): T[] {
  return [
    ...new Map(
      [...current, ...additions].map((item) => [item.id, item]),
    ).values(),
  ];
}

export async function fetchPeripheral(
  view: ExplorationView,
  range: TimeRange,
  cursor: string | null,
  signal: AbortSignal,
): Promise<PeripheralPage> {
  const params = new URLSearchParams({
    time_window: timeWindowParam(range),
    limit: "20",
  });
  if (cursor !== null) params.set("cursor", cursor);
  const payload = object(
    await fetchAPI(
      `/api/v1/exploration/${encodeURIComponent(view.centerId)}/peripheral?${params}`,
      signal,
    ),
  );
  const graph = object(payload.graph);
  const nodes = array(graph.nodes).map((value) => {
    const item = object(value);
    const type = object(item.node_type);
    member(item.tier, ["AMBIENT"] as const);
    return {
      id: string(item.node_id),
      name: string(item.name),
      kind: string(type.display_name),
      kindCode: string(type.code),
      tier: "ambient" as const,
      activityEvidenceGroupCount: number(item.activity_evidence_group_count),
    };
  });
  const known = new Set([...view.nodes, ...nodes].map((node) => node.id));
  const relations = array(graph.relations).map((value) => {
    const item = object(value);
    const source = string(item.source_node_id);
    const target = string(item.target_node_id);
    if (!known.has(source) || !known.has(target))
      throw new APIRequestError("INVALID_RESPONSE", 0, true);
    return {
      id: string(item.relation_id),
      source,
      target,
      label: ontologyLabel(string(item.relation_type_display_name)),
      directionality: directionality(item.directionality),
      evidenceGroupCount: number(item.supporting_evidence_group_count),
      conflict: boolean(item.has_conflict),
      tier:
        source === view.centerId || target === view.centerId
          ? ("direct" as const)
          : ("ambient" as const),
    };
  });
  return { nodes, relations, nextCursor: nullableString(payload.next_cursor) };
}

export interface InsightItem {
  id: string;
  title: string;
  evidenceGroupCount: number;
}
export interface InsightReport extends InsightItem {
  summary: string;
  synthesis: string;
  caveat: string;
  claims: {
    id: string;
    text: string;
    role: "KEY_CLAIM" | "SUPPORTING_CLAIM" | "CONTRASTING_CLAIM";
    traces: SourceTrace[];
  }[];
}
function toInsightItem(value: unknown): InsightItem {
  const item = object(value);
  return {
    id: string(item.insight_id),
    title: string(item.title),
    evidenceGroupCount: number(item.evidence_group_count),
  };
}
export async function fetchNodeInsights(
  nodeId: string,
  range: TimeRange,
  signal: AbortSignal,
): Promise<CursorPage<InsightItem>> {
  const params = new URLSearchParams({
    time_window: timeWindowParam(range),
  });
  const payload = object(
    await fetchAPI(
      `/api/v1/nodes/${encodeURIComponent(nodeId)}/insights?${params}`,
      signal,
    ),
  );
  return { items: array(payload.items).map(toInsightItem), nextCursor: null };
}
export async function fetchInsight(
  id: string,
  _cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<InsightReport>> {
  const item = object(
    await fetchAPI(`/api/v1/insights/${encodeURIComponent(id)}`, signal),
  );
  return {
    items: [
      {
        ...toInsightItem(item),
        summary: string(item.summary),
        synthesis: string(item.synthesis),
        caveat: string(item.caveat),
        claims: array(item.claims).map((value) => {
          const claim = object(value);
          return {
            id: string(claim.claim_id),
            text: string(claim.claim_text),
            role: member(claim.role, [
              "KEY_CLAIM",
              "SUPPORTING_CLAIM",
              "CONTRASTING_CLAIM",
            ] as const),
            traces: array(claim.traces).map(toSourceTrace),
          };
        }),
      },
    ],
    nextCursor: null,
  };
}

export interface PanelClaim {
  id: string;
  text: string;
  modality: string;
  state: string;
  evidenceGroupCount: number;
  asOf: string;
  role: string | null;
  connections: {
    kind: string;
    id: string;
    position: string | null;
    label: string;
  }[];
}
export interface PanelTrace extends SourceTrace {
  periodRole: "IN_WINDOW" | "BACKGROUND" | "UNKNOWN";
}
export interface PanelQuestion {
  id: string;
  text: string;
}
export interface PanelAnswer extends PanelQuestion {
  answer: string;
  caveat: string | null;
  asOf: string;
  sectionId: string | null;
  claims: PanelClaim[];
}
export interface PanelReport {
  id: string;
  title: string;
  summary: string;
  asOf: string;
  evidenceGroupCount: number;
  conclusion: string | null;
  caveat: string | null;
  sections: {
    id: string;
    title: string;
    synthesis: string | null;
    caveat: string | null;
    claims: PanelClaim[];
  }[];
}
function panelParams(range: TimeRange, cursor: string | null = null) {
  const params = new URLSearchParams({
    time_window: timeWindowParam(range),
  });
  if (cursor) params.set("cursor", cursor);
  return params;
}
function toPanelClaim(value: unknown): PanelClaim {
  const row = object(value);
  return {
    id: string(row.claim_id),
    text: string(row.claim_text),
    modality: string(row.modality),
    state: string(row.knowledge_state),
    evidenceGroupCount: number(row.evidence_group_count),
    asOf: string(row.as_of_at),
    role: nullableString(row.role),
    connections: array(row.connections).map((value) => {
      const connection = object(value);
      return {
        kind: member(connection.kind, [
          "RELATION",
          "ATTRIBUTE",
          "EVENT_TIME",
          "CONFLICT",
        ] as const),
        id: string(connection.target_id),
        position: nullableString(connection.position),
        label: string(connection.label),
      };
    }),
  };
}
export async function fetchPanelClaims(
  nodeId: string,
  range: TimeRange,
  cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<PanelClaim>> {
  const body = object(
    await fetchAPI(
      `/api/v1/nodes/${encodeURIComponent(nodeId)}/claims?${panelParams(range, cursor)}`,
      signal,
    ),
  );
  return {
    items: array(body.items).map(toPanelClaim),
    nextCursor: nullableString(body.next_cursor),
  };
}
export async function fetchPanelTraces(
  nodeId: string,
  claim: PanelClaim,
  range: TimeRange,
  cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<PanelTrace>> {
  const params = panelParams(range, cursor);
  params.set("as_of_at", claim.asOf);
  const body = object(
    await fetchAPI(
      `/api/v1/nodes/${encodeURIComponent(nodeId)}/claims/${encodeURIComponent(claim.id)}/evidence?${params}`,
      signal,
    ),
  );
  return {
    items: array(body.items).map((value) => {
      const row = object(value);
      return {
        ...toSourceTrace(row),
        periodRole: member(row.period_role, [
          "IN_WINDOW",
          "BACKGROUND",
          "UNKNOWN",
        ] as const),
      };
    }),
    nextCursor: nullableString(body.next_cursor),
  };
}
export async function fetchPanelQuestions(
  nodeId: string,
  range: TimeRange,
  cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<PanelQuestion>> {
  const body = object(
    await fetchAPI(
      `/api/v1/nodes/${encodeURIComponent(nodeId)}/questions?${panelParams(range, cursor)}`,
      signal,
    ),
  );
  return {
    items: array(body.items).map((value) => {
      const row = object(value);
      return { id: string(row.question_id), text: string(row.question_text) };
    }),
    nextCursor: nullableString(body.next_cursor),
  };
}
export async function fetchPanelAnswer(
  id: string,
  _cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<PanelAnswer>> {
  const row = object(
    await fetchAPI(`/api/v1/questions/${encodeURIComponent(id)}`, signal),
  );
  return {
    items: [
      {
        id: string(row.question_id),
        text: string(row.question_text),
        answer: string(row.answer),
        caveat: nullableString(row.caveat),
        asOf: string(row.as_of_at),
        sectionId: nullableString(row.section_id),
        claims: array(row.claims).map(toPanelClaim),
      },
    ],
    nextCursor: null,
  };
}
export async function fetchPanelReport(
  nodeId: string,
  range: TimeRange,
  detail: boolean,
  signal: AbortSignal,
): Promise<CursorPage<PanelReport>> {
  const params = panelParams(range);
  params.set("detail", String(detail));
  const body = object(
    await fetchAPI(
      `/api/v1/nodes/${encodeURIComponent(nodeId)}/insight-report?${params}`,
      signal,
    ),
  );
  return {
    items: array(body.items).map((value) => {
      const row = object(value);
      return {
        id: string(row.report_id),
        title: string(row.title),
        summary: string(row.summary),
        asOf: string(row.as_of_at),
        evidenceGroupCount: number(row.evidence_group_count),
        conclusion: row.conclusion == null ? null : string(row.conclusion),
        caveat: row.caveat == null ? null : string(row.caveat),
        sections: array(row.sections).map((value) => {
          const section = object(value);
          return {
            id: string(section.section_id),
            title: string(section.title),
            synthesis:
              section.synthesis == null ? null : string(section.synthesis),
            caveat: section.caveat == null ? null : string(section.caveat),
            claims: array(section.claims).map(toPanelClaim),
          };
        }),
      };
    }),
    nextCursor: null,
  };
}
