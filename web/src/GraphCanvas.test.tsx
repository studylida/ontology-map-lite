import { act, cleanup, render } from "@testing-library/react";
import * as THREE from "three";
import type { CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ExplorationView, KnowledgeRelation } from "./data";
import { GraphCanvas } from "./GraphCanvas";

const harness = vi.hoisted(() => ({
  camera: null as THREE.PerspectiveCamera | null,
  target: null as THREE.Vector3 | null,
  navigationEnabled: true,
  labels: null as CSS2DRenderer | null,
  scene: null as THREE.Scene | null,
  options: new Map<string, (...args: never[]) => unknown>(),
  nodes: new Map<string, THREE.Group>(),
  links: new Map<string, THREE.Group>(),
  controlListeners: new Map<string, Set<() => void>>(),
  cameraMoves: [] as { duration: number; position: THREE.Vector3 }[],
}));

// WebGL 경계만 대체하고 실제 GraphCanvas의 effect·frame·DOM 수명주기를 실행한다.
vi.mock("3d-force-graph", () => ({
  default: function FakeGraph(
    container: HTMLElement,
    config: { extraRenderers: CSS2DRenderer[] },
  ) {
    harness.labels = config.extraRenderers[0] ?? null;
    const camera = new THREE.PerspectiveCamera();
    const scene = new THREE.Scene();
    harness.scene = scene;
    const controls = {
      object: camera,
      target: new THREE.Vector3(),
      mouseButtons: {},
      touches: {},
      addEventListener: vi.fn((name: string, listener: () => void) => {
        const listeners = harness.controlListeners.get(name) ?? new Set();
        listeners.add(listener);
        harness.controlListeners.set(name, listeners);
      }),
      removeEventListener: vi.fn((name: string, listener: () => void) => {
        harness.controlListeners.get(name)?.delete(listener);
      }),
      update: vi.fn(),
    };
    harness.camera = camera;
    harness.target = controls.target;
    let nodeFactory: (node: { id: string }) => THREE.Group;
    let linkFactory: (link: KnowledgeRelation) => THREE.Group;
    const dimensions = { width: 800, height: 600 };
    const graph: Record<string, unknown> = {};
    const proxy = new Proxy(graph, {
      get: (_, key: string) => {
        if (key === "controls") return () => controls;
        if (key === "camera") return () => camera;
        if (key === "scene") return () => scene;
        if (key === "cameraPosition")
          return (
            position: THREE.Vector3,
            target: THREE.Vector3,
            duration = 0,
          ) => {
            camera.position.copy(position);
            controls.target.copy(target);
            harness.cameraMoves.push({
              duration,
              position: camera.position.clone(),
            });
            return proxy;
          };
        if (key === "enableNavigationControls")
          return (enabled: boolean) => {
            harness.navigationEnabled = enabled;
            return proxy;
          };
        if (key === "renderer") return () => ({});
        if (key === "postProcessingComposer")
          return () => ({ addPass: vi.fn() });
        if (key === "width" || key === "height")
          return (value?: number) =>
            value === undefined ? dimensions[key] : proxy;
        if (key === "nodeThreeObject")
          return (factory: typeof nodeFactory) => {
            nodeFactory = factory;
            return proxy;
          };
        if (key === "linkThreeObject")
          return (factory: typeof linkFactory) => {
            linkFactory = factory;
            harness.options.set(key, factory);
            return proxy;
          };
        if (key === "graphData")
          return (data: {
            nodes: { id: string }[];
            links: KnowledgeRelation[];
          }) => {
            // 실제 renderer처럼 data 교체 후 이전 객체가 늦게 제거되는 경우도 실행한다.
            for (const [id, visual] of harness.nodes) {
              if (!data.nodes.some((node) => node.id === id)) {
                container.append(visual.userData.label.element);
                window.setTimeout(() => {
                  scene.remove(visual);
                  harness.nodes.delete(id);
                }, 0);
              }
            }
            for (const node of data.nodes) {
              const visual = nodeFactory(node);
              if (visual.parent !== scene) scene.add(visual);
              harness.nodes.set(node.id, visual);
              container.append(visual.userData.label.element);
            }
            for (const link of data.links) {
              const visual = linkFactory(link);
              // 설치된 three-forcegraph가 custom link에 지정하는 group 순서다.
              visual.renderOrder = 10;
              harness.links.set(link.id, visual);
            }
            return proxy;
          };
        return (...args: never[]) => {
          if (typeof args[0] === "function") harness.options.set(key, args[0]);
          return proxy;
        };
      },
    });
    return proxy;
  },
}));

