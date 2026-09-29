import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { createRef, useState } from "react";
import { PerspectiveCamera, Vector3 } from "three";
import { afterEach, assert, beforeEach, expect, test, vi } from "vitest";
import { GraphCanvas, type GraphCanvasHandle } from "./GraphCanvas";
import type { GraphNode } from "./types";
import { useInitialLoading } from "./useInitialLoading";

// WebGL 경계만 대체하고 GraphCanvas의 실제 이펙트와 완료 콜백을 실행한다.
const rendererState = vi.hoisted(() => ({
  camera: null as PerspectiveCamera | null,
  cameraPosition: vi.fn(),
}));
vi.mock("3d-force-graph", () => ({
  default: function ForceGraph3D(
    container: HTMLElement,
    options: {
      extraRenderers: { domElement: HTMLElement }[];
    },
  ) {
    const camera = new PerspectiveCamera(50, 800 / 600);
    rendererState.camera = camera;
    // 라이브러리처럼 스타일이 있는 부모 안에 캔버스와 라벨을 함께 배치한다.
    const scene = document.createElement("div");
    scene.style.position = "relative";
    scene.dataset.testid = "graph-scene";
    scene.append(document.createElement("canvas"));
    for (const renderer of options.extraRenderers)
      scene.append(renderer.domElement);
    container.append(scene);
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
          if (key === "cameraPosition") return rendererState.cameraPosition;
          if (key === "_destructor") return () => scene.remove();
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
  rendererState.cameraPosition.mockClear();
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

test("그래프를 숨기지 않고 두 번 확대하며 중간에 멈춘다", () => {
  render(<LoadingGraph nodes={nodes} onReady={vi.fn()} />);
  act(() => vi.advanceTimersByTime(16));
  const scene = screen.getByTestId("graph-scene");
  const labels = scene.querySelector<HTMLElement>('[data-graph-labels="true"]');
  assert(rendererState.camera);
  const camera = rendererState.camera;
  const far = camera.position.z;
  expect(labels?.style.opacity).toBe("0");
  expect(scene.style.opacity).not.toBe("0");
  act(() => vi.advanceTimersByTime(2100));
  act(() => vi.advanceTimersByTime(16));
  expect(camera.position.z).toBeCloseTo(far);
  act(() => vi.advanceTimersByTime(1024));
  const overview = camera.position.z;
  expect(overview).toBeLessThan(far);
  expect(scene.style.opacity).not.toBe("0");
  expect(Number(labels?.style.opacity)).toBeGreaterThan(0);
  act(() => vi.advanceTimersByTime(160));
  expect(camera.position.z).toBe(overview);
  act(() => vi.advanceTimersByTime(416));
  expect(camera.position.z).toBeLessThan(overview);
  expect(labels?.style.opacity).toBe("1");
  act(() => vi.advanceTimersByTime(1000));
  const final = camera.position.z;
  expect(final).toBeCloseTo(152);
  act(() => vi.advanceTimersByTime(1000));
  expect(camera.position.z).toBe(final);
  expect(rendererState.cameraPosition).not.toHaveBeenCalled();
});

test.each(["pointerdown", "wheel"])(
  "%s 조작은 현재 위치에서 시작 연출을 끝낸다",
  (event) => {
    render(<LoadingGraph nodes={nodes} onReady={vi.fn()} />);
    act(() => vi.advanceTimersByTime(16));
    act(() => vi.advanceTimersByTime(2100));
    act(() => vi.advanceTimersByTime(500));
    const scene = screen.getByTestId("graph-scene");
    assert(rendererState.camera);
    const position = rendererState.camera.position.z;
    fireEvent(scene, new Event(event, { bubbles: true }));
    act(() => vi.advanceTimersByTime(4000));
    expect(rendererState.camera.position.z).toBe(position);
    expect(
      scene.querySelector<HTMLElement>('[data-graph-labels="true"]')?.style
        .opacity,
    ).toBe("1");
  },
);

test("모션 감소 설정은 바로 최종 카메라와 라벨을 표시한다", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  render(<LoadingGraph nodes={nodes} onReady={vi.fn()} />);
  act(() => vi.advanceTimersByTime(16));
  expect(screen.queryByLabelText("초기 로딩")).toBeNull();
  expect(rendererState.camera?.position.z).toBeCloseTo(152);
  expect(
    screen
      .getByTestId("graph-scene")
      .querySelector<HTMLElement>('[data-graph-labels="true"]')?.style.opacity,
  ).toBe("1");
  expect(rendererState.cameraPosition).not.toHaveBeenCalled();
});

test("화면 맞춤은 시작 연출을 취소하고 언마운트는 예약 작업을 정리한다", () => {
  const ref = createRef<GraphCanvasHandle>();
  const view = render(
    <GraphCanvas
      ref={ref}
      nodes={nodes}
      edges={[]}
      selectedNodeId={null}
      onNodeClick={vi.fn()}
      introStarted={false}
    />,
  );
  act(() => vi.advanceTimersByTime(16));
  view.rerender(
    <GraphCanvas
      ref={ref}
      nodes={nodes}
      edges={[]}
      selectedNodeId={null}
      onNodeClick={vi.fn()}
      introStarted
    />,
  );
  act(() => vi.advanceTimersByTime(500));
  act(() => ref.current?.fitToView());
  assert(rendererState.camera);
  const position = rendererState.camera.position.z;
  act(() => vi.advanceTimersByTime(4000));
  expect(rendererState.cameraPosition).toHaveBeenCalledTimes(1);
  expect(rendererState.camera.position.z).toBe(position);
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});
