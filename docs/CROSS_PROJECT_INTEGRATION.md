# 동기 프로젝트 간 연동 규격 및 API 팩트체크 보고서

> **작성일**: 2026-09-30  
> **검증 대상 저장소**:
> - 뉴스 에이전트: [external_news_agent](https://github.com/miro-oss/external_news_agent)
> - 엑셀 에이전트: [excel-ai-agent](https://github.com/hyeok02/excel-ai-agent)
> - 정부지원공고: [GovInsight](https://github.com/gimn70009/GovInsight)
>
> **검증 목적**: 타 대화 세션 및 협업 환경에서 각 동기 프로젝트와의 데이터 연동 가능성, 실제 API 엔드포인트, DTO 스키마, 인증 방식을 정확히 공유하고 협의 사항을 명확히 전달하기 위함.

---

## 1. 종합 요약 (Quick Reference)

| 동기 프로젝트 | 최종 판정 | 실제 연동 경로 / 방식 | 인증 (Auth) | 우리 쪽 어댑터 | 핵심 확인 사항 및 협의 내용 |
| :--- | :---: | :--- | :---: | :--- | :--- |
| **뉴스 에이전트**<br>(`external_news_agent`) | **100% 충분**<br>(협의 불필요) | `GET /api/news/articles/{articleId}` | 불필요<br>(PoC 로컬) | `convert_news_native` | • 원문 본문(`bodyText`), 문장 인덱스(`sentences`), 핵심 주장(`keyPoints`), 근거(`evidence`) 완벽 지원.<br>• 응답이 `ApiResponse<T>` 래퍼로 감싸져 있으므로 `result` 객체를 추출하여 어댑터에 전달 필요. |
| **엑셀 에이전트**<br>(`excel-ai-agent`) | **100% 충분**<br>(협의 불필요) | **"분석 결과 JSON 다운로드"** 파일 업로드<br>*(대안: `GET /api/v1/analyses/{id}/result`)* | 파일 업로드 시 불필요<br>*(API 직접 호출 시 세션 필요)* | `convert_excel_native` | • 프론트엔드에서 다운로드한 JSON의 루트에 `schemaVersion: "1.0"`과 셀/인사이트 구조가 완벽히 포함됨.<br>• 셀 좌표(`실적!B3`, `C3`)와 수치로 20% 증감률 즉시 산출 가능.<br>• 인증 및 래핑 처리를 피하기 위해 **JSON 파일 업로드 방식** 권장. |
| **정부지원공고**<br>(`GovInsight`) | **80% 충분**<br>(필드 1개 협의) | `GET /api/document-detections/{detectionId}` | 필요<br>(Bearer JWT) | `convert_gov_native` | • 요약, 지원규모(20억/1억), 일정, 자격 체크리스트, 전략 제안 완벽 제공.<br>• **단점**: **공고문 원문 전체 텍스트(`bodyText` / `content`) 부재**.<br>• **협의 요청**: 상세 응답에 원문 텍스트 필드 1개 추가 요청. |

---

## 2. 프로젝트별 세부 검증 결과

### 2.1. 뉴스 에이전트 (`external_news_agent`)

- **저장소**: [miro-oss/external_news_agent](https://github.com/miro-oss/external_news_agent)
- **프레임워크**: Spring Boot (Java 17/21), Oracle DB, Flyway
- **판정**: **100% 충분 (데이터 스키마 완벽 일치)**

#### 실제 API 명세
- **컨트롤러**: `com.example.be.domain.articles.controller.ArticleController`
- **엔드포인트**: `GET /api/news/articles/{articleId}`  
  *(기존 기획안의 `GET /api/v1/articles/{id}`가 아니므로 주의)*
- **인증**: 별도 인증 불필요 (`server.address: 127.0.0.1`, PoC 무인증 로컬 허용)
- **응답 래퍼 구조**:
  ```json
  {
    "isSuccess": true,
    "code": "OK",
    "message": "성공입니다.",
    "result": {
      "id": 7001,
      "title": "누리소재, 한결정밀과 정밀부품용 소재 공급계약 체결",
      "bodyText": "누리소재는 2026년 9월 25일...",
      "sentences": [
        { "index": 10, "text": "누리소재는 2026년 9월 25일 한결정밀에 정밀부품용 소재를 공급하는 계약을 체결했다." },
        { "index": 20, "text": "한결정밀은 2027년 생산량 확대를 검토 중이라고 밝혔다." }
      ],
      "analysis": {
        "keyPoints": [
          {
            "text": "누리소재와 공급계약 체결",
            "evidence": [10],
            "claimType": "FACT",
            "attributedTo": null
          },
          {
            "text": "생산량 확대 검토",
            "evidence": [20],
            "claimType": "FORECAST",
            "attributedTo": "한결정밀"
          }
        ]
      },
      "analysisArticleId": 7001
    }
  }
  ```

#### 우리 측 연동 상태 (`convert_news_native`)
- 우리 코드: `server/src/ontology_map/services/converters.py`
- `analysisArticleId == id`, `sentences` 배열 내 `index` 매핑, `keyPoints[].evidence` 정수 배열 대조 로직이 완벽하게 대응됩니다.
- 공급계약 엣지(`SUPPLIES_TO`), 근거 문장 인용(`quote`), 모달리티(`FACT`, `PLAN`)가 즉시 분리 적재됩니다.
- **연동 시 주의점**: HTTP 통신 시 최상위 `result` 객체를 언래핑하여 어댑터에 넘겨주어야 합니다.

---

### 2.2. 엑셀 에이전트 (`excel-ai-agent`)

- **저장소**: [hyeok02/excel-ai-agent](https://github.com/hyeok02/excel-ai-agent)
- **프레임워크**: Spring Boot (Java), React (Vite/TypeScript)
- **판정**: **100% 충분 (JSON 파일 내보내기/업로드 기준)**

#### 실제 연동 경로 및 내보내기 구조
- **프론트엔드 모듈**: `FE/src/utils/analysis/analysisExport.ts` (`downloadAnalysisResult`)
- **내보내기 JSON 스키마**:
  ```json
  {
    "schemaVersion": "1.0",
    "exportedAt": "2026-09-30T00:00:00.000Z",
    "analysis": {
      "analysisId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "createdAt": "2026-09-30T00:00:00.000Z",
      "workbook": {
        "filename": "2025_실적보고.xlsx",
        "sheets": [
          {
            "name": "실적",
            "regions": [
              {
                "headerPaths": [{ "column": "C", "labels": ["2025년"] }],
                "previewRows": [
                  [
                    { "address": "B1", "value": "한결정밀" },
                    { "address": "C1", "value": "억원" },
                    { "address": "C2", "value": "2025" }
                  ],
                  [
                    { "address": "A3", "value": "매출액" },
                    { "address": "B3", "value": 100 },
                    { "address": "C3", "value": 120 }
                  ]
                ]
              }
            ]
          }
        ]
      },
      "insightReport": {
        "overview": "2025년 매출 실적 분석 요약",
        "insights": [
          {
            "title": "전년 대비 매출 20% 증가",
            "fact": "2024년 100억원에서 2025년 120억원으로 성장",
            "evidence": ["실적!B3", "실적!C3"]
          }
        ]
      }
    }
  }
  ```

#### 우리 측 연동 상태 (`convert_excel_native`)
- 우리 코드: `server/src/ontology_map/services/converters.py`
- `schemaVersion == "1.0"` 검증, `extract_cells()`를 통한 `(시트명, 셀주소)` 인덱싱 완벽 지원.
- `실적!B3`(100)와 `실적!C3`(120) 셀로부터 `growth_rate = 20.0%` 자동 계산 및 노드 속성 바인딩.
- **연동 권장 사항**:
  - 백엔드 API 직접 호출(`GET /api/v1/analyses/{id}/result`) 시에는 Spring Security 세션 인증 및 `schemaVersion` 감싸기가 필요하므로,
  - 사용자가 엑셀 에이전트 화면에서 **[분석 결과 다운로드(JSON)]**한 파일을 우리 Intake에 업로드하는 방식이 가장 무결하고 간편합니다.

---

### 2.3. 정부지원공고 (`GovInsight`)

- **저장소**: [gimn70009/GovInsight](https://github.com/gimn70009/GovInsight)
- **프레임워크**: Spring Boot (Java), FastRAG/Python AI
- **판정**: **80% 충분 (경미한 필드 1개 협의 필요)**

#### 실제 API 명세
- **컨트롤러**: `com.publicmonitor.backend.domain.document.web.controller.DocumentDetectionDetailController`
- **엔드포인트**: `GET /api/document-detections/{detectionId}`  
  *(기존 기획안의 `GET /api/documents/{id}/analysis`가 아니므로 주의)*
- **인증**: 필요 (`Authorization: Bearer <JWT>`)
- **현재 응답 DTO 필드 (`DocumentDetectionDetailResponse`)**:
  - `detectionId`, `organizationName`, `boardName`, `title`, `publishedAt`
  - `analysis.summary`: 공고 요약 텍스트
  - `analysis.keyPoints`: 핵심 요약 문장 목록 (`List<String>`)
  - `analysis.proposal.preparation.eligibilityChecklist`: 지원 자격 요건 체크리스트
  - `analysis.proposal.preparation.strategy`: 대응 전략 및 권고 의견
  - `attachments`: 첨부파일 메타데이터 및 다운로드 URL
  - ❌ **결측**: 공고문 원문 전체 텍스트(`bodyText` 또는 `content`)가 없음.

#### 우리 측 연동 상태 및 한계점 (`convert_gov_native`)
- 우리 코드: `server/src/ontology_map/services/converters.py`
- 현재 `convert_gov_native`는 원문 텍스트가 누락되어 있어, 임시 보충 데이터인 `server/demo_data/lite-supplement.json`의 스냅샷을 fallback으로 읽어와 주관기관(`새봄산업지원원`) 엣지를 생성하고 있습니다.
- 원문 본문이 없으면 HITL 검토 화면에서 사용자가 문장을 클릭하여 근거를 확인하는 '문장 하이라이트' 인터랙션이 불가능해집니다.

#### 💬 GovInsight 측 협의 요청문 가이드
다른 세션이나 메신저에서 GovInsight 담당 동기에게 아래와 같이 요청하시면 됩니다:

> **"안녕하세요! 온톨로지 맵 연동 담당입니다.  
> 현재 `GET /api/document-detections/{detectionId}` 응답을 통해 지원 자격과 전략 데이터를 매우 잘 받아보고 있습니다.  
> 다만, 저희 지식그래프 검토(HITL) 화면에서 공고 원문의 실제 문장 위치를 하이라이트하여 사실 근거를 제시해야 하는데요,  
> 혹시 해당 상세 조회 응답 객체에 **공고문 원문 전체 텍스트를 담은 `bodyText` (또는 `content`) 문자열 필드 하나만 추가**해주실 수 있을까요?  
> 원문 필드 하나만 추가되면 공고 요약뿐 아니라 원문 근거 맵핑까지 100% 자동으로 시각화 연동이 완성됩니다!"**

*(만약 동기 측에서 원문 필드 추가가 어렵다고 할 경우: 원문 하이라이트는 생략하고 요약 카드 및 제안서 메타데이터만 노드에 적재하는 fallback으로 대응 가능)*

---

## 3. 결론 및 다음 단계 작업 권장

1. **뉴스 에이전트**: URL 매핑(`GET /api/news/articles/{id}`) 및 응답 래퍼(`result`)만 맞춰주면 즉시 호출/연동 가능.
2. **엑셀 에이전트**: FE의 JSON 내보내기 파일이 우리 어댑터와 100% 일치하므로, 파일 업로드 플로우로 확정.
3. **GovInsight**: 위 협의 문구를 전달하여 원문 필드(`bodyText`) 1개 추가 여부를 확정짓고 연동 진행.
