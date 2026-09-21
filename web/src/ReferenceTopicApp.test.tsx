import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

vi.mock("./GraphCanvas", () => ({
  GraphCanvas: ({
    view,
    onReady,
    onTransitionComplete,
  }: {
    view: { centerId: string };
    onReady: () => void;
    onTransitionComplete: (nodeId: string) => void;
  }) => (
    <section aria-label="동적 지식맵">
      <span>{`요청 중심: ${view.centerId}`}</span>
      <button type="button" onClick={onReady}>
        그래프 준비 완료
      </button>
      <button type="button" onClick={() => onTransitionComplete(view.centerId)}>
        중심 전환 완료
      </button>
    </section>
  ),
}));

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

const topicPayload = {
  topic: {
    node_id: "77",
    topic_code: "SEMICONDUCTOR",
    canonical_display_name: "반도체",
    is_active: true,
  },
  time_window: "RECENT_1_YEAR",
  total_public_membership_count: 1,
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
        activity_evidence_group_count: 4,
      },
    ],
    relations: [
      {
        relation_id: "91",
        source_node_id: "10",
        target_node_id: "77",
        relation_type_display_name: "주제 분류",
        directionality: "DIRECTED",
        supporting_evidence_group_count: 4,
        has_conflict: false,
      },
    ],
  },
};

const memberExploration = {
  center_node_id: "10",
  context_text: "회사 A의 공개 탐색 문맥입니다.",
  context_is_current: false,
  period_highlights: [],
  graph: {
    nodes: [
      {
        node_id: "10",
        name: "회사 A",
        node_type: { code: "COMPANY", display_name: "회사" },
        tier: "CENTER",
        activity_evidence_group_count: 5,
      },
      {
        node_id: "11",
        name: "기술 B",
        node_type: { code: "TECHNOLOGY", display_name: "기술" },
        tier: "DIRECT",
        activity_evidence_group_count: 2,
      },
    ],
    relations: [
      {
        relation_id: "101",
        source_node_id: "10",
        target_node_id: "11",
        relation_type_display_name: "관련 기술",
        directionality: "DIRECTED",
        supporting_evidence_group_count: 2,
        has_conflict: false,
      },
    ],
  },
  recommendations: [],
  followup_questions: [
    { slot: 1, question_text: "질문 1", target_node_id: "11" },
    { slot: 2, question_text: "질문 2", target_node_id: "10" },
  ],
};

const reportPayload = {
  items: [
    {
      report_id: "501",
      title: "반도체 공급망 변화",
      summary: "요약",
      as_of_at: "2026-09-15T00:00:00Z",
      evidence_group_count: 3,
      conclusion: null,
      caveat: null,
      sections: [],
    },
  ],
};

const fetchMock = vi.fn();

describe("Reference Topic App navigation", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/?center=77&range=1y");
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ matches: true }),
    });
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input: string) => {
      if (input === "/api/v1/exploration/77?time_window=RECENT_1_YEAR") {
        return response(
          { error: { code: "NODE_NOT_FOUND", retryable: false } },
          404,
        );
      }
      if (input === "/api/v1/topics/77/exploration?time_window=RECENT_1_YEAR") {
        return response(topicPayload);
      }
      if (input === "/api/v1/exploration/10?time_window=RECENT_1_YEAR") {
        return response(memberExploration);
      }
      if (
        input ===
        "/api/v1/nodes/10/insight-report?time_window=RECENT_1_YEAR&detail=false"
      ) {
        return response(reportPayload);
      }
      if (/\/nodes\/[^/]+\/(relations|questions)/.test(input)) {
        return response({ items: [], next_cursor: null });
      }
      if (input.includes("/peripheral?")) {
        return response({
          graph: { nodes: [], relations: [] },
          next_cursor: null,
        });
      }
      throw new Error(`unexpected request: ${input}`);
    });
    vi.stubGlobal("fetch", (input: string, init?: RequestInit) =>
      fetchMock(input, init),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("keeps the period and trail while entering member Insight and returning to Topic", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "반도체" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "그래프 준비 완료" }));
    expect(
      screen
        .getByRole("button", { name: "최근 1년" })
        .getAttribute("aria-pressed"),
    ).toBe("true");

    fireEvent.click(
      await screen.findByRole("button", { name: /반도체 공급망 변화/ }),
    );
    await screen.findByText("요청 중심: 10");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/exploration/10?time_window=RECENT_1_YEAR",
      expect.anything(),
    );

    fireEvent.click(screen.getByRole("button", { name: "중심 전환 완료" }));
    expect(await screen.findByRole("heading", { name: "회사 A" })).toBeTruthy();
    expect(
      screen
        .getByRole("tab", { name: "인사이트" })
        .getAttribute("aria-selected"),
    ).toBe("true");
    expect(
      screen.getByRole("navigation", { name: "최근 탐색 경로" }).textContent,
    ).toContain("반도체");
    expect(
      screen.getByRole("navigation", { name: "최근 탐색 경로" }).textContent,
    ).toContain("회사 A");

    window.history.replaceState({}, "", "/?center=77&range=1y");
    fireEvent(window, new PopStateEvent("popstate"));
    await screen.findByText("요청 중심: 77");
    fireEvent.click(screen.getByRole("button", { name: "중심 전환 완료" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "반도체" })).toBeTruthy(),
    );
    expect(
      screen
        .getByRole("button", { name: "최근 1년" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });
});
