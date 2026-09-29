import { useEffect, useRef, useState } from "react";

export function useInitialLoading(ready: boolean, failed: boolean) {
  const startedAt = useRef(Date.now());
  const dismissed = useRef(false);
  const [rhythm] = useState(
    () => [0.4 + Math.random() * 0.2, 0.15 + Math.random() * 0.15] as const,
  );
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState<"loading" | "leaving" | "hidden">(
    "loading",
  );

  useEffect(() => {
    if (dismissed.current) return;
    const hide = () => {
      dismissed.current = true;
      setPhase("hidden");
    };
    if (failed) {
      hide();
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      if (ready) {
        setProgress(99);
        hide();
      }
      return;
    }

    let frame = 0;
    const timers: number[] = [];
    const ramp = () => {
      const time = Math.min(1, (Date.now() - startedAt.current) / 1400);
      // 두 파동의 합을 1 미만으로 제한해 역행 없이 진행 속도만 바꾼다.
      const paced =
        time -
        (rhythm[0] * Math.sin(6 * Math.PI * time)) / (6 * Math.PI) -
        (rhythm[1] * Math.sin(14 * Math.PI * time)) / (14 * Math.PI);
      const next = Math.floor(paced * paced * (3 - 2 * paced) * 89);
      setProgress(next);
      if (next < 89) frame = requestAnimationFrame(ramp);
    };
    const schedule = (delay: number, action: () => void) => {
      timers.push(window.setTimeout(action, delay));
    };
    ramp();
    if (ready) {
      schedule(Math.max(0, 1400 - (Date.now() - startedAt.current)), () => {
        cancelAnimationFrame(frame);
        setProgress(90);
        schedule(120, () => setProgress(95));
        schedule(240, () => setProgress(99));
        schedule(400, () => setPhase("leaving"));
        schedule(600, hide);
      });
    }
    return () => {
      cancelAnimationFrame(frame);
      for (const timer of timers) window.clearTimeout(timer);
    };
  }, [ready, failed, rhythm]);

  return { progress, phase };
}
