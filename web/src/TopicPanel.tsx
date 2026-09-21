import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./App.module.css";
import {
  APIRequestError,
  fetchPanelReport,
  type KnowledgeNode,
  type KnowledgeRelation,
  relationPathLabel,
  type TimeRange,
  timeRangeLabel,
} from "./data";
import { type EvidenceSelection, PageNotice } from "./RelationPanel";
import topicStyles from "./Topic.module.css";
import type { TopicExplorationView } from "./topicData";

interface TopicPanelProps {
  view: TopicExplorationView;
  timeRange: TimeRange;
  onClose: () => void;
  onSelect: (nodeId: string) => void;
  onLocate: (nodeId: string, relationId?: string) => void;
  onSelectInsight: (nodeId: string) => void;
  onEvidence?: (selection: EvidenceSelection) => void;
}

function byName(left: KnowledgeNode, right: KnowledgeNode) {
  return left.name.localeCompare(right.name, "ko");
}

function byRecentEvidence(left: KnowledgeNode, right: KnowledgeNode) {
  return (
    right.activityEvidenceGroupCount - left.activityEvidenceGroupCount ||
    byName(left, right)
  );
}

function TopicInsightTitle({
  nodeId,
  timeRange,
  onSelect,
}: {
  nodeId: string;
  timeRange: TimeRange;
  onSelect: () => void;
}) {
  const [title, setTitle] = useState<string | null>(null);
  const [error, setError] = useState<APIRequestError | null>(null);
  const [retrySuccess, setRetrySuccess] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (retry = false) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      setTitle(null);
      setError(null);
      setRetrySuccess(false);
      try {
        const page = await fetchPanelReport(
          nodeId,
          timeRange,
          false,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setTitle(page.items[0]?.title ?? null);
        if (retry) setRetrySuccess(true);
      } catch (caught) {
        if (controller.signal.aborted) return;
        setError(
          caught instanceof APIRequestError
            ? caught
            : new APIRequestError("NETWORK_ERROR", 0, true),
        );
      }
    },
    [nodeId, timeRange],
  );

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  return (
    <>
      <PageNotice
        loading={false}
        error={error}
        empty={false}
        retrySuccess={retrySuccess}
        onRetry={() => void load(true)}
      />
      {title && (
        <button
          type="button"
          className={topicStyles.topicInsightLink}
          onClick={onSelect}
        >
          {title}
          <span aria-hidden="true"> ›</span>
        </button>
      )}
    </>
  );
}

function MemberActions({
  node,
  relation,
  topicName,
  onLocate,
  onEvidence,
}: {
  node: KnowledgeNode;
  relation: KnowledgeRelation | undefined;
  topicName: string;
  onLocate: (nodeId: string, relationId?: string) => void;
  onEvidence: (selection: EvidenceSelection) => void;
}) {
  return (
    <span className={topicStyles.topicActions}>
      <button type="button" onClick={() => onLocate(node.id, relation?.id)}>
        지도에서 강조
      </button>
      {relation && (
        <button
          type="button"
          onClick={() =>
            onEvidence({
              id: relation.id,
              label: relationPathLabel(
                relation.source === node.id ? node.name : topicName,
                relation.label,
                relation.source === node.id ? topicName : node.name,
                relation.directionality,
              ),
            })
          }
        >
          연결 원문
        </button>
      )}
    </span>
  );
}

