import { useCallback, useEffect, useId, useState } from "react";
import styles from "./App.module.css";
import { APIRequestError, type TimeRange } from "./data";
import { PeriodNote } from "./PanelEvidence";
import { PageNotice } from "./RelationPanel";
import {
  fetchPanelAnswer213,
  fetchPanelQuestions213,
  fetchPanelTraces213,
  type PanelClaim213,
  type PanelQuestion213,
  type PanelTrace213,
} from "./read213";
import { useCursorPage } from "./useCursorPage";

type Props = {
  nodeId: string;
  range: TimeRange;
  onReport: (sectionId: string) => void;
  onRecordPeek: (claims: PanelClaim213[]) => void;
};

const roleOrder = {
  KEY_CLAIM: 0,
  SUPPORTING_CLAIM: 1,
  CONTRASTING_CLAIM: 2,
} as const;

function orderedClaims(claims: PanelClaim213[]): PanelClaim213[] {
  return claims
    .map((claim, index) => ({ claim, index }))
    .sort(
      (a, b) =>
        (a.claim.role === null ? 3 : roleOrder[a.claim.role]) -
          (b.claim.role === null ? 3 : roleOrder[b.claim.role]) ||
        a.index - b.index,
    )
    .map(({ claim }) => claim);
}

function bestTrace(traces: PanelTrace213[]): PanelTrace213 | undefined {
  const periodOrder = { IN_WINDOW: 0, BACKGROUND: 1, UNKNOWN: 2 } as const;
  return traces.toSorted(
    (a, b) =>
      periodOrder[a.periodRole] - periodOrder[b.periodRole] ||
      (b.publishedAt ?? "").localeCompare(a.publishedAt ?? "") ||
      a.key.localeCompare(b.key),
  )[0];
}

function AnswerSource({
  nodeId,
  range,
  claims,
  onRecordPeek,
}: Pick<Props, "nodeId" | "range" | "onRecordPeek"> & {
  claims: PanelClaim213[];
}) {
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<APIRequestError | null>(null);
  const [source, setSource] = useState<PanelTrace213 | null>(null);
  const [hasMore, setHasMore] = useState(false);
  useEffect(() => {
    void attempt;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setSource(null);
    void (async () => {
      try {
        for (const claim of orderedClaims(claims)) {
          const page = await fetchPanelTraces213(
            nodeId,
            claim,
            range,
            null,
            controller.signal,
          );
          if (controller.signal.aborted) return;
          const selected = bestTrace(page.items);
          if (!selected) continue;
          setSource(selected);
          setHasMore(
            claims.length > 1 ||
              page.items.length > 1 ||
              page.nextCursor !== null,
          );
          return;
        }
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof APIRequestError
            ? cause
            : new APIRequestError("NETWORK_ERROR", 0, true),
        );
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [attempt, claims, nodeId, range]);

  if (loading)
    return (
      <p className={styles.answerSourceStatus} role="status">
        원문 링크를 불러오는 중입니다.
      </p>
    );
  if (error)
    return (
      <button
        type="button"
        className={styles.answerSourceRetry}
        onClick={() => setAttempt((value) => value + 1)}
      >
        원문 링크 다시 불러오기
      </button>
    );
  if (!source) return null;
  return (
    <div className={styles.answerSource}>
      {source.url === null ? (
        <span>근거 자료 · {source.title}</span>
      ) : (
        <a href={source.url} target="_blank" rel="noopener noreferrer">
          원문 · {source.title}
        </a>
      )}
      {hasMore && (
        <button
          type="button"
          aria-label="이 답변에 사용한 기록 더 보기"
          title="이 답변에 사용한 기록 더 보기"
          onClick={() => onRecordPeek(claims)}
        >
          +
        </button>
      )}
    </div>
  );
}

function Answer({
  questionId,
  nodeId,
  range,
  onReport,
  onRecordPeek,
}: Props & { questionId: string }) {
  const page = useCursorPage(questionId, fetchPanelAnswer213);
  const answer = page.items[0];
  return (
    <div className={styles.questionAnswer}>
      <PageNotice {...page} empty={!answer} onRetry={page.retry} />
      {answer && (
        <>
          <p>{answer.answer}</p>
          {answer.caveat && (
            <p className={styles.panelCaveat}>{answer.caveat}</p>
          )}
          <PeriodNote range={range} asOf={answer.asOf} />
          <AnswerSource
            nodeId={nodeId}
            range={range}
            claims={answer.claims}
            onRecordPeek={onRecordPeek}
          />
          {answer.sectionId && (
            <button
              type="button"
              onClick={() => onReport(answer.sectionId as string)}
            >
              관련 분석 읽기
            </button>
          )}
        </>
      )}
    </div>
  );
}

function Question({
  question,
  ...props
}: Props & { question: PanelQuestion213 }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <article className={styles.questionCard}>
      <button
        type="button"
        className={styles.panelDisclosure}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
      >
        <span>{question.text}</span>
        <span>{open ? "접기" : "답변 보기"}</span>
      </button>
      <div id={id} hidden={!open}>
        {open && <Answer {...props} questionId={question.id} />}
      </div>
    </article>
  );
}

function GeneratedQuestionPanel(props: Props) {
  const { nodeId, range } = props;
  const fetchPage = useCallback(
    (id: string, cursor: string | null, signal: AbortSignal) =>
      fetchPanelQuestions213(id, range, cursor, signal),
    [range],
  );
  const page = useCursorPage(nodeId, fetchPage);
  return (
    <section className={styles.followupSection} aria-label="더 알아보기">
      <h2>더 알아보기</h2>
      <PeriodNote range={range} />
      {page.items.map((question) => (
        <Question key={question.id} question={question} {...props} />
      ))}
      <PageNotice
        {...page}
        empty={!page.items.length}
        additional={page.items.length > 0}
        emptyMessage="이 기간에는 공개된 질문이 없습니다."
        onRetry={page.retry}
      />
      {page.nextCursor && (
        <button
          type="button"
          onClick={page.more}
          disabled={page.loading || !!page.error}
        >
          질문 4개 더 보기
        </button>
      )}
    </section>
  );
}

export function QuestionPanel(props: Props) {
  if (props.range === "all")
    return (
      <section className={styles.followupSection} aria-label="더 알아보기">
        <h2>더 알아보기</h2>
        <p className={styles.panelMeta}>
          최근 90일 또는 최근 1년을 선택하면 질문과 답변을 볼 수 있습니다.
        </p>
      </section>
    );
  return <GeneratedQuestionPanel {...props} />;
}
