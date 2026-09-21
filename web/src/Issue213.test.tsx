import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PanelEvidence } from "./PanelEvidence";
import { EvidenceDialog } from "./RelationPanel";
import { TopicPanel } from "./TopicPanel";
import type { TopicExplorationView } from "./topicData";

const request = vi.fn();
const response = (body: unknown, status = 200) => ({
  ok: status < 400,
  status,
  json: async () => body,
});

const center = {
  node_id: "1",
  name: "중심 회사",
  node_type: { code: "COMPANY", display_name: "회사" },
};
const technology = {
  node_id: "2",
  name: "HBF",
  node_type: { code: "TECHNOLOGY", display_name: "기술" },
};
const person = {
  node_id: "3",
  name: "연결 인물",
  node_type: { code: "PERSON", display_name: "사람" },
};

function relationConnection(
  id: string,
  other: typeof technology | typeof person,
  stance: "SUPPORT" | "DISPUTE",
) {
  return {
    kind: "RELATION",
    target_id: id,
    position: stance,
    label: `${center.name} · 관련 · ${other.name}`,
    relation: {
      relation_id: id,
      display_name: "관련",
      directionality: "DIRECTED",
      source_node: center,
      target_node: other,
      other_node: other,
      stance,
    },
  };
}

const claim = {
  claim_id: "10",
  claim_text: "계획 성격의 비교 근거",
  modality: "PLAN_OR_TARGET",
  knowledge_state: "EVIDENCE_VERIFIED",
  evidence_group_count: 2,
  as_of_at: "2026-09-15T00:00:00Z",
  role: "CONTRASTING_CLAIM",
  connections: [
    relationConnection("100", technology, "SUPPORT"),
    relationConnection("101", person, "DISPUTE"),
  ],
};

beforeEach(() => {
  request.mockReset();
  vi.stubGlobal("fetch", request);
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: {
      configurable: true,
      value: function (this: HTMLDialogElement) {
        this.setAttribute("open", "");
      },
    },
    close: {
      configurable: true,
      value: function (this: HTMLDialogElement) {
        this.removeAttribute("open");
      },
    },
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
  Reflect.deleteProperty(HTMLDialogElement.prototype, "close");
});

describe("Issue #213 relation verification UX", () => {
  it("keeps Claim disclosure separate from multiple Relation and Node actions", async () => {
    request.mockImplementation(async (path: string) => {
      if (path.includes("/claims/10/evidence")) {
        return response({
          items: [
            {
              trace_id: "900",
              source: {
                title: "Claim 원문",
                publisher_name: "출처",
                published_at: "2026-09-10T00:00:00Z",
                published_precision: "DAY",
                canonical_url: "https://example.com/claim",
              },
              quote_text: "Claim 직접 근거",
              locator: { paragraph_number: 1, start_char: 0, end_char: 10 },
              period_role: "IN_WINDOW",
            },
          ],
          next_cursor: null,
        });
      }
      return response({ items: [claim], next_cursor: null });
    });
    const onEvidence = vi.fn();
    const onLocate = vi.fn();
    const openSource = vi.fn();
    render(
      <PanelEvidence
        nodeId="1"
        range="90d"
        onEvidence={onEvidence}
        onLocate={onLocate}
        openSource={openSource}
      />,
    );

    const record = await screen.findByRole("button", {
      name: /계획 성격의 비교 근거/,
    });
    expect(record.textContent).toContain("계획");
    expect(record.textContent).toContain("원문 보기");
    expect(screen.queryByRole("button", { name: "연결 원문" })).toBeNull();

    fireEvent.click(record);
    await waitFor(() =>
      expect(openSource).toHaveBeenCalledWith("https://example.com/claim"),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onEvidence).not.toHaveBeenCalled();
    expect(onLocate).not.toHaveBeenCalled();
    expect(screen.queryByText("전체 관계")).toBeNull();
  });

  it("uses opaque Evidence item identity so same source and locator keeps separate Claim/stance items", async () => {
    const source = {
      title: "공유 출처",
      publisher_name: "공개 출처",
      published_at: "2026-09-01T00:00:00Z",
      published_precision: "DAY",
      canonical_url: "https://example.com/shared",
    };
    const locator = { paragraph_number: 1, start_char: 0, end_char: 5 };
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);
    request.mockResolvedValue(
      response({
        items: [
          {
            item_key: "opaque-support",
            claim_text: "서로 다른 지지 Claim",
            modality: "FACT",
            stance: "SUPPORT",
            source,
            quote_text: "같은 인용",
            locator,
          },
          {
            item_key: "opaque-dispute",
            claim_text: "서로 다른 반박 Claim",
            modality: "PREDICTION_OR_ESTIMATE",
            stance: "DISPUTE",
            source,
            quote_text: "같은 인용",
            locator,
          },
        ],
        trace_count: 2,
        next_cursor: null,
      }),
    );
    render(
      <EvidenceDialog
        selection={{ id: "100", label: "검토 관계" }}
        onClose={vi.fn()}
      />,
    );
    expect(await screen.findAllByText("같은 인용")).toHaveLength(2);
    expect(screen.queryByText("서로 다른 지지 Claim")).toBeNull();
    expect(screen.queryByText("서로 다른 반박 Claim")).toBeNull();
    expect(screen.getByText("연결을 뒷받침")).toBeTruthy();
    expect(screen.getByText("연결과 상충")).toBeTruthy();
    expect(screen.getByText("예측·추정")).toBeTruthy();
    expect(
      consoleError.mock.calls.some((call) =>
        String(call[0]).includes("same key"),
      ),
    ).toBe(false);
  });

  it("opens the common HAS_TOPIC Evidence action without changing Topic center", async () => {
    request.mockResolvedValue(response({ items: [], next_cursor: null }));
    const topicView: TopicExplorationView = {
      centerId: "77",
      context: "",
      contextIsCurrent: false,
      periodHighlights: [],
      nodes: [
        {
          id: "77",
          name: "반도체",
          kind: "주제",
          kindCode: "TOPIC",
          tier: "center",
          activityEvidenceGroupCount: 0,
        },
        {
          id: "1",
          name: center.name,
          kind: "회사",
          kindCode: "COMPANY",
          tier: "direct",
          activityEvidenceGroupCount: 3,
        },
      ],
      relations: [
        {
          id: "700",
          source: "1",
          target: "77",
          label: "주제 분류",
          directionality: "DIRECTED",
          evidenceGroupCount: 3,
          conflict: false,
          tier: "direct",
        },
      ],
      recommendations: [],
      followups: [],
      topic: { nodeId: "77", name: "반도체", isActive: false },
      totalPublicMembershipCount: 1,
    };
    const onEvidence = vi.fn();
    const onSelect = vi.fn();
    render(
      <TopicPanel
        view={topicView}
        timeRange="90d"
        onClose={vi.fn()}
        onSelect={onSelect}
        onLocate={vi.fn()}
        onSelectInsight={vi.fn()}
        onEvidence={onEvidence}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "연결 원문" }));
    expect(onEvidence).toHaveBeenCalledWith(
      expect.objectContaining({ id: "700" }),
    );
    expect(onSelect).not.toHaveBeenCalled();
  });
});
