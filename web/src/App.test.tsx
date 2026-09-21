import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { toExplorationView } from "./data";

vi.mock("./GraphCanvas", () => ({
  GraphCanvas: ({
    view,
    onReady,
    onSelect,
    onTransitionComplete,
    pendingNodeId,
    hiddenKinds,
    focusRequest,
    overviewRequest,
  }: {
    view: { centerId: string; nodes: { id: string }[] };
    pendingNodeId: string | null;
    hiddenKinds: readonly string[];
    focusRequest?: {
      nodeIds: string[];
      relationIds?: string[];
    } | null;
    overviewRequest?: { action: "show" | "restore" } | null;
    onReady: () => void;
    onSelect: (nodeId: string) => void;
    onTransitionComplete: (nodeId: string) => void;
  }) => (
    <section
      aria-label="동적 지식맵"
      data-pending-node={pendingNodeId ?? ""}
      data-hidden-kinds={hiddenKinds.join(",")}
      data-focus-node={focusRequest?.nodeIds.join(",") ?? ""}
      data-focus-relation={focusRequest?.relationIds?.join(",") ?? ""}
      data-overview-action={overviewRequest?.action ?? ""}
    >
      <span>{`요청 중심: ${view.centerId}`}</span>
      <button type="button" onClick={onReady}>
        그래프 준비 완료
      </button>
      <button
        type="button"
        onClick={() =>
          onSelect(
            view.nodes.find((node) => node.id !== view.centerId)?.id ??
              view.centerId,
          )
        }
      >
        다른 graph node 선택
      </button>
      <button type="button" onClick={() => onSelect(view.centerId)}>
        현재 graph node 선택
      </button>
      <button type="button" onClick={() => onTransitionComplete(view.centerId)}>
        중심 전환 완료
      </button>
    </section>
  ),
}));

const names: Record<string, string> = {
  "9223372036854775807": "SK하이닉스",
  "9223372036854775806": "HBF",
  "9223372036854775805": "UCIe",
};

function exploration(centerId = "9223372036854775807") {
  const neighborId =
    centerId === "9223372036854775807"
      ? "9223372036854775806"
      : "9223372036854775807";
  return {
    center_node_id: centerId,
    context_text: `${names[centerId]} 중심의 공개 관계입니다.`,
    context_is_current: false,
    period_highlights: [
      {
        claim_id: "claim-1",
        claim_text: "선택한 기간에 자료에서 확인한 최근 내용입니다.",
        modality: "FACT",
        evidence_group_count: 2,
        latest_published_at: "2026-09-18T00:00:00Z",
        latest_published_precision: "DAY",
      },
    ],
    graph: {
      nodes: [
        {
          node_id: centerId,
          name: names[centerId],
          node_type: { code: "TECHNOLOGY", display_name: "기술" },
          tier: "CENTER",
          activity_evidence_group_count: 6,
        },
        {
          node_id: neighborId,
          name: names[neighborId],
          node_type: { code: "TECHNOLOGY", display_name: "기술" },
          tier: "DIRECT",
          activity_evidence_group_count: 3,
        },
      ],
      relations: [
        {
          relation_id: `relation-${centerId}`,
          source_node_id: centerId,
          target_node_id: neighborId,
          relation_type_display_name: "관련 기술",
          directionality: "SYMMETRIC",
          supporting_evidence_group_count: 3,
          has_conflict: false,
        },
      ],
    },
    recommendations: [
      {
        target_node: {
          node_id: neighborId,
          name: names[neighborId],
          node_type: { code: "TECHNOLOGY", display_name: "기술" },
        },
        path: [
          {
            relation_id: `relation-${centerId}`,
            source_node_id: centerId,
            target_node_id: neighborId,
            source_node_name: names[centerId],
            target_node_name: names[neighborId],
            relation_type_display_name: "관련 기술",
            directionality: "SYMMETRIC",
          },
        ],
        reason_code: "DIRECT",
        via_node_id: null,
        supporting_evidence_group_count: 3,
      },
    ],
    followup_questions: [
      {
        slot: 1,
        question_text: `${names[neighborId]} 중심으로 보기`,
        target_node_id: neighborId,
      },
      {
        slot: 2,
        question_text: `${names[centerId]} 다시 보기`,
        target_node_id: centerId,
      },
    ],
  };
}

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function searchResults(
  items = [
    {
      node_id: "9223372036854775806",
      name: "HBF",
      node_type: { code: "TECHNOLOGY", display_name: "기술" },
    },
    {
      node_id: "9223372036854775805",
      name: "UCIe",
      node_type: { code: "TECHNOLOGY", display_name: "기술" },
    },
  ],
) {
  return { items };
}

