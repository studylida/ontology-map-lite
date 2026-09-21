# #48 PostgreSQL 통합 물리 스키마 검증 v2

## 문서 상태

- 관련 Issue: #48
- 최초 통합 기준: PR #89
- 추가 논리·물리 설계: #91, PR #92
- 최종 inventory: [`48-integrated-inventory-v2.md`](48-integrated-inventory-v2.md)
- 무결성·migration·테스트: [`48-integrity-migration-tests-v2.md`](48-integrity-migration-tests-v2.md)
- 상태: 인사이트 확장을 포함한 통합 설계 검증 완료

PR #89에서 검증한 #40~#47과 #78의 결정은 계속 유효하다. 이 문서는 #68의 인사이트 탭을 위해 추가된 #91을 기준선 위에 통합하고, 변경된 수량·공개 완결성·migration 순서와 회귀 테스트를 다시 검증한다.

## 1. 통합 결과

| 항목 | 결과 |
|---|---|
| 물리 설계 테이블 | 45개 |
| 초기 migration 대상 | 43개 |
| actor 계약까지 비차단 보류 | 2개 |
| 새 테이블 | `node_insight`, `node_insight_claim` |
| 기존 테이블 변경 | `publication_affected_node.node_insight_model_task_id` 추가 |
| 모델 작업 변경 | `NODE_INSIGHT` task kind와 출력 계약 추가 |
| 새 DB blocker | 없음 |
| migration dependency cycle | 없음 |

보류 테이블은 이전과 동일하다.

```text
knowledge_state_event
conflict_state_event
```

두 테이블은 사람 상태 변경의 실제 actor FK가 정해질 때 추가한다. 인사이트 기능은 이 보류에 의존하지 않는다.

## 2. 핵심 설계 판정

### 2.1 `node` 컬럼으로 저장하지 않는다

`node`는 불변 정체성과 유형만 소유한다. 인사이트는 다음 이유로 별도 불변 산출물이다.

- 한 node에 여러 리포트가 존재한다.
- 90일과 1년 입력 범위가 다르다.
- 모델·prompt·출력 계약·입력 지식이 바뀔 수 있다.
- 각 리포트가 사용한 Claim 집합이 다르다.
- 생성 중 결과와 READY 결과를 섞어서는 안 된다.

`node_context`는 한 개의 일반 맥락 설명이고 `node_insight`는 제목·순서·Claim 근거를 가진 여러 분석 리포트이므로 서로 대체하지 않는다.

### 2.2 사실 사본을 만들지 않는다

새 테이블에는 다음 모델 생성 문장만 저장한다.

```text
title
summary_text
synthesis_text
caveat_text
```

화면의 `근거로 확인된 내용`은 `node_insight_claim.role = KEY_CLAIM`인 기존 Claim에서 읽는다. `verified_fact_text`, Relation 사본, 인용문 사본, 출처 사본과 저장된 근거 개수는 만들지 않는다.

`EVIDENCE_VERIFIED`는 출처가 Claim을 실제로 뒷받침한다는 뜻이며 객관적 진실 확정을 뜻하지 않는다. 따라서 UI와 DB 모두 `확인된 사실`보다 `근거로 확인된 내용`이라는 표현을 사용한다.

### 2.3 모델 작업을 bundle 식별자로 재사용한다

한 `NODE_INSIGHT` model task가 다음 결과 묶음을 소유한다.

```text
node 한 개
node_search_document 한 개
as_of_at 한 개
RECENT_90_DAYS 결과 0~3개
RECENT_1_YEAR 결과 0~3개
```

현재 POC에는 별도 `node_insight_set`을 추가하지 않는다. model task가 입력 hash·모델·prompt·출력 계약과 실행 상태를 이미 소유하므로 결과 묶음 식별자 역할을 수행할 수 있다.

### 2.4 결과 없음은 가짜 행으로 표현하지 않는다

의미 있는 인사이트가 없는 범위는 빈 제목이나 `NO_RESULT` 행을 만들지 않는다.

```text
선택된 NODE_INSIGHT task = SUCCESS
+ 해당 time_window의 node_insight 행 = 0개
= 검증된 결과 없음
```

서비스는 Structured Output에서 90일·1년 두 범위를 모두 처리했는지 확인한 뒤 작업을 성공으로 닫는다.

## 3. Evidence Trace와 근거 개수

원문 경로는 기존 Claim 계보를 그대로 사용한다.

```text
node_insight
→ node_insight_claim
→ claim
→ claim_observation
→ observation
→ source_document
```

Relation은 다음 경로로 조회한다.

```text
node_insight_claim
→ claim_relation
→ relation
```

목록의 근거 개수는 저장하지 않고 선택 범위 안에서 계산한다.

```text
COUNT(DISTINCT source_document.evidence_group_id)
```

같은 보도자료를 재게시한 여러 문서와 같은 문서의 여러 observation은 독립 근거 하나로 센다. 이 숫자는 객관적 신뢰도나 사업 중요도 점수가 아니라 인사이트가 사용한 독립 원문 계보 수다.

## 4. 공개 완결성

`publication_affected_node`는 기존 검색 문서·임베딩·맥락 설명과 함께 정확한 인사이트 model task를 선택한다.

```text
node_search_document_id
node_embedding_id
node_context_id
node_insight_model_task_id
```

v2 READY 전환은 다음을 원자적으로 검사한다.

