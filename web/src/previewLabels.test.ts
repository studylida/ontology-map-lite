import { expect, it, vi } from "vitest";
import { placePreviewLabels } from "./previewLabels";

it("겹친 이름을 가까운 빈자리로 옮기고 정지한 화면에서는 다시 측정하지 않는다", () => {
  const container = document.createElement("div");
  container.getBoundingClientRect = () => new DOMRect(0, 0, 400, 300);
  const labels = ["center", "direct"].map((tier) => {
    const label = document.createElement("span");
    label.dataset.nodeId = tier;
    label.dataset.tier = tier;
    label.style.opacity = "1";
    label.getBoundingClientRect = vi.fn(() => new DOMRect(100, 100, 100, 20));
    container.append(label);
    return label;
  });
  placePreviewLabels(container);
  expect(labels[0]?.style.translate).toBe("0px 0px");
  expect(labels[1]?.style.translate).not.toBe("0px 0px");
  placePreviewLabels(container);
  expect(labels[0]?.getBoundingClientRect).toHaveBeenCalledTimes(1);
});

it("밀집한 이름은 우선순위로 숨기고 초점과 공간이 생기면 다시 표시한다", () => {
  const container = document.createElement("div");
  container.getBoundingClientRect = () => new DOMRect(0, 0, 400, 300);
  const labels = ["center", "direct", "twoHop"].map((tier, index) => {
    const label = document.createElement("span");
    label.dataset.nodeId = String(index);
    label.dataset.tier = tier;
    label.style.opacity = "1";
    label.getBoundingClientRect = () => new DOMRect(100, 100, 200, 60);
    container.append(label);
    return label;
  });
  const [center, direct, second] = labels;
  if (!center || !direct || !second) throw new Error("이름이 없습니다.");
  placePreviewLabels(container);
  expect(center.style.visibility).toBe("visible");
  expect(direct.style.visibility).toBe("hidden");
  expect(second.style.visibility).toBe("hidden");
  direct.dataset.focused = "true";
  second.dataset.focused = "true";
  placePreviewLabels(container);
  expect(labels.map((label) => label.style.visibility)).toEqual([
    "visible",
    "visible",
    "visible",
  ]);
  expect(Number(direct.style.zIndex)).toBeGreaterThan(
    Number(center.style.zIndex),
  );
  direct.style.zIndex = "0";
  placePreviewLabels(container);
  expect(Number(direct.style.zIndex)).toBeGreaterThan(
    Number(center.style.zIndex),
  );
  direct.dataset.focused = "false";
  second.dataset.focused = "false";
  center.style.opacity = "0";
  placePreviewLabels(container);
  expect(direct.style.visibility).toBe("visible");
  expect(second.style.visibility).toBe("hidden");
  second.style.transform = "translate(0, 100px)";
  second.getBoundingClientRect = () => new DOMRect(100, 200, 200, 60);
  placePreviewLabels(container);
  expect(second.style.visibility).toBe("visible");
});
