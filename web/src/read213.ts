import {
  APIRequestError,
  type KnowledgeRelation,
  ontologyLabel,
  type SourceTrace,
  type TimeRange,
  timeWindowParam,
} from "./data";

export type ClaimModality =
  | "FACT"
  | "PLAN_OR_TARGET"
  | "PREDICTION_OR_ESTIMATE"
  | "OPINION_OR_EVALUATION";

export type ClaimRole = "KEY_CLAIM" | "SUPPORTING_CLAIM" | "CONTRASTING_CLAIM";

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

function nullableString(value: unknown): string | null {
  return value === null ? null : string(value);
}

function member<T extends string>(value: unknown, choices: readonly T[]): T {
  const matched = choices.find((choice) => choice === value);
  if (matched === undefined) {
    throw new APIRequestError("INVALID_RESPONSE", 0, true);
  }
  return matched;
}

function directionality(value: unknown): KnowledgeRelation["directionality"] {
  return member(value, ["DIRECTED", "SYMMETRIC"] as const);
}

function modality(value: unknown): ClaimModality {
  return member(value, [
    "FACT",
    "PLAN_OR_TARGET",
    "PREDICTION_OR_ESTIMATE",
    "OPINION_OR_EVALUATION",
  ] as const);
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

export interface CursorPage<T> {
  items: T[];
  nextCursor: string | null;
}

export interface ReadNode {
  id: string;
  name: string;
  kind: string;
  kindCode: string;
}

function readNode(value: unknown): ReadNode {
  const item = object(value);
  const type = object(item.node_type);
  return {
    id: string(item.node_id),
    name: string(item.name),
    kind: string(type.display_name),
    kindCode: string(type.code),
  };
}

export interface NodeRelation {
  sourceId: string;
  targetId: string;
  directionality: KnowledgeRelation["directionality"];
  id: string;
  label: string;
  other: ReadNode;
  evidenceGroupCount: number;
  conflict: boolean;
}

function pagePath(path: string, cursor: string | null): string {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor !== null) params.set("cursor", cursor);
  return `${path}?${params}`;
}

export async function fetchNodeRelations213(
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
      return {
        id: string(item.relation_id),
        sourceId: string(item.source_node_id),
        targetId: string(item.target_node_id),
        directionality: directionality(item.directionality),
        label: ontologyLabel(string(item.relation_type_display_name)),
        other: readNode(item.other_node),
        evidenceGroupCount: number(item.supporting_evidence_group_count),
        conflict: boolean(item.has_conflict),
      };
    }),
    nextCursor: nullableString(payload.next_cursor),
  };
}

