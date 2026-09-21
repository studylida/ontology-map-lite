import { useEffect, useRef, useState } from "react";
import { APIRequestError } from "./data";
import { PageNotice } from "./RelationPanel";
import topicStyles from "./Topic.module.css";
import { fetchTopicReferences, type TopicReference } from "./topicData";

export function TopicPicker({
  onSelect,
}: {
  onSelect: (nodeId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [topics, setTopics] = useState<TopicReference[]>([]);
  const [error, setError] = useState<APIRequestError | null>(null);
  const [retrySuccess, setRetrySuccess] = useState(false);
  const loadedRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      controllerRef.current?.abort();
    },
    [],
  );

  const loadTopics = (retry = false) => {
    controllerRef.current?.abort();
    loadedRef.current = true;
    setError(null);
    setRetrySuccess(false);
    const controller = new AbortController();
    controllerRef.current = controller;
    void fetchTopicReferences(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        setTopics(items);
        if (retry) setRetrySuccess(true);
      })
      .catch((caught) => {
        if (controller.signal.aborted) return;
        loadedRef.current = false;
        setError(
          caught instanceof APIRequestError
            ? caught
            : new APIRequestError("NETWORK_ERROR", 0, true),
        );
      });
  };

  const showTopics = () => {
    const nextOpen = !open;
    setOpen(nextOpen);
    if (nextOpen && !loadedRef.current) loadTopics();
  };

  return (
    <div className={topicStyles.topicPicker}>
      <button
        type="button"
        className={topicStyles.topicButton}
        aria-label="주제 목록 열기"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={showTopics}
      >
        주제
      </button>
      {open && (
        <div className={topicStyles.topicPopover}>
          <PageNotice
            loading={false}
            error={error}
            empty={false}
            retrySuccess={retrySuccess}
            onRetry={() => loadTopics(true)}
          />
          {!error && (
            <div role="listbox" aria-label="주제 목록">
              {topics.map((topic) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={false}
                  key={topic.nodeId}
                  className={topicStyles.topicOption}
                  onClick={() => {
                    setOpen(false);
                    onSelect(topic.nodeId);
                  }}
                >
                  <span>{topic.name}</span>
                  {!topic.isActive && <small>신규 연결 중단</small>}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
