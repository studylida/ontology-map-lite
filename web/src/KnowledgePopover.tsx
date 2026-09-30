// web/src/KnowledgePopover.tsx
import { useEffect, useRef, useState } from "react";
import { searchNodes } from "./api";
import type { NodeSearchItem } from "./types";
import styles from "./KnowledgePopover.module.css";

interface KnowledgePopoverProps {
  onSelectNode: (node: NodeSearchItem) => void;
}

interface MatchItem {
  node: NodeSearchItem;
  matchedReason: string;
}

export function KnowledgePopover({ onSelectNode }: KnowledgePopoverProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [position, setPosition] = useState<{ top: number; left: number }>({
    top: 0,
    left: 0,
  });
  const [selectedText, setSelectedText] = useState<string>("");
  const [results, setResults] = useState<MatchItem[]>([]);
  const popoverRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    // 텍스트 드래그 및 마우스 뗌(mouseup) 감지
    const handleMouseUp = async (e: MouseEvent) => {
      // 팝오버 내부 클릭 시 무시
      if (popoverRef.current && popoverRef.current.contains(e.target as Node)) {
        return;
      }

      const selection = window.getSelection();
      if (!selection || selection.isCollapsed) {
        return;
      }

      const text = selection.toString().trim();

      // 모달(dialog) 내부에서 텍스트를 드래그한 경우 모달 UI 조작과의 간섭 방지
      const anchorNode = selection.anchorNode;
      const anchorEl = anchorNode instanceof HTMLElement ? anchorNode : anchorNode?.parentElement;
      if (anchorEl?.closest('[role="dialog"]') || anchorEl?.closest('dialog')) {
        return;
      }

      // 2글자 미만이거나 30글자 초과 시 팝오버를 띄우지 않음
      if (text.length < 2 || text.length > 30) {
        return;
      }

      try {
        // 온톨로지 DB에서 연관 노드 검색
        const raw = await searchNodes(text);
        if (raw.length === 0) {
          setIsOpen(false);
          return;
        }

        // 간단한 매칭 이유 레이블링
        const matches: MatchItem[] = raw.slice(0, 4).map((node) => {
          let reason = "연관 노드";
          if (node.name.toLowerCase().includes(text.toLowerCase()))
            reason = "이름 일치";
          else if (
            node.classification_code?.toLowerCase().includes(text.toLowerCase())
          )
            reason = "분류 일치";
          return { node, matchedReason: reason };
        });

        const rect = selection.getRangeAt(0).getBoundingClientRect();

        // 스마트 뷰포트 배치 (Collision Detection & Smart Flipping)
        const popoverWidth = 380;
        const estimatedHeight = 320;
        const margin = 20;

        // 가로: 선택 영역 중앙에 맞추되 좌우 여백(20px) 내로 안전하게 클램핑
        let left = rect.left + rect.width / 2 - popoverWidth / 2;
        left = Math.max(
          margin,
          Math.min(window.innerWidth - popoverWidth - margin, left),
        );

        // 세로: 기본은 선택 영역 아래(+10px), 아래 공간 부족 시 상단 플립
        let top = rect.bottom + 10;
        if (top + estimatedHeight > window.innerHeight - margin) {
          if (rect.top - estimatedHeight - 10 >= margin) {
            top = rect.top - estimatedHeight - 10;
          } else {
            top = Math.max(margin, window.innerHeight - estimatedHeight - margin);
          }
        }

        setSelectedText(text);
        setResults(matches);
        setPosition({ top, left });
        setIsOpen(true);
      } catch (err) {
        console.error("지식 팝오버 검색 실패:", err);
      }
    };

    // Esc 키 및 외부 클릭 감지
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };
    const handleClickOutside = (e: MouseEvent) => {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    document.addEventListener("mouseup", handleMouseUp);
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("mouseup", handleMouseUp);
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  if (!isOpen) return null;

  return (
    <div
      ref={popoverRef}
      className={styles.popover}
      style={{ top: `${position.top}px`, left: `${position.left}px` }}
    >
      {/* 팝오버 헤더: 선택 단어 뱃지 및 닫기 버튼 */}
      <div className={styles.header}>
        <div className={styles.queryBadge}>
          <span>🔗</span>
          <span>'{selectedText}' 연관 지식</span>
          <span className={styles.countBadge}>{results.length}건</span>
        </div>
        <button
          type="button"
          className={styles.closeBtn}
          onClick={() => setIsOpen(false)}
          title="닫기 (Esc)"
        >
          ✕
        </button>
      </div>

      {/* 연관 지식 노드 목록 */}
      <ul className={styles.resultsList}>
        {results.map(({ node, matchedReason }) => (
          <li
            key={node.id}
            className={styles.resultItem}
            onClick={() => {
              onSelectNode(node);
              setIsOpen(false);
            }}
          >
            <div className={styles.itemHeader}>
              <span className={styles.badge}>{matchedReason}</span>
              <span className={styles.nodeName}>{node.name}</span>
            </div>
            {node.description && (
              <div className={styles.nodeDesc}>{node.description}</div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
