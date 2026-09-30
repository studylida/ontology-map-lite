// web/src/useGraphIntroAnimation.ts
import type { ForceGraph3DInstance } from "3d-force-graph";
import { useCallback, useEffect, useRef, type RefObject } from "react";
import type * as THREE from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { easeInOutCubic, type RuntimeLink, type RuntimeNode } from "./graphVisuals";

interface IntroAnimationOptions {
  graphRef: RefObject<ForceGraph3DInstance<RuntimeNode, RuntimeLink> | null>;
  containerRef: RefObject<HTMLDivElement | null>;
  introStarted: boolean;
  readyRef: RefObject<boolean>;
  fitDistance: (wide?: boolean) => number | undefined;
}

export function useGraphIntroAnimation({
  graphRef,
  containerRef,
  introStarted,
  readyRef,
  fitDistance,
}: IntroAnimationOptions) {
  const introCompletedRef = useRef(false);
  const introAnimRef = useRef<number | null>(null);

  const cancelIntro = useCallback(() => {
    if (!readyRef.current) return;
    introCompletedRef.current = true;
    if (introAnimRef.current !== null) {
      cancelAnimationFrame(introAnimRef.current);
      introAnimRef.current = null;
    }
    const graph = graphRef.current;
    if (graph) {
      const controls = graph.controls() as OrbitControls;
      if (controls) controls.maxDistance = 2500;
    }
    const labels = containerRef.current?.querySelector<HTMLElement>(
      '[data-graph-labels="true"]',
    );
    if (labels) labels.style.opacity = "1";
  }, [containerRef, readyRef, graphRef]);

  useEffect(() => {
    if (!introStarted || introCompletedRef.current) return;
    const graph = graphRef.current;
    const centerZ = fitDistance(false);
    if (!graph || !readyRef.current || centerZ === undefined) return;
    const camera = graph.camera() as THREE.PerspectiveCamera;
    const controls = graph.controls() as OrbitControls;
    const labels = containerRef.current?.querySelector<HTMLElement>(
      '[data-graph-labels="true"]',
    );

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      camera.position.set(0, 0, centerZ);
      controls.target.set(0, 0, 0);
      controls.maxDistance = 2500;
      controls.update();
      cancelIntro();
      return;
    }

    // 1단계 시작: 전체 지식그래프가 밤하늘의 진짜 한 점(별빛)으로 보일 만큼 축소된 원거리 (farZ = 8000)
    const farZ = 8000;
    // 2단계 목표: 지식맵 전체를 조망할 수 있는 개요 거리 (overviewZ)
    const overviewZ = fitDistance(true) ?? Math.min(2200, (centerZ * 1.5) / 0.95);

    camera.position.set(0, 0, farZ);
    controls.target.set(0, 0, 0);
    controls.update();

    if (labels) {
      labels.style.opacity = "0";
    }

    let begun: number | undefined;
    const interpolate = (from: number, to: number, t: number) =>
      Math.exp(Math.log(from) + (Math.log(to) - Math.log(from)) * easeInOutCubic(t));

    // 전체 인트로 연출 (2300ms):
    // Phase 1 (0 ~ 900ms): 한 점(z=8000)에서 전체 개요(overviewZ)로 빠른 고속 줌인
    // Phase 2 (900 ~ 1400ms): 감속 호흡 구간 (overviewZ -> overviewZ * 0.93) + 라벨 부드럽게 페이드인 (0 -> 1)
    // Phase 3 (1400 ~ 2300ms): 1-hop 상세(centerZ)로 가속 및 안착
    const frame = (now: number) => {
      begun ??= now;
      const elapsed = Math.min(2300, now - begun);

      if (elapsed < 900) {
        const t = elapsed / 900;
        camera.position.z = interpolate(farZ, overviewZ, t);
        if (labels) labels.style.opacity = "0";
      } else if (elapsed < 1400) {
        const t = (elapsed - 900) / 500;
        // 아예 멈추지 않고 미세하게 줌인하는 감속 호흡 연출
        camera.position.z = interpolate(overviewZ, overviewZ * 0.93, t);
        if (labels) {
          labels.style.opacity = String(easeInOutCubic(t));
        }
      } else {
        const t = (elapsed - 1400) / 900;
        camera.position.z = interpolate(overviewZ * 0.93, centerZ, t);
        if (labels) labels.style.opacity = "1";
      }
      controls.update();

      if (elapsed < 2300) {
        introAnimRef.current = requestAnimationFrame(frame);
      } else {
        camera.position.z = centerZ;
        controls.maxDistance = 2500;
        controls.update();
        cancelIntro();
      }
    };
    introAnimRef.current = requestAnimationFrame(frame);
    return () => {
      if (introAnimRef.current !== null) cancelAnimationFrame(introAnimRef.current);
      introAnimRef.current = null;
      if (labels) labels.style.opacity = "1";
    };
  }, [introStarted, fitDistance, cancelIntro, graphRef, containerRef, readyRef]);

  const resetIntroState = useCallback(() => {
    if (introAnimRef.current !== null) {
      cancelAnimationFrame(introAnimRef.current);
      introAnimRef.current = null;
    }
    introCompletedRef.current = false;
  }, []);

  return {
    introCompletedRef,
    cancelIntro,
    resetIntroState,
  };
}
