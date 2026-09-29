// web/src/IngestionQueueNotice.tsx
import { useEffect, useRef, useState } from "react";
import type { ExtractionTaskSummary } from "./types";
import styles from "./IngestionQueueNotice.module.css";

interface IngestionQueueNoticeProps {
  tasks: ExtractionTaskSummary[];
  onSelectTask: (taskId: string) => void;
  onDismissTask: (taskId: string) => void;
}

function formatTaskErrorMessage(rawError?: string | null): string {
  if (!rawError) return "지식 추출 중 문제가 발생했습니다.";
  const err = rawError.toLowerCase();

  if (err.includes("401") || err.includes("unauthorized") || err.includes("invalid_api_key")) {
    return "AI 분석 서비스 인증에 실패했습니다. 잠시 후 다시 시도하거나 관리자에게 문의해 주세요.";
  }
  if (err.includes("429") || err.includes("rate limit") || err.includes("quota")) {
    return "AI 서비스 사용량 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.";
  }
  if (err.includes("50000") || err.includes("상한선")) {
    return "문서 내용이 너무 길어 처리할 수 없습니다. (최대 50,000자 이내만 가능)";
  }
  if (err.includes("timeout") || err.includes("timed out")) {
    return "요청 처리 시간이 초과되었습니다. 네트워크 상태를 확인 후 다시 시도해 주세요.";
  }

  return "문서 분석 및 지식 추출 처리에 실패했습니다. 잠시 후 다시 시도해 주세요.";
}

export function IngestionQueueNotice({
  tasks,
  onSelectTask,
  onDismissTask,
}: IngestionQueueNoticeProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // 진행 중인 작업, 완료된 작업, 실패한 작업 개수 계산
  const activeCount = tasks.filter(
    (t) => t.status === "pending" || t.status === "processing",
  ).length;
  const completedCount = tasks.filter((t) => t.status === "completed").length;
  const failedCount = tasks.filter((t) => t.status === "failed").length;

  // 바깥 클릭 시 드롭다운 닫기
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // 표시할 작업이 전혀 없으면 렌더링하지 않음
  if (tasks.length === 0) return null;

  return (
    <div ref={containerRef} className={styles.container}>
      <button
        type="button"
        className={`${styles.indicatorBtn} ${failedCount > 0 && activeCount === 0 && completedCount === 0 ? styles.indicatorFailed : ""}`}
        onClick={() => setIsOpen((prev) => !prev)}
        title="작업 대기열 보기"
      >
        {activeCount > 0 ? (
          <>
            <span className={styles.spinner} />
            <span>분석 중 {activeCount}건</span>
          </>
        ) : failedCount > 0 && completedCount === 0 ? (
          <>
            <span>⚠️</span>
            <span>추출 실패 {failedCount}건</span>
          </>
        ) : failedCount > 0 ? (
          <>
            <span>🔔</span>
            <span>완료 {completedCount}건 (실패 {failedCount}건)</span>
          </>
        ) : (
          <>
            <span>🔔</span>
            <span>완료 {completedCount}건</span>
          </>
        )}
      </button>

      {isOpen && (
        <div className={styles.dropdown}>
          <div className={styles.dropdownHeader}>
            <span>지식 인제스트 대기열 ({tasks.length})</span>
          </div>

          <ul className={styles.taskList}>
            {tasks.map((task) => (
              <li
                key={task.id}
                className={`${styles.taskItem} ${task.status === "failed" ? styles.taskItemFailed : ""}`}
              >
                <div className={styles.taskTitle}>{task.title}</div>

                {task.status === "failed" && (
                  <div className={styles.errorNoticeBox}>
                    <span>⚠️ {formatTaskErrorMessage(task.error)}</span>
                  </div>
                )}

                <div className={styles.taskFooter}>
                  <div>
                    {task.status === "pending" && (
                      <span className={styles.statusPending}>대기 중...</span>
                    )}
                    {task.status === "processing" && (
                      <span className={styles.statusProcessing}>
                        추출 분석 중...
                      </span>
                    )}
                    {task.status === "completed" && (
                      <span className={styles.statusCompleted}>
                        완료 (노드 {task.node_count}개)
                      </span>
                    )}
                    {task.status === "failed" && (
                      <span className={styles.statusFailed}>처리 실패</span>
                    )}
                  </div>

                  <div
                    style={{
                      display: "flex",
                      gap: "6px",
                      alignItems: "center",
                    }}
                  >
                    {task.status === "completed" && (
                      <button
                        type="button"
                        className={styles.reviewBtn}
                        onClick={() => {
                          onSelectTask(task.id);
                          setIsOpen(false);
                        }}
                      >
                        검토하기
                      </button>
                    )}
                    <button
                      type="button"
                      className={styles.dismissBtn}
                      onClick={() => onDismissTask(task.id)}
                      title="목록에서 제거"
                    >
                      ✕
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
