import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SourceAddDialog } from "./App";

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as Response;
}

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value() {
      this.setAttribute("open", "");
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, "close", {
    configurable: true,
    value() {
      this.removeAttribute("open");
    },
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
  Reflect.deleteProperty(HTMLDialogElement.prototype, "close");
});

it("저장된 본문을 유지하면서 READY까지 처리 상태를 갱신한다", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      response({
        source_document_id: "41",
        processing_job_id: "73",
        status: "QUEUED",
        body_hash: "abc123",
        normalized_body: "정규화된 본문",
      }),
    )
    .mockResolvedValueOnce(
      response({ status: "RUNNING", stage: "Claim 추출", elapsed_seconds: 2 }),
    )
    .mockResolvedValueOnce(response({ status: "READY", elapsed_seconds: 5 }));
  vi.stubGlobal("fetch", fetchMock);
  render(<SourceAddDialog onClose={vi.fn()} />);

  fireEvent.change(screen.getByLabelText("자료 파일"), {
    target: { files: [new File(["본문"], "source.txt")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "자료 저장" }));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText(/자료 처리 중 · Claim 추출/)).toBeTruthy();
  expect(screen.getByText("정규화된 본문")).toBeTruthy();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });

  expect(screen.getByText("자료가 지도에 공개되었습니다. · 5초")).toBeTruthy();
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "/api/v1/source-intake/41?processing_job_id=73",
  );
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it("상태 조회가 세 번 실패하면 멈추고 사용자가 다시 확인할 수 있다", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      response({
        source_document_id: "41",
        processing_job_id: "73",
        status: "QUEUED",
        body_hash: "abc123",
        normalized_body: "정규화된 본문",
      }),
    )
    .mockRejectedValueOnce(new Error("offline"))
    .mockRejectedValueOnce(new Error("offline"))
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(response({ status: "READY", elapsed_seconds: 5 }));
  vi.stubGlobal("fetch", fetchMock);
  render(<SourceAddDialog onClose={vi.fn()} />);

  fireEvent.change(screen.getByLabelText("자료 파일"), {
    target: { files: [new File(["본문"], "source.txt")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "자료 저장" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });

  expect(screen.getByText("처리 상태 확인을 중단했습니다.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "다시 확인" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText("자료가 지도에 공개되었습니다. · 5초")).toBeTruthy();
});
