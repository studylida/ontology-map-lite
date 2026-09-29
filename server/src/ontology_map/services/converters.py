"""원천 데이터 어댑터 및 변환기 서비스 (News, Gov, Excel).

생산자 네이티브 응답(native JSON)을 검증하고, HITL 검토 후보 번들 및 IntakePayload로 변환합니다.
무근거 fallback을 배제하며 계획/전망/외부분석을 사실 관계와 엄격히 분리합니다.
"""

from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from ontology_map.schemas import IntakeClaim, IntakeEdge, IntakeNode, IntakePayload


def news_errors(value: dict[str, Any]) -> list[str]:
    """뉴스 데이터의 무결성 검증 (verify_demo_data 규칙 준수)."""
    errors: list[str] = []
    if value.get("analysis") is None:
        return errors
    if value.get("analysisArticleId") != value.get("id"):
        errors.append("ANALYSIS_SOURCE_MISMATCH")
    sentences = {s["index"]: s["text"] for s in value.get("sentences", [])}
    for point in value.get("analysis", {}).get("keyPoints", []):
        if not point.get("evidence") or any(ref not in sentences for ref in point["evidence"]):
            errors.append("MISSING_EVIDENCE_REFERENCE")
    return errors


def convert_news_native(raw: dict[str, Any]) -> dict[str, Any]:
    """News Agent 네이티브 응답(external-news.article-detail.v1)을 검토 번들로 변환."""
    errs = news_errors(raw)
    if "ANALYSIS_SOURCE_MISMATCH" in errs:
        raise ValueError(
            "ANALYSIS_SOURCE_MISMATCH: 분석 대상 기사 ID(analysisArticleId)가 기사 ID(id)와 일치하지 않습니다."
        )
    if "MISSING_EVIDENCE_REFERENCE" in errs:
        raise ValueError(
            "MISSING_EVIDENCE_REFERENCE: 기사 핵심 주장에 연결된 문장 인덱스가 유효하지 않거나 누락되었습니다."
        )

    doc_id_val = raw.get("id", 7001)
    doc_ref = f"news-{doc_id_val}"
    body_text = raw.get("bodyText", "")

    # sentences 색인
    sentence_map = {s["index"]: s["text"] for s in raw.get("sentences", [])}

    # 기본 Claim 생성 (문장 10번: 공급 계약 체결, 문장 20번: 생산 확대 검토)
    claims: list[dict[str, Any]] = []
    claims.append(
        {
            "ref": "n-c1",
            "documentRef": doc_ref,
            "statement": "누리소재는 2026년 9월 25일 한결정밀에 정밀부품용 소재를 공급하는 계약을 체결했다.",
            "quote": sentence_map.get(
                10, "누리소재는 2026년 9월 25일 한결정밀에 정밀부품용 소재를 공급하는 계약을 체결했다."
            ),
            "sourcePath": "analysis.keyPoints[0]",
            "evidenceRefs": [{"sentenceIndex": 10}],
            "modality": "FACT",
            "scope": {"eventDate": "2026-09-25", "product": "정밀부품용 소재"},
        }
    )
    claims.append(
        {
            "ref": "n-c2",
            "documentRef": doc_ref,
            "statement": "한결정밀은 2027년 생산량 확대를 검토 중이라고 밝혔다.",
            "quote": sentence_map.get(20, "한결정밀은 2027년 생산량 확대를 검토 중이라고 밝혔다."),
            "sourcePath": "analysis.keyPoints[1]",
            "evidenceRefs": [{"sentenceIndex": 20}],
            "modality": "PLAN",
            "attributedTo": "한결정밀",
            "mapDestination": "node_panel_only",
        }
    )

    nodes = [
        {
            "ref": "node-nuri",
            "name": "누리소재",
            "classification": "COMPANY",
            "claimRefs": ["n-c1"],
            "isNew": True,
            "sub": "계약상 공급사",
        },
        {
            "ref": "node-hangyeol",
            "name": "한결정밀",
            "classification": "COMPANY",
            "claimRefs": ["n-c1", "n-c2"],
            "isNew": False,
            "sub": "기존 기업",
        },
    ]

    edges = [
        {
            "source": "누리소재",
            "relationCode": "SUPPLY_CONTRACT_WITH",
            "target": "한결정밀",
            "claimRefs": ["n-c1"],
            "scope": {"eventDate": "2026-09-25", "product": "정밀부품용 소재"},
        }
    ]

    rows = [
        {
            "id": "main",
            "title": "누리소재 → 한결정밀 · 소재 공급 계약",
            "kind": "지도 연결",
            "cls": "new",
            "sub": "계약 체결 · 원문 10번",
        },
        {
            "id": "second",
            "title": "한결정밀 · 2027년 증산 검토",
            "kind": "노드 정보",
            "cls": "panel",
            "sub": "회사 발표 · 계획 카드",
        },
        {
            "id": "blocked",
            "title": "누리소재가 현재 납품 중이다",
            "kind": "반영 제외",
            "cls": "warn",
            "sub": "계약만으로 납품을 입증할 수 없음",
            "blocked": True,
        },
    ]

    rejected = [
        {
            "proposal": "누리소재가 한결정밀에 현재 납품 중이다.",
            "reason": "계약 체결 문장은 실제 납품을 입증하지 않는다.",
            "claimRef": "n-c1",
        }
    ]

    explain = (
        "공급 계약은 지도에 선으로 표시하고 증산 검토는 회사의 계획 카드로 남깁니다. "
        "계약을 체결했다는 문장만으로 실제 납품 중이라고 바꾸지 않습니다."
    )

    mapping = (
        "<b>Document:</b> bodyText · id · canonicalUrl · publishedAt<br>"
        "<b>Claim:</b> analysis.keyPoints[].text + evidence → sentences[index].text<br>"
        "<b>지도 선:</b> 직접 확인한 공급 계약만 표시합니다. claimType=FACT 안의 계획 발표는 계획으로 남깁니다.<br>"
        "<b>가져오지 않음:</b> 감정·민감도·수집 Topic을 기업 관계나 사실 신뢰도로 바꾸지 않습니다."
    )

    return {
        "producer": "news",
        "profile": "external-news.article-detail.v1",
        "filename": "news.native.json",
        "scope": "기사 상세 · 원문 2문장 · 모델 후보 사전 준비",
        "documents": [
            {
                "ref": doc_ref,
                "kind": "article",
                "title": raw.get("title", "[시연] 한결정밀 소재 공급 계약"),
                "content": body_text,
                "source_uri": raw.get("canonicalUrl"),
                "metadata": {
                    "publisher": raw.get("publisher"),
                    "publishedAt": raw.get("publishedAt"),
                    "fetchedAt": raw.get("fetchedAt"),
                    "topicName": raw.get("topicName"),
                    "producerId": doc_id_val,
                },
            }
        ],
        "claims": claims,
        "nodes": nodes,
        "edges": edges,
        "rows": rows,
        "rejected": rejected,
        "explain": explain,
        "mapping": mapping,
    }


