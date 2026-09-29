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
}

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

  // 수동 입력 모드 지원
  const [showManualInput, setShowManualInput] = useState<boolean>(false);
  const [manualJsonText, setManualJsonText] = useState<string>("");

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

  // 체크박스 토글 핸들러
  const handleToggleItem = (id: string, blocked?: boolean) => {
    if (blocked) return;
    setEnabledItemIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  // 전체 선택 / 해제
  const handleToggleAll = () => {
    if (!bundleData?.rows) return;
    const available = bundleData.rows.filter((r: CandidateRow) => !r.blocked);
    if (enabledItemIds.size > 0) {
      setEnabledItemIds(new Set());
    } else {
      setEnabledItemIds(new Set(available.map((r: CandidateRow) => r.id)));
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

        {/* 2. 히어로 영역 */}
        <div className={styles.hero}>
          <div className={styles.heroTitleGroup}>
            <h1>무엇을 반영할지, 근거와 함께 확인하세요</h1>
            <p>
              후보 항목을 체크하여 승인하고, 오른쪽에서 실제 원천 근거와 문맥을
              대조 검토합니다.
            </p>
          </div>
          <span className={styles.stepCue}>
            자료 선택 → 근거 검토 → 반영
          </span>
        </div>

        {/* 3. 프로듀서 선택 바 */}
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
            02 반영 전후 (Phase 3 준비 중)
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
            <div
              style={{
                padding: "50px 20px",
                textAlign: "center",
                color: "#94a7c0",
                background: "#0c1628",
                borderRadius: "10px",
                border: "1px dashed #25354e",
              }}
            >
              <h3 style={{ color: "#ecf1fa", marginBottom: "8px" }}>
                02 반영 전후 (BEFORE / AFTER) 비교 탭
              </h3>
              <p style={{ fontSize: "13px" }}>
                Phase 3에서 2D SVG 경량 듀얼 다이어그램과 전년 대비 속성 비교표가
                활성화됩니다.
              </p>
              <button
                type="button"
                className={styles.actionBtn}
                style={{ marginTop: "14px" }}
                onClick={() => setActiveTab("review")}
              >
                ← 01 근거 검토 탭으로 돌아가기
              </button>
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

                <div className={styles.candidateList}>
                  {bundleData?.rows?.map((row: CandidateRow) => {
                    const isSelected = selectedItemId === row.id;
                    const isChecked = enabledItemIds.has(row.id);
                    const isBlocked = Boolean(row.blocked);

                    let tagClass = styles.tagPanel;
                    if (row.cls === "new") tagClass = styles.tagNew;
                    if (row.cls === "note") tagClass = styles.tagNote;
                    if (row.cls === "warn" || isBlocked)
                      tagClass = styles.tagWarn;

                    return (
                      <div
                        key={row.id}
                        className={`${styles.candidateRow} ${
                          isSelected ? styles.selectedRow : ""
                        } ${isBlocked ? styles.blockedRow : ""}`}
                        onClick={() => setSelectedItemId(row.id)}
                      >
                        <div className={styles.checkboxContainer}>
                          <input
                            type="checkbox"
                            checked={isChecked}
                            disabled={isBlocked}
                            onChange={() =>
                              handleToggleItem(row.id, isBlocked)
                            }
                            onClick={(e) => e.stopPropagation()}
                            aria-label={`${row.title} 반영 여부`}
                          />
                        </div>
                        <div className={styles.candidateContent}>
                          <div className={styles.candidateTitle}>
                            {row.title}
                          </div>
                          <div className={styles.candidateMeta}>
                            <span className={`${styles.tag} ${tagClass}`}>
                              {row.kind}
                            </span>
                            <span className={styles.candidateSub}>
                              {row.sub}
                            </span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
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
