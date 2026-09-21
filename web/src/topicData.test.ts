import { afterEach, describe, expect, it, vi } from "vitest";
import type { APIRequestError } from "./data";
import {
  fetchCenterExploration,
  isTopicExploration,
  toTopicExplorationView,
  toTopicReferences,
} from "./topicData";

const topicPayload = {
  topic: {
    node_id: "77",
    topic_code: "SEMICONDUCTOR",
    canonical_display_name: "반도체",
    is_active: false,
  },
  time_window: "RECENT_90_DAYS",
  total_public_membership_count: 2,
  recent_member_count: 1,
  recent_activity_evidence_group_count: 4,
  graph: {
    nodes: [
      {
        node_id: "77",
        name: "반도체",
        node_type: { code: "TOPIC", display_name: "주제" },
        tier: "CENTER",
        activity_evidence_group_count: 4,
      },
      {
        node_id: "10",
        name: "회사 A",
        node_type: { code: "COMPANY", display_name: "회사" },
        tier: "DIRECT",
        activity_evidence_group_count: 3,
      },
      {
        node_id: "11",
        name: "기술 B",
        node_type: { code: "TECHNOLOGY", display_name: "기술" },
        tier: "DIRECT",
        activity_evidence_group_count: 0,
      },
    ],
    relations: [
      {
        relation_id: "91",
        source_node_id: "10",
        target_node_id: "77",
        relation_type_display_name: "주제 분류",
        directionality: "DIRECTED",
        supporting_evidence_group_count: 9,
        has_conflict: false,
      },
      {
        relation_id: "92",
        source_node_id: "11",
        target_node_id: "77",
        relation_type_display_name: "주제 분류",
        directionality: "DIRECTED",
        supporting_evidence_group_count: 2,
        has_conflict: false,
      },
    ],
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Reference Topic reads", () => {
  it("sorts Topic references by Korean canonical name and keeps inactive rows", () => {
    const items = toTopicReferences({
      items: [
        {
          node_id: "2",
          topic_code: "TWO",
          canonical_display_name: "투자",
          is_active: false,
        },
        {
          node_id: "1",
          topic_code: "ONE",
          canonical_display_name: "반도체",
          is_active: true,
        },
      ],
    });

    expect(items.map((item) => item.name)).toEqual(["반도체", "투자"]);
    expect(items[1]?.isActive).toBe(false);
  });

  it("keeps selected-period member activity separate from all-time relation counts", () => {
    const view = toTopicExplorationView(topicPayload);

    expect(isTopicExploration(view)).toBe(true);
    expect(view.totalPublicMembershipCount).toBe(2);
    expect(
      view.nodes.find((node) => node.id === "10")?.activityEvidenceGroupCount,
    ).toBe(3);
    expect(
      view.nodes.find((node) => node.id === "11")?.activityEvidenceGroupCount,
    ).toBe(0);
    expect(
      view.relations.find((relation) => relation.id === "91")
        ?.evidenceGroupCount,
    ).toBe(9);
  });

  it("falls back to Topic exploration only when the general center is not public", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: "NODE_NOT_FOUND", retryable: false },
          }),
          { status: 404, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(topicPayload), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const view = await fetchCenterExploration("77", "90d");

    expect(isTopicExploration(view)).toBe(true);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/exploration/77?time_window=RECENT_90_DAYS",
      undefined,
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/topics/77/exploration?time_window=RECENT_90_DAYS",
      undefined,
    );
  });

  it("preserves a retryable Topic read failure after the general 404 fallback", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: "NODE_NOT_FOUND", retryable: false },
          }),
          { status: 404, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: "TOPIC_READ_FAILED", retryable: true },
          }),
          { status: 500, headers: { "content-type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchCenterExploration("77", "90d")).rejects.toMatchObject({
      code: "TOPIC_READ_FAILED",
      status: 500,
      retryable: true,
    } satisfies Partial<APIRequestError>);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("preserves the general publication-not-ready request without Topic fallback", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: { code: "PUBLICATION_NOT_READY", retryable: true },
        }),
        { status: 503, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchCenterExploration("12", "90d")).rejects.toMatchObject({
      code: "PUBLICATION_NOT_READY",
      status: 503,
    } satisfies Partial<APIRequestError>);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
