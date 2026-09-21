const layouts = new WeakMap<HTMLElement, string>();

function priority(label: HTMLElement) {
  if (label.dataset.focused === "true") return -1;
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

// ponytail: 검토 자료 규모의 O(n²) label 배치다. 수백 개 label에서 비용이 확인되면 공간 색인을 검토한다.
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
  const signature = `${container.clientWidth}:${container.clientHeight}:${labels.map((label) => `${label.dataset.nodeId}:${label.textContent}:${label.style.transform}:${label.style.opacity}:${label.dataset.focused}:${label.dataset.tier}`).join("|")}`;
  // CSS2DRenderer가 매 frame 설정하는 깊이 순서보다 중심·초점 이름을 앞에 둔다.
  for (const label of labels)
    if (priority(label) <= 0)
      label.style.zIndex = String(allLabels.length + 1 - priority(label));
  if (layouts.get(container) === signature) return;
  layouts.set(container, signature);
  const bounds = container.getBoundingClientRect();
  for (const label of labels) label.style.translate = "none";
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
    const cost = (candidate: Box) =>
      placed.reduce((sum, other) => sum + overlap(candidate, other), 0) +
      10 *
        (candidate.width * candidate.height -
          overlap(candidate, {
            x: bounds.x,
            y: bounds.y,
            width: bounds.width,
            height: bounds.height,
          }));
    const best = candidates.reduce(
      (best, candidate) => (cost(candidate) < cost(best) ? candidate : best),
      candidates[0] ?? box,
    );
    label.style.translate = `${best.x - box.x}px ${best.y - box.y}px`;
    const protectedLabel = priority(label) <= 0;
    const hidden = !protectedLabel && cost(best) > 0;
    label.style.visibility = hidden ? "hidden" : "visible";
    if (hidden) return;
    placed.push({ ...best, width: best.width + 4, height: best.height + 3 });
  });
}
