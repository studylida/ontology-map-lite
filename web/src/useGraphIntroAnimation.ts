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
    const labels = containerRef.current?.querySelector<HTMLElement>(
      '[data-graph-labels="true"]',
    );
    if (labels) labels.style.opacity = "1";
  }, [containerRef, readyRef]);

  useEffect(() => {
    if (!introStarted || introCompletedRef.current) return;
    const graph = graphRef.current;
    const centerZ = fitDistance();
    if (!graph || !readyRef.current || centerZ === undefined) return;
    const camera = graph.camera() as THREE.PerspectiveCamera;
    const controls = graph.controls() as OrbitControls;
    const labels = containerRef.current?.querySelector<HTMLElement>(
      '[data-graph-labels="true"]',
    );

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      camera.position.set(0, 0, centerZ);
      controls.target.set(0, 0, 0);
      controls.update();
      cancelIntro();
      return;
    }

    const farZ = camera.position.z;
    const overviewZ = Math.min(farZ, (centerZ * 1.5) / 0.95);
    let begun: number | undefined;
    const interpolate = (from: number, to: number, t: number) =>
      Math.exp(Math.log(from) + (Math.log(to) - Math.log(from)) * easeInOutCubic(t));

    const frame = (now: number) => {
      begun ??= now;
      const elapsed = Math.min(2400, now - begun);
      if (elapsed < 1000) {
        camera.position.z = interpolate(farZ, overviewZ, elapsed / 1000);
      } else if (elapsed < 1300) {
        camera.position.z = overviewZ;
        if (labels) {
          labels.style.opacity = String(easeInOutCubic((elapsed - 1000) / 300));
        }
      } else {
        camera.position.z = interpolate(overviewZ, centerZ, (elapsed - 1300) / 1100);
        if (labels) labels.style.opacity = "1";
      }
      controls.update();

      if (elapsed < 2400) {
        introAnimRef.current = requestAnimationFrame(frame);
      } else {
        camera.position.z = centerZ;
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
