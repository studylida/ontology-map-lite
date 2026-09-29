// web/src/HitlReviewModal.tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { commitAdapterInput, convertAdapterInput, seedDemoData } from "./api";
import styles from "./HitlReviewModal.module.css";

export interface HitlReviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  onIngestionSuccess?: (newNodeId?: number) => void;
  reviewTaskId?: string | null;
  initialProducer?: "news" | "gov" | "excel";
}

type ProducerType = "news" | "gov" | "excel";

interface CandidateRow {
  id: string;
  title: string;
  kind: string;
  cls: string;
  sub: string;
  blocked?: boolean;
  dependsOn?: string[];
  targetType?: "edge" | "property" | "report" | "blocked";
}

// 백엔드 응답 지연/누락 시에도 100% 동작을 보장하는 프론트엔드 내장 의존성 사전
const FALLBACK_DEPENDENCIES: Record<ProducerType, Record<string, string[]>> = {
  excel: {
    second: ["main"], // 2024 대비 20% 증가는 2025 매출 120억원(main)에 필수 종속
  },
  gov: {
    second: ["main"], // 세부 속성(총20억·최대1억·마감)은 주관 사업 노드(main)에 종속
  },
  news: {
    // 뉴스 확장을 위한 슬롯
  },
};


interface EvidenceLocationProps {
  location: string;
  verification: string;
  rawPath: string;
}

function EvidenceLocationCard({
  location,
  verification,
  rawPath,
}: EvidenceLocationProps) {
  return (
    <div className={styles.evidenceLocationCard}>
      <div className={styles.locationHeader}>
        <span className={styles.locationPin} aria-hidden="true">
          📍
        </span>
        <span className={styles.locationLabel}>원천 데이터 출처 및 위치:</span>
        <strong className={styles.locationValue}>{location}</strong>
      </div>
      <div className={styles.verificationRow}>
        <span className={styles.verificationBadge}>대조 확인 내용</span>
        <span className={styles.verificationDesc}>{verification}</span>
      </div>
      <details className={styles.devPathDetails}>
        <summary>🛠️ 시스템 내부 필드 매핑 경로 (기술 참조용)</summary>
        <code>{rawPath}</code>
      </details>
    </div>
  );
}

