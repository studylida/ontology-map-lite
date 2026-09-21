import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

vi.mock("./GraphCanvas", () => ({
  GraphCanvas: ({ view }: { view: { centerId: string } }) => (
    <section aria-label="동적 지식맵">{view.centerId}</section>
  ),
}));

const A = "9223372036854775807";
const B = "9223372036854775806";
const reads: string[] = [];

function response(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

function exploration() {
  return {
    center_node_id: B,
    context_text: "HBF 중심 공개 관계",
    context_is_current: false,
    period_highlights: [],
    graph: {
      nodes: [
        {
          node_id: B,
          name: "HBF",
          node_type: { code: "TECHNOLOGY", display_name: "기술" },
          tier: "CENTER",
          activity_evidence_group_count: 6,
        },
        {
          node_id: A,
          name: "SK하이닉스",
          node_type: { code: "COMPANY", display_name: "회사" },
          tier: "DIRECT",
          activity_evidence_group_count: 3,
        },
      ],
      relations: [],
    },
    recommendations: [],
    followup_questions: [],
  };
}

describe("no-center history restoration", () => {
  beforeEach(() => {
    reads.length = 0;
    vi.stubEnv("VITE_DEFAULT_CENTER_NODE_ID", "");
    vi.stubGlobal("fetch", async (input: string) => {
      if (input.startsWith("/api/v1/nodes/search?")) {
        return response({
          items: [
            {
              node_id: B,
              name: "HBF",
              node_type: { code: "TECHNOLOGY", display_name: "기술" },
            },
          ],
        });
      }
      if (input.startsWith(`/api/v1/exploration/${B}?`)) {
        reads.push(input);
        return response(exploration());
      }
      if (input.includes("/peripheral?")) {
        return response({
          graph: { nodes: [], relations: [] },
          next_cursor: null,
        });
      }
      if (/\/api\/v1\/nodes\/[^/]+\/(questions|relations|claims)/.test(input)) {
        return response({ items: [], next_cursor: null });
      }
      if (input.includes("/insight-report?")) return response({ items: [] });
      if (input === "/api/v1/topics") return response({ items: [] });
      return response({ items: [], next_cursor: null });
    });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ matches: true }),
    });
    window.history.replaceState({}, "", "/?range=1y");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/");
  });

  it("restores start and reloads Forward target without history writes", async () => {
    render(<App />);
    expect(
      (
        await screen.findAllByText(
          "탐색할 대상을 검색하거나 주제를 선택해 주세요.",
        )
      ).length,
    ).toBeGreaterThan(0);
    expect(reads).toHaveLength(0);

    fireEvent.change(screen.getByRole("combobox", { name: "대상 검색" }), {
      target: { value: "HBF" },
    });
    fireEvent.click(await screen.findByRole("option", { name: /HBF/ }));
    expect(await screen.findByRole("heading", { name: "HBF" })).toBeTruthy();
    expect(reads).toEqual([
      `/api/v1/exploration/${B}?time_window=RECENT_1_YEAR`,
    ]);

    const pushSpy = vi.spyOn(window.history, "pushState");
    const replaceSpy = vi.spyOn(window.history, "replaceState");
    window.history.replaceState({}, "", "/?range=1y");
    replaceSpy.mockClear();
    await act(async () => window.dispatchEvent(new PopStateEvent("popstate")));

    expect(window.location.search).toBe("?range=1y");
    expect(screen.queryByRole("heading", { name: "HBF" })).toBeNull();
    expect(screen.queryByRole("region", { name: "동적 지식맵" })).toBeNull();
    expect(
      within(
        screen.getByRole("navigation", { name: "최근 탐색 경로" }),
      ).queryByText("HBF"),
    ).toBeNull();
    expect(
      screen.getAllByText("탐색할 대상을 검색하거나 주제를 선택해 주세요.")
        .length,
    ).toBeGreaterThan(0);
    expect(reads).toHaveLength(1);
    expect(pushSpy).not.toHaveBeenCalled();
    expect(replaceSpy).not.toHaveBeenCalled();

    window.history.replaceState({}, "", `/?center=${B}&range=1y`);
    replaceSpy.mockClear();
    await act(async () => window.dispatchEvent(new PopStateEvent("popstate")));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "HBF" })).toBeTruthy(),
    );
    expect(reads).toEqual([
      `/api/v1/exploration/${B}?time_window=RECENT_1_YEAR`,
      `/api/v1/exploration/${B}?time_window=RECENT_1_YEAR`,
    ]);
    expect(pushSpy).not.toHaveBeenCalled();
    expect(replaceSpy).not.toHaveBeenCalled();
  });
});
