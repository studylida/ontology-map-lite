// web/src/NodeSearch.tsx
import { useEffect, useRef, useState } from "react";
import { searchNodes } from "./api";
import type { NodeSearchItem } from "./types";
import styles from "./NodeSearch.module.css";

interface MatchResult {
  node: NodeSearchItem;
  score: number;
  matchedReason: string;
}

interface NodeSearchProps {
  onSelectNode: (node: NodeSearchItem) => void;
}

export function NodeSearch({ onSelectNode }: NodeSearchProps) {
  const [keyword, setKeyword] = useState<string>("");
  const [results, setResults] = useState<MatchResult[]>([]);
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // [블로그 knowledgeEngine.ts 이식]: 가중치 스코어링 함수
  function scoreAndRankNodes(query: string, rawNodes: NodeSearchItem[]): MatchResult[] {
    const cleanQuery = query.trim().toLowerCase();
    if (!cleanQuery) return [];

    const ranked: MatchResult[] = [];

    for (const node of rawNodes) {
      let score = 0;
      let matchedReason = "";

      const lowerName = node.name.toLowerCase();
      const lowerCls = (node.classification_code ?? "").toLowerCase();
      const lowerDesc = (node.description ?? "").toLowerCase();

      // 1순위: 분류(Classification) 일치 (+15점)
      if (lowerCls === cleanQuery || lowerCls.includes(cleanQuery) || cleanQuery.includes(lowerCls)) {
        score += 15;
        matchedReason = `[${node.classification_code}] 분류 일치`;
      }

      // 2순위: 노드 이름 부분 일치 (+10점)
      if (lowerName.includes(cleanQuery) || cleanQuery.includes(lowerName)) {
        score += 10;
        if (!matchedReason) matchedReason = "노드명 일치";
      }

      // 3순위: 설명 일치 (+4점)
      if (lowerDesc && lowerDesc.includes(cleanQuery)) {
        score += 4;
        if (!matchedReason) matchedReason = "설명 키워드 연관";
      }

      if (score > 0) {
        ranked.push({ node, score, matchedReason });
      }
    }

    return ranked.sort((a, b) => b.score - a.score).slice(0, 6);
  }

  // 디바운스(300ms) API 호출 및 스코어링
  useEffect(() => {
    if (!keyword.trim()) {
      setResults([]);
      setIsOpen(false);
      return;
    }

    // [빈칸 1]: 300ms 후에 searchNodes(keyword)를 호출하는 타이머를 작성하세요.
    const timer = setTimeout(async () => {
      try {
        const raw = await searchNodes(keyword);
        const scored = scoreAndRankNodes(keyword, raw);
        setResults(scored);
        setIsOpen(scored.length > 0);
      } catch (err) {
        console.error("검색 실패:", err);
      }
    }, 300);

    // [빈칸 2]: 다음 타이핑 시 이전 타이머를 취소(Cleanup)하는 함수를 반환하세요.
    return () => clearTimeout(timer);
  }, [keyword]);

  // Esc 키 및 외부 클릭 감지 (블로그 KnowledgePopover 인터랙션)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  return (
    <div ref={containerRef} className={styles.searchContainer}>
      <div className={styles.inputWrapper}>
        <span className={styles.searchIcon}>🔍</span>
        <input
          type="text"
          placeholder="노드 이름, 분류, 키워드 검색..."
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onFocus={() => results.length > 0 && setIsOpen(true)}
          className={styles.searchInput}
        />
      </div>

      {isOpen && (
        <ul className={styles.dropdown}>
          {results.map(({ node, matchedReason }) => (
            <li
              key={node.id}
              className={styles.resultItem}
              onClick={() => {
                onSelectNode(node);
                setIsOpen(false);
                setKeyword(node.name);
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
      )}
    </div>
  );
}