function searchInput(): HTMLInputElement {
  return screen.getByRole("combobox") as HTMLInputElement;
}

async function enterSearch(query: string) {
  fireEvent.change(searchInput(), { target: { value: query } });
  expect(screen.getByText("검색 결과를 불러오는 중입니다.")).toBeTruthy();
  await screen.findByRole("listbox");
}

const fetchMock = vi.fn();

describe("exploration API 화면", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_DEFAULT_CENTER_NODE_ID", "9223372036854775807");
    vi.stubGlobal("fetch", (input: string, init?: RequestInit) => {
      if (/\/nodes\/[^/]+\/(relations|questions)/.test(input))
        return Promise.resolve(response({ items: [], next_cursor: null }));
      if (input.includes("/peripheral?"))
        return Promise.resolve(
          response({ graph: { nodes: [], relations: [] }, next_cursor: null }),
        );
      return fetchMock(input, init);
    });
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input: string) => {
      if (input.startsWith("/api/v1/nodes/search?")) {
        return response(searchResults());
      }
      const centerId = input.match(/exploration\/([^?]+)/)?.[1];
      return response(exploration(centerId));
    });
    window.history.replaceState({}, "", "/?range=90d");
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ matches: true }),
    });
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
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("기본 중심을 한 번 요청하고 문자열 bigint ID 응답을 표시한다", async () => {
    render(<App />);
    expect(screen.getByLabelText("탐색 데이터 불러오는 중")).toBeTruthy();
    expect(
      await screen.findByRole("heading", { name: "SK하이닉스" }),
    ).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/exploration/9223372036854775807?time_window=RECENT_90_DAYS",
    );
    expect(searchInput().disabled).toBe(false);
    expect(screen.queryByText("Evidence Trace")).toBeNull();
    expect(screen.getByText("SK하이닉스 중심의 공개 관계입니다.")).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "최근 이력 요약" }),
    ).toBeTruthy();
    expect(
      screen.getByText("선택한 기간에 자료에서 확인한 최근 내용입니다."),
    ).toBeTruthy();
    expect(screen.getByRole("tab", { name: "개요" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "기록" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "인사이트" })).toBeTruthy();
  });

  it("자료 추가 창에서 파일을 선택하거나 놓아도 API를 호출하지 않는다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    const addButton = screen.getByRole("button", { name: "자료 추가" });
    const callsBeforeOpen = fetchMock.mock.calls.length;

    addButton.focus();
    fireEvent.click(addButton);
    const dialog = screen.getByRole("dialog", { name: "자료 추가" });
    expect(
      within(dialog).getByText("자료를 추가하면 백엔드가 정규화해 저장합니다."),
    ).toBeTruthy();
    const input = within(dialog).getByLabelText("자료 파일");
    const firstFile = new File(["첫 번째 본문"], "article.txt", {
      type: "text/plain",
    });
    fireEvent.change(input, { target: { files: [firstFile] } });
    expect(within(dialog).getByRole("status").textContent).toContain(
      "선택한 파일: article.txt",
    );

    const dropzone = within(dialog)
      .getByText("파일을 끌어다 놓거나 선택하세요.")
      .closest("label");
    const secondFile = new File(["두 번째 본문"], "report.pdf", {
      type: "application/pdf",
    });
    fireEvent.drop(dropzone as HTMLElement, {
      dataTransfer: { files: [secondFile] },
    });
    expect(within(dialog).getByRole("status").textContent).toContain(
      "선택한 파일: report.pdf",
    );
    expect(fetchMock).toHaveBeenCalledTimes(callsBeforeOpen);

    fireEvent.click(
      within(dialog).getByRole("button", { name: "자료 추가 창 닫기" }),
    );
    expect(screen.queryByRole("dialog", { name: "자료 추가" })).toBeNull();
    expect(document.activeElement).toBe(addButton);
  });

  it("자료 추가 왼쪽의 JEV 검증 버튼이 pre-promotion 창을 연다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    const jevButton = screen.getByRole("button", { name: "JEV 검증" });
    const addButton = screen.getByRole("button", { name: "자료 추가" });

    expect(
      jevButton.compareDocumentPosition(addButton) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    fireEvent.click(jevButton);
    const dialog = screen.getByRole("dialog", { name: "JEV 검증" });
    expect(
      within(dialog).getByText(
        "DB 저장 · OpenAI 호출 · promotion · publication 없음",
      ),
    ).toBeTruthy();
  });

  it("유형을 여러 개 선택·해제해도 API를 다시 요청하거나 중심을 이동하지 않는다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    fireEvent.click(screen.getByRole("button", { name: "범례" }));
    const before = fetchMock.mock.calls.length;
    const url = window.location.href;
    const filterMessage = "유형 필터로 노드가 숨겨져 있습니다.";
    expect(screen.queryByText(filterMessage)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "전체 해제" }));
    const notice = screen.getByText(filterMessage).parentElement;
    fireEvent.click(notice?.querySelector("button") as HTMLButtonElement);
    expect(screen.queryByText(filterMessage)).toBeNull();
    expect(
      screen.getByRole("region", { name: "동적 지식맵" }).dataset.hiddenKinds,
    ).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "전체 해제" }));
    for (const name of ["사람", "회사", "기술", "주제", "사건"])
      expect(
        screen.getByRole("button", { name }).getAttribute("aria-pressed"),
      ).toBe("false");
    expect(screen.queryByText("노드 유형 · 전체 표시")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "사람" }));
    fireEvent.click(
      screen
        .getByRole("button", { name: "회사" })
        .querySelector("i") as HTMLElement,
    );
    expect(
      screen.getByRole("button", { name: "회사" }).getAttribute("aria-pressed"),
    ).toBe("true");
    fireEvent.click(
      screen.getByRole("button", { name: "범례 · 필터 적용 중" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "범례 · 필터 적용 중" }),
    );
    expect(
      screen.getByRole("region", { name: "동적 지식맵" }).dataset.hiddenKinds,
    ).toBe("TECHNOLOGY,TOPIC,EVENT");
    fireEvent.click(
      within(screen.getByLabelText("지식맵 범례")).getByRole("button", {
        name: "전체 표시",
      }),
    );
    expect(
      screen.getByRole("region", { name: "동적 지식맵" }).dataset.hiddenKinds,
    ).toBe("");
    expect(fetchMock.mock.calls).toHaveLength(before);
    expect(window.location.href).toBe(url);
  });

  it("graph node를 선택하면 aggregate 한 번으로 새 중심을 전환한다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    fireEvent.click(
      screen.getByRole("button", { name: "다른 graph node 선택" }),
    );
    await screen.findByText("요청 중심: 9223372036854775806");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "SK하이닉스" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "중심 전환 완료" }));
    expect(await screen.findByRole("heading", { name: "HBF" })).toBeTruthy();
  });

  it("브랜드 버튼으로 선택 기간을 유지해 기본 중심으로 돌아가고 현재 홈에서는 다시 요청하지 않는다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    const home = screen.getByRole("button", {
      name: "비스텔리젼스 홈으로 이동",
    });
    expect(home.tagName).toBe("BUTTON");

    fireEvent.click(screen.getByRole("button", { name: "최근 1년" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.click(
      screen.getByRole("button", { name: "다른 graph node 선택" }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    fireEvent.click(screen.getByRole("button", { name: "중심 전환 완료" }));
    await screen.findByRole("heading", { name: "HBF" });

    home.focus();
    expect(document.activeElement).toBe(home);
    fireEvent.click(home);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe(
      "/api/v1/exploration/9223372036854775807?time_window=RECENT_1_YEAR",
    );
    fireEvent.click(screen.getByRole("button", { name: "중심 전환 완료" }));
    expect(
      await screen.findByRole("heading", { name: "SK하이닉스" }),
    ).toBeTruthy();
    expect(new URL(window.location.href).searchParams.get("range")).toBe("1y");

    fireEvent.click(home);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("추천 선택은 aggregate를 한 번 요청하고 이전 이동형 질문은 표시하지 않는다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    fireEvent.click(screen.getByRole("button", { name: /HBF.*확인된 관계/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "중심 전환 완료" }));
    await screen.findByRole("heading", { name: "HBF" });
    expect(
      screen.queryByRole("button", { name: "SK하이닉스 중심으로 보기" }),
    ).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("새 선택이 이전 응답과 이전 전환 완료를 무효화한다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    let finishOld: (value: Response) => void = () => {};
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finishOld = resolve;
        }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "다른 graph node 선택" }),
    );
    expect(
      screen
        .getByRole("region", { name: "동적 지식맵" })
        .getAttribute("data-pending-node"),
    ).toBe("9223372036854775806");
    fireEvent.click(
      screen.getByRole("button", { name: "현재 graph node 선택" }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    await act(async () =>
      finishOld(response(exploration("9223372036854775806"))),
    );
    fireEvent.click(screen.getByRole("button", { name: "중심 전환 완료" }));
    expect(screen.getByRole("heading", { name: "SK하이닉스" })).toBeTruthy();
    expect(screen.getByText("요청 중심: 9223372036854775807")).toBeTruthy();
    expect(
      screen
        .getByRole("region", { name: "동적 지식맵" })
        .getAttribute("data-pending-node"),
    ).toBe("");
  });

  it("시간 범위를 바꾸면 같은 중심의 1년 aggregate를 한 번 요청한다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    fireEvent.click(screen.getByRole("button", { name: "최근 1년" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/exploration/9223372036854775807?time_window=RECENT_1_YEAR",
    );
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "최근 1년" })
          .getAttribute("aria-pressed"),
      ).toBe("true"),
    );
  });

  it("전체 기간은 이력 조회만 요청하고 기간별 질문과 인사이트를 안내한다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });

    fireEvent.click(screen.getByRole("button", { name: "전체 기간" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/exploration/9223372036854775807?time_window=ALL_TIME",
    );
    expect(new URL(window.location.href).searchParams.get("range")).toBe("all");
    expect(
      screen.getByText(
        "최근 90일 또는 최근 1년을 선택하면 질문과 답변을 볼 수 있습니다.",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "인사이트" }));
    expect(
      screen.getByText(
        "최근 90일 또는 최근 1년을 선택하면 기간별 인사이트를 볼 수 있습니다.",
      ),
    ).toBeTruthy();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        /\/(questions|insight-report)(\?|$)/.test(input),
      ),
    ).toBe(false);
  });

  it("지도 강조는 중심·주소·조회 횟수를 유지한다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    const before = fetchMock.mock.calls.length;
    const url = window.location.href;

    fireEvent.click(
      screen.getByRole("button", { name: "추천 노드 지도에서 강조" }),
    );

    const map = screen.getByRole("region", { name: "동적 지식맵" });
    expect(map.dataset.focusNode).toBe(
      "9223372036854775806,9223372036854775807",
    );
    expect(map.dataset.focusRelation).toBe("relation-9223372036854775807");
    expect(fetchMock).toHaveBeenCalledTimes(before);
    expect(window.location.href).toBe(url);
    expect(screen.getByRole("heading", { name: "SK하이닉스" })).toBeTruthy();
  });

  it("404를 재시도 불가 상태로 표시한다", async () => {
    fetchMock.mockResolvedValue(
      response({ error: { code: "NODE_NOT_FOUND", retryable: false } }, 404),
    );
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "요청한 대상을 찾을 수 없습니다.",
    );
    expect(screen.queryByRole("button", { name: "다시 조회" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "기본 탐색으로 이동" }),
    ).toBeNull();
  });

  it("503 PUBLICATION_NOT_READY에서 재시도해 성공한다", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response(
          { error: { code: "PUBLICATION_NOT_READY", retryable: true } },
          503,
        ),
      )
      .mockResolvedValueOnce(response(exploration()));
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "현재 이 대상의 공개 탐색 자료를 불러올 수 없습니다.",
    );
    fireEvent.click(screen.getByRole("button", { name: "다시 조회" }));
    expect(
      await screen.findByRole("heading", { name: "SK하이닉스" }),
    ).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("network error에서 재시도해 성공한다", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("offline"));
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "탐색 데이터를 불러오지 못했습니다.",
    );
    fetchMock.mockResolvedValueOnce(response(exploration()));
    fireEvent.click(screen.getByRole("button", { name: "다시 조회" }));
    expect(
      await screen.findByRole("heading", { name: "SK하이닉스" }),
    ).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("성공 응답에 graph node가 없으면 empty 상태를 표시한다", async () => {
    const empty = exploration();
    empty.graph.nodes = [];
    empty.graph.relations = [];
    empty.recommendations = [];
    empty.followup_questions = [];
    fetchMock.mockResolvedValue(response(empty));
    render(<App />);
    expect(
      await screen.findByText("표시할 탐색 데이터가 없습니다."),
    ).toBeTruthy();
  });

  it("별칭 검색을 limit 5로 요청하고 응답 순서와 문자열 ID를 유지한다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });

    await enterSearch("HBF");

    const searchCall = fetchMock.mock.calls.find(([input]) =>
      input.startsWith("/api/v1/nodes/search?"),
    );
    expect(searchCall?.[0]).toBe("/api/v1/nodes/search?q=HBF&limit=5");
    const options = screen.getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "HBF기술",
      "UCIe기술",
    ]);
    expect(searchInput().getAttribute("aria-activedescendant")).toContain(
      "9223372036854775806",
    );
    fireEvent.keyDown(searchInput(), { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("방향키와 Enter로 후보를 선택해 exploration을 한 번 요청한다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    await enterSearch("HBF");

    fireEvent.keyDown(searchInput(), { key: "ArrowDown" });
    expect(
      screen
        .getByRole("option", { name: /UCIe/ })
        .getAttribute("aria-selected"),
    ).toBe("true");
    fireEvent.keyDown(searchInput(), { key: "ArrowUp" });
    fireEvent.keyDown(searchInput(), { key: "Enter" });

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          input.includes("/exploration/"),
        ),
      ).toHaveLength(2),
    );
    expect(searchInput().value).toBe("HBF");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain(
      "/api/v1/exploration/9223372036854775806",
    );
  });

  it("마우스로 후보를 선택해 preferred name을 남기고 exploration을 한 번 요청한다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    await enterSearch("interconnect");

    fireEvent.click(screen.getByRole("option", { name: /UCIe/ }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([input]) =>
          input.includes("/exploration/"),
        ),
      ).toHaveLength(2),
    );
    expect(searchInput().value).toBe("UCIe");
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain(
      "/api/v1/exploration/9223372036854775805",
    );
  });

  it("공백 검색은 요청하지 않고 결과를 지운다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    await enterSearch("HBF");
    const callsBeforeBlank = fetchMock.mock.calls.length;

    fireEvent.change(searchInput(), { target: { value: "   " } });

    expect(screen.queryByRole("listbox")).toBeNull();
    await new Promise((resolve) => window.setTimeout(resolve, 300));
    expect(fetchMock).toHaveBeenCalledTimes(callsBeforeBlank);
  });

  it("검색 빈 결과와 재시도 가능한 오류를 표시하고 다시 요청한다", async () => {
    let searchAttempt = 0;
    fetchMock.mockImplementation(async (input: string) => {
      if (!input.startsWith("/api/v1/nodes/search?")) {
        const centerId = input.match(/exploration\/([^?]+)/)?.[1];
        return response(exploration(centerId));
      }
      searchAttempt += 1;
      if (searchAttempt === 1) return response(searchResults([]));
      if (searchAttempt === 2) throw new TypeError("offline");
      return response(searchResults());
    });
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });

    fireEvent.change(searchInput(), { target: { value: "없음" } });
    expect(await screen.findByText("검색 결과가 없습니다.")).toBeTruthy();
    fireEvent.change(searchInput(), { target: { value: "HBF" } });
    expect(
      await screen.findByText("검색 결과를 불러오지 못했습니다."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(await screen.findByRole("listbox")).toBeTruthy();
    expect(searchAttempt).toBe(3);
  });

  it("늦게 도착한 이전 검색 응답이 최신 결과를 덮지 않는다", async () => {
    let resolveFirst: ((value: Response) => void) | undefined;
    let resolveSecond: ((value: Response) => void) | undefined;
    let searchAttempt = 0;
    fetchMock.mockImplementation((input: string) => {
      if (!input.startsWith("/api/v1/nodes/search?")) {
        const centerId = input.match(/exploration\/([^?]+)/)?.[1];
        return Promise.resolve(response(exploration(centerId)));
      }
      searchAttempt += 1;
      return new Promise<Response>((resolve) => {
        if (searchAttempt === 1) resolveFirst = resolve;
        else resolveSecond = resolve;
      });
    });
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });

    fireEvent.change(searchInput(), { target: { value: "old" } });
    await waitFor(() => expect(searchAttempt).toBe(1));
    fireEvent.change(searchInput(), { target: { value: "new" } });
    await waitFor(() => expect(searchAttempt).toBe(2));
    await act(async () => {
      resolveSecond?.(response(searchResults(searchResults().items.slice(1))));
    });
    expect(await screen.findByRole("option", { name: /UCIe/ })).toBeTruthy();

    await act(async () => {
      resolveFirst?.(
        response(searchResults(searchResults().items.slice(0, 1))),
      );
    });
    expect(screen.queryByRole("option", { name: /HBF/ })).toBeNull();
    expect(screen.getByRole("option", { name: /UCIe/ })).toBeTruthy();
  });

  it("center가 없어도 오류가 아니며 Node 검색과 주제로 재진입할 수 있다", async () => {
    vi.stubEnv("VITE_DEFAULT_CENTER_NODE_ID", "");
    window.history.replaceState({}, "", "/?range=90d");
    render(<App />);
    expect(
      (
        await screen.findAllByText(
          "탐색할 대상을 검색하거나 주제를 선택해 주세요.",
        )
      )[0],
    ).toBeTruthy();
    expect(searchInput().disabled).toBe(false);
    expect(screen.getByRole("button", { name: "주제 목록 열기" })).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("초기 INVALID_REQUEST는 retry 없는 요청 오류로 남고 재진입 동선을 유지한다", async () => {
    fetchMock.mockResolvedValue(
      response({ error: { code: "INVALID_REQUEST", retryable: false } }, 422),
    );
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "요청을 확인할 수 없습니다. 다른 대상을 검색하거나 주제를 선택해 주세요.",
    );
    expect(screen.queryByRole("button", { name: "다시 조회" })).toBeNull();
    expect(searchInput().disabled).toBe(false);
    expect(screen.getByRole("button", { name: "주제 목록 열기" })).toBeTruthy();
  });

  it("실패 center와 다른 배포 기본 center가 있을 때만 기본 탐색 escape를 제공한다", async () => {
    window.history.replaceState(
      {},
      "",
      "/?center=9223372036854775806&range=90d",
    );
    fetchMock
      .mockResolvedValueOnce(
        response({ error: { code: "NODE_NOT_FOUND", retryable: false } }, 404),
      )
      .mockResolvedValueOnce(response(exploration("9223372036854775807")));
    render(<App />);
    const defaultEscape = await screen.findByRole("button", {
      name: "기본 탐색으로 이동",
    });
    fireEvent.click(defaultEscape);
    expect(
      await screen.findByRole("heading", { name: "SK하이닉스" }),
    ).toBeTruthy();
    expect(new URL(window.location.href).searchParams.get("center")).toBe(
      "9223372036854775807",
    );
  });

  it("A에서 B network read가 실패하면 A와 trail을 유지하고 B를 commit하지 않는다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    fetchMock.mockRejectedValueOnce(new TypeError("offline"));
    fireEvent.click(
      screen.getByRole("button", { name: "다른 graph node 선택" }),
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "HBF 대상을 열 수 없습니다.",
    );
    expect(screen.getByRole("heading", { name: "SK하이닉스" })).toBeTruthy();
    expect(
      within(
        screen.getByRole("navigation", { name: "최근 탐색 경로" }),
      ).queryByRole("button", { name: "HBF" }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "다시 조회" })).toBeTruthy();
  });

  it("A에서 B 503 read가 실패해도 A를 유지하고 공개 상태를 추측하지 않는다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    fetchMock.mockResolvedValueOnce(
      response(
        { error: { code: "PUBLICATION_NOT_READY", retryable: true } },
        503,
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "다른 graph node 선택" }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("HBF 대상을 열 수 없습니다.");
    expect(alert.textContent).toContain(
      "현재 이 대상의 공개 탐색 자료를 불러올 수 없습니다.",
    );
    expect(alert.textContent).not.toMatch(/준비 중|복구 중|생성 중|곧 제공/);
    expect(screen.getByRole("heading", { name: "SK하이닉스" })).toBeTruthy();
  });

  it("기간 read 실패는 성공한 기간을 유지하고 retry 성공 뒤에만 새 기간을 commit한다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    fetchMock.mockResolvedValueOnce(
      response(
        { error: { code: "PUBLICATION_NOT_READY", retryable: true } },
        503,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "최근 1년" }));
    await screen.findByRole("alert");
    expect(
      screen
        .getByRole("button", { name: "최근 90일" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen
        .getByRole("button", { name: "최근 1년" })
        .getAttribute("aria-pressed"),
    ).toBe("false");
    expect(new URL(window.location.href).searchParams.get("range")).toBe("90d");

    fetchMock.mockResolvedValueOnce(response(exploration()));
    fireEvent.click(screen.getByRole("button", { name: "다시 조회" }));
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "최근 1년" })
          .getAttribute("aria-pressed"),
      ).toBe("true"),
    );
    expect(new URL(window.location.href).searchParams.get("range")).toBe("1y");
    expect(
      await screen.findByText("최신 공개 상태로 다시 불러왔습니다."),
    ).toBeTruthy();
  });

  it("popstate target read 실패는 현재 화면과 history 의미를 유지하고 retry 성공 때만 target을 반영한다", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "SK하이닉스" });
    fireEvent.click(
      screen.getByRole("button", { name: "다른 graph node 선택" }),
    );
    await screen.findByText("요청 중심: 9223372036854775806");
    fireEvent.click(screen.getByRole("button", { name: "중심 전환 완료" }));
    await screen.findByRole("heading", { name: "HBF" });

    const push = vi.spyOn(window.history, "pushState");
    fetchMock.mockResolvedValueOnce(
      response(
        { error: { code: "PUBLICATION_NOT_READY", retryable: true } },
        503,
      ),
    );
    window.history.replaceState(
      {},
      "",
      "/?center=9223372036854775807&range=90d",
    );
    window.dispatchEvent(new PopStateEvent("popstate"));
    await screen.findByRole("alert");
    expect(screen.getByRole("heading", { name: "HBF" })).toBeTruthy();
    expect(push).not.toHaveBeenCalled();

    fetchMock.mockResolvedValueOnce(
      response(exploration("9223372036854775807")),
    );
    fireEvent.click(screen.getByRole("button", { name: "다시 조회" }));
    await screen.findByText("요청 중심: 9223372036854775807");
    fireEvent.click(screen.getByRole("button", { name: "중심 전환 완료" }));
    expect(
      await screen.findByRole("heading", { name: "SK하이닉스" }),
    ).toBeTruthy();
    expect(push).not.toHaveBeenCalled();
  });
});