beforeEach(() => {
  harness.options.clear();
  harness.nodes.clear();
  harness.links.clear();
  harness.controlListeners.clear();
  harness.cameraMoves.length = 0;
  vi.useFakeTimers();
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
    window.setTimeout(() => callback(performance.now()), 16),
  );
  vi.stubGlobal("cancelAnimationFrame", (id: number) =>
    window.clearTimeout(id),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const node = (id: string, tier: "center" | "direct" | "ambient") => ({
  id,
  name: id,
  kind: "기술",
  kindCode: "TECHNOLOGY",
  tier,
  activityEvidenceGroupCount: 1,
});
const view: ExplorationView = {
  centerId: "1",
  context: "",
  contextIsCurrent: false,
  periodHighlights: [],
  nodes: [node("1", "center"), node("2", "direct")],
  relations: [],
  recommendations: [],
  followups: [],
};
const props = () => ({
  view,
  introStarted: false,
  pendingNodeId: null,
  panelOpen: true,
  onReady: vi.fn(),
  onIntroComplete: vi.fn(),
  onPanBoundary: vi.fn(),
  onSelect: vi.fn(),
  onEvidence: vi.fn(),
  onTransitionComplete: vi.fn(),
});

it("첫 frame 이전의 초기 page 병합에도 준비 신호를 전달하고 label을 중복하지 않는다", () => {
  const callbacks = props();
  const page = { ...view, nodes: [...view.nodes, node("3", "ambient")] };
  const { rerender, container, unmount } = render(
    <GraphCanvas {...callbacks} />,
  );
  rerender(<GraphCanvas {...callbacks} view={page} />);
  act(() => vi.advanceTimersByTime(16));
  expect(callbacks.onReady).toHaveBeenCalledTimes(1);
  expect(container.querySelectorAll("[data-node-id]")).toHaveLength(3);
  rerender(<GraphCanvas {...callbacks} view={page} introStarted />);
  act(() => vi.advanceTimersByTime(2000));
  expect(callbacks.onIntroComplete).toHaveBeenCalledTimes(1);
  const label = container.querySelector("[data-node-id='3']");
  rerender(<GraphCanvas {...callbacks} introStarted />);
  act(() => vi.advanceTimersByTime(16));
  expect(label?.isConnected).toBe(false);
  expect(container.querySelectorAll("[data-node-id]")).toHaveLength(2);
  unmount();
  expect(document.querySelectorAll("[data-node-id]")).toHaveLength(0);
});

it("진행 중 전환을 취소하고 원래 중심으로 돌아오면 busy 상태도 해제한다", () => {
  const callbacks = props();
  const { rerender, container } = render(<GraphCanvas {...callbacks} />);
  act(() => vi.advanceTimersByTime(16));
  rerender(<GraphCanvas {...callbacks} introStarted />);
  act(() => vi.advanceTimersByTime(2000));
  const next = {
    ...view,
    centerId: "2",
    nodes: [node("2", "center"), node("1", "direct")],
  };
  rerender(<GraphCanvas {...callbacks} view={next} introStarted />);
  act(() => vi.advanceTimersByTime(160));
  expect(container.querySelector("section")?.getAttribute("aria-busy")).toBe(
    "true",
  );
  rerender(<GraphCanvas {...callbacks} introStarted />);
  expect(container.querySelector("section")?.getAttribute("aria-busy")).toBe(
    "false",
  );
  expect(callbacks.onTransitionComplete).not.toHaveBeenCalled();
});

it("노드 장식이 간선 선택을 가로채지 않고 이동한 선을 클릭하면 관계 정보를 전달한다", () => {
  const callbacks = props();
  const relation: KnowledgeRelation = {
    id: "edge",
    source: "1",
    target: "2",
    label: "관련 기술",
    tier: "direct",
    directionality: "DIRECTED",
    evidenceGroupCount: 3,
    conflict: false,
  };
  render(
    <GraphCanvas {...callbacks} view={{ ...view, relations: [relation] }} />,
  );
  const nodeVisual = harness.nodes.get("1");
  const linkVisual = harness.links.get("edge");
  if (!nodeVisual || !linkVisual) throw new Error("graph 객체가 없습니다.");
  expect(nodeVisual.renderOrder).toBeGreaterThan(linkVisual.renderOrder);
  nodeVisual.updateMatrixWorld(true);
  const raycaster = new THREE.Raycaster(
    new THREE.Vector3(4, 0, 100),
    new THREE.Vector3(0, 0, -1),
  );
  raycaster.camera = new THREE.PerspectiveCamera();
  expect(raycaster.intersectObject(nodeVisual, true)).toHaveLength(0);
  const update = harness.options.get("linkPositionUpdate") as unknown as (
    object: THREE.Group,
    coordinates: {
      start: { x: number; y: number; z: number };
      end: { x: number; y: number; z: number };
    },
  ) => void;
  update(linkVisual, {
    start: { x: 0, y: 0, z: 0 },
    end: { x: 40, y: 0, z: 0 },
  });
  raycaster.set(new THREE.Vector3(20, 2, 100), new THREE.Vector3(0, 0, -1));
  linkVisual.updateMatrixWorld(true);
  expect(raycaster.intersectObject(linkVisual, true).length).toBeGreaterThan(0);
  update(linkVisual, {
    start: { x: 200, y: 0, z: 0 },
    end: { x: 240, y: 0, z: 0 },
  });
  raycaster.set(new THREE.Vector3(220, 2, 100), new THREE.Vector3(0, 0, -1));
  expect(raycaster.intersectObject(linkVisual, true).length).toBeGreaterThan(0);
  const click = harness.options.get("onLinkClick") as unknown as (
    link: KnowledgeRelation,
  ) => void;
  act(() => click(relation));
  expect(callbacks.onEvidence).toHaveBeenCalledWith({
    id: "edge",
    label: expect.stringContaining("관련 기술"),
  });
  expect(callbacks.onSelect).not.toHaveBeenCalled();
});

it("요청한 관계만 잠시 강조한 뒤 camera 이동 없이 원래 상태로 돌아간다", () => {
  const callbacks = props();
  const relation: KnowledgeRelation = {
    id: "edge",
    source: "1",
    target: "2",
    label: "관련 기술",
    tier: "direct",
    directionality: "DIRECTED",
    evidenceGroupCount: 3,
    conflict: false,
  };
  const data = { ...view, relations: [relation] };
  const { rerender, queryByRole } = render(
    <GraphCanvas {...callbacks} view={data} />,
  );
  const camera = harness.camera?.position.clone();
  const target = harness.target?.clone();

  rerender(
    <GraphCanvas
      {...callbacks}
      view={data}
      focusRequest={{ key: 1, nodeIds: ["1", "2"], relationIds: ["edge"] }}
    />,
  );
  expect(queryByRole("status")?.textContent).toContain("관련 기술");
  act(() => vi.advanceTimersByTime(2499));
  expect(queryByRole("status")).toBeTruthy();
  act(() => vi.advanceTimersByTime(1));
  expect(queryByRole("status")).toBeNull();
  expect(harness.camera?.position).toEqual(camera);
  expect(harness.target).toEqual(target);
  expect(callbacks.onSelect).not.toHaveBeenCalled();
});

it("전체 지도는 현재 노드를 맞춰 보여준 뒤 복귀하며 직접 조작하면 복귀를 멈춘다", () => {
  const callbacks = props();
  const onOverviewActiveChange = vi.fn();
  const overviewRequest = { key: 1, action: "show" } as const;
  const { rerender } = render(
    <GraphCanvas
      {...callbacks}
      designPreview
      onOverviewActiveChange={onOverviewActiveChange}
    />,
  );
  const before = harness.camera?.position.clone();
  rerender(
    <GraphCanvas
      {...callbacks}
      designPreview
      overviewRequest={overviewRequest}
      onOverviewActiveChange={onOverviewActiveChange}
    />,
  );
  expect(onOverviewActiveChange).toHaveBeenLastCalledWith(true);
  expect(harness.cameraMoves.at(-1)?.duration).toBe(1000);

  act(() => vi.advanceTimersByTime(5000));
  expect(harness.cameraMoves.at(-1)?.duration).toBe(1000);
  act(() => vi.advanceTimersByTime(1000));
  expect(onOverviewActiveChange).toHaveBeenLastCalledWith(false);
  expect(harness.camera?.position).toEqual(before);
  const moveCount = harness.cameraMoves.length;
  rerender(
    <GraphCanvas
      {...callbacks}
      designPreview
      panelOpen={false}
      overviewRequest={overviewRequest}
      onOverviewActiveChange={onOverviewActiveChange}
    />,
  );
  expect(harness.cameraMoves).toHaveLength(moveCount);

  rerender(
    <GraphCanvas
      {...callbacks}
      designPreview
      overviewRequest={{ key: 2, action: "show" }}
      onOverviewActiveChange={onOverviewActiveChange}
    />,
  );
  for (const listener of harness.controlListeners.get("start") ?? [])
    listener();
  act(() => vi.advanceTimersByTime(6000));
  expect(onOverviewActiveChange).toHaveBeenLastCalledWith(true);
});

function visual(id: string) {
  const value = harness.nodes.get(id);
  if (!value) throw new Error(`node ${id}가 없습니다.`);
  return value;
}

it("기간 변경은 위치와 camera를 유지하며 320ms 보간하고 page 도착과 재선택에도 이전 목표로 돌아가지 않는다", () => {
  const callbacks = props();
  const { rerender } = render(<GraphCanvas {...callbacks} />);
  act(() => vi.advanceTimersByTime(16));
  rerender(<GraphCanvas {...callbacks} introStarted />);
  act(() => vi.advanceTimersByTime(2000));
  const before = visual("1").userData.radius;
  const position = visual("1").position.clone();
  const camera = harness.camera?.position.clone();
  const target = harness.target?.clone();
  const larger = {
    ...view,
    nodes: view.nodes.map((n) => ({ ...n, activityEvidenceGroupCount: 6 })),
  };
  rerender(<GraphCanvas {...callbacks} introStarted view={larger} />);
  expect(visual("1").userData.radius).toBe(before);
  act(() => vi.advanceTimersByTime(160));
  const halfway = visual("1").userData.radius;
  expect(halfway).toBeGreaterThan(before);
  rerender(
    <GraphCanvas
      {...callbacks}
      introStarted
      view={{ ...larger, nodes: [...larger.nodes, node("3", "ambient")] }}
    />,
  );
  act(() => vi.advanceTimersByTime(160));
  const end = visual("1").userData.radius;
  expect(end).toBeGreaterThan(halfway);
  act(() => vi.advanceTimersByTime(400));
  expect(visual("1").userData.radius).toBe(end);
  expect(visual("1").position).toEqual(position);
  expect(harness.camera?.position).toEqual(camera);
  expect(harness.target).toEqual(target);
  rerender(<GraphCanvas {...callbacks} introStarted />);
  act(() => vi.advanceTimersByTime(160));
  const interrupted = visual("1").userData.radius;
  rerender(<GraphCanvas {...callbacks} introStarted view={larger} />);
  expect(visual("1").userData.radius).toBe(interrupted);
  act(() => vi.advanceTimersByTime(1000));
  expect(visual("1").userData.radius).toBe(end);
  expect(callbacks.onTransitionComplete).not.toHaveBeenCalled();
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  rerender(<GraphCanvas {...callbacks} introStarted />);
  expect(visual("1").userData.radius).toBe(before);
});

it("응답 대기 중 선택 node는 고정하고 주변만 떠 움직이다 현재 화면 위치에서 전환한다", () => {
  const callbacks = props();
  const { rerender } = render(<GraphCanvas {...callbacks} />);
  act(() => vi.advanceTimersByTime(16));
  rerender(<GraphCanvas {...callbacks} introStarted />);
  act(() => vi.advanceTimersByTime(2000));
  const selected = visual("2").position.clone();
  const neighbor = visual("1").position.clone();
  const camera = harness.camera?.position.clone();
  rerender(<GraphCanvas {...callbacks} introStarted pendingNodeId="2" />);
  act(() => vi.advanceTimersByTime(400));
  expect(visual("2").position).toEqual(selected);
  expect(visual("1").position).not.toEqual(neighbor);
  expect(harness.camera?.position).toEqual(camera);
  const floating = visual("1").position.clone();
  const next = {
    ...view,
    centerId: "2",
    nodes: [node("2", "center"), node("1", "direct")],
  };
  rerender(<GraphCanvas {...callbacks} introStarted view={next} />);
  expect(visual("1").position).toEqual(floating);
  expect(visual("2").position).toEqual(selected);
  act(() => vi.advanceTimersByTime(1300));
  const settled = visual("1").position.clone();
  act(() => vi.advanceTimersByTime(1000));
  expect(visual("1").position).toEqual(settled);
  expect(callbacks.onTransitionComplete).toHaveBeenCalledExactlyOnceWith("2");
});

it("초기 연출 중 새 중심 응답이 도착해도 intro 종료 후 전환 completion을 잃지 않는다", () => {
  const callbacks = props();
  const { rerender } = render(<GraphCanvas {...callbacks} />);
  act(() => vi.advanceTimersByTime(16));
  rerender(<GraphCanvas {...callbacks} introStarted />);
  act(() => vi.advanceTimersByTime(800));
  const next = {
    ...view,
    centerId: "2",
    nodes: [node("2", "center"), node("1", "direct")],
  };
  rerender(
    <GraphCanvas {...callbacks} introStarted view={next} pendingNodeId="2" />,
  );
  act(() => vi.advanceTimersByTime(2200));
  expect(callbacks.onIntroComplete).toHaveBeenCalledTimes(1);
  expect(callbacks.onTransitionComplete).toHaveBeenCalledExactlyOnceWith("2");
  rerender(
    <GraphCanvas {...callbacks} introStarted introCompleted view={next} />,
  );
  expect(callbacks.onTransitionComplete).toHaveBeenCalledTimes(1);
});

it("대기 중 재선택·오류 종료는 떠 움직임을 정리하고 reduced motion에서는 움직이지 않는다", () => {
  const callbacks = props();
  const { rerender, container } = render(<GraphCanvas {...callbacks} />);
  act(() => vi.advanceTimersByTime(16));
  rerender(<GraphCanvas {...callbacks} introStarted />);
  act(() => vi.advanceTimersByTime(2000));
  rerender(<GraphCanvas {...callbacks} introStarted pendingNodeId="2" />);
  act(() => vi.advanceTimersByTime(300));
  const selected = visual("1").position.clone();
  rerender(<GraphCanvas {...callbacks} introStarted pendingNodeId="1" />);
  act(() => vi.advanceTimersByTime(200));
  expect(visual("1").position).toEqual(selected);
  rerender(<GraphCanvas {...callbacks} introStarted />);
  const stopped = visual("2").position.clone();
  act(() => vi.advanceTimersByTime(1000));
  expect(visual("2").position).toEqual(stopped);
  expect(container.querySelector("section")?.getAttribute("aria-busy")).toBe(
    "false",
  );
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  rerender(<GraphCanvas {...callbacks} introStarted pendingNodeId="1" />);
  act(() => vi.advanceTimersByTime(1000));
  expect(visual("2").position).toEqual(stopped);
  expect(callbacks.onTransitionComplete).not.toHaveBeenCalled();
});

it("미리보기는 중심·초점 연결을 파란색으로, 충돌을 우선 빨간 점선으로 표시하고 발광을 숨긴다", () => {
  const callbacks = props();
  const direct = {
    id: "direct",
    source: "1",
    target: "2",
    label: "연결",
    directionality: "DIRECTED" as const,
    evidenceGroupCount: 3,
    conflict: false,
    tier: "direct" as const,
  };
  const remote = {
    ...direct,
    id: "remote",
    source: "2",
    target: "3",
    tier: "threeHop" as const,
  };
  const conflict = { ...remote, id: "conflict", conflict: true };
  const data = {
    ...view,
    nodes: [...view.nodes, node("3", "ambient")],
    relations: [direct, remote, conflict],
  };
  const { rerender, getByRole } = render(
    <GraphCanvas {...callbacks} view={data} designPreview />,
  );
  act(() => vi.advanceTimersByTime(16));
  rerender(
    <GraphCanvas {...callbacks} view={data} designPreview introStarted />,
  );
  act(() => vi.advanceTimersByTime(4000));
  const color = (id: string) =>
    harness.links.get(id)?.userData.lines[0].material.color.getHexString();
  expect(color("direct")).toBe("72a7ff");
  expect(color("remote")).toBe("829bb5");
  expect(color("conflict")).toBe("f26d78");
  expect(harness.links.get("direct")?.userData.lines).toHaveLength(3);
  expect(
    harness.links.get("conflict")?.userData.lines[0].material,
  ).toBeInstanceOf(THREE.LineDashedMaterial);
  expect(visual("1").userData.halo.visible).toBe(false);
  expect(visual("1").userData.core.visible).toBe(false);
  expect(visual("1").userData.shell.visible).toBe(false);
  const isVisible = () => harness.options.get("linkVisibility");
  expect(isVisible()?.(remote as never)).toBe(false);
  act(() => getByRole("button", { name: "3 · 기술" }).focus());
  expect(isVisible()?.(remote as never)).toBe(true);
  expect(isVisible()?.(conflict as never)).toBe(true);
  expect(color("remote")).toBe("829bb5");
  act(() => vi.advanceTimersByTime(450));
  const recreate = harness.options.get("linkThreeObject");
  if (!recreate) throw new Error("간선 생성기가 없습니다.");
  const revealed = recreate(remote as never) as THREE.Group;
  const revealedColor = () =>
    (
      (revealed.children[0] as THREE.Line).material as THREE.LineBasicMaterial
    ).color.getHexString();
  expect(revealedColor()).not.toBe("72a7ff");
  expect(color("remote")).toBe(revealedColor());
  expect(color("conflict")).toBe("f26d78");
  act(() => getByRole("button", { name: "3 · 기술" }).blur());
  act(() => vi.advanceTimersByTime(450));
  expect(isVisible()?.(remote as never)).toBe(false);
  expect(revealedColor()).toBe("829bb5");
  expect(color("direct")).toBe("72a7ff");
});

it("미리보기 준비 이동은 2단위 이내에서 멈추고 응답 순간 위치·속도를 이어받으며 오류 때 복귀한다", () => {
  const callbacks = props();
  const { rerender } = render(<GraphCanvas {...callbacks} designPreview />);
  act(() => vi.advanceTimersByTime(16));
  rerender(<GraphCanvas {...callbacks} designPreview introStarted />);
  act(() => vi.advanceTimersByTime(4000));
  const selected = visual("2").position.clone();
  const origin = visual("1").position.clone();
  const camera = harness.camera?.position.clone();
  rerender(
    <GraphCanvas {...callbacks} designPreview introStarted pendingNodeId="2" />,
  );
  act(() => vi.advanceTimersByTime(200));
  const position = visual("1").position.clone();
  const velocity = visual("1").userData.velocity.clone();
  expect(position.distanceTo(origin)).toBeGreaterThan(0);
  expect(position.distanceTo(origin)).toBeLessThanOrEqual(2);
  const next = {
    ...view,
    centerId: "2",
    nodes: [node("2", "center"), node("1", "direct")],
  };
  rerender(
    <GraphCanvas {...callbacks} designPreview introStarted view={next} />,
  );
  expect(visual("1").position).toEqual(position);
  expect(visual("1").userData.velocity).toEqual(velocity);
  expect(visual("2").position).toEqual(selected);
  expect(harness.camera?.position).toEqual(camera);
  act(() => vi.advanceTimersByTime(1300));
  const settled = visual("2").position.clone();
  rerender(
    <GraphCanvas
      {...callbacks}
      designPreview
      introStarted
      view={next}
      pendingNodeId="1"
    />,
  );
  act(() => vi.advanceTimersByTime(400));
  const waiting = visual("2").position.clone();
  expect(waiting.distanceTo(settled)).toBeCloseTo(2);
  act(() => vi.advanceTimersByTime(3000));
  expect(visual("2").position).toEqual(waiting);
  expect(visual("2").userData.velocity.length()).toBe(0);
  rerender(
    <GraphCanvas {...callbacks} designPreview introStarted view={next} />,
  );
  expect(visual("2").position).toEqual(waiting);
  act(() => vi.advanceTimersByTime(400));
  expect(visual("2").position).toEqual(settled);
  expect(callbacks.onTransitionComplete).toHaveBeenCalledTimes(1);
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  rerender(
    <GraphCanvas
      {...callbacks}
      designPreview
      introStarted
      view={next}
      pendingNodeId="1"
    />,
  );
  act(() => vi.advanceTimersByTime(1000));
  expect(visual("2").position).toEqual(settled);
  rerender(
    <GraphCanvas {...callbacks} designPreview introStarted view={next} />,
  );
  expect(visual("2").position).toEqual(settled);
});

it("로딩이 이미 끝난 상태에서 지도가 다시 생성되어도 초기 연출과 조작 복구를 완료한다", () => {
  const callbacks = props();
  const { container } = render(
    <GraphCanvas {...callbacks} designPreview introStarted />,
  );
  const { camera, target } = harness;
  if (!camera || !target) throw new Error("camera가 없습니다.");
  const initialDistance = camera.position.distanceTo(target);
  expect(harness.navigationEnabled).toBe(false);
  act(() => vi.advanceTimersByTime(1500));
  const overviewDistance = camera.position.distanceTo(target);
  expect(overviewDistance).toBeLessThan(initialDistance / 50);
  act(() => vi.advanceTimersByTime(160));
  expect(camera.position.distanceTo(target)).toBe(overviewDistance);
  expect(callbacks.onIntroComplete).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(600));
  expect(camera.position.distanceTo(target)).toBeLessThan(overviewDistance);
  act(() => vi.advanceTimersByTime(1740));
  expect(callbacks.onReady).toHaveBeenCalledTimes(1);
  expect(callbacks.onIntroComplete).toHaveBeenCalledTimes(1);
  expect(harness.navigationEnabled).toBe(true);
  expect(container.querySelector("section")?.getAttribute("aria-busy")).toBe(
    "false",
  );
  expect(initialDistance).toBeGreaterThan(
    90 * camera.position.distanceTo(target),
  );
});

it("초기 확대에서는 근접 경계에 도달하기 전부터 주변 node를 천천히 숨긴다", () => {
  render(
    <GraphCanvas
      {...props()}
      view={{
        ...view,
        nodes: [...view.nodes, { ...node("3", "ambient"), tier: "twoHop" }],
      }}
      designPreview
      introStarted
    />,
  );
  act(() => vi.advanceTimersByTime(2300));
  expect(visual("3").userData.surface.material.opacity).toBeGreaterThan(0);
  expect(visual("3").userData.surface.material.opacity).toBeLessThan(
    visual("3").userData.style.opacity,
  );
  const { camera, labels, scene } = harness;
  if (!camera || !labels || !scene) throw new Error("graph가 없습니다.");
  labels.render(scene, camera);
  const second = visual("3");
  expect(second.userData.surface.material.opacity).toBeGreaterThan(0);
  expect(second.userData.surface.material.opacity).toBeLessThan(
    second.userData.style.opacity,
  );
  act(() => vi.advanceTimersByTime(1700));
  labels.render(scene, camera);
  expect(second.visible).toBe(false);
  expect(harness.navigationEnabled).toBe(true);
});

it("초기 연출을 이미 마친 지도는 재생성 시 확대를 반복하지 않고 조작을 복구한다", () => {
  const callbacks = props();
  render(
    <GraphCanvas {...callbacks} designPreview introStarted introCompleted />,
  );
  const camera = harness.camera;
  if (!camera) throw new Error("camera가 없습니다.");
  const initialPosition = camera.position.clone();
  act(() => vi.advanceTimersByTime(16));
  expect(harness.navigationEnabled).toBe(true);
  expect(camera.position.distanceTo(initialPosition)).toBeLessThan(0.001);
  expect(callbacks.onReady).toHaveBeenCalledTimes(1);
  expect(callbacks.onIntroComplete).not.toHaveBeenCalled();
});

it("중심 재배치는 사용자의 배율과 전환 도중 바꾼 배율을 유지한다", () => {
  const callbacks = props();
  const { rerender } = render(
    <GraphCanvas {...callbacks} designPreview introStarted introCompleted />,
  );
  act(() => vi.advanceTimersByTime(16));
  const { camera, target } = harness;
  if (!camera || !target) throw new Error("camera가 없습니다.");
  camera.position.copy(target).add(new THREE.Vector3(0, 0, 450));
  const next = {
    ...view,
    centerId: "2",
    nodes: [node("2", "center"), node("1", "direct")],
  };
  rerender(
    <GraphCanvas
      {...callbacks}
      view={next}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(400));
  expect(camera.position.distanceTo(target)).toBeCloseTo(450);
  camera.position.copy(target).add(new THREE.Vector3(0, 0, 290));
  act(() => vi.advanceTimersByTime(1400));
  expect(camera.position.distanceTo(target)).toBeCloseTo(290);
  expect(callbacks.onTransitionComplete).toHaveBeenCalledWith("2");
  expect(harness.navigationEnabled).toBe(true);
});

it("근접 조망에서 숨긴 2단계는 축소하면 같은 좌표로 나타나고 숨은 node는 클릭을 가로채지 않는다", () => {
  const callbacks = props();
  const page = {
    ...view,
    nodes: [
      ...view.nodes,
      { ...node("3", "ambient"), tier: "twoHop" as const },
    ],
  };
  render(
    <GraphCanvas
      {...callbacks}
      view={page}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(16));
  const { camera, target, labels, scene } = harness;
  if (!camera || !target || !labels || !scene)
    throw new Error("graph가 없습니다.");
  const second = harness.nodes.get("3");
  if (!second) throw new Error("2단계가 없습니다.");
  const position = second.position.clone();
  labels.render(scene, camera);
  expect(second.visible).toBe(false);
  expect(
    new THREE.Raycaster(
      new THREE.Vector3(position.x, position.y, 1000),
      new THREE.Vector3(0, 0, -1),
    ).intersectObject(second, true),
  ).toHaveLength(0);
  const distance = camera.position.distanceTo(target);
  target.x += distance * 0.1;
  camera.position.x += distance * 0.1;
  labels.render(scene, camera);
  expect(second.visible).toBe(true);
  const partial = second.userData.surface.material.opacity;
  expect(partial).toBeGreaterThan(0);
  expect(partial).toBeLessThan(second.userData.style.opacity);
  expect(second.userData.occluder.material.opacity).toBeLessThan(1);
  target.x -= distance * 0.1;
  camera.position.x -= distance * 0.1;
  labels.render(scene, camera);
  expect(second.visible).toBe(false);
  camera.position.sub(target).multiplyScalar(2).add(target);
  labels.render(scene, camera);
  expect(second.visible).toBe(true);
  expect(second.userData.surface.material.opacity).toBe(
    second.userData.style.opacity,
  );
  expect(second.position).toEqual(position);
});

it("2단계 이후 이름은 배율과 무관하게 숨기고 node·간선 초점에서만 표시한다", () => {
  const page = {
    ...view,
    nodes: [
      ...view.nodes,
      { ...node("3", "ambient"), tier: "twoHop" as const },
      node("4", "ambient"),
    ],
    relations: [
      {
        id: "remote",
        source: "3",
        target: "4",
        label: "관계",
        tier: "ambient" as const,
        directionality: "DIRECTED" as const,
        evidenceGroupCount: 1,
        conflict: false,
      },
    ],
  };
  const { getByRole } = render(
    <GraphCanvas
      {...props()}
      view={page}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(16));
  const { camera, target, labels, scene } = harness;
  if (!camera || !target || !labels || !scene)
    throw new Error("graph가 없습니다.");
  const near = camera.position.distanceTo(target);
  const draw = () => labels.render(scene, camera);
  camera.position.z = near * 1.3;
  draw();
  expect(visual("3").userData.label.visible).toBe(false);
  expect(visual("2").userData.surface.material.opacity).toBeGreaterThan(
    visual("3").userData.surface.material.opacity,
  );
  expect(visual("2").userData.shell.visible).toBe(true);
  expect(visual("3").userData.shell.visible).toBe(false);
  expect(visual("4").visible).toBe(true);
  expect(visual("4").userData.label.visible).toBe(false);
  camera.position.z = near * 1.55;
  draw();
  expect(visual("3").userData.label.visible).toBe(false);
  camera.position.z = near * 2;
  draw();
  expect(visual("3").userData.label.visible).toBe(false);
  expect(visual("2").userData.label.visible).toBe(true);
  act(() => getByRole("button", { name: "4 · 기술" }).focus());
  act(() => vi.advanceTimersByTime(450));
  draw();
  expect(visual("4").userData.label.visible).toBe(true);
  act(() => getByRole("button", { name: "4 · 기술" }).blur());
  act(() => harness.options.get("onLinkHover")?.(page.relations[0] as never));
  act(() => vi.advanceTimersByTime(450));
  draw();
  expect(visual("3").userData.label.visible).toBe(true);
  expect(visual("4").userData.label.visible).toBe(true);
  act(() => harness.options.get("onLinkHover")?.(null as never));
  act(() => vi.advanceTimersByTime(450));
  draw();
  expect(visual("3").userData.label.visible).toBe(false);
});

it("근접 조망은 1단계 간선만, 축소 뒤에는 2단계 간선까지만 표시한다", () => {
  const callbacks = props();
  const relations = (["direct", "twoHop", "threeHop", "ambient"] as const).map(
    (tier) => ({
      id: tier,
      source: "1",
      target: "2",
      label: tier,
      tier,
      directionality: "DIRECTED" as const,
      evidenceGroupCount: 1,
      conflict: false,
    }),
  );
  render(
    <GraphCanvas
      {...callbacks}
      view={{ ...view, relations }}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(16));
  const { camera, target, labels, scene } = harness;
  if (!camera || !target || !labels || !scene)
    throw new Error("graph가 없습니다.");
  labels.render(scene, camera);
  const nearVisibility = harness.options.get("linkVisibility");
  if (!nearVisibility) throw new Error("간선 표시 규칙이 없습니다.");
  expect(relations.map((link) => nearVisibility(link as never))).toEqual([
    true,
    false,
    false,
    false,
  ]);
  camera.position.sub(target).multiplyScalar(2).add(target);
  labels.render(scene, camera);
  const farVisibility = harness.options.get("linkVisibility");
  if (!farVisibility) throw new Error("간선 표시 규칙이 없습니다.");
  expect(relations.map((link) => farVisibility(link as never))).toEqual([
    true,
    true,
    false,
    false,
  ]);
});

it("hover는 현재 밝기에서 강조로 전환한 뒤 유지하고 테마 변경은 graph와 배율을 보존한다", () => {
  const callbacks = props();
  const { rerender } = render(
    <GraphCanvas {...callbacks} designPreview introStarted introCompleted />,
  );
  act(() => vi.advanceTimersByTime(16));
  const visual = harness.nodes.get("2");
  const camera = harness.camera;
  const scene = harness.scene;
  if (!visual || !camera || !scene) throw new Error("graph가 없습니다.");
  const draw = () =>
    Reflect.apply(scene.onBeforeRender, scene, [null, scene, camera, null]);
  act(() => harness.options.get("onNodeHover")?.({ id: "2" } as never));
  act(() => vi.advanceTimersByTime(16));
  draw();
  expect(visual.userData.shell.material.opacity).toBeCloseTo(0.15);
  act(() => vi.advanceTimersByTime(300));
  draw();
  const opacity = visual.userData.shell.material.opacity;
  expect(opacity).toBeGreaterThan(0.15);
  expect(opacity).toBeLessThan(0.5);
  expect(visual.userData.shell.visible).toBe(true);
  act(() => vi.advanceTimersByTime(200));
  draw();
  expect(visual.userData.shell.material.opacity).not.toBe(opacity);
  expect(visual.userData.shell.material.opacity).toBe(0.5);
  const scale = visual.userData.shell.scale.clone();
  act(() => vi.advanceTimersByTime(1500));
  draw();
  expect(visual.userData.shell.material.opacity).toBe(0.5);
  expect(visual.userData.shell.scale).toEqual(scale);
  expect(vi.getTimerCount()).toBe(0);
  const position = camera.position.clone();
  rerender(
    <GraphCanvas
      {...callbacks}
      designPreview
      introStarted
      introCompleted
      theme="light"
    />,
  );
  draw();
  expect(harness.camera).toBe(camera);
  expect(camera.position).toEqual(position);
  expect(visual.userData.occluder.material.color.getHexString()).toBe("f5f7fa");
  expect(visual.userData.shell.material.opacity).toBe(0.22);
  act(() => harness.options.get("onNodeHover")?.(null as never));
  act(() => vi.advanceTimersByTime(200));
  draw();
  const fading = visual.userData.shell.material.opacity;
  expect(fading).toBeGreaterThan(0.066);
  expect(fading).toBeLessThan(0.22);
  act(() => harness.options.get("onNodeHover")?.({ id: "2" } as never));
  act(() => vi.advanceTimersByTime(16));
  draw();
  expect(visual.userData.shell.material.opacity).toBe(fading);
  act(() => vi.advanceTimersByTime(420));
  draw();
  expect(visual.userData.shell.material.opacity).toBe(0.22);
  act(() => harness.options.get("onNodeHover")?.(null as never));
  act(() => vi.advanceTimersByTime(420));
  draw();
  expect(visual.userData.shell.material.opacity).toBeCloseTo(0.066);
  expect(visual.userData.shell.visible).toBe(true);
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  act(() => harness.options.get("onNodeHover")?.({ id: "2" } as never));
  act(() => vi.advanceTimersByTime(16));
  draw();
  expect(visual.userData.shell.material.opacity).toBe(0.22);
  expect(vi.getTimerCount()).toBe(0);
});

it("배치 완료 뒤 다시 생성된 간선도 첫 hover 전에 좌표를 갖는다", () => {
  const relation: KnowledgeRelation = {
    id: "late-link",
    source: "1",
    target: "2",
    label: "관계",
    tier: "direct",
    directionality: "DIRECTED",
    evidenceGroupCount: 1,
    conflict: false,
  };
  render(
    <GraphCanvas
      {...props()}
      view={{ ...view, relations: [relation] }}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(16));
  const recreate = harness.options.get("linkThreeObject");
  if (!recreate) throw new Error("간선 생성기가 없습니다.");
  const group = recreate(relation as never) as THREE.Group;
  const line = group.children[0] as THREE.Line;
  const raycaster = new THREE.Raycaster(
    new THREE.Vector3(0, 0, 100),
    new THREE.Vector3(0, 0, -1),
  );
  expect(() => raycaster.intersectObject(group, true)).not.toThrow();
  const position = line.geometry.getAttribute("position");
  const source = harness.nodes.get("1")?.position;
  const target = harness.nodes.get("2")?.position;
  if (!source || !target) throw new Error("간선의 양 끝이 없습니다.");
  expect(position.count).toBeGreaterThan(1);
  expect(position.getX(0)).toBeCloseTo(source.x);
  expect(position.getY(position.count - 1)).toBeCloseTo(target.y);
});

it("유형 필터는 좌표·배율을 보존하며 숨긴 노드와 연결을 선택·강조하지 않고 2.5초 뒤 강조를 끝낸다", () => {
  const callbacks = props();
  const relation = {
    id: "filtered",
    source: "1",
    target: "2",
    label: "연결",
    tier: "direct" as const,
    directionality: "DIRECTED" as const,
    evidenceGroupCount: 2,
    conflict: false,
  };
  const data = {
    ...view,
    nodes: [
      node("1", "center"),
      { ...node("2", "direct"), kind: "회사", kindCode: "COMPANY" },
    ],
    relations: [relation],
  };
  const { rerender, queryByRole } = render(
    <GraphCanvas
      {...callbacks}
      view={data}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(32));
  const { labels, scene, camera, target } = harness;
  if (!labels || !scene || !camera || !target)
    throw new Error("graph가 없습니다.");
  const draw = () => labels.render(scene, camera);
  draw();
  const positions = data.nodes.map((n) => visual(n.id).position.clone());
  const cameraPosition = camera.position.clone();
  const hidden = ["COMPANY"];
  rerender(
    <GraphCanvas
      {...callbacks}
      view={data}
      hiddenKinds={hidden}
      designPreview
      introStarted
      introCompleted
    />,
  );
  draw();
  expect(visual("2").visible).toBe(false);
  expect(harness.links.get("filtered")?.visible).toBe(false);
  expect(queryByRole("button", { name: "2 · 회사" })).toBeNull();
  expect(queryByRole("button", { name: /서로 다른 근거/ })).toBeNull();
  act(() => {
    harness.options.get("onNodeClick")?.(data.nodes[1] as never);
    harness.options.get("onLinkClick")?.(relation as never);
    harness.options.get("onNodeHover")?.(data.nodes[0] as never);
  });
  draw();
  expect(callbacks.onSelect).not.toHaveBeenCalled();
  expect(callbacks.onEvidence).not.toHaveBeenCalled();
  expect(harness.options.get("linkVisibility")?.(relation as never)).toBe(
    false,
  );
  act(() => harness.options.get("onNodeHover")?.(null as never));
  act(() => vi.advanceTimersByTime(450));
  draw();
  expect(visual("1").userData.shell.material.opacity).toBeGreaterThan(0);
  const allHidden = ["COMPANY", "TECHNOLOGY"];
  rerender(
    <GraphCanvas
      {...callbacks}
      view={data}
      hiddenKinds={allHidden}
      designPreview
      introStarted
      introCompleted
    />,
  );
  draw();
  expect(visual("1").visible).toBe(false);
  const page = {
    ...data,
    nodes: [
      ...data.nodes,
      { ...node("3", "direct"), kind: "회사", kindCode: "COMPANY" },
    ],
  };
  rerender(
    <GraphCanvas
      {...callbacks}
      view={page}
      hiddenKinds={hidden}
      designPreview
      introStarted
      introCompleted
    />,
  );
  draw();
  expect(visual("3").visible).toBe(false);
  expect(visual("1").userData.shell.material.opacity).toBe(0);
  act(() => vi.advanceTimersByTime(600));
  draw();
  expect(visual("1").userData.shell.material.opacity).toBeGreaterThan(0);
  act(() => vi.advanceTimersByTime(2100));
  draw();
  expect(visual("1").userData.shell.visible).toBe(false);
  rerender(
    <GraphCanvas
      {...callbacks}
      view={page}
      hiddenKinds={[]}
      designPreview
      introStarted
      introCompleted
    />,
  );
  draw();
  expect(visual("2").visible).toBe(true);
  expect(harness.links.get("filtered")?.visible).toBe(true);
  expect(data.nodes.map((n) => visual(n.id).position)).toEqual(positions);
  expect(camera.position).toEqual(cameraPosition);
  expect(callbacks.onTransitionComplete).not.toHaveBeenCalled();
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  rerender(
    <GraphCanvas
      {...callbacks}
      view={page}
      hiddenKinds={hidden}
      designPreview
      introStarted
      introCompleted
    />,
  );
  draw();
  const steady = visual("1").userData.shell.material.opacity;
  act(() => vi.advanceTimersByTime(1000));
  draw();
  expect(visual("1").userData.shell.material.opacity).toBe(steady);
  act(() => vi.advanceTimersByTime(1600));
  draw();
  expect(visual("1").userData.shell.visible).toBe(false);
});

it("2단계 간선과 화살표는 이동·확대 중 노드와 같은 진행률로 나타나고 사라진다", () => {
  const relation = {
    id: "fade",
    source: "1",
    target: "3",
    label: "연결",
    tier: "twoHop" as const,
    directionality: "DIRECTED" as const,
    evidenceGroupCount: 3,
    conflict: true,
  };
  render(
    <GraphCanvas
      {...props()}
      view={{
        ...view,
        nodes: [...view.nodes, { ...node("3", "ambient"), tier: "twoHop" }],
        relations: [relation],
      }}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(32));
  const { labels, camera, target, scene } = harness;
  if (!labels || !camera || !target || !scene)
    throw new Error("graph가 없습니다.");
  const initial = camera.position.clone();
  const draw = (scale: number) => {
    camera.position.copy(initial).sub(target).multiplyScalar(scale).add(target);
    labels.render(scene, camera);
  };
  const opacity = () =>
    harness.links.get("fade")?.userData.lines[1].material.opacity as number;
  draw(1);
  expect(opacity()).toBe(0);
  draw(1.1);
  const partial = opacity();
  expect(partial).toBeGreaterThan(0);
  expect(partial).toBeLessThan(0.32);
  expect(partial / 0.32).toBeCloseTo(
    visual("3").userData.surface.material.opacity /
      visual("3").userData.style.opacity,
  );
  expect(harness.links.get("fade")?.userData.arrow.material.opacity).toBe(
    partial,
  );
  draw(1.3);
  expect(opacity()).toBeCloseTo(0.32);
  draw(1.1);
  expect(opacity()).toBeCloseTo(partial);
  draw(1);
  expect(opacity()).toBe(0);
  expect(harness.links.get("fade")?.visible).toBe(false);
});

it("대표 가닥을 고정하고 hover·중심 관계 변화에서 추가 가닥만 현재 밝기부터 전환한다", () => {
  const callbacks = props();
  const relations = [1, 3, 6].map((count) => ({
    id: String(count),
    source: "1",
    target: "2",
    label: "연결",
    tier: "twoHop" as const,
    directionality: "DIRECTED" as const,
    evidenceGroupCount: count,
    conflict: count === 6,
  }));
  const data = { ...view, relations };
  const { rerender } = render(
    <GraphCanvas
      {...callbacks}
      view={data}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(32));
  const { camera, target, labels, scene } = harness;
  if (!camera || !target || !labels || !scene)
    throw new Error("graph가 없습니다.");
  camera.position.sub(target).multiplyScalar(2).add(target);
  const draw = () => labels.render(scene, camera);
  const opacities = (id: string): number[] =>
    harness.links
      .get(id)
      ?.userData.lines.map(
        (line: THREE.Line) => (line.material as THREE.Material).opacity,
      );
  draw();
  for (const count of [1, 3, 6]) {
    const values = opacities(String(count));
    expect(values.filter((value) => value > 0)).toHaveLength(1);
    expect(values[Math.floor((count - 1) / 2)]).toBeGreaterThan(0);
  }
  const geometry = harness.links.get("6")?.userData.lines[2].geometry;
  const vertices = Array.from(geometry.getAttribute("position").array);
  act(() => harness.options.get("onNodeHover")?.(data.nodes[1] as never));
  draw();
  expect(opacities("6")[0]).toBe(0);
  act(() => vi.advanceTimersByTime(200));
  draw();
  const midway = opacities("6")[0] ?? 0;
  expect(midway).toBeGreaterThan(0);
  expect(midway).toBeLessThan(opacities("6")[2] ?? 0);
  act(() => harness.options.get("onNodeHover")?.(null as never));
  draw();
  expect(opacities("6")[0]).toBeCloseTo(midway);
  act(() => vi.advanceTimersByTime(200));
  draw();
  expect(opacities("6")[0]).toBeLessThan(midway);
  act(() => vi.advanceTimersByTime(500));
  draw();
  expect(opacities("6").filter((value) => value > 0)).toHaveLength(1);
  const direct = {
    ...data,
    relations: relations.map((link) => ({ ...link, tier: "direct" as const })),
  };
  rerender(
    <GraphCanvas
      {...callbacks}
      view={direct}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(500));
  draw();
  expect(opacities("6").filter((value) => value > 0)).toHaveLength(6);
  rerender(
    <GraphCanvas
      {...callbacks}
      view={data}
      designPreview
      introStarted
      introCompleted
    />,
  );
  draw();
  expect(opacities("6")[0]).toBeGreaterThan(0);
  act(() => vi.advanceTimersByTime(500));
  draw();
  expect(opacities("6").filter((value) => value > 0)).toHaveLength(1);
  expect(Array.from(geometry.getAttribute("position").array)).toEqual(vertices);
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  act(() => harness.options.get("onLinkHover")?.(relations[2] as never));
  draw();
  expect(opacities("6").filter((value) => value > 0)).toHaveLength(6);
  expect(opacities("3").filter((value) => value > 0)).toHaveLength(1);
  rerender(
    <GraphCanvas
      {...callbacks}
      view={data}
      hiddenKinds={["TECHNOLOGY"]}
      designPreview
      introStarted
      introCompleted
    />,
  );
  draw();
  expect(opacities("6").every((value) => value === 0)).toBe(true);
});

it("추가 page가 기본 밝기를 갱신해도 WebGL 프레임 전에 거리별 표시를 복원한다", () => {
  const callbacks = props();
  const data = {
    ...view,
    nodes: [
      ...view.nodes,
      { ...node("3", "ambient"), tier: "twoHop" as const },
    ],
  };
  const { rerender } = render(
    <GraphCanvas
      {...callbacks}
      view={data}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(32));
  const { camera, target, scene } = harness;
  if (!camera || !target || !scene) throw new Error("graph가 없습니다.");
  // 실제 Three.js처럼 label renderer보다 앞에서 호출한다.
  const drawWebGL = () =>
    Reflect.apply(scene.onBeforeRender, scene, [null, scene, camera, null]);
  drawWebGL();
  expect(visual("3").visible).toBe(false);
  const near = camera.position.clone();
  camera.position.sub(target).multiplyScalar(1.1).add(target);
  drawWebGL();
  const partial = visual("3").userData.surface.material.opacity;
  expect(partial).toBeGreaterThan(0);
  expect(partial).toBeLessThan(0.9);
  const page = { ...data, nodes: [...data.nodes, node("4", "ambient")] };
  rerender(
    <GraphCanvas
      {...callbacks}
      view={page}
      designPreview
      introStarted
      introCompleted
    />,
  );
  drawWebGL();
  expect(visual("3").userData.surface.material.opacity).toBeCloseTo(partial);
  camera.position.copy(near);
  drawWebGL();
  expect(visual("3").visible).toBe(false);
  expect(visual("4").visible).toBe(false);
  rerender(
    <GraphCanvas
      {...callbacks}
      view={{ ...page, nodes: [...page.nodes, node("5", "ambient")] }}
      designPreview
      introStarted
      introCompleted
    />,
  );
  drawWebGL();
  expect(visual("3").visible).toBe(false);
  expect(visual("5").visible).toBe(false);
});

it("이웃 hover는 연결 노드의 이름과 유형색을 드러내고 해제 시 복귀한다", () => {
  const data = {
    ...view,
    nodes: [
      ...view.nodes,
      ...["3", "4"].map((id) => ({
        ...node(id, "ambient"),
        tier: "twoHop" as const,
      })),
    ],
    relations: [
      {
        id: "neighbor",
        source: "2",
        target: "3",
        label: "연결",
        tier: "twoHop" as const,
        directionality: "DIRECTED" as const,
        evidenceGroupCount: 1,
        conflict: false,
      },
    ],
  };
  render(
    <GraphCanvas
      {...props()}
      view={data}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(32));
  const { camera, scene } = harness;
  if (!camera || !scene) throw new Error("graph가 없습니다.");
  const draw = () =>
    Reflect.apply(scene.onBeforeRender, scene, [null, scene, camera, null]);
  draw();
  const position = camera.position.clone();
  expect(visual("3").visible).toBe(false);
  act(() => harness.options.get("onNodeHover")?.(data.nodes[1] as never));
  act(() => vi.advanceTimersByTime(200));
  draw();
  expect(visual("3").visible).toBe(true);
  expect(visual("3").userData.reveal).toBeGreaterThan(0);
  expect(visual("3").userData.reveal).toBeLessThan(1);
  act(() => vi.advanceTimersByTime(250));
  draw();
  expect(visual("3").userData.reveal).toBe(1);
  expect(visual("3").userData.hoverOpacity).toBe(1);
  expect(visual("3").userData.label.visible).toBe(true);
  expect(visual("4").visible).toBe(false);
  expect(harness.links.get("neighbor")?.userData.reveal).toBe(1);
  expect(camera.position).toEqual(position);
  act(() => harness.options.get("onNodeHover")?.(null as never));
  act(() => vi.advanceTimersByTime(450));
  draw();
  expect(visual("3").visible).toBe(false);
});

it("먼 노드는 hover에서 본체와 이름도 서서히 선명해지고 해제·필터 적용 시 복귀한다", () => {
  const callbacks = props();
  const data = { ...view, nodes: [...view.nodes, node("3", "ambient")] };
  const { rerender } = render(
    <GraphCanvas
      {...callbacks}
      view={data}
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(32));
  const { camera, target, scene } = harness;
  if (!camera || !target || !scene) throw new Error("graph가 없습니다.");
  camera.position.sub(target).multiplyScalar(2).add(target);
  const draw = () =>
    Reflect.apply(scene.onBeforeRender, scene, [null, scene, camera, null]);
  const opacity = () => visual("3").userData.surface.material.opacity as number;
  draw();
  const baseline = opacity();
  const neighborShellScale = visual("2").userData.shell.scale.clone();
  const material = visual("3").userData.surface.material;
  const neutral = material.emissive.clone();
  expect(neutral.getHexString()).toBe("808080");
  expect(neutral.equals(visual("3").userData.core.material.color)).toBe(false);
  act(() => harness.options.get("onNodeHover")?.(data.nodes[2] as never));
  draw();
  expect(opacity()).toBe(baseline);
  act(() => vi.advanceTimersByTime(200));
  draw();
  expect(visual("2").userData.shell.scale).toEqual(neighborShellScale);
  expect(visual("2").userData.hoverOpacity).toBe(0);
  expect(opacity()).toBeGreaterThan(baseline);
  expect(opacity()).toBeLessThan(1);
  act(() => vi.advanceTimersByTime(250));
  draw();
  expect(opacity()).toBeCloseTo(1);
  expect(material.emissive.getHexString()).toBe(
    visual("3").userData.core.material.color.getHexString(),
  );
  expect(Number(visual("3").userData.label.element.style.opacity)).toBeCloseTo(
    1,
  );
  act(() => harness.options.get("onNodeHover")?.(null as never));
  act(() => vi.advanceTimersByTime(450));
  draw();
  expect(opacity()).toBeCloseTo(baseline);
  expect(material.emissive.equals(neutral)).toBe(true);
  expect(visual("3").userData.label.visible).toBe(false);
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  act(() => harness.options.get("onNodeHover")?.(data.nodes[2] as never));
  act(() => vi.advanceTimersByTime(16));
  draw();
  expect(opacity()).toBeCloseTo(1);
  rerender(
    <GraphCanvas
      {...callbacks}
      view={data}
      hiddenKinds={["TECHNOLOGY"]}
      designPreview
      introStarted
      introCompleted
    />,
  );
  draw();
  expect(visual("3").visible).toBe(false);
});

it("라이트 모드 hover 외곽선이 유형 색을 가리지 않는다", () => {
  const data = { ...view, nodes: [...view.nodes, node("3", "ambient")] };
  render(
    <GraphCanvas
      {...props()}
      view={data}
      theme="light"
      designPreview
      introStarted
      introCompleted
    />,
  );
  act(() => vi.advanceTimersByTime(32));
  const { camera, target, scene } = harness;
  if (!camera || !target || !scene) throw new Error("graph가 없습니다.");
  camera.position.sub(target).multiplyScalar(2).add(target);
  const draw = () =>
    Reflect.apply(scene.onBeforeRender, scene, [null, scene, camera, null]);
  act(() => harness.options.get("onNodeHover")?.(data.nodes[2] as never));
  act(() => vi.advanceTimersByTime(450));
  draw();
  expect(visual("3").userData.shell.material.opacity).toBeCloseTo(0.22);
  expect(visual("3").userData.surface.material.color.getHexString()).not.toBe(
    visual("3").userData.shell.material.color.getHexString(),
  );
});