export function TopicPanel({
  view,
  timeRange,
  onClose,
  onSelect,
  onLocate,
  onSelectInsight,
  onEvidence = () => undefined,
}: TopicPanelProps) {
  const { rich, remainingRecent, older, groups, relationByMember } =
    useMemo(() => {
      const members = view.nodes.filter((node) => node.id !== view.centerId);
      const recent = members
        .filter((node) => node.activityEvidenceGroupCount > 0)
        .sort(byRecentEvidence);
      const richNodes = recent.slice(0, 3);
      const richIds = new Set(richNodes.map((node) => node.id));
      const olderNodes = members
        .filter((node) => node.activityEvidenceGroupCount === 0)
        .sort(
          (left, right) =>
            left.kind.localeCompare(right.kind, "ko") || byName(left, right),
        );
      const grouped = new Map<string, KnowledgeNode[]>();
      for (const node of olderNodes) {
        const current = grouped.get(node.kind) ?? [];
        current.push(node);
        grouped.set(node.kind, current);
      }
      const relations = new Map<string, KnowledgeRelation>();
      for (const relation of view.relations) {
        const memberId =
          relation.source === view.centerId ? relation.target : relation.source;
        relations.set(memberId, relation);
      }
      return {
        rich: richNodes,
        remainingRecent: recent.filter((node) => !richIds.has(node.id)),
        older: olderNodes,
        groups: grouped,
        relationByMember: relations,
      };
    }, [view]);

  const periodLabel = timeRangeLabel(timeRange);

  return (
    <aside
      className={styles.detailPanel}
      aria-label={`${view.topic.name} 주제 상세 정보`}
    >
      <button
        type="button"
        className={styles.closeButton}
        aria-label="상세 패널 닫기"
        onClick={onClose}
      >
        ×
      </button>
      <header className={styles.panelHeader} data-kind="주제">
        <span className={styles.nodeKind}>
          <i className={styles.nodeTypeDot} />
          주제
        </span>
        <h1>{view.topic.name}</h1>
      </header>
      <div className={topicStyles.topicPanelBody}>
        {view.totalPublicMembershipCount === 0 ? (
          <p className={styles.empty}>아직 공개된 연결 대상이 없습니다.</p>
        ) : (
          <>
            <section>
              <h2>{periodLabel}에 근거가 많은 연결</h2>
              {rich.length > 0 ? (
                <div className={topicStyles.topicRichCards}>
                  {rich.map((node) => (
                    <article
                      key={node.id}
                      className={topicStyles.topicRichCard}
                      data-kind={node.kind}
                    >
                      <button
                        type="button"
                        className={topicStyles.topicMemberButton}
                        onClick={() => onSelect(node.id)}
                      >
                        <span>
                          <i className={styles.nodeTypeDot} />
                          <strong>{node.name}</strong>
                        </span>
                        <small>{node.kind}</small>
                      </button>
                      <MemberActions
                        node={node}
                        relation={relationByMember.get(node.id)}
                        topicName={view.topic.name}
                        onLocate={onLocate}
                        onEvidence={onEvidence}
                      />
                      {timeRange !== "all" && (
                        <TopicInsightTitle
                          nodeId={node.id}
                          timeRange={timeRange}
                          onSelect={() => onSelectInsight(node.id)}
                        />
                      )}
                    </article>
                  ))}
                </div>
              ) : (
                <p className={styles.empty}>
                  {periodLabel}에 확인된 연결 원문이 없습니다.
                </p>
              )}
            </section>

            {remainingRecent.length > 0 && (
              <section>
                <h2>{periodLabel}에 근거가 있는 연결</h2>
                <div className={topicStyles.topicMemberList}>
                  {remainingRecent.map((node) => (
                    <article key={node.id}>
                      <button type="button" onClick={() => onSelect(node.id)}>
                        <span>{node.name}</span>
                        <small>{node.kind}</small>
                      </button>
                      <MemberActions
                        node={node}
                        relation={relationByMember.get(node.id)}
                        topicName={view.topic.name}
                        onLocate={onLocate}
                        onEvidence={onEvidence}
                      />
                    </article>
                  ))}
                </div>
              </section>
            )}

            {older.length > 0 && (
              <section>
                <h2>그 외 연결</h2>
                {[...groups].map(([kind, nodes]) => (
                  <div className={topicStyles.topicMemberGroup} key={kind}>
                    <h3>{kind}</h3>
                    <div className={topicStyles.topicMemberList}>
                      {nodes.map((node) => (
                        <article key={node.id}>
                          <button
                            type="button"
                            onClick={() => onSelect(node.id)}
                          >
                            <span>{node.name}</span>
                          </button>
                          <MemberActions
                            node={node}
                            relation={relationByMember.get(node.id)}
                            topicName={view.topic.name}
                            onLocate={onLocate}
                            onEvidence={onEvidence}
                          />
                        </article>
                      ))}
                    </div>
                  </div>
                ))}
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
