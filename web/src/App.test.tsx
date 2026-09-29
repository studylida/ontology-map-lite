import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import type { GraphNode } from "./types";

const api = vi.hoisted(() => ({
  fetchTopDegreeNode: vi.fn(),
  fetchSubgraph: vi.fn(),
  fetchAgentTasks: vi.fn().mockResolvedValue([]),
  fetchNodeDetails: vi.fn().mockResolvedValue(null),
  searchNodes: vi.fn().mockResolvedValue([]),
}));
vi.mock("./api", () => api);
vi.mock("./GraphCanvas", () => ({
  GraphCanvas: ({
    nodes,
    onReady,
    onNodeClick,
  }: {
    nodes: GraphNode[];
    onReady: () => void;
    onNodeClick: (node: GraphNode) => void;
  }) => {
    useEffect(() => {
      if (nodes.length) onReady();
    }, [nodes, onReady]);
    return (
      <div data-testid="graph">
        {nodes.map((node) => (
          <button type="button" key={node.id} onClick={() => onNodeClick(node)}>
            {node.name}
          </button>
        ))}
      </div>
    );
  },
}));

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  api.fetchTopDegreeNode.mockReset();
  api.fetchSubgraph.mockReset();
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
    window.setTimeout(() => callback(performance.now()), 16),
  );
  vi.stubGlobal("cancelAnimationFrame", (id: number) =>
    window.clearTimeout(id),
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test.each([0, 2000])(
  "연결 실패(%ims)는 같은 로딩 화면에서 안내하고 새로고침으로 재시도한다",
  async (delay) => {
    api.fetchTopDegreeNode.mockImplementation(
      () =>
        new Promise((_, reject) => {
          window.setTimeout(() => reject(new Error("offline")), delay);
        }),
    );
    const reload = vi.fn();
    vi.stubGlobal("location", { reload });
    render(<App />);
    const overlay = screen.getByText("0%").closest("[data-leaving]");
    await act(() => vi.advanceTimersByTimeAsync(1399));
    expect(screen.queryByRole("alert")).toBeNull();
    await act(() =>
      vi.advanceTimersByTimeAsync(Math.max(1400, delay) - 1399 + 1),
    );
    await act(() => vi.advanceTimersByTimeAsync(1));
    const alert = screen.getByRole("alert");
    expect(alert.closest("[data-leaving]")).toBe(overlay);
    expect(alert.textContent).toContain("서버 연결 상태");
    expect(screen.queryByText("99%")).toBeNull();
    expect(api.searchNodes).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(reload).toHaveBeenCalledTimes(1);
  },
);

test("초기 로딩 후 조회 실패는 지도를 유지하고 로딩 연출을 재시작하지 않는다", async () => {
  api.fetchTopDegreeNode.mockResolvedValue({ id: 1 });
  api.fetchSubgraph
    .mockResolvedValueOnce({
      nodes: [{ id: 1, name: "테스트 노드", classification_id: 1 }],
      edges: [],
    })
    .mockRejectedValueOnce(new Error("offline"));
  render(<App />);
  await act(async () => {});
  await act(() => vi.advanceTimersByTimeAsync(2200));
  expect(screen.queryByText(/\d+%/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "테스트 노드" }));
  await act(async () => {});
  expect(screen.getByTestId("graph").textContent).toContain("테스트 노드");
  expect(screen.getByRole("alert").closest("[data-leaving]")).toBeNull();
  expect(screen.queryByRole("button", { name: "다시 시도" })).toBeNull();
  expect(api.searchNodes).not.toHaveBeenCalled();
});

test("빈 그래프 응답도 무한 로딩 대신 실패 안내로 끝난다", async () => {
  api.fetchTopDegreeNode.mockResolvedValue({ id: 1 });
  api.fetchSubgraph.mockResolvedValue({ nodes: [], edges: [] });
  render(<App />);
  await act(async () => {});
  await act(() => vi.advanceTimersByTimeAsync(1600));
  expect(screen.getByRole("alert").textContent).toContain(
    "지식맵을 불러오지 못했습니다",
  );
  expect(screen.queryByText("99%")).toBeNull();
});
