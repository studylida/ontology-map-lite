import { act, cleanup, render, screen } from "@testing-library/react";
import { useState } from "react";
import { PerspectiveCamera, Vector3 } from "three";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { GraphCanvas } from "./GraphCanvas";
import type { GraphNode } from "./types";
import { useInitialLoading } from "./useInitialLoading";

// WebGL 경계만 대체하고 GraphCanvas의 실제 이펙트와 완료 콜백을 실행한다.
vi.mock("3d-force-graph", () => ({
  default: function ForceGraph3D() {
    const camera = new PerspectiveCamera(50, 800 / 600);
    const controls = {
      target: new Vector3(),
      mouseButtons: {},
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      update: vi.fn(),
    };
    const renderer = {};
    const graph: object = new Proxy(
      {},
      {
        get: (_, key) => {
          if (key === "camera") return () => camera;
          if (key === "controls") return () => controls;
          if (key === "renderer") return () => renderer;
          if (key === "width" || key === "height") {
            return (value?: number) =>
              value === undefined ? (key === "width" ? 800 : 600) : graph;
          }
          return () => graph;
        },
      },
    );
    return graph;
  },
}));

const nodes: GraphNode[] = [
  { id: 1, name: "중심", classification_id: 1, tier: "CENTER" },
];

function LoadingGraph({
  nodes,
  onReady,
}: {
  nodes: GraphNode[];
  onReady: () => void;
}) {
  const [ready, setReady] = useState(false);
  const { progress, phase } = useInitialLoading(ready, false);
  return (
    <>
      <GraphCanvas
        nodes={nodes}
        edges={[]}
        centerNodeId={1}
        selectedNodeId={null}
        onNodeClick={() => {}}
        onReady={() => {
          onReady();
          setReady(true);
        }}
        introStarted={phase === "hidden"}
      />
      {phase !== "hidden" && (
        <output aria-label="초기 로딩">{progress}%</output>
      )}
    </>
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
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

test.each([false, true])(
  "데이터 준비 후 로딩을 한 번 종료한다 (지연 도착: %s)",
  (delayed) => {
    const onReady = vi.fn();
    const view = render(
      <LoadingGraph nodes={delayed ? [] : nodes} onReady={onReady} />,
    );
    expect(onReady).not.toHaveBeenCalled();

    if (delayed) {
      act(() => vi.advanceTimersByTime(1600));
      expect(screen.getByLabelText("초기 로딩").textContent).toBe("89%");
      expect(onReady).not.toHaveBeenCalled();
      view.rerender(<LoadingGraph nodes={nodes} onReady={onReady} />);
    }

    act(() => vi.advanceTimersByTime(16));
    expect(onReady).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(2100));
    expect(screen.queryByLabelText("초기 로딩")).toBeNull();

    view.rerender(<LoadingGraph nodes={[...nodes]} onReady={onReady} />);
    act(() => vi.advanceTimersByTime(3000));
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("초기 로딩")).toBeNull();
  },
);
