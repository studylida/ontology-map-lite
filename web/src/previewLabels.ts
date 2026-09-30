// web/src/previewLabels.ts

const layouts = new WeakMap<HTMLElement, string>();

function priority(label: HTMLElement) {
  return ["center", "direct", "twoHop", "threeHop", "ambient"].indexOf(
    label.dataset.tier ?? "ambient",
  );
}

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

function overlap(a: Box, b: Box) {
  return (
    Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x)) *
    Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y))
  );
}

export function placePreviewLabels(container: HTMLElement) {
  const allLabels = [
    ...container.querySelectorAll<HTMLElement>("[data-node-id]"),
  ];
  const labels = allLabels
    .filter(
      (label) =>
        label.style.display !== "none" && Number(label.style.opacity) > 0,
    )
    .sort(
      (a, b) =>
        priority(a) - priority(b) ||
        (a.dataset.nodeId ?? "").localeCompare(b.dataset.nodeId ?? "", "en", {
          numeric: true,
        }),
    );

  // dataset.focused를 시그니처에서 배제하여 호버 시 불필요한 전체 재배치/연쇄 흔들림 원천 방지
  const signature = `${container.clientWidth}:${container.clientHeight}:${labels
    .map(
      (label) =>
        `${label.dataset.nodeId}:${label.textContent}:${label.style.transform}:${label.style.opacity}:${label.dataset.tier}`,
    )
    .join("|")}`;

  for (const label of labels) {
    if (label.dataset.focused === "true") {
      label.style.zIndex = "999";
    } else if (priority(label) <= 0) {
      label.style.zIndex = String(allLabels.length + 1 - priority(label));
    } else {
      label.style.zIndex = "10";
    }
  }

  if (layouts.get(container) === signature) return;
  layouts.set(container, signature);

  const bounds = container.getBoundingClientRect();
  for (const label of labels) {
    label.style.translate = "none";
  }

  const boxes = labels.map((label) => label.getBoundingClientRect());
  const placed: Box[] = [];

  labels.forEach((label, index) => {
    const box = boxes[index];
    if (!box) return;

    const offsets = [
      [0, 0],
      [0, -24],
      [0, 24],
      [-box.width - 16, 0],
      [-box.width / 2 - 8, -24],
      [-box.width / 2 - 8, 24],
      [-box.width - 16, -24],
      [-box.width - 16, 24],
    ];

    const candidates = offsets.map(([dx = 0, dy = 0]) => ({
      x: box.x + dx,
      y: box.y + dy,
      width: box.width,
      height: box.height,
    }));

    const cost = (candidate: Box, offsetIdx: number) =>
      placed.reduce((sum, other) => sum + overlap(candidate, other), 0) * 10 +
      (offsetIdx === 0 ? 0 : 25) +
      10 *
        (candidate.width * candidate.height -
          overlap(candidate, {
            x: bounds.x,
            y: bounds.y,
            width: bounds.width,
            height: bounds.height,
          }));

    let best = candidates[0] ?? box;
    let minCost = cost(best, 0);

    candidates.forEach((cand, cIdx) => {
      const c = cost(cand, cIdx);
      if (c < minCost) {
        minCost = c;
        best = cand;
      }
    });

    label.style.translate = `${best.x - box.x}px ${best.y - box.y}px`;
    const protectedLabel = priority(label) <= 1;
    const hidden = !protectedLabel && minCost > 60;
    label.style.visibility = hidden ? "hidden" : "visible";
    if (hidden) return;
    placed.push({ ...best, width: best.width + 4, height: best.height + 3 });
  });
}
