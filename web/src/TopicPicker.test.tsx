import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TopicPicker } from "./TopicPicker";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TopicPicker", () => {
  it("loads a separate Korean-sorted Topic list and keeps inactive rows selectable", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            {
              node_id: "9",
              topic_code: "INVESTMENT",
              canonical_display_name: "투자",
              is_active: false,
            },
            {
              node_id: "7",
              topic_code: "SEMICONDUCTOR",
              canonical_display_name: "반도체",
              is_active: true,
            },
          ],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSelect = vi.fn();

    render(<TopicPicker onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "주제 목록 열기" }));

    const list = await screen.findByRole("listbox", { name: "주제 목록" });
    const options = within(list).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "반도체",
      "투자신규 연결 중단",
    ]);

    fireEvent.click(options[1] as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith("9");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/topics",
      expect.any(Object),
    );
  });

  it("surfaces a retryable Topic list read failure and retries the same request", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: "PUBLICATION_NOT_READY", retryable: true },
          }),
          { status: 503, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            items: [
              {
                node_id: "7",
                topic_code: "SEMICONDUCTOR",
                canonical_display_name: "반도체",
                is_active: true,
              },
            ],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<TopicPicker onSelect={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "주제 목록 열기" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(
      "현재 이 영역의 공개 자료를 불러올 수 없습니다.",
    );
    const retry = within(alert).getByRole("button", { name: "다시 조회" });
    fireEvent.click(retry);

    const list = await screen.findByRole("listbox", { name: "주제 목록" });
    expect(within(list).getByRole("option", { name: "반도체" })).toBeTruthy();
    expect(
      screen.getByText("최신 공개 상태로 다시 불러왔습니다."),
    ).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/topics");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/v1/topics");
  });
});