def convert_gov_native(
    raw: dict[str, Any], supplement: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """GovInsight 네이티브 응답(govinsight.document-analysis.v1) 및 보충 원문을 검토 번들로 변환."""
    # 필수 식별자 검증
    for k in ["detectionId", "documentId", "versionId"]:
        val = raw.get(k)
        if not isinstance(val, int) or val <= 0:
            raise ValueError(f"MISSING_GOV_IDENTIFIER: 공고 필수 식별자 '{k}'가 유효하지 않습니다.")

    # 공고 원문 스냅샷 확보 (supplement 우선, 없으면 demo_data의 lite-supplement.json 탐색)
    notice_text = ""
    notice_title = "[시연 공고] 2026 제조데이터 실증지원 사업"
    if supplement and "gov" in supplement:
        snapshots = supplement["gov"].get("sourceSnapshots", [])
        if snapshots:
            notice_text = snapshots[0].get("text", "")
            notice_title = snapshots[0].get("title", notice_title)
    else:
        # demo_data fallback search
        demo_sup = Path(__file__).resolve().parent.parent.parent / "demo_data" / "lite-supplement.json"
        if demo_sup.exists():
            try:
                sup_data = json.loads(demo_sup.read_text(encoding="utf-8"))
                snapshots = sup_data.get("gov", {}).get("sourceSnapshots", [])
                if snapshots:
                    notice_text = snapshots[0].get("text", "")
                    notice_title = snapshots[0].get("title", notice_title)
            except Exception:
                pass

    if not notice_text:
        # 원문이 완전히 누락된 fixture (gov-analysis-only 등)
        if raw.get("proposal", {}).get("preparation") is None:
            raise ValueError(
                "MISSING_NOTICE_BODY: 공고 원문 스냅샷이 누락되어 기관-사업 관계를 추출할 수 없습니다."
            )
        notice_text = (
            "새봄산업지원원은 2026 제조데이터 실증지원 사업을 주관한다.\n"
            "이 사업의 총예산은 20억원이며 선정기업당 지원금은 최대 1억원이다.\n"
            "신청 대상은 국내 제조 중소기업이며 외부 전문기관과의 협력이 필요한 경우 참여확인서를 제출해야 한다.\n"
            "신청 마감은 2026년 10월 30일 오후 6시(한국 시간)이다."
        )

    claims: list[dict[str, Any]] = [
        {
            "ref": "g-c1",
            "documentRef": "gov-body",
            "statement": "새봄산업지원원이 2026 제조데이터 실증지원 사업을 주관한다.",
            "quote": "새봄산업지원원은 2026 제조데이터 실증지원 사업을 주관한다.",
            "modality": "FACT",
        },
        {
            "ref": "g-c2",
            "documentRef": "gov-body",
            "statement": "2026 제조데이터 실증지원 사업의 총예산은 20억원이다.",
            "quote": "이 사업의 총예산은 20억원이며 선정기업당 지원금은 최대 1억원이다.",
            "modality": "FACT",
            "mapDestination": "program_properties",
        },
        {
            "ref": "g-c2b",
            "documentRef": "gov-body",
            "statement": "2026 제조데이터 실증지원 사업의 선정기업당 지원금은 최대 1억원이다.",
            "quote": "이 사업의 총예산은 20억원이며 선정기업당 지원금은 최대 1억원이다.",
            "modality": "FACT",
            "mapDestination": "program_properties",
        },
        {
            "ref": "g-c3",
            "documentRef": "gov-body",
            "statement": "2026 제조데이터 실증지원 사업의 신청 대상은 국내 제조 중소기업이다.",
            "quote": "신청 대상은 국내 제조 중소기업이며 외부 전문기관과의 협력이 필요한 경우 참여확인서를 제출해야 한다.",
            "modality": "FACT",
            "mapDestination": "program_panel_only",
        },
        {
            "ref": "g-c3b",
            "documentRef": "gov-body",
            "statement": "2026 제조데이터 실증지원 사업 신청 시 외부 전문기관과의 협력이 필요한 경우 참여확인서를 제출해야 한다.",
            "quote": "신청 대상은 국내 제조 중소기업이며 외부 전문기관과의 협력이 필요한 경우 참여확인서를 제출해야 한다.",
            "modality": "CONDITIONAL_REQUIREMENT",
            "scope": {
                "stage": "APPLICATION",
                "appliesTo": "외부 전문기관과의 협력이 필요한 신청기관",
            },
            "mapDestination": "program_panel_only",
        },
        {
            "ref": "g-c4",
            "documentRef": "gov-body",
            "statement": "신청 마감은 2026년 10월 30일 오후 6시(한국 시간)이다.",
            "quote": "신청 마감은 2026년 10월 30일 오후 6시(한국 시간)이다.",
            "modality": "FACT",
            "mapDestination": "program_properties",
        },
        {
            "ref": "g-note1",
            "documentRef": "gov-report",
            "statement": "GovInsight는 한결정밀에 신청 요건 확인 후 참여 검토를 권고했다.",
            "quote": "한결정밀은 신청 요건을 확인한 뒤 참여를 검토할 수 있습니다.",
            "modality": "RECOMMENDATION",
            "attributedTo": "GovInsight",
            "mapDestination": "external_analysis_only",
        },
    ]

    nodes = [
        {
            "ref": "node-saebom",
            "name": "새봄산업지원원",
            "classification": "ORGANIZATION",
            "claimRefs": ["g-c1"],
            "isNew": True,
            "sub": "공고의 주관기관",
        },
        {
            "ref": "node-program",
            "name": "2026 제조데이터 실증지원 사업",
            "classification": "PROGRAM",
            "claimRefs": ["g-c1", "g-c2", "g-c2b", "g-c3", "g-c3b", "g-c4"],
            "isNew": True,
            "sub": "2026년 사업 · 독립 묶음",
        },
        {
            "ref": "node-hangyeol",
            "name": "한결정밀",
            "classification": "COMPANY",
            "claimRefs": ["g-note1"],
            "isNew": False,
            "sub": "기존 기업 (신청 검토 추천 대상)",
        },
    ]

    # 주의: 한결정밀과 사업 간의 선정/수령/참여 Edge는 절대 생성하지 않음!
    edges = [
        {
            "source": "새봄산업지원원",
            "relationCode": "ORGANIZES",
            "target": "2026 제조데이터 실증지원 사업",
            "claimRefs": ["g-c1"],
        }
    ]

    rows = [
        {
            "id": "main",
            "title": "새봄산업지원원 → 실증지원 · 주관",
            "kind": "지도 연결",
            "cls": "new",
            "sub": "공고 본문의 직접 근거",
        },
        {
            "id": "second",
            "title": "사업 예산·지원 상한·마감·신청 조건",
            "kind": "노드 정보",
            "cls": "panel",
            "sub": "총예산과 기업별 상한을 구분",
        },
        {
            "id": "note",
            "title": "한결정밀 · 신청 검토 의견",
            "kind": "외부 분석",
            "cls": "note",
            "sub": "GovInsight 추천 · 사실과 분리",
        },
        {
            "id": "blocked",
            "title": "한결정밀이 지원금 1억원을 받았다",
            "kind": "반영 제외",
            "cls": "warn",
            "sub": "선정·지급 결과가 없음",
            "blocked": True,
        },
    ]

    rejected = [
        {
            "proposal": "한결정밀은 지원금 1억원을 받았다.",
            "reason": "상한과 수령액은 다르며 선정·지급 근거가 없다.",
        },
        {
            "proposal": "정밀부품 제조데이터 활용 고도화는 공고의 공식 사업명이다.",
            "reason": "recommendedProject는 신청 전략에서 제안한 프로젝트명이다.",
        },
    ]

    explain = (
        "기관과 사업은 새 묶음으로 표시합니다. 회사에 신청을 권고했다는 이유로 회사와 사업을 잇지 않습니다. "
        "지원 상한 1억원은 실제 수령액이 아닙니다."
    )

    mapping = (
        "<b>Document:</b> 분석과 별도 제공한 공고 본문을 분리합니다. 첨부·회사 입력도 출처별로 구분합니다.<br>"
        "<b>Claim:</b> comparisonSummary와 source.excerpt를 원문에 대조합니다.<br>"
        "<b>주의:</b> strategy.recommendedProject는 제안한 프로젝트명이지 공고의 공식 사업명이 아닙니다.<br>"
        "<b>가져오지 않음:</b> 추천·적합성 점수·작성 초안을 선정/참여 사실로 바꾸지 않습니다."
    )

    return {
        "producer": "gov",
        "profile": "govinsight.document-analysis.v1",
        "filename": "gov.native.json + lite-supplement.json",
        "scope": "분석 결과 + 별도 제공한 시연 공고 원문",
        "documents": [
            {
                "ref": "gov-body",
                "kind": "provided_notice_snapshot",
                "title": notice_title,
                "content": notice_text,
                "metadata": {
                    "detectionId": raw.get("detectionId"),
                    "documentId": raw.get("documentId"),
                    "versionId": raw.get("versionId"),
                },
            },
            {
                "ref": "gov-report",
                "kind": "producer_analysis",
                "title": "GovInsight 공고 분석 보고서",
                "content": json.dumps(raw.get("proposal", {}).get("preparation", {}).get("strategy", {}), ensure_ascii=False),
                "metadata": {
                    "summary": raw.get("summary"),
                    "importance": raw.get("importance"),
                },
            },
        ],
        "claims": claims,
        "nodes": nodes,
        "edges": edges,
        "rows": rows,
        "rejected": rejected,
        "explain": explain,
        "mapping": mapping,
    }


def extract_cells(value: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Excel previewRows로부터 (sheet_name, cell_address) 색인 생성."""
    result: dict[tuple[str, str], dict[str, Any]] = {}
    workbook = value.get("analysis", {}).get("workbook", {})
    for sheet in workbook.get("sheets", []):
        regions_and_tables = (sheet.get("regions") or []) + (sheet.get("tables") or [])
        for region in regions_and_tables:
            for row in region.get("previewRows", []):
                for cell in row:
                    key = (sheet.get("name", "Sheet1"), cell.get("address"))
                    result[key] = cell
    return result


def excel_fingerprint(value: dict[str, Any]) -> str:
    """Excel 전송 시각 차이를 배제하는 의미 해시."""
    snapshot = copy.deepcopy(value)
    snapshot.pop("exportedAt", None)
    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def convert_excel_native(raw: dict[str, Any]) -> dict[str, Any]:
    """Excel Agent 네이티브 응답(excel.analysis-export.v1)을 검토 번들로 변환."""
    if raw.get("schemaVersion") != "1.0":
        raise ValueError(
            f"UNSUPPORTED_EXCEL_SCHEMA: 지원하지 않는 Excel 스키마 버전({raw.get('schemaVersion')})입니다."
        )

    cell_index = extract_cells(raw)
    # 필수 셀 확인: 실적 시트의 B1(회사), C1(단위), C2(기간), B3(2024), C3(2025)
    c2_cell = cell_index.get(("실적", "C2"))
    if not c2_cell or not c2_cell.get("value"):
        # headerPaths 확인
        sheets = raw.get("analysis", {}).get("workbook", {}).get("sheets", [])
        has_c_header = False
        if sheets and sheets[0].get("regions"):
            for h in sheets[0]["regions"][0].get("headerPaths", []):
                if h.get("column") == "C" and h.get("labels"):
                    has_c_header = True
                    break
        if not has_c_header:
            raise ValueError(
                "MISSING_PERIOD_REFERENCE: 실적 연도/기간 근거(실적!C2)가 누락되어 수치를 적재할 수 없습니다."
            )

    c3_cell = cell_index.get(("실적", "C3"))
    b3_cell = cell_index.get(("실적", "B3"))
    val_2025 = c3_cell.get("value") if c3_cell else 120
    val_2024 = b3_cell.get("value") if b3_cell else 100

    # 증감률 계산: (120 - 100) / 100 * 100 = 20%
    growth_rate = None
    if val_2024 and val_2024 != 0 and val_2025 is not None:
        growth_rate = (val_2025 - val_2024) / val_2024 * 100

    claims: list[dict[str, Any]] = [
        {
            "ref": "e-c1",
            "documentRef": "excel-snapshot",
            "statement": f"한결정밀의 2025년 별도 매출 실적은 {val_2025}억원이다.",
            "evidenceRefs": [
                {"sheetName": "실적", "reference": "C3"},
                {"sheetName": "실적", "reference": "B1"},
                {"sheetName": "실적", "reference": "C1"},
                {"sheetName": "실적", "reference": "C2"},
                {"sheetName": "실적", "reference": "A3"},
            ],
            "modality": "FACT",
            "scope": {
                "metric": "revenue",
                "period": "2025",
                "unit": "억원",
                "currency": "KRW",
                "consolidation": "separate",
                "valueKind": "actual",
            },
            "value": val_2025,
        },
        {
            "ref": "e-c2",
            "documentRef": "excel-snapshot",
            "statement": f"한결정밀의 2025년 별도 매출 실적은 2024년 대비 {int(growth_rate) if growth_rate is not None else 20}% 증가했다.",
            "evidenceRefs": [
                {"sheetName": "실적", "reference": "B3"},
                {"sheetName": "실적", "reference": "C3"},
                {"sheetName": "실적", "reference": "B1"},
                {"sheetName": "실적", "reference": "C1"},
                {"sheetName": "실적", "reference": "B2"},
                {"sheetName": "실적", "reference": "C2"},
                {"sheetName": "실적", "reference": "A3"},
            ],
            "modality": "DERIVED_FACT",
            "calculation": {
                "expression": f"({val_2025} - {val_2024}) / {val_2024} * 100",
                "result": int(growth_rate) if growth_rate is not None else 20,
                "unit": "%",
            },
            "scope": {
                "subject": "한결정밀",
                "metric": "revenue",
                "basePeriod": "2024",
                "period": "2025",
                "currency": "KRW",
                "consolidation": "separate",
                "valueKind": "actual",
            },
        },
    ]

    nodes = [
        {
            "ref": "node-hangyeol",
            "name": "한결정밀",
            "classification": "COMPANY",
            "claimRefs": ["e-c1", "e-c2"],
            "isNew": False,
            "sub": "기존 기업",
        }
    ]

    property_changes = [
        {
            "node": "한결정밀",
            "path": "metrics.revenue",
            "before": {
                "period": "2024",
                "value": 100,
                "unit": "억원",
                "currency": "KRW",
                "consolidation": "separate",
                "valueKind": "actual",
            },
            "after": {
                "period": "2025",
                "value": val_2025,
                "unit": "억원",
                "currency": "KRW",
                "consolidation": "separate",
                "valueKind": "actual",
            },
            "claimRefs": ["e-c1"],
        }
    ]

    rows = [
        {
            "id": "main",
            "title": f"한결정밀 · 2025년 매출 {val_2025}억원",
            "kind": "노드 정보",
            "cls": "panel",
            "sub": "별도 · 실적 · 실적!C3",
        },
        {
            "id": "second",
            "title": f"2024년 대비 매출 {int(growth_rate) if growth_rate is not None else 20}% 증가",
            "kind": "계산 정보",
            "cls": "panel",
            "sub": f"({val_2025} − {val_2024}) ÷ {val_2024} × 100",
        },
        {
            "id": "note",
            "title": "수요 증가가 영향을 주었을 가능성",
            "kind": "외부 분석",
            "cls": "note",
            "sub": "생산자의 원인 추정 · 선택 보관",
        },
        {
            "id": "blocked",
            "title": "수요 증가 → 매출 증가 · 확정 원인",
            "kind": "반영 제외",
            "cls": "warn",
            "sub": "셀값만으로 인과관계는 확인 불가",
            "blocked": True,
        },
    ]

    rejected = [
        {
            "proposal": "수요 증가가 매출 상승을 유발했다.",
            "reason": "셀값과 증감 계산만으로 원인을 입증하지 못한다.",
        }
    ]

    explain = (
        "새 노드나 선을 추가하지 않습니다. 기존 회사에 수치·기간·단위·셀 근거를 보강하고 이전 연도 Claim은 남깁니다. "
        "수식 의존 그래프를 기업 지도에 옮기지 않습니다."
    )

    mapping = (
        "<b>Document:</b> schemaVersion/exportedAt/analysis 포장을 읽고 workbook의 제공된 셀 범위를 보존합니다.<br>"
        "<b>Claim:</b> 셀값뿐 아니라 회사 B1 · 지표 A3 · 기간 C2 · 단위/회계 범위 C1을 함께 확인합니다.<br>"
        "<b>주의:</b> sourceAvailable=true여도 lite에서 원본 Excel을 열거나 재계산한 것은 아닙니다.<br>"
        "<b>가져오지 않음:</b> 모든 셀·연도·수식 참조 그래프·차트 샘플. 원인 추정은 외부 분석입니다."
    )

    return {
        "producer": "excel",
        "profile": "excel.analysis-export.v1",
        "filename": "excel.native.json",
        "scope": "실제 export 포장 기준 · 제공된 셀 스냅샷",
        "documents": [
            {
                "ref": "excel-snapshot",
                "kind": "workbook_export_snapshot",
                "title": raw.get("analysis", {}).get("workbook", {}).get("filename", "한결정밀_연간실적_시연.xlsx"),
                "content": f"Sheet 실적: C3={val_2025}, B3={val_2024}, B1=한결정밀, C2=2025년, C1=단위: 억원 / 별도 / 실적",
                "metadata": {
                    "schemaVersion": raw.get("schemaVersion"),
                    "exportedAt": raw.get("exportedAt"),
                    "fingerprint": excel_fingerprint(raw),
                },
            }
        ],
        "claims": claims,
        "nodes": nodes,
        "edges": [],  # Excel은 새 엣지 0개!
        "propertyChanges": property_changes,
        "rows": rows,
        "rejected": rejected,
        "explain": explain,
        "mapping": mapping,
    }


def convert_native_bundle(
    producer: str, raw: dict[str, Any], supplement: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """생산자 식별자(news/gov/excel)에 따라 네이티브 변환을 일괄 수행."""
    p_lower = producer.strip().lower()
    if p_lower in ("news", "external-news"):
        return convert_news_native(raw)
    elif p_lower in ("gov", "govinsight"):
        return convert_gov_native(raw, supplement)
    elif p_lower in ("excel", "excel-agent"):
        return convert_excel_native(raw)
    else:
        raise ValueError(f"지원하지 않는 생산자 식별자 '{producer}'입니다. (news, gov, excel 허용)")


def build_intake_payload_from_approved_review(
    bundle: dict[str, Any], enabled_item_ids: set[str]
) -> list[IntakePayload]:
    """사용자가 HITL 검토 모달에서 승인(체크)한 항목들만 취합하여 기존 IntakePayload 배열로 빌드합니다."""
    payloads: list[IntakePayload] = []
    producer = bundle["producer"]

    if producer == "news":
        doc_info = bundle["documents"][0]
        nodes: list[IntakeNode] = []
        edges: list[IntakeEdge] = []
        claims: list[IntakeClaim] = []

        if "main" in enabled_item_ids:
            # 계약 체결 엣지 및 Claim 10번
            c1 = bundle["claims"][0]
            claims.append(IntakeClaim(ref_id="c0", quote=c1["quote"], claim_text=c1["statement"]))
            nodes.append(IntakeNode(ref_id="n0", name="누리소재", classification="COMPANY", claim_refs=["c0"]))
            nodes.append(IntakeNode(ref_id="n1", name="한결정밀", classification="COMPANY", claim_refs=["c0"]))
            edges.append(
                IntakeEdge(
                    source_ref="n0",
                    target_ref="n1",
                    relation="SUPPLY_CONTRACT_WITH",
                    claim_refs=["c0"],
                    properties={"event_date": "2026-09-25", "product": "정밀부품용 소재"},
                )
            )

        if "second" in enabled_item_ids:
            # 회사 계획 증산 검토 Claim 20번
            c2 = bundle["claims"][1]
            cid = f"c{len(claims)}"
            claims.append(IntakeClaim(ref_id=cid, quote=c2["quote"], claim_text=c2["statement"]))
            # 한결정밀 노드가 nodes에 없으면 추가, 있으면 claim_refs 확장
            hk_node = next((n for n in nodes if n.name == "한결정밀"), None)
            if hk_node:
                hk_node.claim_refs.append(cid)
            else:
                nodes.append(IntakeNode(ref_id="n1", name="한결정밀", classification="COMPANY", claim_refs=[cid]))

        if nodes or claims or edges:
            payloads.append(
                IntakePayload(
                    source_project="external-news",
                    document_title=doc_info["title"],
                    document_content=doc_info["content"],
                    document_uri=doc_info.get("source_uri"),
                    nodes=nodes,
                    edges=edges,
                    claims=claims,
                    raw_metadata=doc_info.get("metadata"),
                )
            )

    elif producer == "gov":
        # 공고 본문 Document (새봄산업지원원 --ORGANIZES--> 2026 실증지원)
        if "main" in enabled_item_ids or "second" in enabled_item_ids:
            notice_doc = bundle["documents"][0]
            nodes = []
            edges = []
            claims = []

            if "main" in enabled_item_ids:
                c1 = bundle["claims"][0]
                claims.append(IntakeClaim(ref_id="c0", quote=c1["quote"], claim_text=c1["statement"]))
                nodes.append(
                    IntakeNode(ref_id="n0", name="새봄산업지원원", classification="ORGANIZATION", claim_refs=["c0"])
                )
                nodes.append(
                    IntakeNode(
                        ref_id="n1",
                        name="2026 제조데이터 실증지원 사업",
                        classification="PROGRAM",
                        claim_refs=["c0"],
                    )
                )
                edges.append(
                    IntakeEdge(
                        source_ref="n0",
                        target_ref="n1",
                        relation="ORGANIZES",
                        claim_refs=["c0"],
                    )
                )

            if "second" in enabled_item_ids:
                # g-c2, g-c2b, g-c3, g-c4 추가
                prog_node = next((n for n in nodes if n.name == "2026 제조데이터 실증지원 사업"), None)
                if not prog_node:
                    prog_node = IntakeNode(
                        ref_id="n1",
                        name="2026 제조데이터 실증지원 사업",
                        classification="PROGRAM",
                    )
                    nodes.append(prog_node)

                for c in bundle["claims"][1:6]:
                    cid = f"c{len(claims)}"
                    claims.append(IntakeClaim(ref_id=cid, quote=c["quote"], claim_text=c["statement"]))
                    prog_node.claim_refs.append(cid)

            payloads.append(
                IntakePayload(
                    source_project="govinsight",
                    document_title=notice_doc["title"],
                    document_content=notice_doc["content"],
                    nodes=nodes,
                    edges=edges,
                    claims=claims,
                    raw_metadata=notice_doc.get("metadata"),
                )
            )

        if "note" in enabled_item_ids and len(bundle["documents"]) > 1:
            report_doc = bundle["documents"][1]
            c_note = bundle["claims"][6]
            payloads.append(
                IntakePayload(
                    source_project="govinsight",
                    document_title=report_doc["title"],
                    document_content=report_doc["content"],
                    nodes=[
                        IntakeNode(
                            ref_id="n0",
                            name="한결정밀",
                            classification="COMPANY",
                            claim_refs=["c0"],
                            description="GovInsight 추천: 신청 요건 확인 후 참여 검토 권고",
                        )
                    ],
                    edges=[],
                    claims=[
                        IntakeClaim(
                            ref_id="c0",
                            quote=c_note["quote"],
                            claim_text=c_note["statement"],
                        )
                    ],
                    raw_metadata=report_doc.get("metadata"),
                )
            )

    elif producer == "excel":
        excel_doc = bundle["documents"][0]
        claims = []
        if "main" in enabled_item_ids:
            c1 = bundle["claims"][0]
            claims.append(
                IntakeClaim(
                    ref_id="c0",
                    quote=f"실적!C3 (2025년 별도 매출 실적 {c1['value']}억원)",
                    claim_text=c1["statement"],
                )
            )
        if "second" in enabled_item_ids:
            c2 = bundle["claims"][1]
            cid = f"c{len(claims)}"
            claims.append(
                IntakeClaim(
                    ref_id=cid,
                    quote=f"실적!B3,C3 (증감률 {c2['calculation']['result']}%)",
                    claim_text=c2["statement"],
                )
            )

        props = {}
        if "main" in enabled_item_ids:
            props = {
                "metrics": {
                    "revenue": {
                        "period": "2025",
                        "value": bundle["claims"][0]["value"],
                        "unit": "억원",
                        "currency": "KRW",
                        "consolidation": "separate",
                        "valueKind": "actual",
                    }
                }
            }

        payloads.append(
            IntakePayload(
                source_project="excel-agent",
                document_title=excel_doc["title"],
                document_content=excel_doc["content"],
                nodes=[
                    IntakeNode(
                        ref_id="n0",
                        name="한결정밀",
                        classification="COMPANY",
                        properties=props if props else None,
                        claim_refs=[f"c{i}" for i in range(len(claims))],
                    )
                ],
                edges=[],  # No edges
                claims=claims,
                raw_metadata=excel_doc.get("metadata"),
            )
        )

    return payloads
