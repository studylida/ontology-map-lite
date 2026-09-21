import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useInitialLoading } from "./useInitialLoading";

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const advance = (ms: number) => act(() => vi.advanceTimersByTime(ms));

it("빠른 준비에서도 ramp를 마친 뒤 90·95·99와 fade를 순서대로 표시한다", () => {
  const { result, rerender } = renderHook(
    ({ ready }) => useInitialLoading(ready, false),
    { initialProps: { ready: false } },
  );
  advance(500);
  expect(result.current.progress).toBeGreaterThan(0);
  rerender({ ready: true });
  advance(899);
  expect(result.current.progress).toBeLessThan(90);
  advance(1);
  expect(result.current.progress).toBe(90);
  advance(120);
  expect(result.current.progress).toBe(95);
  advance(120);
  expect(result.current).toEqual({ progress: 99, phase: "loading" });
  advance(160);
  expect(result.current.phase).toBe("leaving");
  advance(200);
  expect(result.current).toEqual({ progress: 99, phase: "hidden" });
  rerender({ ready: false });
  advance(3000);
  expect(result.current.phase).toBe("hidden");
});

it("느린 준비는 89에서 기다리고 실패·retry에서 loading을 다시 시작하지 않는다", () => {
  const { result, rerender } = renderHook(
    ({ failed }) => useInitialLoading(false, failed),
    { initialProps: { failed: false } },
  );
  advance(4000);
  expect(result.current).toEqual({ progress: 89, phase: "loading" });
  rerender({ failed: true });
  expect(result.current.phase).toBe("hidden");
  rerender({ failed: false });
  advance(2000);
  expect(result.current.phase).toBe("hidden");
  expect(vi.getTimerCount()).toBe(0);
});

it("진행률은 초반에 가속하고 끝에서는 감속한다", () => {
  const { result } = renderHook(() => useInitialLoading(false, false));
  advance(350);
  const first = result.current.progress;
  advance(350);
  const middle = result.current.progress;
  advance(350);
  const last = result.current.progress;
  advance(400);
  expect(middle - first).toBeGreaterThan(first);
  expect(last - middle).toBeGreaterThan(89 - last);
  expect(result.current.progress).toBe(89);
});

it("불규칙한 속도에서도 진행률은 뒤로 가지 않고 준비 전 89를 넘지 않는다", () => {
  vi.spyOn(Math, "random").mockReturnValue(0.99);
  const { result, rerender } = renderHook(() =>
    useInitialLoading(false, false),
  );
  let previous = 0;
  for (let i = 0; i < 100; i++) {
    advance(16);
    expect(result.current.progress).toBeGreaterThanOrEqual(previous);
    expect(result.current.progress).toBeLessThanOrEqual(89);
    previous = result.current.progress;
    rerender();
  }
  expect(previous).toBe(89);
  vi.restoreAllMocks();
});

it("늦은 graph-ready 이후 종료하고 unmount에서 예약된 frame과 timer를 정리한다", () => {
  const { result, rerender, unmount } = renderHook(
    ({ ready }) => useInitialLoading(ready, false),
    { initialProps: { ready: false } },
  );
  advance(3000);
  rerender({ ready: true });
  advance(240);
  expect(result.current.progress).toBe(99);
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});

it("reduced motion은 준비 후 바로 99로 종료한다", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  const { result, rerender } = renderHook(
    ({ ready }) => useInitialLoading(ready, false),
    { initialProps: { ready: false } },
  );
  expect(result.current.progress).toBe(0);
  rerender({ ready: true });
  expect(result.current).toEqual({ progress: 99, phase: "hidden" });
  expect(vi.getTimerCount()).toBe(0);
});
