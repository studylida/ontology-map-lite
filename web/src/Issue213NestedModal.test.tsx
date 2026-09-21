import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReportDialog } from "./InsightPanel";
import { EvidenceDialog, type EvidenceSelection } from "./RelationPanel";

const request = vi.fn();
const response = (body: unknown, status = 200) => ({
  ok: status < 400,
  status,
  json: async () => body,
});

const sourceNode = {
  node_id: "1",
  name: "중심 회사",
  node_type: { code: "COMPANY", display_name: "회사" },
};
const otherNode = {
  node_id: "2",
  name: "상대 노드",
  node_type: { code: "TECHNOLOGY", display_name: "기술" },
};
const claim = {
  claim_id: "10",
  claim_text: "보고서 Claim",
  modality: "PREDICTION_OR_ESTIMATE",
  knowledge_state: "EVIDENCE_VERIFIED",
  evidence_group_count: 2,
  as_of_at: "2026-09-15T00:00:00Z",
  role: "KEY_CLAIM",
  connections: [
    {
      kind: "CONFLICT",
      target_id: "200",
      position: "SEPTEMBER",
      label: "같은 대상에 관한 엇갈리는 주장",
    },
    {
      kind: "RELATION",
      target_id: "100",
      position: "SUPPORT",
      label: "중심 회사 · 관련 · 상대 노드",
      relation: {
        relation_id: "100",
        display_name: "관련",
        directionality: "DIRECTED",
        source_node: sourceNode,
        target_node: otherNode,
        other_node: otherNode,
        stance: "SUPPORT",
      },
    },
  ],
};
const report = {
  items: [
    {
      report_id: "20",
      node_id: "1",
      title: "종합 보고서",
      summary: "요약",
      as_of_at: "2026-09-15T00:00:00Z",
      time_window: "RECENT_90_DAYS",
      evidence_group_count: 2,
      conclusion: "결론",
      caveat: null,
      sections: [
        {
          section_id: "21",
          title: "핵심 발견",
          synthesis: "해석",
          caveat: null,
          claims: [claim],
        },
      ],
    },
  ],
  next_cursor: null,
};
const claimTrace = {
  items: [
    {
      trace_id: "900",
      source: {
        title: "Claim 출처",
        publisher_name: "출처",
        published_at: "2026-09-10T00:00:00Z",
        published_precision: "DAY",
        canonical_url: "https://example.com/claim",
      },
      quote_text: "Claim 원문 근거",
      locator: { paragraph_number: 1, start_char: 0, end_char: 10 },
      period_role: "IN_WINDOW",
    },
  ],
  next_cursor: null,
};
const relationTrace = {
  items: [
    {
      item_key: "relation-trace-100",
      claim_text: "관계를 지지하는 Claim",
      modality: "FACT",
      stance: "SUPPORT",
      source: {
        title: "Relation 출처",
        publisher_name: "출처",
        published_at: "2026-09-11T00:00:00Z",
        published_precision: "DAY",
        canonical_url: "https://example.com/relation",
      },
      quote_text: "Relation 원문 근거",
      locator: { paragraph_number: 2, start_char: 11, end_char: 20 },
    },
  ],
  trace_count: 1,
  next_cursor: null,
};

function NestedFlow() {
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null);
  return (
    <>
      <ReportDialog
        nodeId="1"
        timeRange="90d"
        sectionId=""
        onClose={vi.fn()}
        onEvidence={setEvidence}
        onLocate={vi.fn()}
      />
      {evidence && (
        <EvidenceDialog
          key={evidence.id}
          selection={evidence}
          onClose={() => setEvidence(null)}
        />
      )}
    </>
  );
}

beforeEach(() => {
  request.mockReset();
  request.mockImplementation(async (path: string) => {
    if (path.includes("/insight-report")) return response(report);
    if (path.includes("/claims/10/evidence")) return response(claimTrace);
    if (path.includes("/relations/100/evidence"))
      return response(relationTrace);
    return response({ items: [], next_cursor: null });
  });
  vi.stubGlobal("fetch", request);
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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
  Reflect.deleteProperty(HTMLDialogElement.prototype, "close");
});

it("shows the report Claim connection after expanding its source details", async () => {
  render(<NestedFlow />);

  await screen.findByRole("heading", { name: "종합 보고서" });
  fireEvent.click(screen.getByRole("button", { name: /보고서 Claim/ }));
  expect(screen.getByRole("button", { name: "지도에서 강조" })).toBeTruthy();
});

it("keeps the report mounted, expanded and scrolled while nested Relation Evidence opens and restores opener focus", async () => {
  render(<NestedFlow />);

  await screen.findByRole("heading", { name: "종합 보고서" });
  const reportDialog = screen.getByRole("dialog");
  const claimDisclosure = screen.getByRole("button", { name: /보고서 Claim/ });
  fireEvent.click(claimDisclosure);
  await screen.findByText("Claim 원문 근거");

  reportDialog.scrollTop = 137;
  const evidenceOpener = screen.getByRole("button", {
    name: "상대 노드 관련 연결 원문 보기",
  });
  evidenceOpener.focus();
  fireEvent.click(evidenceOpener);

  await screen.findByText("Relation 원문 근거");
  expect(screen.getAllByRole("dialog")).toHaveLength(2);
  expect(screen.getByText("Claim 원문 근거")).toBeTruthy();
  expect(reportDialog.scrollTop).toBe(137);

  fireEvent.click(screen.getByRole("button", { name: "원문 창 닫기" }));
  await waitFor(() => expect(screen.getAllByRole("dialog")).toHaveLength(1));
  expect(screen.getByText("Claim 원문 근거")).toBeTruthy();
  expect(reportDialog.scrollTop).toBe(137);
  await waitFor(() => expect(document.activeElement).toBe(evidenceOpener));
});
