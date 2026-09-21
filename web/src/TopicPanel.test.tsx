import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeNode } from "./data";
import { TopicPanel } from "./TopicPanel";
import type { TopicExplorationView } from "./topicData";

function node(
  id: string,
  name: string,
  kind: string,
  kindCode: string,
  evidence: number,
): KnowledgeNode {
  return {
    id,
    name,
    kind,
    kindCode,
    tier: "direct",
    activityEvidenceGroupCount: evidence,
  };
}

function topicView(members: KnowledgeNode[]): TopicExplorationView {
  return {
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
      ...members,
    ],
    relations: [],
    recommendations: [],
    followups: [],
    topic: { nodeId: "77", name: "반도체", isActive: true },
    totalPublicMembershipCount: members.length,
  };
}

function emptyReportResponse() {
  return new Response(JSON.stringify({ items: [] }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function reportResponse(title: string) {
  return new Response(
    JSON.stringify({
      items: [
        {
          report_id: "501",
          title,
          summary: "요약",
          as_of_at: "2026-09-15T00:00:00Z",
          evidence_group_count: 3,
          conclusion: null,
          caveat: null,
          sections: [],
        },
      ],
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TopicPanel", () => {
  it("shows only the top three recent-evidence members as rich cards without evidence counts", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => emptyReportResponse());
    vi.stubGlobal("fetch", fetchMock);
    const view = topicView([
      node("1", "가 회사", "회사", "COMPANY", 8),
      node("2", "나 기술", "기술", "TECHNOLOGY", 6),
      node("3", "다 사람", "사람", "PERSON", 4),
      node("4", "라 회사", "회사", "COMPANY", 2),
      node("5", "마 기술", "기술", "TECHNOLOGY", 0),
    ]);

    render(
      <TopicPanel
        view={view}
        timeRange="90d"
        onClose={() => undefined}
        onSelect={() => undefined}
        onLocate={() => undefined}
        onSelectInsight={() => undefined}
      />,
    );

    const richSection = screen.getByRole("heading", {
      name: "최근 90일에 근거가 많은 연결",
    }).parentElement;
    expect(richSection).not.toBeNull();
    const rich = within(richSection as HTMLElement);
    expect(rich.getByText("가 회사")).toBeTruthy();
    expect(rich.getByText("나 기술")).toBeTruthy();
    expect(rich.getByText("다 사람")).toBeTruthy();
    expect(rich.queryByText("라 회사")).toBeNull();
    expect(richSection?.textContent).not.toMatch(/근거\s*\d|\d+개/);

    const recentSection = screen.getByRole("heading", {
      name: "최근 90일에 근거가 있는 연결",
    }).parentElement;
    expect(
      within(recentSection as HTMLElement).getByText("라 회사"),
    ).toBeTruthy();
    expect(
      within(recentSection as HTMLElement).queryByText("가 회사"),
    ).toBeNull();

    const otherSection = screen.getByRole("heading", {
      name: "그 외 연결",
    }).parentElement;
    expect(
      within(otherSection as HTMLElement).getByText("마 기술"),
    ).toBeTruthy();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "다시 조회" })).toBeNull();
  });

  it("distinguishes no membership from no selected-period evidence", () => {
    const { rerender } = render(
      <TopicPanel
        view={topicView([])}
        timeRange="90d"
        onClose={() => undefined}
        onSelect={() => undefined}
        onLocate={() => undefined}
        onSelectInsight={() => undefined}
      />,
    );
    expect(screen.getByText("아직 공개된 연결 대상이 없습니다.")).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(
      screen.queryByText("이 기간에는 공개된 후속 질문이 없습니다."),
    ).toBeNull();
    expect(
      screen.queryByText("이 기간에는 공개된 인사이트가 없습니다."),
    ).toBeNull();

    rerender(
      <TopicPanel
        view={topicView([node("5", "마 기술", "기술", "TECHNOLOGY", 0)])}
        timeRange="1y"
        onClose={() => undefined}
        onSelect={() => undefined}
        onLocate={() => undefined}
        onSelectInsight={() => undefined}
      />,
    );
    expect(
      screen.getByText("최근 1년에 확인된 연결 원문이 없습니다."),
    ).toBeTruthy();
    expect(screen.getByText("마 기술")).toBeTruthy();
  });

  it("uses an available public report title to enter the member Insight tab", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(reportResponse("반도체 공급망 변화"));
    vi.stubGlobal("fetch", fetchMock);
    const onSelectInsight = vi.fn();

    render(
      <TopicPanel
        view={topicView([node("1", "가 회사", "회사", "COMPANY", 8)])}
        timeRange="90d"
        onClose={() => undefined}
        onSelect={() => undefined}
        onLocate={() => undefined}
        onSelectInsight={onSelectInsight}
      />,
    );

    const title = await screen.findByRole("button", {
      name: /반도체 공급망 변화/,
    });
    fireEvent.click(title);
    expect(onSelectInsight).toHaveBeenCalledWith("1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/nodes/1/insight-report?time_window=RECENT_90_DAYS&detail=false",
      expect.any(Object),
    );
  });

  it("keeps report-title read failure distinct from normal empty and retries only that read", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: "PANEL_NOT_READY", retryable: true },
          }),
          { status: 503, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(reportResponse("재조회된 인사이트"));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <TopicPanel
        view={topicView([node("1", "가 회사", "회사", "COMPANY", 8)])}
        timeRange="90d"
        onClose={() => undefined}
        onSelect={() => undefined}
        onLocate={() => undefined}
        onSelectInsight={() => undefined}
      />,
    );

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(
      "현재 이 영역의 공개 자료를 불러올 수 없습니다.",
    );
    expect(alert.textContent).not.toMatch(/준비 중|생성 중|복구 중/);
    fireEvent.click(within(alert).getByRole("button", { name: "다시 조회" }));

    expect(
      await screen.findByRole("button", { name: /재조회된 인사이트/ }),
    ).toBeTruthy();
    expect(
      screen.getByText("최신 공개 상태로 다시 불러왔습니다."),
    ).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/nodes/1/insight-report?time_window=RECENT_90_DAYS&detail=false",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(fetchMock.mock.calls[0]?.[0]);
  });
});