function sourceTrace(value: unknown, key: string): SourceTrace {
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
  if (url !== null && !/^https?:\/\//i.test(url)) {
    throw new APIRequestError("INVALID_RESPONSE", 0, true);
  }
  return {
    key,
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
}

export interface EvidenceTrace213 extends SourceTrace {
  claimText: string;
  modality: ClaimModality;
  stance: "SUPPORT" | "DISPUTE";
}

export async function fetchRelationEvidence213(
  id: string,
  cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<EvidenceTrace213>> {
  const payload = object(
    await fetchAPI(
      pagePath(`/api/v1/relations/${encodeURIComponent(id)}/evidence`, cursor),
      signal,
    ),
  );
  return {
    items: array(payload.items).map((value) => {
      const item = object(value);
      return {
        ...sourceTrace(item, string(item.item_key)),
        claimText: string(item.claim_text),
        modality: modality(item.modality),
        stance: member(item.stance, ["SUPPORT", "DISPUTE"] as const),
      };
    }),
    nextCursor: nullableString(payload.next_cursor),
  };
}

export interface ClaimRelationConnection {
  id: string;
  displayName: string;
  directionality: KnowledgeRelation["directionality"];
  sourceNode: ReadNode;
  targetNode: ReadNode;
  otherNode: ReadNode;
  stance: "SUPPORT" | "DISPUTE";
}

export interface PanelConnection213 {
  kind: "RELATION" | "ATTRIBUTE" | "EVENT_TIME" | "CONFLICT";
  id: string;
  position: string | null;
  label: string;
  relation: ClaimRelationConnection | null;
}

export interface PanelClaim213 {
  id: string;
  text: string;
  modality: ClaimModality;
  state: string;
  evidenceGroupCount: number;
  asOf: string;
  role: ClaimRole | null;
  connections: PanelConnection213[];
}

function panelClaim(value: unknown): PanelClaim213 {
  const row = object(value);
  return {
    id: string(row.claim_id),
    text: string(row.claim_text),
    modality: modality(row.modality),
    state: string(row.knowledge_state),
    evidenceGroupCount: number(row.evidence_group_count),
    asOf: string(row.as_of_at),
    role:
      row.role === null
        ? null
        : member(row.role, [
            "KEY_CLAIM",
            "SUPPORTING_CLAIM",
            "CONTRASTING_CLAIM",
          ] as const),
    connections: array(row.connections).map((value) => {
      const connection = object(value);
      const kind = member(connection.kind, [
        "RELATION",
        "ATTRIBUTE",
        "EVENT_TIME",
        "CONFLICT",
      ] as const);
      const rawRelation = connection.relation;
      let relation: ClaimRelationConnection | null = null;
      if (rawRelation != null) {
        const item = object(rawRelation);
        relation = {
          id: string(item.relation_id),
          displayName: ontologyLabel(string(item.display_name)),
          directionality: directionality(item.directionality),
          sourceNode: readNode(item.source_node),
          targetNode: readNode(item.target_node),
          otherNode: readNode(item.other_node),
          stance: member(item.stance, ["SUPPORT", "DISPUTE"] as const),
        };
      }
      if ((kind === "RELATION") !== (relation !== null)) {
        throw new APIRequestError("INVALID_RESPONSE", 0, true);
      }
      return {
        kind,
        id: string(connection.target_id),
        position: nullableString(connection.position),
        label: ontologyLabel(string(connection.label)),
        relation,
      };
    }),
  };
}

export interface PanelTrace213 extends SourceTrace {
  periodRole: "IN_WINDOW" | "BACKGROUND" | "UNKNOWN";
}

export interface PanelQuestion213 {
  id: string;
  text: string;
}

export interface PanelAnswer213 extends PanelQuestion213 {
  answer: string;
  caveat: string | null;
  asOf: string;
  sectionId: string | null;
  claims: PanelClaim213[];
}

export interface PanelReport213 {
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
    claims: PanelClaim213[];
  }[];
}

function panelParams(range: TimeRange, cursor: string | null = null) {
  const params = new URLSearchParams({
    time_window: timeWindowParam(range),
  });
  if (cursor) params.set("cursor", cursor);
  return params;
}

export async function fetchPanelClaims213(
  nodeId: string,
  range: TimeRange,
  cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<PanelClaim213>> {
  const body = object(
    await fetchAPI(
      `/api/v1/nodes/${encodeURIComponent(nodeId)}/claims?${panelParams(range, cursor)}`,
      signal,
    ),
  );
  return {
    items: array(body.items).map(panelClaim),
    nextCursor: nullableString(body.next_cursor),
  };
}

export async function fetchPanelTraces213(
  nodeId: string,
  claim: PanelClaim213,
  range: TimeRange,
  cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<PanelTrace213>> {
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
      const item = object(value);
      return {
        ...sourceTrace(item, string(item.trace_id)),
        periodRole: member(item.period_role, [
          "IN_WINDOW",
          "BACKGROUND",
          "UNKNOWN",
        ] as const),
      };
    }),
    nextCursor: nullableString(body.next_cursor),
  };
}

export async function fetchPanelQuestions213(
  nodeId: string,
  range: TimeRange,
  cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<PanelQuestion213>> {
  const body = object(
    await fetchAPI(
      `/api/v1/nodes/${encodeURIComponent(nodeId)}/questions?${panelParams(range, cursor)}`,
      signal,
    ),
  );
  return {
    items: array(body.items).map((value) => {
      const item = object(value);
      return { id: string(item.question_id), text: string(item.question_text) };
    }),
    nextCursor: nullableString(body.next_cursor),
  };
}

export async function fetchPanelAnswer213(
  questionId: string,
  _cursor: string | null,
  signal: AbortSignal,
): Promise<CursorPage<PanelAnswer213>> {
  const item = object(
    await fetchAPI(
      `/api/v1/questions/${encodeURIComponent(questionId)}`,
      signal,
    ),
  );
  return {
    items: [
      {
        id: string(item.question_id),
        text: string(item.question_text),
        answer: string(item.answer),
        caveat: nullableString(item.caveat),
        asOf: string(item.as_of_at),
        sectionId: nullableString(item.section_id),
        claims: array(item.claims).map(panelClaim),
      },
    ],
    nextCursor: null,
  };
}

export async function fetchPanelReport213(
  nodeId: string,
  range: TimeRange,
  detail: boolean,
  signal: AbortSignal,
): Promise<CursorPage<PanelReport213>> {
  const params = panelParams(range);
  params.set("detail", detail ? "true" : "false");
  const body = object(
    await fetchAPI(
      `/api/v1/nodes/${encodeURIComponent(nodeId)}/insight-report?${params}`,
      signal,
    ),
  );
  return {
    items: array(body.items).map((value) => {
      const item = object(value);
      return {
        id: string(item.report_id),
        title: string(item.title),
        summary: string(item.summary),
        asOf: string(item.as_of_at),
        evidenceGroupCount: number(item.evidence_group_count),
        conclusion:
          item.conclusion === undefined
            ? null
            : nullableString(item.conclusion),
        caveat: item.caveat === undefined ? null : nullableString(item.caveat),
        sections: array(item.sections).map((value) => {
          const section = object(value);
          return {
            id: string(section.section_id),
            title: string(section.title),
            synthesis:
              section.synthesis === undefined
                ? null
                : nullableString(section.synthesis),
            caveat:
              section.caveat === undefined
                ? null
                : nullableString(section.caveat),
            claims:
              section.claims === undefined
                ? []
                : array(section.claims).map(panelClaim),
          };
        }),
      };
    }),
    nextCursor: null,
  };
}