it("중심에 닿은 간선만 직접 관계로 강조하고 직접 이웃끼리의 선은 구분한다", () => {
  const payload = exploration();
  const center = payload.graph.nodes[0];
  const neighbor = payload.graph.nodes[1];
  const edge = payload.graph.relations[0];
  if (!center || !neighbor || !edge)
    throw new Error("검토 graph가 비었습니다.");
  payload.graph.nodes.push({ ...neighbor, node_id: "3" });
  payload.graph.relations.push(
    {
      ...edge,
      relation_id: "neighbors",
      source_node_id: neighbor.node_id,
      target_node_id: "3",
    },
    {
      ...edge,
      relation_id: "incoming",
      source_node_id: "3",
      target_node_id: center.node_id,
    },
  );
  expect(toExplorationView(payload).relations.map((r) => r.tier)).toEqual([
    "direct",
    "twoHop",
    "direct",
  ]);
});

it("이전 탐색 응답에 최신 상태와 기간 하이라이트가 없어도 연다", () => {
  const payload = exploration();
  delete (payload as Partial<typeof payload>).context_is_current;
  delete (payload as Partial<typeof payload>).period_highlights;
  expect(toExplorationView(payload)).toMatchObject({
    contextIsCurrent: false,
    periodHighlights: [],
  });
});

it("미리보기 헤더에서 라이트와 다크 모드를 전환한다", () => {
  const { unmount } = render(<App designPreview />);
  const toggle = screen.getByRole("button", {
    name: "라이트 모드",
    hidden: true,
  });
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(document.documentElement.dataset.theme).toBe("light");
  fireEvent.click(toggle);
  expect(document.documentElement.dataset.theme).toBe("dark");
  unmount();
  expect(document.documentElement.dataset.theme).toBeUndefined();
});
