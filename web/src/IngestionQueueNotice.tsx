// web/src/IngestionQueueNotice.tsx
import { useEffect, useRef, useState } from "react";
import type { ExtractionTaskSummary } from "./types";
import styles from "./IngestionQueueNotice.module.css";

interface IngestionQueueNoticeProps {
  tasks: ExtractionTaskSummary[];
  onSelectTask: (taskId: string) => void;
  onDismissTask: (taskId: string) => void;
}

export function IngestionQueueNotice({
  tasks,
  onSelectTask,
  onDismissTask,
}: IngestionQueueNoticeProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // 진행 중인 작업과 완료된 작업 개수 계산
  const activeCount = tasks.filter(
    (t) => t.status === "pending" || t.status === "processing",
  ).length;
  const completedCount = tasks.filter((t) => t.status === "completed").length;

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
        className={styles.indicatorBtn}
        onClick={() => setIsOpen((prev) => !prev)}
        title="작업 대기열 보기"
      >
        {activeCount > 0 ? (
          <>
            <span className={styles.spinner} />
            <span>분석 중 {activeCount}건</span>
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
              <li key={task.id} className={styles.taskItem}>
                <div className={styles.taskTitle}>{task.title}</div>
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
                      <span className={styles.statusFailed}>실패</span>
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
