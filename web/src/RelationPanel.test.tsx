import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { type EvidenceTrace, fetchRelationEvidence } from "./data";
import {
  EvidenceDialog,
  type EvidenceSelection,
  publicationLabel,
} from "./RelationPanel";

const request = vi.fn();
const response = (body: unknown, status = 200) => ({
  ok: status < 400,
  status,
  json: async () => body,
});
const trace = {
  item_key: "opaque-item-key",
  claim_text: "확인한 기술 관계",
  modality: "FACT",
  stance: "SUPPORT",
  source: {
    title: "발표 자료",
    publisher_name: "공개 출처",
    published_at: "2026-08-01T00:00:00Z",
    published_precision: "MONTH",
    canonical_url: "https://example.com/source",
  },
  quote_text: "원문 인용",
  locator: { paragraph_number: null, start_char: 0, end_char: 5 },
};

beforeEach(() => {
  request.mockReset();
  vi.stubGlobal("fetch", request);
  // jsdom에는 native dialog 메서드가 없어 열림 상태만 흉내 낸다. 실제 focus trap은 브라우저에서 검증한다.
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: {
      configurable: true,
      value: function (this: HTMLDialogElement) {
        this.setAttribute("open", "");
      },
    },
    close: {
      configurable: true,
      value: function (this: HTMLDialogElement) {
        this.removeAttribute("open");
      },
    },
  });
});
afterEach(() => {
  cleanup();
  Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
  Reflect.deleteProperty(HTMLDialogElement.prototype, "close");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function Flow() {
  const [selection, setSelection] = useState<EvidenceSelection | null>(null);
  return (
    <>
      <button
        type="button"
        onClick={() =>
          setSelection({ id: "1", label: "SK하이닉스 → HBF · 관련 기술" })
        }
      >
        HBF 연결 원문
      </button>
      {selection && (
        <EvidenceDialog
          key={selection.id}
          selection={selection}
          onClose={() => setSelection(null)}
        />
      )}
    </>
  );
}

it("Relation 선택 때만 공용 근거 창을 열고 cursor를 그대로 전달하며 닫은 뒤 focus를 복귀한다", async () => {
  request.mockImplementation(async (path: string) => {
    return response({
      items: path.includes("cursor=") ? [] : [trace],
      next_cursor: path.includes("cursor=") ? null : "opaque +/?",
      trace_count: 1,
    });
  });
  render(<Flow />);
  const opener = screen.getByRole("button", { name: "HBF 연결 원문" });
  expect(request).not.toHaveBeenCalled();
  opener.focus();
  fireEvent.click(opener);
  await screen.findByText("원문 인용");
  expect(screen.getByRole("dialog").getAttribute("open")).toBe("");
  expect(screen.getByText("출처 · 공개 출처")).toBeTruthy();
  expect(screen.getByText("게시일 · 2026-08")).toBeTruthy();
  expect(screen.queryByText("원문 위치")).toBeNull();
  expect(screen.queryByText("9223372036854775807")).toBeNull();
  expect(
    screen
      .getByRole("link", { name: "원문 기사 열기 · 발표 자료" })
      .getAttribute("href"),
  ).toBe("https://example.com/source");
  fireEvent.click(screen.getByRole("button", { name: "원문 더 보기" }));
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  expect(
    new URL(request.mock.calls[1]?.[0], "https://example.com").searchParams.get(
      "cursor",
    ),
  ).toBe("opaque +/?");
  const dialog = screen.getByRole("dialog");
  const first = screen.getByRole("button", { name: "원문 창 닫기" });
  const last = screen.getByRole("link", {
    name: "원문 기사 열기 · 발표 자료",
  });
  // jsdom에는 레이아웃이 없어 현재 표시된 두 조작 요소의 영역을 제공한다.
  for (const element of [first, last]) {
    vi.spyOn(element, "getClientRects").mockReturnValue([
      new DOMRect(0, 0, 44, 44),
    ] as unknown as DOMRectList);
  }
  last.focus();
  fireEvent.keyDown(last, { key: "Tab" });
  expect(document.activeElement).toBe(first);
  fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
  expect(document.activeElement).toBe(last);
  vi.spyOn(dialog, "getBoundingClientRect").mockReturnValue(
    new DOMRect(100, 100, 400, 300),
  );
  fireEvent.click(dialog, { clientX: 120, clientY: 120 });
  fireEvent.click(screen.getByText("원문 인용"), { clientX: 0, clientY: 0 });
  expect(screen.getByRole("dialog")).toBe(dialog);
  fireEvent.click(dialog, { clientX: 50, clientY: 120 });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(opener);
});

it("Claim과 원문 인용이 같아도 정확한 원문만 한 번 표시한다", async () => {
  request.mockResolvedValue(
    response({
      items: [{ ...trace, quote_text: trace.claim_text }],
      next_cursor: null,
      trace_count: 1,
    }),
  );
  const { container } = render(
    <EvidenceDialog
      selection={{ id: "1", label: "검토 관계" }}
      onClose={vi.fn()}
    />,
  );
  await screen.findByText(trace.claim_text);
  expect(screen.getAllByText(trace.claim_text)).toHaveLength(1);
  expect(container.querySelectorAll("blockquote")).toHaveLength(1);
});

it.each([404, 422, 503, 0])(
  "오류 %s를 빈 결과와 구분하고 retry 정책을 따른다",
  async (status) => {
    if (status === 0) request.mockRejectedValueOnce(new TypeError("offline"));
    else
      request.mockResolvedValueOnce(
        response(
          {
            error: {
              code:
                status === 503
                  ? "PANEL_NOT_READY"
                  : status === 404
                    ? "PANEL_NOT_FOUND"
                    : "INVALID_REQUEST",
              retryable: status === 503,
            },
          },
          status,
        ),
      );
    request.mockResolvedValue(
      response({ items: [trace], next_cursor: null, trace_count: 1 }),
    );
    render(<Flow />);
    fireEvent.click(screen.getByRole("button", { name: "HBF 연결 원문" }));
    await screen.findByRole("alert");
    expect(screen.queryByText("현재 공개된 자료가 없습니다.")).toBeNull();
    if (status === 503 || status === 0) {
      fireEvent.click(screen.getByRole("button", { name: "다시 조회" }));
      expect(await screen.findByText("원문 인용")).toBeTruthy();
    } else
      expect(screen.queryByRole("button", { name: "다시 조회" })).toBeNull();
  },
);

it("Evidence dialog 첫 read 실패가 dialog를 닫지 않고 retry와 사용자 close를 유지한다", async () => {
  request.mockResolvedValueOnce(
    response({ error: { code: "PANEL_NOT_READY", retryable: true } }, 503),
  );
  render(
    <EvidenceDialog
      selection={{ id: "1", label: "검토 관계" }}
      onClose={vi.fn()}
    />,
  );
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByRole("dialog").getAttribute("open")).toBe("");
  expect(screen.getByRole("button", { name: "다시 조회" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "원문 창 닫기" })).toBeTruthy();
});

it("Trace page1 성공 뒤 next page 404에서도 page1과 dialog를 유지한다", async () => {
  request
    .mockResolvedValueOnce(
      response({ items: [trace], next_cursor: "next", trace_count: 1 }),
    )
    .mockResolvedValueOnce(
      response({ error: { code: "PANEL_NOT_FOUND", retryable: false } }, 404),
    );
  render(
    <EvidenceDialog
      selection={{ id: "1", label: "검토 관계" }}
      onClose={vi.fn()}
    />,
  );
  await screen.findByText("원문 인용");
  fireEvent.click(screen.getByRole("button", { name: "원문 더 보기" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "추가 자료를 불러올 수 없습니다. 이미 불러온 내용은 계속 볼 수 있습니다.",
  );
  expect(screen.getByText("원문 인용")).toBeTruthy();
  expect(screen.getByRole("dialog").getAttribute("open")).toBe("");
});

it("Trace 추가 조회 503은 기존 근거를 유지하고 같은 cursor로 재시도한다", async () => {
  request
    .mockResolvedValueOnce(
      response({ items: [trace], next_cursor: "opaque +/?", trace_count: 2 }),
    )
    .mockResolvedValueOnce(
      response({ error: { code: "PANEL_NOT_READY", retryable: true } }, 503),
    )
    .mockResolvedValueOnce(
      response({
        items: [{ ...trace, item_key: "second", quote_text: "추가 원문" }],
        next_cursor: null,
        trace_count: 2,
      }),
    );
  render(
    <EvidenceDialog
      selection={{ id: "1", label: "검토 관계" }}
      onClose={vi.fn()}
    />,
  );
  await screen.findByText("원문 인용");
  fireEvent.click(screen.getByRole("button", { name: "원문 더 보기" }));
  await screen.findByRole("alert");
  expect(screen.getByText("원문 인용")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "다시 조회" }));
  await screen.findByText("추가 원문");
  expect(request.mock.calls[2]?.[0]).toBe(request.mock.calls[1]?.[0]);
  expect(screen.getByRole("dialog").getAttribute("open")).toBe("");
});

it("근거 URL의 실행 가능한 scheme을 거부하고 날짜 정밀도를 확대하지 않는다", async () => {
  request.mockResolvedValue(
    response({
      items: [
        {
          ...trace,
          source: { ...trace.source, canonical_url: "javascript:alert(1)" },
        },
      ],
      next_cursor: null,
    }),
  );
  await expect(
    fetchRelationEvidence("1", null, new AbortController().signal),
  ).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  const value = {
    publishedAt: "2026-08-01T00:00:00Z",
    precision: "YEAR",
  } as EvidenceTrace;
  expect(publicationLabel(value)).toBe("2026");
  expect(publicationLabel({ ...value, publishedAt: null })).toBe(
    "확인되지 않음",
  );
});

it("외부 URL이 없는 업로드 근거는 인용과 제목을 표시한다", async () => {
  request.mockResolvedValue(
    response({
      items: [
        {
          ...trace,
          source: { ...trace.source, canonical_url: null },
        },
      ],
      next_cursor: null,
    }),
  );
  render(<Flow />);
  fireEvent.click(screen.getByRole("button", { name: "HBF 연결 원문" }));
  await screen.findByText("원문 인용");
  expect(screen.getByText("발표 자료")).toBeTruthy();
  expect(screen.queryByRole("link", { name: /원문 기사 열기/ })).toBeNull();
});