export function HitlReviewModal({
  isOpen,
  onClose,
  onIngestionSuccess,
  initialProducer = "news",
}: HitlReviewModalProps) {
  const [producer, setProducer] = useState<ProducerType>(initialProducer);
  const [activeTab, setActiveTab] = useState<"review" | "compare">("review");
  const [selectedItemId, setSelectedItemId] = useState<string>("main");
  const [enabledItemIds, setEnabledItemIds] = useState<Set<string>>(
    () => new Set(["main", "second"]),
  );

  const [bundleData, setBundleData] = useState<any>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [committing, setCommitting] = useState<boolean>(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 은은한 반짝임(Flash / Pulse) 시각 피드백 상태 (항목 ID -> appear / disappear)
  const [flashedItemIds, setFlashedItemIds] = useState<Map<string, "appear" | "disappear">>(
    () => new Map(),
  );

  // 수동 입력 모드 지원
  const [showManualInput, setShowManualInput] = useState<boolean>(false);
  const [manualJsonText, setManualJsonText] = useState<string>("");

  // 은은한 반짝임 애니메이션 트리거 함수 (850ms 동안 유지 후 자동 해제)
  const triggerFlash = useCallback((affectedIds: string[], type: "appear" | "disappear") => {
    setFlashedItemIds((prev) => {
      const next = new Map(prev);
      for (const id of affectedIds) {
        next.set(id, type);
      }
      return next;
    });

    setTimeout(() => {
      setFlashedItemIds((prev) => {
        const next = new Map(prev);
        for (const id of affectedIds) {
          next.delete(id);
        }
        return next;
      });
    }, 850);
  }, []);

  // 프로듀서 변경 시 번들 로드 함수
  const loadProducerBundle = useCallback(
    async (targetProducer: ProducerType, customRaw?: any) => {
      setLoading(true);
      setError(null);
      try {
        let rawJson = customRaw;
        let supplementJson: any = null;

        if (!rawJson) {
          if (targetProducer === "news") {
            const res = await fetch("/demo-data/news.native.json");
            rawJson = await res.json();
          } else if (targetProducer === "gov") {
            const [gRes, supRes] = await Promise.all([
              fetch("/demo-data/gov.native.json"),
              fetch("/demo-data/lite-supplement.json"),
            ]);
            rawJson = await gRes.json();
            supplementJson = await supRes.json();
          } else if (targetProducer === "excel") {
            const res = await fetch("/demo-data/excel.native.json");
            rawJson = await res.json();
          }
        }

        const bundle = await convertAdapterInput({
          producer: targetProducer,
          raw_json: rawJson,
          supplement_json: supplementJson,
        });

        setBundleData(bundle);

        // 기본 활성 선택 항목 초기화
        if (targetProducer === "gov") {
          setEnabledItemIds(new Set(["main", "second", "note"]));
        } else {
          setEnabledItemIds(new Set(["main", "second"]));
        }
        setSelectedItemId("main");
      } catch (err: any) {
        setError(err.message || "시연 데이터 번들을 불러오지 못했습니다.");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  // 모달 열림 또는 프로듀서 변경 시 자동 로드
  useEffect(() => {
    if (isOpen) {
      loadProducerBundle(producer);
    }
  }, [isOpen, producer, loadProducerBundle]);

  if (!isOpen) return null;

  // 특정 항목의 선행 필수 의존 목록 추출 (백엔드 메타데이터 우선 + 프론트 fallback)
  const getItemDependencies = (
    rowId: string,
    currentProducer: ProducerType,
    rows?: CandidateRow[],
  ): string[] => {
    const targetRow = rows?.find((r) => r.id === rowId);
    if (targetRow?.dependsOn && targetRow.dependsOn.length > 0) {
      return targetRow.dependsOn;
    }
    return FALLBACK_DEPENDENCIES[currentProducer]?.[rowId] || [];
  };

  // 선행 필수 조건(dependsOn) 충족 여부 확인 (범용 DAG)
  const isPrerequisiteMet = (
    rowId: string,
    currentProducer: ProducerType,
    enabled: Set<string>,
    rows?: CandidateRow[],
  ): boolean => {
    const deps = getItemDependencies(rowId, currentProducer, rows);
    if (deps.length === 0) return true;
    return deps.every((depId) => enabled.has(depId));
  };

  // 체크박스 토글 핸들러 (의존 관계 자동 연쇄 해제 & 선행조건 연동 + 은은한 반짝임 피드백)
  const handleToggleItem = (id: string, blocked?: boolean) => {
    if (blocked) return;

    setEnabledItemIds((prev) => {
      const next = new Set(prev);
      const isCurrentlyChecked = next.has(id);
      const rows: CandidateRow[] = bundleData?.rows || [];

      if (isCurrentlyChecked) {
        // 1. 체크 해제 시: 해당 항목 제거 및 이 항목에 의존하는 모든 자식 항목들을 재귀적 연쇄 해제 (Cascade Uncheck)
        const toUncheck = [id];
        const affected = [id];
        next.delete(id);

        while (toUncheck.length > 0) {
          const parentId = toUncheck.pop()!;
          for (const r of rows) {
            const deps = getItemDependencies(r.id, producer, rows);
            if (deps.includes(parentId) && next.has(r.id)) {
              next.delete(r.id);
              toUncheck.push(r.id);
              affected.push(r.id);
            }
          }
        }
        triggerFlash(affected, "disappear");
      } else {
        // 2. 체크 시: 선행 조건 항목들이 꺼져 있으면 선행 부모들도 함께 켜줌 (Auto-enable prerequisites)
        const deps = getItemDependencies(id, producer, rows);
        const affected = [id];
        for (const depId of deps) {
          if (!next.has(depId)) {
            next.add(depId);
            affected.push(depId);
          }
        }
        next.add(id);
        triggerFlash(affected, "appear");
      }
      return next;
    });
  };

  // 전체 선택 / 해제
  const handleToggleAll = () => {
    if (!bundleData?.rows) return;
    const available = bundleData.rows.filter((r: CandidateRow) => !r.blocked);
    const availableIds: string[] = available.map((r: CandidateRow) => r.id);

    if (enabledItemIds.size > 0) {
      setEnabledItemIds(new Set());
      triggerFlash(Array.from(enabledItemIds), "disappear");
    } else {
      setEnabledItemIds(new Set(availableIds));
      triggerFlash(availableIds, "appear");
    }
  };


  // 시연 베이스라인 시드 초기화
  const handleResetDemoSeed = async () => {
    try {
      await seedDemoData();
      setToastMessage("시연 기준 데이터(한결정밀·부산공장·2024 실적)가 초기화되었습니다.");
      setTimeout(() => setToastMessage(null), 3000);
      onIngestionSuccess?.();
    } catch (err: any) {
      setError(err.message || "시드 초기화 실패");
    }
  };

  // 수동 JSON 적용 핸들러
  const handleApplyManualJson = async () => {
    try {
      const parsed = JSON.parse(manualJsonText);
      await loadProducerBundle(producer, parsed);
      setShowManualInput(false);
      setToastMessage("수동 입력된 JSON이 검토 번들로 변환되었습니다.");
      setTimeout(() => setToastMessage(null), 2500);
    } catch (err: any) {
      setError("유효한 JSON 형식이 아닙니다: " + err.message);
    }
  };

  // 최종 반영 및 저장 핸들러
  const handleCommit = async () => {
    if (!bundleData || enabledItemIds.size === 0) return;
    setCommitting(true);
    setError(null);
    try {
      await commitAdapterInput({
        bundle: bundleData,
        enabled_ids: Array.from(enabledItemIds),
      });

      setToastMessage("지식그래프에 성공적으로 반영되었습니다!");
      setTimeout(() => {
        setToastMessage(null);
        onIngestionSuccess?.();
        onClose();
      }, 700);
    } catch (err: any) {
      setError(err.message || "지식맵 반영에 실패했습니다.");
      setCommitting(false);
    }
  };

  // 실시간 변경 요약 카운트 계산
  const summaryCountsText = () => {
    const a = enabledItemIds.has("main");
    const b = enabledItemIds.has("second");
    const n = enabledItemIds.has("note");
    if (!enabledItemIds.size) return "선택한 변경 없음";

    if (producer === "news") {
      return `새 노드 ${a ? 1 : 0} · 새 연결 ${a ? 1 : 0} · 계획 카드 ${b ? 1 : 0}`;
    }
    if (producer === "gov") {
      return `새 노드 ${a ? 2 : b ? 1 : 0} · 새 연결 ${a ? 1 : 0} · 외부 분석 ${n ? 1 : 0}`;
    }
    return `새 노드 0 · 새 연결 0 · 수치 ${a ? 1 : 0} · 계산 ${b ? 1 : 0} · 분석 ${n ? 1 : 0}`;
  };

  // 우측 근거 뷰어 내용 렌더링
  const renderEvidenceViewer = () => {
    const s = selectedItemId;
    const isBlocked = s === "blocked";

    // 하단 매핑 토글 공통 렌더러
    const renderMappingToggle = () =>
      bundleData?.mapping ? (
        <details className={styles.mappingToggle}>
          <summary>어떤 필드에서 가져왔고, 무엇은 가져오지 않나요?</summary>
          <div
            className={styles.mappingContent}
            dangerouslySetInnerHTML={{ __html: bundleData.mapping }}
          />
        </details>
      ) : null;

    if (s === "base") {
      return (
        <div className={styles.evidenceBody}>
          <div className={styles.sourceHeader}>
            <span className={`${styles.tag} ${styles.tagPanel}`}>
              시연 기준 자료
            </span>
            <span className={styles.sourceTitle}>
              한결정밀 · 기존에 등록된 정보
            </span>
          </div>
          <div className={styles.quoteBox}>
            한결정밀은 부산공장을 운영한다.
            <br />
            한결정밀의 2024년 별도 매출 실적은 100억원이다.
          </div>

          <EvidenceLocationCard
            location="시연 기준 초기 데이터 · [한결정밀] 기업 프로필 및 시설 운영 내역"
            verification="한결정밀의 부산공장 운영 및 2024년 별도 매출 100억원 (기존 지식맵 기준 데이터 유지)"
            rawPath="before-seed.proposal.json · 온톨로지 시드 기준 데이터"
          />

          <div className={styles.routeGrid}>
            <div className={styles.routeItem}>
              <small>DOCUMENT · 출처</small>
              <strong>기존 시연 기준 문서</strong>
            </div>
            <div className={styles.routeItem}>
              <small>CLAIM · 정리할 내용</small>
              <strong>공장 운영 및 2024년 매출</strong>
            </div>
            <div className={styles.routeItem}>
              <small>지도에서의 표시</small>
              <strong className={styles.routeTarget}>기존 관계·속성 유지</strong>
            </div>
          </div>
          <p className={styles.subNote}>
            기존 시설의 근거입니다. 새로 들어온 계약·공고·Excel의 근거를 대신
            붙이지 않습니다.
          </p>
          {renderMappingToggle()}
        </div>
      );
    }

    if (producer === "news") {
      return (
        <div className={styles.evidenceBody}>
          <div className={styles.sourceHeader}>
            <span
              className={`${styles.tag} ${
                isBlocked ? styles.tagWarn : styles.tagNew
              }`}
            >
              시연 기사 · {s === "second" ? "20" : "10"}번 문장
            </span>
            <span className={styles.sourceTitle}>한결정밀 소재 공급 계약</span>
            <span className={styles.chip}>게시일 2026.09.26</span>
          </div>

          <div className={styles.quoteBox}>
            {s === "second" ? (
              <>
                누리소재는 2026년 9월 25일 한결정밀에 정밀부품용 소재를 공급하는
                계약을 체결했다.
                <br />
                <mark>
                  한결정밀은 2027년 생산량 확대를 검토 중이라고 밝혔다.
                </mark>
              </>
            ) : isBlocked ? (
              <>
                <mark>
                  누리소재는 2026년 9월 25일 한결정밀에 정밀부품용 소재를 공급하는
                  계약을 체결했다.
                </mark>
              </>
            ) : (
              <>
                <mark>
                  누리소재는 2026년 9월 25일 한결정밀에 정밀부품용 소재를 공급하는
                  계약을 체결했다.
                </mark>
                <br />
                <span style={{ color: "#94a7c0" }}>
                  한결정밀은 2027년 생산량 확대를 검토 중이라고 밝혔다.
                </span>
              </>
            )}
          </div>

          <EvidenceLocationCard
            location={
              s === "second"
                ? "기사 본문 2번째 핵심 문장 (문맥 번호: 20번)"
                : isBlocked
                ? "기사 본문 전체 대조 (원문 근거 부재)"
                : "기사 본문 1번째 핵심 문장 (문맥 번호: 10번)"
            }
            verification={
              s === "second"
                ? "한결정밀의 2027년 생산량 확대 검토 공식 발언 확인 (확정 사실이 아닌 '계획 카드'로 분류)"
                : isBlocked
                ? "기사 본문에는 계약 체결 사실만 명시되어 있으며, 실제 납품이 진행 중이라는 근거는 없어 지도 반영 차단"
                : "2026년 9월 25일 누리소재와 한결정밀의 정밀부품용 소재 공급 계약 체결 사실 확인"
            }
            rawPath={
              s === "second"
                ? "analysis.keyPoints[1] → evidence[20] → sentences[index=20].text"
                : isBlocked
                ? "analysis.keyPoints[*] ↔ sentences[*] (일치하는 근거 문장 부재로 차단)"
                : "analysis.keyPoints[0] → evidence[10] → sentences[index=10].text"
            }
          />

          <div className={styles.routeGrid}>
            <div className={styles.routeItem}>
              <small>DOCUMENT · 출처</small>
              <strong>
                {isBlocked ? "계약 체결 문장은 있음" : "기사 본문"}
              </strong>
            </div>
            <div className={styles.routeItem}>
              <small>CLAIM · 정리할 내용</small>
              <strong>
                {s === "second"
                  ? "회사가 증산을 검토 중이라고 밝힘"
                  : isBlocked
                  ? "납품 중이라는 후보는 입증되지 않음"
                  : "정밀부품용 소재 공급 계약 체결"}
              </strong>
            </div>
            <div className={styles.routeItem}>
              <small>지도에서의 표시</small>
              <strong className={styles.routeTarget}>
                {s === "second"
                  ? "한결정밀의 계획 카드"
                  : isBlocked
                  ? "반영 제외 (차단)"
                  : "누리소재 → 한결정밀 공급 계약 선"}
              </strong>
            </div>
          </div>

          {s === "second" && (
            <div className={styles.factWarning}>
              ⚠️ 생산자 표기는 FACT여도 내용은 계획에 대한 발언입니다. ‘증산
              완료’라는 확정 선을 만들지 않습니다.
            </div>
          )}
          {isBlocked && (
            <div className={styles.factWarning}>
              🚫 문장이 원문에 있다는 것과 이 주장을 뒷받침한다는 것은 다릅니다.
              첫 Claim이나 같은 문서를 대신 붙여 임의로 통과시키지 않습니다.
            </div>
          )}
          {!isBlocked && s !== "second" && (
            <p className={styles.subNote}>
              근거 문장 1개가 관계를 직접 설명합니다. ‘현재 납품 중’까지 확인한
              자료는 아닙니다.
            </p>
          )}
          {renderMappingToggle()}
        </div>
      );
    }

    if (producer === "gov") {
      return (
        <div className={styles.evidenceBody}>
          <div className={styles.sourceHeader}>
            <span
              className={`${styles.tag} ${
                isBlocked
                  ? styles.tagWarn
                  : s === "note"
                  ? styles.tagNote
                  : styles.tagNew
              }`}
            >
              {s === "note" ? "GovInsight 분석 결과" : "보충한 공고 원문 본문"}
            </span>
            <span className={styles.sourceTitle}>
              2026 제조데이터 실증지원 사업
            </span>
          </div>

          {s === "note" ? (
            <div className={styles.quoteBox}>
              <mark>
                한결정밀은 신청 요건을 확인한 뒤 참여를 검토할 수 있습니다.
              </mark>
            </div>
          ) : s === "second" ? (
            <div className={styles.quoteBox}>
              <mark>
                이 사업의 총예산은 20억원이며 선정기업당 지원금은 최대 1억원이다.
              </mark>
              <br />
              신청 대상은 국내 제조 중소기업이며 외부 전문기관과의 협력이 필요한
              경우 참여확인서를 제출해야 한다.
              <br />
              신청 마감은 2026년 10월 30일 오후 6시(한국 시간)이다.
            </div>
          ) : isBlocked ? (
            <div className={styles.quoteBox}>
              선정기업당 지원금은 <mark>최대 1억원</mark>이다.
            </div>
          ) : (
            <div className={styles.quoteBox}>
              <mark>
                새봄산업지원원은 2026 제조데이터 실증지원 사업을 주관한다.
              </mark>
            </div>
          )}

          <EvidenceLocationCard
            location={
              s === "note"
                ? "GovInsight AI 분석 리포트 · 전략 제안 메모"
                : s === "second"
                ? "공고문 본문 · 지원 규모 및 신청 자격 조항"
                : isBlocked
                ? "공고문 지원 상한액 조항 (한결정밀 수령 근거 없음)"
                : "공고문 본문 · 주관기관 명시 조항"
            }
            verification={
              s === "note"
                ? "한결정밀의 참여 검토를 권고하는 AI 분석 의견 (공식 공고문 원문이 아닌 분석가 추천 코멘트)"
                : s === "second"
                ? "사업 총예산 20억원, 기업당 최대 지원금 1억원, 접수 마감(2026.10.30 18:00) 요건 확인"
                : isBlocked
                ? "공고의 최대 지원 한도(1억원)일 뿐이며, 한결정밀이 실제 선정되었거나 지원금을 수령했다는 근거가 없어 반영 차단"
                : "새봄산업지원원이 '2026 제조데이터 실증지원 사업'을 공식 주관함을 확인"
            }
            rawPath={
              s === "note"
                ? "proposal.preparation.strategy.recommendedParticipation"
                : s === "second"
                ? "comparisonSummary.* + preparation.eligibilityChecklist[].source ↔ lite-supplement.gov.sourceSnapshots[0]"
                : isBlocked
                ? "comparisonSummary.supportScale ↔ 별도 제공한 공고 본문 (수령 사실 입증 불가)"
                : "keyPoints[0] ↔ lite-supplement.gov.sourceSnapshots[0].text"
            }
          />

          <div className={styles.routeGrid}>
            <div className={styles.routeItem}>
              <small>DOCUMENT · 출처</small>
              <strong>
                {s === "note"
                  ? "GovInsight 분석 보고서"
                  : isBlocked
                  ? "지원 상한이 적힌 공고문"
                  : "별도 공고 본문 스냅샷"}
              </strong>
            </div>
            <div className={styles.routeItem}>
              <small>CLAIM · 정리할 내용</small>
              <strong>
                {s === "note"
                  ? "GovInsight의 신청 검토 추천"
                  : s === "second"
                  ? "예산·지원 상한·마감·신청 조건"
                  : isBlocked
                  ? "한결정밀의 실제 수령 근거 없음"
                  : "새봄산업지원원이 사업을 주관함"}
              </strong>
            </div>
            <div className={styles.routeItem}>
              <small>지도에서의 표시</small>
              <strong className={styles.routeTarget}>
                {s === "note"
                  ? "회사 패널의 외부 분석 추천 카드"
                  : s === "second"
                  ? "사업의 속성·요건 카드 (새 노드/선 없음)"
                  : isBlocked
                  ? "반영 제외 (차단)"
                  : "새봄산업지원원 → 사업 주관 선"}
              </strong>
            </div>
          </div>

          {s === "note" && (
            <div className={styles.factWarning}>
              ⚠️ 회사와 사업 사이에 선정·참여 선은 만들지 않습니다. 추천 의견
              자체를 사이드패널 연계 분석 카드로 보관하는 것입니다.
            </div>
          )}
          {isBlocked && (
            <div className={styles.factWarning}>
              🚫 사업의 지원금 상한(최대 1억)은 특정 회사의 수령액이 아닙니다.
              신청 추천도 선정이나 지급 사실을 입증하지 못합니다.
            </div>
          )}
          {s === "main" && (
            <p className={styles.subNote}>
              공고의 게시기관과 주관기관을 같다고 추측하지 않습니다. 공식
              사업명은 공고 원문에서 직접 확인합니다.
            </p>
          )}
          {renderMappingToggle()}
        </div>
      );
    }

    if (producer === "excel") {
      return (
        <div className={styles.evidenceBody}>
          <div className={styles.sourceHeader}>
            <span
              className={`${styles.tag} ${
                isBlocked
                  ? styles.tagWarn
                  : s === "note"
                  ? styles.tagNote
                  : styles.tagNew
              }`}
            >
              Excel export · 실적 시트
            </span>
            <span className={styles.sourceTitle}>
              한결정밀 · 연간 실적 시연
            </span>
            <span className={styles.chip}>단위: 억원 / 별도 / 실적</span>
          </div>

          {s === "note" || isBlocked ? (
            <div className={styles.quoteBox}>
              <mark>수요 증가가 영향을 주었을 가능성이 있습니다.</mark>
            </div>
          ) : (
            <table
              className={styles.sheetTable}
              aria-label="회사·기간·단위와 매출 근거"
            >
              <thead>
                <tr>
                  <th></th>
                  <th>A</th>
                  <th>B</th>
                  <th>C</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th>1</th>
                  <td>회사</td>
                  <td className={styles.sheetContext}>한결정밀</td>
                  <td className={styles.sheetContext}>
                    단위: 억원 / 별도 / 실적
                  </td>
                </tr>
                <tr>
                  <th>2</th>
                  <td>항목</td>
                  <td
                    className={
                      s === "second" ? styles.sheetContext : undefined
                    }
                  >
                    2024년
                  </td>
                  <td className={styles.sheetContext}>2025년</td>
                </tr>
                <tr>
                  <th>3</th>
                  <td className={styles.sheetContext}>매출</td>
                  <td
                    className={s === "second" ? styles.sheetCell : undefined}
                  >
                    100
                  </td>
                  <td className={styles.sheetCell}>120</td>
                </tr>
                <tr>
                  <th>4</th>
                  <td>영업이익</td>
                  <td>8</td>
                  <td>10</td>
                </tr>
              </tbody>
            </table>
          )}

          <EvidenceLocationCard
            location={
              s === "note"
                ? "Excel AI Agent 분석 리포트 · 원인 분석 추정 항목"
                : isBlocked
                ? "스프레드시트 셀값 (인과관계 증명 불가)"
                : s === "second"
                ? "[실적] 시트 · B3(2024년 100억) 및 C3(2025년 120억) 셀 연계 계산"
                : "[실적] 시트 · C3 셀 (2025년 매출 120억) / 문맥 셀: B1(한결정밀), A3(매출), C1(단위: 억원)"
            }
            verification={
              s === "note"
                ? "매출 증가 원인에 대한 모델의 추정 메모 (스프레드시트 원천 확정 사실과 구분하여 외부 분석 카드로 보관)"
                : isBlocked
                ? "매출 셀값이 증가했다는 수치만으로 '수요 증가가 매출 증가의 직접 원인'임을 확정할 수 없어 인과관계 선 생성 차단"
                : s === "second"
                ? "동일 기준 실적 비교 산식: (120억 - 100억) ÷ 100억 × 100 = 전년 대비 +20% 증가 산출"
                : "한결정밀의 2025년 별도 기준 매출 실적이 120억원임을 확인 (신규 노드 없이 기존 기업 속성 보강)"
            }
            rawPath={
              s === "note" || isBlocked
                ? "analysis.insightReport.insights[0].cause"
                : `analysis.workbook.sheets[0].regions[0].previewRows → 실적!${
                    s === "second" ? "B3 + C3 (증감률 계산)" : "C3 + B1/A3/C2/C1"
                  }`
            }
          />

          <div className={styles.routeGrid}>
            <div className={styles.routeItem}>
              <small>DOCUMENT · 출처</small>
              <strong>제공된 워크북 셀 스냅샷</strong>
            </div>
            <div className={styles.routeItem}>
              <small>CLAIM · 정리할 내용</small>
              <strong>
                {s === "second"
                  ? "동일 기준의 매출 20% 증가"
                  : isBlocked
                  ? "원인을 확정할 직접 근거 없음"
                  : s === "note"
                  ? "생산자가 제안한 원인 추정"
                  : "2025년 별도 매출 실적 120억원"}
              </strong>
            </div>
            <div className={styles.routeItem}>
              <small>지도에서의 표시</small>
              <strong className={styles.routeTarget}>
                {s === "second"
                  ? "회사 비교 카드 (산식과 두 셀 보관)"
                  : isBlocked
                  ? "반영 제외 (인과관계 선 금지)"
                  : s === "note"
                  ? "외부 분석 카드만"
                  : "회사 속성·지표 카드 (새 노드/선 0개)"}
              </strong>
            </div>
          </div>

          {isBlocked && (
            <div className={styles.factWarning}>
              🚫 매출이 늘었다는 셀값으로 증가 원인을 증명할 수는 없습니다. 이
              후보는 지도에 반영하지 않습니다.
            </div>
          )}
          {s === "note" && (
            <div className={styles.factWarning}>
              💡 원인 추정은 외부 분석 카드로만 보관하며, 확정 사실과 구분합니다.
            </div>
          )}
          {!isBlocked && s !== "note" && (
            <p className={styles.subNote}>
              {s === "second"
                ? "(120 − 100) ÷ 100 × 100 = 20%. 같은 회사·지표·통화·범위의 실적끼리 비교합니다."
                : "원본 Excel을 직접 열거나 재계산한 것이 아닙니다. 제공된 셀과 문맥을 확인하고 이전 기간의 근거는 온전히 남깁니다."}
            </p>
          )}
          {renderMappingToggle()}
        </div>
      );
    }

    return null;
  };

  // 후보 목록 렌더링 헬퍼 (Tab 01 및 Tab 02 공용)
  const renderCandidateList = () => (
    <div className={styles.candidateList}>
      {bundleData?.rows?.map((row: CandidateRow) => {
        const isSelected = selectedItemId === row.id;
        const isChecked = enabledItemIds.has(row.id);
        const isBlocked = Boolean(row.blocked);
        const isPrereqMet = isPrerequisiteMet(
          row.id,
          producer,
          enabledItemIds,
          bundleData?.rows,
        );
        const isDisabled = isBlocked || (!isChecked && !isPrereqMet);

        let tagClass = styles.tagPanel;
        if (row.cls === "new") tagClass = styles.tagNew;
        if (row.cls === "note") tagClass = styles.tagNote;
        if (row.cls === "warn" || isBlocked) tagClass = styles.tagWarn;

        return (
          <div
            key={row.id}
            className={`${styles.candidateRow} ${
              isSelected ? styles.selectedRow : ""
            } ${isBlocked ? styles.blockedRow : ""} ${
              isDisabled && !isBlocked ? styles.candidatePillDisabled : ""
            }`}
            onClick={() => setSelectedItemId(row.id)}
          >
            <div className={styles.checkboxContainer}>
              <input
                type="checkbox"
                checked={isChecked}
                disabled={isDisabled}
                onChange={() => handleToggleItem(row.id, isBlocked)}
                onClick={(e) => e.stopPropagation()}
                aria-label={`${row.title} 반영 여부`}
              />
            </div>
            <div className={styles.candidateContent}>
              <div className={styles.candidateTitle}>
                {row.title}
                {!isPrereqMet && !isChecked && !isBlocked && (
                  <span
                    style={{
                      fontSize: "10.5px",
                      marginLeft: "6px",
                      color: "#fbbf24",
                      fontWeight: "normal",
                    }}
                  >
                    🔒선행필요
                  </span>
                )}
              </div>
              <div className={styles.candidateMeta}>
                <span className={`${styles.tag} ${tagClass}`}>{row.kind}</span>
                <span className={styles.candidateSub}>{row.sub}</span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );

  // 은은한 반짝임(Flash) 클래스 추출 헬퍼
  const getRowFlashClass = (rowId: string): string => {
    const flashType = flashedItemIds.get(rowId);
    if (flashType === "appear") return styles.rowFlashAppear;
    if (flashType === "disappear") return styles.rowFlashDisappear;
    return "";
  };

  const getSvgFlashClass = (rowId: string): string => {
    const flashType = flashedItemIds.get(rowId);
    if (flashType === "appear") return styles.svgFlashAppear;
    if (flashType === "disappear") return styles.svgFlashDisappear;
    return "";
  };

  // 속성 전후 비교표 렌더링 헬퍼 (Tab 02)
  const renderComparisonTable = (side: "before" | "after") => {
    if (producer === "news") {
      const isMainChecked = enabledItemIds.has("main");
      const isSecondChecked = enabledItemIds.has("second");
      return (
        <table className={styles.compareTable}>
          <tbody>
            <tr>
              <th>기준 기업</th>
              <td>한결정밀 (정밀부품 가공 · 본사)</td>
            </tr>
            <tr
              className={`
                ${
                  side === "after"
                    ? isMainChecked
                      ? styles.compareRowChanged
                      : styles.compareRowExcluded
                    : ""
                }
                ${side === "after" ? getRowFlashClass("main") : ""}
              `}
            >
              <th>공급 파트너</th>
              <td>
                {side === "before" ? (
                  <span style={{ color: "#7b93ae" }}>정보 없음 (미등록)</span>
                ) : isMainChecked ? (
                  <span>
                    <strong>누리소재</strong> (2026.09.25 공급계약 신규 연결)
                  </span>
                ) : (
                  <span>
                    — <span className={styles.excludedBadge}>반영 제외됨</span>
                    <small style={{ color: "#94a7c0", marginLeft: "6px" }}>
                      (상단 '누리소재 공급계약' 미체크 상태)
                    </small>
                  </span>
                )}
              </td>
            </tr>
            <tr>
              <th>소유 시설</th>
              <td>부산공장 (운영 중 · 간선 1개)</td>
            </tr>
            <tr
              className={`
                ${
                  side === "after"
                    ? isSecondChecked
                      ? styles.compareRowChanged
                      : styles.compareRowExcluded
                    : ""
                }
                ${side === "after" ? getRowFlashClass("second") : ""}
              `}
            >
              <th>리포트 카드</th>
              <td>
                {side === "before" ? (
                  <span style={{ color: "#7b93ae" }}>등록된 계획 없음</span>
                ) : isSecondChecked ? (
                  <span>
                    <span className={styles.reportBadge}>리포트 보관</span>{" "}
                    2027년 증산 검토 계획 카드 보관
                  </span>
                ) : (
                  <span>
                    —{" "}
                    <span className={styles.excludedBadge}>
                      리포트 반영 제외
                    </span>
                    <small style={{ color: "#94a7c0", marginLeft: "6px" }}>
                      (상단 '2027년 증산 검토' 미체크 상태)
                    </small>
                  </span>
                )}
              </td>
            </tr>
            <tr
              className={`
                ${
                  side === "after"
                    ? isMainChecked
                      ? styles.compareRowPending
                      : styles.compareRowExcluded
                    : ""
                }
                ${side === "after" ? getRowFlashClass("main") : ""}
              `}
            >
              <th>AI 분석 갱신</th>
              <td>
                {side === "before" ? (
                  <span style={{ color: "#7b93ae" }}>기존 2024 분석 유지</span>
                ) : isMainChecked ? (
                  <span>
                    <strong>종합 리포트 갱신 예정</strong> (누리소재 공급계약
                    반영)
                  </span>
                ) : (
                  <span>
                    —{" "}
                    <span className={styles.excludedBadge}>
                      계약 미반영 시 미갱신
                    </span>
                    <small style={{ color: "#94a7c0", marginLeft: "6px" }}>
                      (공급계약 미반영으로 종합 리포트 갱신 생략)
                    </small>
                  </span>
                )}
              </td>
            </tr>
          </tbody>
        </table>
      );
    } else if (producer === "gov") {
      const isMainChecked = enabledItemIds.has("main");
      const isSecondChecked = enabledItemIds.has("second");
      const isNoteChecked = enabledItemIds.has("note");
      return (
        <table className={styles.compareTable}>
          <tbody>
            <tr
              className={`
                ${
                  side === "after"
                    ? isMainChecked
                      ? styles.compareRowChanged
                      : styles.compareRowExcluded
                    : ""
                }
                ${side === "after" ? getRowFlashClass("main") : ""}
              `}
            >
              <th>주관 기관</th>
              <td>
                {side === "before" ? (
                  <span style={{ color: "#7b93ae" }}>정보 없음 (미등록)</span>
                ) : isMainChecked ? (
                  <span>
                    <strong>새봄산업지원원</strong> (신규 주관기관 노드)
                  </span>
                ) : (
                  <span>
                    — <span className={styles.excludedBadge}>반영 제외됨</span>
                    <small style={{ color: "#94a7c0", marginLeft: "6px" }}>
                      (상단 '새봄산업지원원 주관' 미체크 상태)
                    </small>
                  </span>
                )}
              </td>
            </tr>
            <tr
              className={`
                ${
                  side === "after"
                    ? isMainChecked
                      ? styles.compareRowChanged
                      : styles.compareRowExcluded
                    : ""
                }
                ${side === "after" ? getRowFlashClass("main") : ""}
              `}
            >
              <th>지원 사업</th>
              <td>
                {side === "before" ? (
                  <span style={{ color: "#7b93ae" }}>공고 미등록</span>
                ) : isMainChecked ? (
                  <span>
                    <strong>2026 제조데이터 실증지원 사업</strong> (신규 사업
                    노드)
                  </span>
                ) : (
                  <span>
                    — <span className={styles.excludedBadge}>반영 제외됨</span>
                    <small style={{ color: "#94a7c0", marginLeft: "6px" }}>
                      (상단 공고 노드 생성 미체크 상태)
                    </small>
                  </span>
                )}
              </td>
            </tr>
            <tr
              className={`
                ${
                  side === "after"
                    ? isSecondChecked
                      ? styles.compareRowChanged
                      : styles.compareRowExcluded
                    : ""
                }
                ${side === "after" ? getRowFlashClass("second") : ""}
              `}
            >
              <th>사업 세부 속성</th>
              <td>
                {side === "before" ? (
                  <span style={{ color: "#7b93ae" }}>속성 없음</span>
                ) : isSecondChecked ? (
                  <span>
                    <strong>총 20억원 · 기업당 최대 1억원 · 마감 10.30</strong>
                  </span>
                ) : (
                  <span>
                    —{" "}
                    <span className={styles.excludedBadge}>
                      속성 반영 제외 (기본명칭만 등록)
                    </span>
                    <small style={{ color: "#94a7c0", marginLeft: "6px" }}>
                      (상단 '사업 예산·지원 상한' 미체크 상태)
                    </small>
                  </span>
                )}
              </td>
            </tr>
            <tr
              className={`
                ${
                  side === "after"
                    ? isNoteChecked
                      ? styles.compareRowChanged
                      : styles.compareRowExcluded
                    : ""
                }
                ${side === "after" ? getRowFlashClass("note") : ""}
              `}
            >
              <th>추천의견 리포트</th>
              <td>
                {side === "before" ? (
                  <span style={{ color: "#7b93ae" }}>추천의견 없음</span>
                ) : isNoteChecked ? (
                  <span>
                    <span className={styles.reportBadge}>리포트 보관</span>{" "}
                    GovInsight 신청 요건 검토 권고의견
                  </span>
                ) : (
                  <span>
                    —{" "}
                    <span className={styles.excludedBadge}>
                      리포트 반영 제외
                    </span>
                    <small style={{ color: "#94a7c0", marginLeft: "6px" }}>
                      (상단 '신청 검토 의견' 미체크 상태)
                    </small>
                  </span>
                )}
              </td>
            </tr>
          </tbody>
        </table>
      );
    } else {
      // excel
      const isMainChecked = enabledItemIds.has("main");
      const isSecondChecked = enabledItemIds.has("second");
      const isNoteChecked = enabledItemIds.has("note");
      return (
        <table className={styles.compareTable}>
          <tbody>
            <tr>
              <th>기준 기업</th>
              <td>
                {side === "before" ? (
                  "한결정밀"
                ) : (
                  <span>
                    한결정밀{" "}
                    {isMainChecked && (
                      <span className={styles.tagWarn}>속성 보강</span>
                    )}
                  </span>
                )}
              </td>
            </tr>
            <tr>
              <th>2024년 별도 매출</th>
              <td>100억원 (실적!B3 기준치 유지)</td>
            </tr>
            <tr
              className={`
                ${
                  side === "after"
                    ? isMainChecked
                      ? styles.compareRowChanged
                      : styles.compareRowExcluded
                    : ""
                }
                ${side === "after" ? getRowFlashClass("main") : ""}
              `}
            >
              <th>2025년 별도 매출</th>
              <td>
                {side === "before" ? (
                  <span style={{ color: "#7b93ae" }}>미등록 (공백)</span>
                ) : isMainChecked ? (
                  <span>
                    <strong>120억원</strong> (실적!C3 발췌 · 신규 적재)
                  </span>
                ) : (
                  <span>
                    — <span className={styles.excludedBadge}>매출 반영 제외</span>
                    <small style={{ color: "#94a7c0", marginLeft: "6px" }}>
                      (상단 '2025년 매출 120억원' 미체크 상태 · 기존 100억원만 유지)
                    </small>
                  </span>
                )}
              </td>
            </tr>
            <tr
              className={`
                ${
                  side === "after"
                    ? isSecondChecked
                      ? styles.compareRowChanged
                      : styles.compareRowExcluded
                    : ""
                }
                ${side === "after" ? getRowFlashClass("second") : ""}
              `}
            >
              <th>전년 대비 증감률</th>
              <td>
                {side === "before" ? (
                  <span style={{ color: "#7b93ae" }}>산식 없음</span>
                ) : isSecondChecked ? (
                  <span>
                    <strong>+20% 증가</strong> ((120 - 100) / 100 × 100)
                  </span>
                ) : (
                  <span>
                    — <span className={styles.excludedBadge}>증감률 제외</span>
                    <small style={{ color: "#94a7c0", marginLeft: "6px" }}>
                      (선행 2025년 매출 120억원 미체크로 계산 제외)
                    </small>
                  </span>
                )}
              </td>
            </tr>
            <tr
              className={`
                ${
                  side === "after"
                    ? isNoteChecked
                      ? styles.compareRowChanged
                      : styles.compareRowExcluded
                    : ""
                }
                ${side === "after" ? getRowFlashClass("note") : ""}
              `}
            >
              <th>수요추정 리포트</th>
              <td>
                {side === "before" ? (
                  <span style={{ color: "#7b93ae" }}>추정 없음</span>
                ) : isNoteChecked ? (
                  <span>
                    <span className={styles.reportBadge}>리포트 보관</span>{" "}
                    수요 증가 영향 가능성 메모 보관
                  </span>
                ) : (
                  <span>
                    —{" "}
                    <span className={styles.excludedBadge}>
                      리포트 반영 제외
                    </span>
                    <small style={{ color: "#94a7c0", marginLeft: "6px" }}>
                      (상단 '수요 증가 가능성' 미체크 상태)
                    </small>
                  </span>
                )}
              </td>
            </tr>
          </tbody>
        </table>
      );
    }
  };

  // 2D SVG 경량 듀얼 다이어그램 렌더링 헬퍼 (Tab 02)
  const renderDualGraph = (side: "before" | "after") => {
    const isBefore = side === "before";

    const defs = (
      <defs>
        <marker
          id={`arrow-default-${side}`}
          viewBox="0 0 10 10"
          refX="7"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#59708d" />
        </marker>
        <marker
          id={`arrow-cyan-${side}`}
          viewBox="0 0 10 10"
          refX="7"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#64d1ef" />
        </marker>
        <marker
          id={`arrow-sky-${side}`}
          viewBox="0 0 10 10"
          refX="7"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#38bdf8" />
        </marker>
      </defs>
    );

    if (producer === "news") {
      const isMainChecked = enabledItemIds.has("main");
      const showNewEdge = !isBefore && isMainChecked;
      const isSelected = selectedItemId === "main";

      return (
        <svg
          className={styles.graphSvg}
          viewBox="0 0 540 220"
          role="img"
          aria-label={`News Agent ${side.toUpperCase()} 그래프`}
        >
          {defs}

          {/* 1. 누리소재 노드 영역 (x: 40, y: 55, w: 130, h: 46) */}
          {showNewEdge ? (
            <g
              className={`${styles.svgNode} ${
                isSelected ? styles.svgNodeSelected : ""
              } ${side === "after" ? getSvgFlashClass("main") : ""}`}
              onClick={() => setSelectedItemId("main")}
            >
              <rect
                x="40"
                y="55"
                width="130"
                height="46"
                rx="7"
                fill="#133144"
                stroke={isSelected ? "#64d1ef" : "#3b82a6"}
                strokeWidth={isSelected ? 2 : 1.5}
              />
              <rect
                x="44"
                y="59"
                width="34"
                height="15"
                rx="3"
                fill="#1e485f"
              />
              <text
                x="48"
                y="70"
                fill="#64d1ef"
                fontSize="9"
                fontWeight="700"
              >
                + 신규
              </text>
              <text
                x="85"
                y="73"
                fill="#ffffff"
                fontSize="13"
                fontWeight="700"
              >
                누리소재
              </text>
              <text x="85" y="90" fill="#94a7c0" fontSize="10">
                소재 공급 기업
              </text>
            </g>
          ) : (
            <g
              opacity="0.6"
              className={side === "after" ? getSvgFlashClass("main") : ""}
            >
              <rect
                x="40"
                y="55"
                width="130"
                height="46"
                rx="7"
                fill="#0d1726"
                stroke="#25354e"
                strokeWidth="1.2"
                strokeDasharray="4 3"
              />
              <text
                x="105"
                y="76"
                textAnchor="middle"
                fill="#64748b"
                fontSize="11"
                fontWeight="600"
              >
                공급 계약처 없음
              </text>
              <text
                x="105"
                y="91"
                textAnchor="middle"
                fill="#475569"
                fontSize="9.5"
              >
                (2026.09 미등록)
              </text>
            </g>
          )}

          {/* 2. 공급 계약 간선 (누리소재 -> 한결정밀) */}
          {showNewEdge ? (
            <g
              className={`${styles.svgEdge} ${
                side === "after" ? getSvgFlashClass("main") : ""
              }`}
              onClick={() => setSelectedItemId("main")}
            >
              <line
                x1="170"
                y1="78"
                x2="280"
                y2="78"
                stroke="#64d1ef"
                strokeWidth="2.2"
                markerEnd={`url(#arrow-cyan-${side})`}
              />
              <rect
                x="180"
                y="63"
                width="90"
                height="16"
                rx="4"
                fill="#0d2434"
                stroke="#2c5b73"
                strokeWidth="1"
              />
              <text
                x="225"
                y="75"
                textAnchor="middle"
                fill="#64d1ef"
                fontSize="10"
                fontWeight="700"
              >
                공급 계약 (09.25)
              </text>
            </g>
          ) : (
            <line
              x1="170"
              y1="78"
              x2="280"
              y2="78"
              stroke="#1e2c3e"
              strokeWidth="1.2"
              strokeDasharray="4 3"
            />
          )}

          {/* 3. 한결정밀 노드 (x: 290, y: 55, w: 135, h: 46) - 좌우 위치 100% 동일 */}
          <g className={styles.svgNode}>
            <rect
              x="290"
              y="55"
              width="135"
              height="46"
              rx="7"
              fill="#122438"
              stroke="#345474"
              strokeWidth="1.5"
            />
            <text
              x="357"
              y="74"
              textAnchor="middle"
              fill="#ffffff"
              fontSize="13"
              fontWeight="700"
            >
              한결정밀
            </text>
            <text
              x="357"
              y="90"
              textAnchor="middle"
              fill="#94a7c0"
              fontSize="10"
            >
              정밀부품 가공 · 본사
            </text>
          </g>

          {/* 4. 운영 간선 (한결정밀 -> 부산공장) */}
          <line
            x1="357"
            y1="101"
            x2="415"
            y2="145"
            stroke="#405973"
            strokeWidth="1.5"
            markerEnd={`url(#arrow-default-${side})`}
          />
          <rect x="365" y="115" width="34" height="15" rx="3" fill="#0b1728" />
          <text
            x="382"
            y="126"
            textAnchor="middle"
            fill="#7d96b2"
            fontSize="9.5"
          >
            운영
          </text>

          {/* 5. 부산공장 노드 (x: 375, y: 145, w: 105, h: 36) - 좌우 위치 100% 동일 */}
          <g className={styles.svgNode}>
            <rect
              x="375"
              y="145"
              width="105"
              height="36"
              rx="6"
              fill="#101c2d"
              stroke="#273d56"
              strokeWidth="1.2"
            />
            <text
              x="427"
              y="163"
              textAnchor="middle"
              fill="#e2e8f0"
              fontSize="11.5"
              fontWeight="600"
            >
              부산공장
            </text>
            <text
              x="427"
              y="174"
              textAnchor="middle"
              fill="#71869e"
              fontSize="9"
            >
              제조 시설
            </text>
          </g>

          {/* 하단 요약 안내 */}
          <text
            x="270"
            y="206"
            textAnchor="middle"
            fill={showNewEdge ? "#64d1ef" : "#64748b"}
            fontSize="11"
            fontWeight="600"
          >
            {showNewEdge
              ? "✨ 누리소재 → 한결정밀 공급 계약 선(Edge) 1개 추가 예정"
              : "기준 상태: 기존 등록된 한결정밀 - 부산공장 연결 유지"}
          </text>
        </svg>
      );
    } else if (producer === "gov") {
      const isMainChecked = enabledItemIds.has("main");
      const isSecondChecked = enabledItemIds.has("second");
      const showGovNodes = !isBefore && isMainChecked;
      const isMainSelected = selectedItemId === "main";
      const isSecondSelected = selectedItemId === "second";

      return (
        <svg
          className={styles.graphSvg}
          viewBox="0 0 540 220"
          role="img"
          aria-label={`GovInsight ${side.toUpperCase()} 그래프`}
        >
          {defs}

          {showGovNodes ? (
            <g>
              {/* 1. 새봄산업지원원 노드 (x: 40, y: 72, w: 160, h: 52) */}
              <g
                className={`${styles.svgNode} ${
                  isMainSelected ? styles.svgNodeSelected : ""
                } ${side === "after" ? getSvgFlashClass("main") : ""}`}
                onClick={() => setSelectedItemId("main")}
              >
                <rect
                  x="40"
                  y="72"
                  width="160"
                  height="52"
                  rx="7"
                  fill="#172b3c"
                  stroke={isMainSelected ? "#64d1ef" : "#3b7296"}
                  strokeWidth={isMainSelected ? 2 : 1.5}
                />
                <rect
                  x="44"
                  y="76"
                  width="36"
                  height="16"
                  rx="3"
                  fill="#1b455f"
                />
                <text
                  x="48"
                  y="88"
                  fill="#64d1ef"
                  fontSize="9.5"
                  fontWeight="700"
                >
                  + 신규
                </text>
                <text
                  x="88"
                  y="91"
                  fill="#ffffff"
                  fontSize="13"
                  fontWeight="700"
                >
                  새봄산업지원원
                </text>
                <text x="88" y="111" fill="#94a7c0" fontSize="10">
                  공고 주관기관 (ORGANIZATION)
                </text>
              </g>

              {/* 2. 주관 간선 (새봄산업지원원 -> 실증지원) */}
              <g
                className={`${styles.svgEdge} ${
                  side === "after" ? getSvgFlashClass("main") : ""
                }`}
                onClick={() => setSelectedItemId("main")}
              >
                <line
                  x1="200"
                  y1="98"
                  x2="280"
                  y2="98"
                  stroke="#38bdf8"
                  strokeWidth="2.2"
                  markerEnd={`url(#arrow-sky-${side})`}
                />
                <rect
                  x="215"
                  y="83"
                  width="50"
                  height="18"
                  rx="4"
                  fill="#0c1d2e"
                  stroke="#244b68"
                  strokeWidth="1"
                />
                <text
                  x="240"
                  y="96"
                  textAnchor="middle"
                  fill="#38bdf8"
                  fontSize="10"
                  fontWeight="700"
                >
                  주관
                </text>
              </g>

              {/* 3. 2026 제조데이터 실증지원 사업 노드 (x: 280, y: 72, w: 220, h: 52) */}
              <g
                className={`${styles.svgNode} ${
                  isSecondSelected ? styles.svgNodeSelected : ""
                } ${
                  side === "after"
                    ? getSvgFlashClass("second") || getSvgFlashClass("main")
                    : ""
                }`}
                onClick={() => setSelectedItemId("second")}
              >
                <rect
                  x="280"
                  y="72"
                  width="220"
                  height="52"
                  rx="7"
                  fill="#241e38"
                  stroke={isSecondSelected ? "#a855f7" : "#5d4681"}
                  strokeWidth={isSecondSelected ? 2 : 1.5}
                />
                <rect
                  x="284"
                  y="76"
                  width="36"
                  height="16"
                  rx="3"
                  fill="#3a2a56"
                />
                <text
                  x="288"
                  y="88"
                  fill="#c084fc"
                  fontSize="9.5"
                  fontWeight="700"
                >
                  + 신규
                </text>
                <text
                  x="328"
                  y="91"
                  fill="#ffffff"
                  fontSize="13"
                  fontWeight="700"
                >
                  제조데이터 실증지원
                </text>
                <text
                  x="390"
                  y="111"
                  textAnchor="middle"
                  fill={isSecondChecked ? "#c4b5fd" : "#718096"}
                  fontSize={isSecondChecked ? "10" : "9"}
                >
                  {isSecondChecked
                    ? "총 20억원 · 기업당 최대 1억원"
                    : "(세부 속성 반영 제외됨)"}
                </text>
              </g>

              {/* 하단 요약 안내 */}
              <g>
                <rect
                  x="60"
                  y="170"
                  width="420"
                  height="26"
                  rx="5"
                  fill="rgba(14, 116, 144, 0.18)"
                  stroke="#0891b2"
                  strokeWidth="1"
                />
                <text
                  x="270"
                  y="187"
                  textAnchor="middle"
                  fill="#38bdf8"
                  fontSize="11"
                  fontWeight="600"
                >
                  ✨ 정부지원 공고 독립 지식 묶음 등록 (신규 노드 2개 · 주관 간선 1개)
                </text>
              </g>
            </g>
          ) : (
            <g
              opacity="0.75"
              className={side === "after" ? getSvgFlashClass("main") : ""}
            >
              <rect
                x="70"
                y="55"
                width="400"
                height="100"
                rx="8"
                fill="#0d1726"
                stroke="#25354e"
                strokeWidth="1.2"
                strokeDasharray="4 3"
              />
              <text
                x="270"
                y="100"
                textAnchor="middle"
                fill="#64748b"
                fontSize="12.5"
                fontWeight="600"
              >
                {isBefore
                  ? "정부지원사업 공고 미등록"
                  : "— 주관 간선 및 공고 노드 반영 제외됨 —"}
              </text>
              <text
                x="270"
                y="120"
                textAnchor="middle"
                fill="#475569"
                fontSize="11"
              >
                {isBefore
                  ? "(새봄산업지원원 및 실증지원 사업 정보 없음)"
                  : "(상단 '새봄산업지원원 → 실증지원' 체크 시 생성)"}
              </text>
            </g>
          )}
        </svg>
      );
    } else {
      // Excel Agent
      const isMainChecked = enabledItemIds.has("main");
      const isSecondChecked = enabledItemIds.has("second");
      const isMainSelected = selectedItemId === "main";
      const isSecondSelected = selectedItemId === "second";
      const showNewMetric = !isBefore && isMainChecked;

      return (
        <svg
          className={styles.graphSvg}
          viewBox="0 0 540 220"
          role="img"
          aria-label={`Excel Agent ${side.toUpperCase()} 그래프`}
        >
          {defs}

          {/* 1. 한결정밀 노드 (x: 140, y: 45, w: 155, h: 48) */}
          <g
            className={`${styles.svgNode} ${
              isMainSelected ? styles.svgNodeSelected : ""
            }`}
            onClick={() => setSelectedItemId("main")}
          >
            <rect
              x="140"
              y="45"
              width="155"
              height="48"
              rx="7"
              fill={showNewMetric ? "#133549" : "#122438"}
              stroke={
                showNewMetric
                  ? isMainSelected
                    ? "#64d1ef"
                    : "#f59e0b"
                  : "#345474"
              }
              strokeWidth={showNewMetric ? 2 : 1.5}
            />
            {showNewMetric && (
              <>
                <rect
                  x="142"
                  y="47"
                  width="46"
                  height="15"
                  rx="3"
                  fill="#452709"
                />
                <text
                  x="146"
                  y="58"
                  fill="#f59e0b"
                  fontSize="9"
                  fontWeight="700"
                >
                  값 보강
                </text>
              </>
            )}
            <text
              x="217"
              y={showNewMetric ? 70 : 66}
              textAnchor="middle"
              fill="#ffffff"
              fontSize="13"
              fontWeight="700"
            >
              한결정밀
            </text>
            <text
              x="217"
              y={showNewMetric ? 84 : 82}
              textAnchor="middle"
              fill="#94a7c0"
              fontSize="10"
            >
              정밀부품 가공 · 본사
            </text>
          </g>

          {/* 2. 운영 간선 (한결정밀 -> 부산공장) */}
          <line
            x1="295"
            y1="69"
            x2="375"
            y2="69"
            stroke="#405973"
            strokeWidth="1.5"
            markerEnd={`url(#arrow-default-${side})`}
          />
          <rect x="323" y="60" width="28" height="15" rx="3" fill="#0b1728" />
          <text
            x="337"
            y="71"
            textAnchor="middle"
            fill="#7d96b2"
            fontSize="9.5"
          >
            운영
          </text>

          {/* 3. 부산공장 노드 (x: 380, y: 48, w: 105, h: 42) */}
          <g className={styles.svgNode}>
            <rect
              x="380"
              y="48"
              width="105"
              height="42"
              rx="6"
              fill="#101c2d"
              stroke="#273d56"
              strokeWidth="1.2"
            />
            <text
              x="432"
              y="68"
              textAnchor="middle"
              fill="#e2e8f0"
              fontSize="12"
              fontWeight="600"
            >
              부산공장
            </text>
            <text
              x="432"
              y="81"
              textAnchor="middle"
              fill="#71869e"
              fontSize="9"
            >
              제조 시설
            </text>
          </g>

          {/* 4. 실적 메트릭 카드 */}
          {showNewMetric ? (
            <g
              className={`${styles.svgNode} ${
                isSecondSelected ? styles.svgNodeSelected : ""
              } ${
                side === "after"
                  ? getSvgFlashClass("second") || getSvgFlashClass("main")
                  : ""
              }`}
              onClick={() => setSelectedItemId("second")}
            >
              <line
                x1="217"
                y1="93"
                x2="217"
                y2="115"
                stroke="#f59e0b"
                strokeWidth="1.5"
                strokeDasharray="3 3"
              />
              <rect
                x="115"
                y="115"
                width="215"
                height="58"
                rx="7"
                fill="#0d2638"
                stroke="#2e6d8a"
                strokeWidth="1.5"
              />
              <text x="127" y="132" fill="#94a7c0" fontSize="10">
                📊 2025년 별도 매출 (실적!C3)
              </text>
              <text
                x="127"
                y="152"
                fill="#64d1ef"
                fontSize="15"
                fontWeight="700"
              >
                120억원
              </text>
              {isSecondChecked && (
                <text
                  x="188"
                  y="152"
                  fill="#38bdf8"
                  fontSize="11"
                  fontWeight="700"
                >
                  (전년 대비 +20% ↑)
                </text>
              )}
              <text x="127" y="165" fill="#708ea8" fontSize="9">
                계산 산식: (120억 - 100억) / 100억 × 100
              </text>
            </g>
          ) : (
            <g
              opacity="0.75"
              className={side === "after" ? getSvgFlashClass("main") : ""}
            >
              <line
                x1="217"
                y1="93"
                x2="217"
                y2="118"
                stroke="#334861"
                strokeWidth="1.2"
                strokeDasharray="3 3"
              />
              <rect
                x="125"
                y="118"
                width="190"
                height="52"
                rx="7"
                fill="#0e1828"
                stroke="#223348"
                strokeWidth="1.2"
              />
              <text x="137" y="136" fill="#7e94ac" fontSize="10">
                2024년 별도 매출: 100억원
              </text>
              <text x="137" y="154" fill="#526880" fontSize="10">
                2025년 실적 데이터 미등록
              </text>
            </g>
          )}

          {/* 5. 0개 연결 원칙 안내 뱃지 */}
          <g>
            <rect
              x="60"
              y="185"
              width="420"
              height="24"
              rx="5"
              fill="rgba(14, 116, 144, 0.18)"
              stroke="#0891b2"
              strokeWidth="1"
            />
            <text
              x="270"
              y="201"
              textAnchor="middle"
              fill="#38bdf8"
              fontSize="10.5"
              fontWeight="700"
            >
              ✨ 신규 노드 0개 · 신규 간선 0개 (기존 한결정밀 노드의 수치
              속성만 보강)
            </text>
          </g>
        </svg>
      );
    }
  };

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        {/* 1. 상단 브랜드 바 */}
        <header className={styles.brandBar}>
          <span className={styles.brand}>
            ONTOLOGY <em>MAP</em> / LITE
          </span>
          <span className={styles.brandSub}>자료 검토</span>
          <span className={styles.grow} />
          <span className={`${styles.tag} ${styles.tagNew}`}>
            시연 및 실시간 검토 모드
          </span>
          <button
            type="button"
            className={styles.closeBtn}
            onClick={onClose}
            aria-label="모달 닫기"
          >
            ✕
          </button>
        </header>

        {/* 2. 프로듀서 선택 바 */}
        <div className={styles.producerBar}>
          <div className={styles.producerTabs}>
            <button
              type="button"
              className={`${styles.producerCardBtn} ${
                producer === "news" ? styles.activeProducer : ""
              }`}
              onClick={() => setProducer("news")}
            >
              📰 News Agent · 기사
            </button>
            <button
              type="button"
              className={`${styles.producerCardBtn} ${
                producer === "gov" ? styles.activeProducer : ""
              }`}
              onClick={() => setProducer("gov")}
            >
              🏛️ GovInsight · 공고 분석
            </button>
            <button
              type="button"
              className={`${styles.producerCardBtn} ${
                producer === "excel" ? styles.activeProducer : ""
              }`}
              onClick={() => setProducer("excel")}
            >
              📊 Excel Agent · 실적 분석
            </button>
          </div>

          <span className={styles.chip}>{bundleData?.filename || ""}</span>
          <span className={styles.nativePath}>{bundleData?.profile || ""}</span>

          <span className={styles.grow} />

          <button
            type="button"
            className={styles.actionBtn}
            onClick={() => setShowManualInput(!showManualInput)}
          >
            {showManualInput ? "시연 프리셋 모드" : "직접 JSON 입력"}
          </button>

          <button
            type="button"
            className={styles.actionBtn}
            onClick={handleResetDemoSeed}
            title="시연 기준 데이터(한결정밀·부산공장)를 초기화합니다"
          >
            시연 기준 시드 복원
          </button>
        </div>

        {/* 수동 JSON 입력 드롭다운 패널 */}
        {showManualInput && (
          <div
            style={{
              padding: "12px 24px",
              background: "#080f1d",
              borderBottom: "1px solid #25354e",
              display: "flex",
              flexDirection: "column",
              gap: "8px",
            }}
          >
            <span style={{ fontSize: "12px", color: "#94a7c0" }}>
              생산자({producer}) 규격의 원본 native JSON 데이터를 붙여넣으세요:
            </span>
            <textarea
              value={manualJsonText}
              onChange={(e) => setManualJsonText(e.target.value)}
              placeholder="{\n  'title': '...', \n  'analysis': { ... }\n}"
              rows={4}
              style={{
                background: "#0f192b",
                border: "1px solid #25354e",
                borderRadius: "6px",
                color: "#ecf1fa",
                fontFamily: "ui-monospace, monospace",
                fontSize: "12px",
                padding: "8px",
                resize: "vertical",
              }}
            />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button
                type="button"
                className={styles.actionBtn}
                onClick={handleApplyManualJson}
              >
                JSON 변환 및 검토 로드
              </button>
            </div>
          </div>
        )}

        {/* 4. 탭 헤더 */}
        <div className={styles.tabHeader} role="tablist">
          <button
            type="button"
            className={`${styles.tabBtn} ${
              activeTab === "review" ? styles.activeTab : ""
            }`}
            onClick={() => setActiveTab("review")}
            role="tab"
            aria-selected={activeTab === "review"}
          >
            01 근거 검토
          </button>
          <button
            type="button"
            className={`${styles.tabBtn} ${
              activeTab === "compare" ? styles.activeTab : ""
            }`}
            onClick={() => setActiveTab("compare")}
            role="tab"
            aria-selected={activeTab === "compare"}
          >
            02 반영 전후 (BEFORE / AFTER)
          </button>

          <div className={styles.tabCounts}>
            <span className={styles.chip}>{summaryCountsText()}</span>
          </div>
        </div>

        {/* 5. 탭 본문 영역 */}
        <main className={styles.tabBody}>
          {error && (
            <div
              style={{
                padding: "12px",
                background: "rgba(239, 68, 68, 0.15)",
                border: "1px solid #ef4444",
                borderRadius: "8px",
                color: "#fca5a5",
                fontSize: "13px",
                marginBottom: "16px",
              }}
            >
              ⚠️ {error}
            </div>
          )}

          {loading ? (
            <div
              style={{
                padding: "60px 0",
                textAlign: "center",
                color: "#94a7c0",
              }}
            >
              어댑터 검토 번들 로딩 중...
            </div>
          ) : activeTab === "compare" ? (
            <div className={styles.compareContainer}>
              {/* 상단 1줄 시뮬레이션 항목 선택 바 (체크박스 토글 시 아래 BEFORE/AFTER 즉각 반영) */}
              <div className={styles.compactCandidateBar}>
                <div className={styles.compactBarLabel}>
                  <span className={styles.simulationIcon}>⚡</span>
                  <strong>반영 항목 토글:</strong>
                </div>

                <div className={styles.compactCandidatePills}>
                  {bundleData?.rows?.map((row: CandidateRow) => {
                    const isChecked = enabledItemIds.has(row.id);
                    const isBlocked = Boolean(row.blocked);
                    const isPrereqMet = isPrerequisiteMet(
                      row.id,
                      producer,
                      enabledItemIds,
                      bundleData?.rows,
                    );
                    const isDisabled = isBlocked || (!isChecked && !isPrereqMet);

                    let tooltip = row.sub;
                    if (isBlocked) {
                      tooltip = "무근거 후보는 반영이 차단되었습니다";
                    } else if (!isPrereqMet && !isChecked) {
                      tooltip = "선행 필수 항목이 먼저 선택되어야 활성화됩니다";
                    }

                    return (
                      <label
                        key={row.id}
                        className={`${styles.candidatePill} ${
                          isChecked ? styles.candidatePillChecked : ""
                        } ${isBlocked ? styles.candidatePillBlocked : ""} ${
                          isDisabled && !isBlocked
                            ? styles.candidatePillDisabled
                            : ""
                        }`}
                        title={tooltip}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          disabled={isDisabled}
                          onChange={() => handleToggleItem(row.id, isBlocked)}
                        />
                        <span className={styles.pillTitle}>
                          {row.title}
                          {!isPrereqMet && !isChecked && !isBlocked && (
                            <span
                              style={{
                                fontSize: "10px",
                                marginLeft: "5px",
                                color: "#fbbf24",
                                fontWeight: "normal",
                              }}
                            >
                              🔒선행필요
                            </span>
                          )}
                        </span>
                        <span className={styles.pillBadge}>{row.kind}</span>
                      </label>
                    );
                  })}
                </div>

                <button
                  type="button"
                  className={styles.compactToggleAllBtn}
                  onClick={handleToggleAll}
                >
                  {enabledItemIds.size > 0 ? "전체 해제" : "전체 선택"}
                </button>
              </div>

              {/* BEFORE / AFTER 듀얼 패널 (한눈에 바로 비교) */}
              <div className={styles.panes}>
                {/* 좌측 BEFORE 패널 */}
                <div className={styles.pane}>
                  <div className={styles.paneHeader}>
                    <div className={styles.paneTitle}>
                      <span className={styles.paneBadgeBefore}>BEFORE</span>
                      <strong>현재 상태 (지식그래프 기준선)</strong>
                    </div>
                    <span className={styles.paneSub}>반영 전 원천 DB 상태</span>
                  </div>
                  <div className={styles.graphBox}>
                    {renderDualGraph("before")}
                  </div>
                  <div className={styles.tableBox}>
                    {renderComparisonTable("before")}
                  </div>
                </div>

                {/* 우측 AFTER 패널 */}
                <div className={`${styles.pane} ${styles.paneAfter}`}>
                  <div className={styles.paneHeader}>
                    <div className={styles.paneTitle}>
                      <span className={styles.paneBadgeAfter}>AFTER</span>
                      <strong>반영 예정 (선택 항목 실시간 시뮬레이션)</strong>
                    </div>
                    <span className={styles.paneSub}>
                      선택 {enabledItemIds.size}개 항목 승인 시 결과
                    </span>
                  </div>
                  <div className={styles.graphBox}>
                    {renderDualGraph("after")}
                  </div>
                  <div className={styles.tableBox}>
                    {renderComparisonTable("after")}
                  </div>
                </div>
              </div>

              {/* 하단 범례 바 */}
              <div className={styles.legendBar}>
                <div className={styles.legendItems}>
                  <span className={styles.legendItem}>
                    <span className={styles.legendDotNew} /> + 신규 노드/간선
                  </span>
                  <span className={styles.legendItem}>
                    <span className={styles.legendDotChanged} /> 속성 값 보강
                  </span>
                  <span className={styles.legendItem}>
                    <span className={styles.legendDotSame} /> 좌우 노드 좌표 일치 (위치 불변)
                  </span>
                  <span className={styles.legendItem}>
                    <span className={styles.legendDotSafe} /> 체크 해제 시 안전하게 제외
                  </span>
                </div>
                <span className={styles.legendNotice}>
                  ※ 원천 문장과 신문/공고/엑셀 원본 근거 대조는 상단 '01 근거 검토' 탭에서 확인하실 수 있습니다.
                </span>
              </div>
            </div>
          ) : (
            <div className={styles.splitLayout}>
              {/* 좌측 후보 목록 */}
              <section className={styles.candidatePanel}>
                <div className={styles.panelHeader}>
                  <h3>반영할 항목</h3>
                  <span
                    style={{
                      fontSize: "11px",
                      color: "#94a7c0",
                      cursor: "pointer",
                    }}
                    onClick={handleToggleAll}
                  >
                    {enabledItemIds.size > 0 ? "전체 해제" : "전체 선택"}
                  </span>
                </div>
                {renderCandidateList()}
              </section>

              {/* 우측 공통 근거 뷰어 */}
              <section className={styles.evidenceViewer}>
                <div className={styles.panelHeader}>
                  <h3>선택한 항목의 근거와 표시 위치</h3>
                </div>
                {renderEvidenceViewer()}
              </section>
            </div>
          )}
        </main>

        {/* 6. 하단 액션 바 */}
        <footer className={styles.footerBar}>
          <div className={styles.footerSummary}>
            <strong>{summaryCountsText()}</strong>
            <p className={styles.footerHint}>
              선택 제외는 기존 데이터 삭제가 아니며, 후보만 반영에서 안전하게
              제외됩니다.
            </p>
          </div>

          <span className={styles.grow} />

          <button
            type="button"
            className={styles.actionBtn}
            onClick={onClose}
            disabled={committing}
          >
            취소
          </button>

          <button
            type="button"
            className={styles.primaryBtn}
            onClick={handleCommit}
            disabled={committing || enabledItemIds.size === 0}
          >
            {committing ? "지식맵에 반영 중..." : "선택한 변경 반영 및 저장"}
          </button>
        </footer>

        {/* 토스트 메시지 */}
        {toastMessage && (
          <div className={styles.toastNotice} role="status">
            ✨ {toastMessage}
          </div>
        )}
      </div>
    </div>
  );
}