1. 선택한 검색 문서·임베딩·맥락 설명이 같은 node와 검색 문서에 속한다.
2. 후속 질문 slot 1·2가 완전하다.
3. 인사이트 작업이 `NODE_INSIGHT + SUCCESS`다.
4. 인사이트 작업이 같은 node와 같은 검색 문서를 사용한다.
5. 90일·1년 두 범위를 모두 처리했다.
6. 각 비어 있지 않은 인사이트에 Claim과 `KEY_CLAIM`이 최소 한 건 있다.
7. 모든 basis Claim과 Evidence Trace가 공개 가능하다.
8. 모든 affected node가 검사를 통과한 뒤에만 batch를 READY로 바꾼다.

인사이트 생성 실패는 이미 COMMITTED인 기준 지식을 rollback하지 않는다. 새 publication만 FAILED가 되고 이전 READY 결과를 계속 제공한다.

## 5. 거절·lint 이후 동작

연결 Claim이 `ON_HOLD`, `REJECTED`가 되거나 열린 `BLOCKING` lint finding을 가지면 해당 Claim을 사용한 인사이트 전체를 즉시 read-time에서 제외한다.

일부 Claim만 제거하고 기존 summary·synthesis를 계속 보여주지 않는다. 모델 문장의 전제가 달라졌을 수 있기 때문이다.

```text
basis Claim 비공개
→ 역방향 인덱스로 영향 insight·node 조회
→ 기존 stale insight 즉시 제외
→ 새 검색 문서·인사이트·맥락·질문 준비
→ 새 publication READY 뒤 교체
```

WARNING finding은 자동으로 숨기지 않으며 새 생성의 caveat 입력으로 사용할 수 있다.

## 6. migration 검증

v2 migration 순서는 다음과 같이 acyclic하다.

```text
기존 #48 Phase 0~10
→ node_insight
→ node_insight_claim
→ publication_affected_node
→ 인덱스·COMMENT·seed
→ actor 계약 뒤 상태 이벤트
```

- `node_insight`는 먼저 존재하는 node, 검색 문서, model task만 참조한다.
- `node_insight_claim`은 먼저 존재하는 insight와 Claim만 참조한다.
- `publication_affected_node`를 모든 파생 결과 뒤에 만들면 forward FK가 순환하지 않는다.
- kind·status·same-node·두 window 완결성은 서비스가 검사하므로 custom trigger dependency가 생기지 않는다.

기존 READY 행에는 새 nullable FK를 임의로 backfill하지 않는다. 실제 인사이트 기능을 공개하기 전에 새 publication bundle을 준비한다.

## 7. 기존 #48 결정 회귀 결과

다음 결정은 변경 없이 통과했다.

- PostgreSQL 18.6과 pgvector 0.8.6
- Qwen `qwen3.7-text-embedding`, dense `vector(1024)`, cosine `<=>`, exact top 50
- 공유 `knowledge_item` PK
- `ON DELETE RESTRICT ON UPDATE RESTRICT`
- 교차 행 의미 검사는 서비스 트랜잭션 한 곳에서 수행
- expression GIN 전문 검색과 RRF
- 지도 좌표·밝기·부분 그래프 snapshot 비영속
- 폐기된 ontology manifest와 evidence-group assignment 미복원
- `ix_observation__source_document`, `ix_agent_attempt__task` 초기 중복 인덱스 제외
- #64와 #81은 schema 비차단 후속 작업

## 8. HBF end-to-end 판정

다음 흐름을 고아 근거나 조기 공개 없이 표현할 수 있다.

```text
source_document
→ observation
→ Claim·Relation
→ promotion COMMITTED
→ node_search_document
→ Qwen embedding
→ node_context·질문
→ NODE_INSIGHT model task
→ node_insight·node_insight_claim
→ publication READY
→ 제목 목록·독립 근거 묶음 수
→ 상세 dialog의 분석 문장·Claim·Evidence Trace
```

모델이 만든 `협상 범위를 넓힐 수 있다` 같은 문장은 synthesis로 표시되며 실제 계약·매출 사실 Claim으로 승격되지 않는다. Claim이 없으면 `근거로 확인된 내용`에 표시하지 않는다.

## 9. 완료 판단

#40~#47, #69, #78과 #91의 논리·물리 설계를 함께 구현할 수 있는 PostgreSQL inventory와 migration·검증 계약이 완성됐다.

다만 이 완료는 다음 작업을 수행했다는 뜻이 아니다.

```text
실제 CREATE TABLE DDL
Alembic migration
SQLAlchemy model
PostgreSQL container 실행
Qwen adapter와 실제 모델 호출
인사이트 prompt와 품질 평가
API·repository·UI fixture 교체
실제 HBF seed와 회귀 테스트 실행
```

실제 구현에서는 v2 inventory와 테스트 계약을 기준으로 migration과 서비스 코드를 작성하고, 결과가 문서 계약과 일치하는지 실행 검증해야 한다.

## 10. 후속 변경 경계

- 시간 범위별 인사이트 작업을 독립 교체해야 할 때만 `node_insight_set`을 검토한다.
- Claim 하나가 여러 Relation을 가리켜 UI에서 의도한 Relation을 판별할 수 없다는 실제 사례가 생길 때만 직접 Relation 연결을 검토한다.
- 인사이트를 publication 필수에서 선택 기능으로 낮추려면 READY 검증만 명시적으로 변경하고 기존 산출물을 삭제하지 않는다.
- 인사이트 구조를 바꾸면 #91의 소유 문서를 먼저 수정한 뒤 #48 통합 검증을 다시 수행한다.
