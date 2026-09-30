// web/src/FilterBar.tsx
import { useState, useRef, useEffect } from "react";
import type { GraphFilterState, DatePreset } from "./types";
import styles from "./FilterBar.module.css";

interface FilterBarProps {
  filterState: GraphFilterState;
  onChangeFilter: (next: GraphFilterState) => void;
}

const TYPE_OPTIONS = [
  { code: "COMPANY", label: "기업", color: "#b085f5" },
  { code: "TECH", label: "기술", color: "#22d3ee" },
  { code: "AGENCY", label: "기관", color: "#f472b6" },
  { code: "PROGRAM", label: "지원사업", color: "#34d399" },
  { code: "FACILITY", label: "생산시설", color: "#60a5fa" },
  { code: "PROJECT", label: "프로젝트", color: "#4ade80" },
  { code: "METRIC", label: "지표", color: "#facc15" },
];

function formatDate(d: Date): string {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function FilterBar({ filterState, onChangeFilter }: FilterBarProps) {
  const [isCalendarOpen, setIsCalendarOpen] = useState(false);
  const calendarRef = useRef<HTMLDivElement>(null);

  // 기본 기준일: 오늘 (2026-09-30)
  const today = new Date();
  const todayStr = formatDate(today);

  const [tempStart, setTempStart] = useState(
    filterState.startDate ?? formatDate(new Date(today.getTime() - 30 * 86400000)),
  );
  const [tempEnd, setTempEnd] = useState(filterState.endDate ?? todayStr);

  // 캘린더 팝오버 외부 클릭 감지
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (calendarRef.current && !calendarRef.current.contains(e.target as Node)) {
        setIsCalendarOpen(false);
      }
    }
    if (isCalendarOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isCalendarOpen]);

  // 유형 칩 클릭 핸들러 (다중 선택 토글)
  const handleToggleType = (code: string) => {
    const nextSet = new Set(filterState.selectedTypes);
    if (nextSet.has(code)) {
      nextSet.delete(code);
    } else {
      nextSet.add(code);
    }
    onChangeFilter({
      ...filterState,
      selectedTypes: nextSet,
    });
  };

  const handleSelectAllTypes = () => {
    onChangeFilter({
      ...filterState,
      selectedTypes: new Set(),
    });
  };

  // 기간 프리셋 클릭 핸들러
  const handleSelectPreset = (preset: DatePreset) => {
    if (preset === "ALL") {
      setIsCalendarOpen(false);
      onChangeFilter({
        ...filterState,
        datePreset: "ALL",
        startDate: null,
        endDate: null,
      });
      return;
    }

    if (preset === "CUSTOM") {
      setIsCalendarOpen((prev) => !prev);
      return;
    }

    setIsCalendarOpen(false);
    let days = 30;
    if (preset === "3M") days = 90;
    if (preset === "1Y") days = 365;

    const start = new Date(today.getTime() - days * 86400000);
    const startStr = formatDate(start);

    onChangeFilter({
      ...filterState,
      datePreset: preset,
      startDate: startStr,
      endDate: todayStr,
    });
  };

  const handleApplyCustomDate = () => {
    if (!tempStart || !tempEnd) return;
    setIsCalendarOpen(false);
    onChangeFilter({
      ...filterState,
      datePreset: "CUSTOM",
      startDate: tempStart,
      endDate: tempEnd,
    });
  };

  const handleResetFilters = () => {
    setIsCalendarOpen(false);
    onChangeFilter({
      selectedTypes: new Set(),
      datePreset: "ALL",
      startDate: null,
      endDate: null,
    });
  };

  const isFilterActive =
    filterState.selectedTypes.size > 0 || filterState.datePreset !== "ALL";

  return (
    <div className={styles.filterBar}>
      {/* 1. 노드 유형 다중 선택 필터 */}
      <div className={styles.filterGroup}>
        <span className={styles.groupLabel}>유형:</span>
        <button
          type="button"
          className={`${styles.chip} ${filterState.selectedTypes.size === 0 ? styles.activeChip : ""}`}
          onClick={handleSelectAllTypes}
        >
          전체
        </button>
        {TYPE_OPTIONS.map((opt) => {
          const isSelected = filterState.selectedTypes.has(opt.code);
          return (
            <button
              key={opt.code}
              type="button"
              className={`${styles.chip} ${isSelected ? styles.activeChip : ""}`}
              onClick={() => handleToggleType(opt.code)}
              title={`${opt.label} 유형 노드 표시 토글`}
            >
              <span
                className={styles.colorDot}
                style={{ backgroundColor: opt.color }}
              />
              {opt.label}
            </button>
          );
        })}
      </div>

      <div className={styles.divider} />

      {/* 2. 기간 프리셋 및 달력 필터 */}
      <div className={styles.filterGroup}>
        <span className={styles.groupLabel}>기간:</span>
        <button
          type="button"
          className={`${styles.chip} ${filterState.datePreset === "ALL" ? styles.activeChip : ""}`}
          onClick={() => handleSelectPreset("ALL")}
        >
          전체
        </button>
        <button
          type="button"
          className={`${styles.chip} ${filterState.datePreset === "1M" ? styles.activeChip : ""}`}
          onClick={() => handleSelectPreset("1M")}
        >
          1개월
        </button>
        <button
          type="button"
          className={`${styles.chip} ${filterState.datePreset === "3M" ? styles.activeChip : ""}`}
          onClick={() => handleSelectPreset("3M")}
        >
          3개월
        </button>
        <button
          type="button"
          className={`${styles.chip} ${filterState.datePreset === "1Y" ? styles.activeChip : ""}`}
          onClick={() => handleSelectPreset("1Y")}
        >
          1년
        </button>

        {/* 커스텀 달력 버튼 */}
        <div className={styles.customDateWrapper} ref={calendarRef}>
          <button
            type="button"
            className={`${styles.chip} ${filterState.datePreset === "CUSTOM" ? styles.activeChip : ""}`}
            onClick={() => handleSelectPreset("CUSTOM")}
            title="기간 직접 지정"
          >
            <span>📅</span>
            {filterState.datePreset === "CUSTOM" && filterState.startDate
              ? `${filterState.startDate} ~ ${filterState.endDate}`
              : "기간 선택"}
          </button>

          {isCalendarOpen && (
            <div className={styles.calendarPopover}>
              <div className={styles.popoverHeader}>
                <strong>기간 직접 지정</strong>
                <button
                  type="button"
                  className={styles.closeBtn}
                  onClick={() => setIsCalendarOpen(false)}
                >
                  ✕
                </button>
              </div>
              <div className={styles.dateInputs}>
                <label>
                  <span>시작일</span>
                  <input
                    type="date"
                    value={tempStart}
                    onChange={(e) => setTempStart(e.target.value)}
                  />
                </label>
                <span className={styles.dateWave}>~</span>
                <label>
                  <span>종료일</span>
                  <input
                    type="date"
                    value={tempEnd}
                    onChange={(e) => setTempEnd(e.target.value)}
                  />
                </label>
              </div>
              <div className={styles.popoverActions}>
                <button
                  type="button"
                  className={styles.applyBtn}
                  onClick={handleApplyCustomDate}
                >
                  적용
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 3. 전체 필터 리셋 버튼 */}
      {isFilterActive && (
        <button
          type="button"
          className={styles.resetBtn}
          onClick={handleResetFilters}
          title="모든 필터를 기본 상태로 초기화합니다"
        >
          ✕ 필터 초기화
        </button>
      )}
    </div>
  );
}
