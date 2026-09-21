import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  type ExplorationView,
  fetchPeripheral,
  type PeripheralPage,
} from "./data";
import { usePeripheral } from "./usePeripheral";

vi.mock("./data", async (original) => ({
  ...(await original<typeof import("./data")>()),
  fetchPeripheral: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const node = (id: string, tier: "center" | "ambient") => ({
  id,
  name: id,
  kind: "Person",
  kindCode: "PERSON",
  tier,
  activityEvidenceGroupCount: 1,
});
const view: ExplorationView = {
  centerId: "a",
  context: "",
  contextIsCurrent: false,
  periodHighlights: [],
  nodes: [node("a", "center")],
  relations: [],
  recommendations: [],
  followups: [],
};
const page: PeripheralPage = {
  nodes: [node("a", "ambient"), node("b", "ambient")],
  relations: [],
  nextCursor: "next",
};

it("첫 페이지를 자동 요청하고 한 요청씩 병합하며 실패한 cursor만 명시적으로 재시도한다", async () => {
  let resolve!: (value: PeripheralPage) => void;
  vi.mocked(fetchPeripheral).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const { result } = renderHook(() => usePeripheral(view, "90d", true));
  expect(fetchPeripheral).toHaveBeenCalledTimes(1);
  act(() => {
    result.current.trigger();
    result.current.trigger();
  });
  expect(fetchPeripheral).toHaveBeenCalledTimes(1);
  await act(async () => resolve(page));
  expect(
    result.current.graphView?.nodes.map(({ id, tier }) => [id, tier]),
  ).toEqual([
    ["a", "center"],
    ["b", "ambient"],
  ]);
  vi.mocked(fetchPeripheral).mockRejectedValueOnce(new Error("offline"));
  await act(async () => result.current.trigger());
  expect(result.current.error).not.toBeNull();
  expect(result.current.graphView?.nodes).toHaveLength(2);
  await act(async () => result.current.trigger());
  expect(fetchPeripheral).toHaveBeenCalledTimes(2);
  vi.mocked(fetchPeripheral).mockResolvedValueOnce({
    ...page,
    nextCursor: null,
  });
  await act(async () => result.current.retry());
  expect(vi.mocked(fetchPeripheral).mock.calls.map((call) => call[2])).toEqual([
    null,
    "next",
    "next",
  ]);
  expect(result.current.graphView?.nodes).toHaveLength(2);
  expect(result.current.retrySuccess).toBe(true);
  expect(result.current.exhausted).toBe(true);
  act(() => result.current.trigger());
  expect(fetchPeripheral).toHaveBeenCalledTimes(3);
});

it("기간 변경은 이전 요청을 취소하고 늦은 응답을 버린 뒤 첫 페이지부터 시작한다", async () => {
  let resolve!: (value: PeripheralPage) => void;
  vi.mocked(fetchPeripheral).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const { result, rerender } = renderHook(
    ({ range }: { range: "90d" | "1y" }) => usePeripheral(view, range, true),
    { initialProps: { range: "90d" } },
  );
  act(() => result.current.trigger());
  const signal = vi.mocked(fetchPeripheral).mock.calls[0]?.[3];
  vi.mocked(fetchPeripheral).mockReturnValueOnce(new Promise(() => {}));
  rerender({ range: "1y" });
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve(page));
  expect(result.current.graphView).toBe(view);
  expect(fetchPeripheral).toHaveBeenLastCalledWith(
    view,
    "1y",
    null,
    expect.any(AbortSignal),
  );
});

it("intro 중 도착한 page는 완료 후 병합한다", async () => {
  vi.mocked(fetchPeripheral).mockResolvedValueOnce(page);
  const { result, rerender } = renderHook(
    ({ present }) => usePeripheral(view, "90d", true, present),
    { initialProps: { present: false } },
  );
  await act(async () => {});
  expect(result.current.graphView).toBe(view);
  rerender({ present: true });
  expect(result.current.graphView?.nodes).toHaveLength(2);
  expect(fetchPeripheral).toHaveBeenCalledTimes(1);
});
