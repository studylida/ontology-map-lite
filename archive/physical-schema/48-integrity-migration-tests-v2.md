# #48 통합 무결성·migration·테스트 계약 v2

## 문서 상태

- 관련 Issue: #48
- 기준선: [`48-integrity-migration-tests.md`](48-integrity-migration-tests.md), PR #89
- 추가 설계: #91, PR #92
- inventory: [`48-integrated-inventory-v2.md`](48-integrated-inventory-v2.md)
- 상태: 인사이트 확장을 포함한 최종 회귀 검증 기준

기존 #48 검증 계약은 그대로 유지한다. 이 문서는 새 인사이트 구조 때문에 달라진 무결성 책임, migration phase, rollback, 공개 완결성과 테스트를 추가한다.

## 1. 통합 판정

#91 이후에도 다음 경계는 유지된다.

```text
DB row-local 제약
→ 값 형태, PK·FK, UNIQUE, 닫힌 code와 nullable 조합

서비스 트랜잭션
→ 여러 행·테이블·그래프 의미와 publication 완결성

read-time filter
→ 거절·보류·열린 BLOCKING lint와 stale 인사이트 즉시 제외
```

인사이트 도입을 위해 custom trigger, 별도 원문 사본, 전역 graph version이나 지도 snapshot을 추가하지 않는다.

## 2. 무결성 책임 추가

| 규칙 | 보장 주체 |
|---|---|
| `node_insight_id` 고유성 | DB PK |
| insight의 node와 검색 문서 일치 | DB 복합 FK |
| model task 존재 | DB FK |
| time window 두 값만 허용 | DB CHECK |
| slot 1~3 | DB CHECK |
| 같은 task·window·slot 중복 금지 | DB UNIQUE |
| title·summary·synthesis·caveat nonblank | DB CHECK |
| insight 안의 Claim 중복 금지 | DB 복합 PK |
| 같은 display order 중복 금지 | DB UNIQUE |
| role 닫힌 목록 | DB CHECK |
| Claim에서 insight 역조회 | `(claim_id, node_insight_id)` 인덱스 |
| model task kind가 `NODE_INSIGHT` | 서비스 트랜잭션 |
| model task와 output contract 종류 일치 | 서비스 트랜잭션 |
| 한 task의 모든 결과가 같은 node·검색 문서·as-of 사용 | 서비스 트랜잭션 |
| 출력 Claim이 실제 입력 집합에 포함 | 서비스 트랜잭션 |
| Claim이 중심 node와 관련 | 서비스 트랜잭션 |
| Claim이 공개 가능하고 검색 문서 basis에 포함 | 서비스 트랜잭션 |
| 각 인사이트에 Claim과 KEY_CLAIM 최소 한 건 | 서비스 트랜잭션 |
| 90일·1년 모두 처리 | 서비스 트랜잭션 |
| 결과 전체 저장 뒤 task SUCCESS | 서비스 트랜잭션 |
| publication이 같은 node·검색 문서의 insight task 선택 | READY 서비스 트랜잭션 |
| basis Claim 거절·차단 뒤 stale insight 미노출 | read-time filter + 재생성 작업 |
| evidence count의 동일 원문 계보 중복 제거 | 조회 SQL의 `COUNT(DISTINCT evidence_group_id)` |

같은 서비스 규칙을 DB custom trigger로 복제하지 않는다.

## 3. `NODE_INSIGHT` 작업 원자성

한 작업의 완료 순서는 다음과 같다.

```text
1. model_task row 잠금
2. 아직 종료되지 않았고 기존 결과가 없는지 확인
3. Structured Output 계약 검사
4. Claim·Relation·Evidence Trace 입력 범위 검증
5. node_insight 0~6개 insert
6. 각 nonempty insight의 node_insight_claim insert
7. 두 window 처리와 모든 cross-row 불변식 재검사
8. model_task.status = SUCCESS, finished_at 기록
9. commit
```

5~8 가운데 하나라도 실패하면 전체를 rollback한다. 부분적인 인사이트 목록이나 basis 없는 모델 문장이 남아서는 안 된다.

`SUCCESS + 0개 row`는 다음 조건에서만 허용한다.

- 출력 계약이 90일과 1년 두 범위를 모두 명시했다.
- 각 범위가 의미 있는 결과 없음으로 검증됐다.
- 서비스가 가짜 빈 title·summary 행을 만들지 않았다.

## 4. publication READY v2

기존 READY 검증에 인사이트를 추가한다.

### 4.1 필요한 선택

각 `publication_affected_node`는 v2 공개에서 다음을 가진다.

```text
node_search_document_id
node_embedding_id
node_context_id
node_insight_model_task_id
```

후속 질문은 선택 context의 slot 1·2에서 조회한다.

### 4.2 잠금과 검사

서비스는 batch와 모든 affected row를 잠근 뒤 다음을 검사한다.

1. `promotion_status = COMMITTED`다.
2. `publication_status = PREPARING`이다.
3. 검색 문서·임베딩·context가 같은 node와 검색 문서다.
4. context의 질문 slot 1·2가 완전하다.
5. `node_insight_model_task_id`가 있다.
6. insight task의 kind가 `NODE_INSIGHT`, status가 `SUCCESS`다.
7. task 결과의 모든 insight가 affected node와 선택 검색 문서를 참조한다.
8. task 결과의 `as_of_at`이 하나다.
9. 90일·1년 범위를 모두 검증했다.
10. 존재하는 insight마다 basis Claim과 KEY_CLAIM이 있다.
11. 모든 basis Claim은 공개 가능하고 열린 BLOCKING finding이 없다.
12. 검색 문서 basis와 상세 Evidence Trace도 유효하다.
13. 모든 affected node가 같은 검사를 통과한다.
14. 통과하면 batch를 READY로 바꾼다.

인사이트 작업 실패는 promotion COMMITTED를 되돌리지 않는다. publication만 FAILED가 되고 이전 READY bundle을 계속 선택한다.

## 5. read-time stale 차단

READY 이후라도 Claim은 사람 거절이나 새 lint 정책의 BLOCKING finding으로 즉시 비공개가 될 수 있다.

```text
node_insight_claim.claim_id
→ knowledge_item.current_state
→ 열린 BLOCKING lint finding
```

다음 중 하나면 해당 인사이트 전체를 결과에서 제외한다.

```text
Claim state = ON_HOLD
Claim state = REJECTED
Claim에 열린 BLOCKING finding 있음
```

KEY_CLAIM 하나만 숨기고 summary·synthesis를 계속 제공하지 않는다. 모델 문장의 전제가 달라질 수 있기 때문이다.

역방향 인덱스 `ix_node_insight_claim__claim`으로 영향을 받은 인사이트와 node를 찾고 새 publication 준비를 시작한다.

## 6. evidence count 검증

목록 count는 선택 insight의 모든 Claim에서 범위 안의 observation을 따라 계산한다.

### 6.1 90일

```text
published_at >= as_of_at - interval '90 days'
published_at < as_of_at
```

### 6.2 1년

```text
published_at >= as_of_at - interval '1 year'
published_at < as_of_at
```

### 6.3 집계

```text
COUNT(DISTINCT source_document.evidence_group_id)
```

다음 사례를 검사한다.

- 같은 evidence group의 문서 세 개 → count 1
- 같은 문서의 observation 두 개 → count 1
- 독립 evidence group 두 개 → count 2
- 범위 밖 문서 → 해당 window count에서 제외
- `published_at UNKNOWN` → 범위 count에서 제외
- contrasting Claim의 evidence는 basis 목록에는 표시하되 지지 근거 count와 혼동하지 않도록 API 계약에서 역할별 count를 구분하거나 전체 근거 count라고 명시

POC 목록이 하나의 count만 표시한다면 이름을 `근거 묶음 N개`로 사용하고 `지지 강도`라고 부르지 않는다.

## 7. 결정적 `NODE_INSIGHT` 입력 hash

기존 SHA-256 목록에 `NINS1`을 추가한다.

```text
ASCII "NINS1"
+ node_id
+ node_search_document_id
+ as_of_at UTC microseconds
+ window code 2개 고정 순서
+ 정렬된 공개 Claim 입력
+ 각 Claim의 문장·언어·modality·시간
+ 정렬된 relation ID
+ 선택 범위의 observation ID·evidence_group_id
+ 비교 가능한 conflict set·position
```

문자열은 UTF-8, NFC, LF와 길이 prefix를 사용한다. 모델·prompt·output contract는 기존 `model_task.cache_key`에서 포함하므로 `input_hash`에 중복하지 않는다.

변경 시 규칙:

- 입력 framing을 바꾸면 prefix를 `NINS2`처럼 올린다.
- 기존 `input_hash`와 결과 행을 UPDATE하지 않는다.
- 새 model task와 새 insight를 생성한다.

## 8. migration phase v2

기존 phase를 다음처럼 갱신한다.

```text
Phase 0
pgvector extension

Phase 1
독립 코드·계약·lint 정의

Phase 2
relation·attribute revision과 정책 구성

Phase 3
준비 문서와 observation

Phase 4
model_task·agent_attempt·blocked_fingerprint·promotion_batch

Phase 5
knowledge_item·node·relation·claim

Phase 6
alias·external identifier·merge·event time·Claim 연결

Phase 7
persisted graph lint

Phase 8
conflict snapshot과 summary

Phase 9
node_search_document·search_document_basis

Phase 10
node_embedding·node_context·followup_question

Phase 11
node_insight·node_insight_claim

Phase 12
publication_affected_node

Phase 13
초기 인덱스·COMMENT·seed

Phase 14
actor 계약 뒤 knowledge_state_event·conflict_state_event
```

### 8.1 순환성 검사

- `node_insight`는 이미 생성된 `node_search_document`, `model_task`, `node`를 참조한다.
- `node_insight_claim`은 이미 생성된 `node_insight`, `claim`을 참조한다.
- `publication_affected_node`는 모든 파생 결과 뒤에 생성한다.
- insight task FK는 model task만 직접 참조하므로 DDL 순환이 없다.
- READY의 kind·status·same-node 검사는 서비스가 담당하므로 trigger 순환이 없다.

따라서 v2 migration graph도 acyclic하다.

## 9. 기존 READY 데이터 전환

컬럼 추가:

```text
publication_affected_node.node_insight_model_task_id NULL
```

기존 READY row에는 NULL을 유지한다. 과거 데이터에 임의의 insight task를 연결하지 않는다.

기능 전환 절차:

1. schema와 nullable FK 배포
2. `NODE_INSIGHT` task-kind와 output contract seed
3. 모델 생성·저장 서비스 배포
4. 영향 node의 새 insight task 실행
5. search·embedding·context·questions·insight를 포함한 새 publication 준비
6. READY 전환
7. 인사이트 UI가 v2 READY 결과만 조회하도록 전환

구 publication은 인사이트 기능 미지원 legacy 결과로 분류한다. 새 기능 배포 뒤에도 이전 READY를 fallback으로 사용할지, 인사이트 없는 상태를 표시할지는 API 구현 Issue에서 정한다.

## 10. rollback

### 10.1 schema migration 실패

새 테이블과 nullable publication FK가 아직 사용되지 않았다면 역순으로 제거할 수 있다.

```text
publication FK 제거
node_insight_claim 제거
node_insight 제거
task-kind CHECK 원복
```

### 10.2 데이터 생성 실패

- model task를 RETRY_WAIT 또는 FINAL_FAILED로 처리
- partial insight row는 같은 트랜잭션 rollback
- promotion graph는 유지
- publication은 이전 READY 유지

### 10.3 기능 철회

인사이트 UI를 철회해도 기존 인사이트 행을 자동 삭제하지 않는다. publication READY 필수 검증에서 insight task를 제외하고 read path를 끈다. POC 보존 원칙에 따라 과거 결과는 유지한다.

## 11. schema test

- `node_insight` PK·FK·UNIQUE·CHECK introspection
- `node_insight_claim` PK·FK·UNIQUE·CHECK introspection
- `publication_affected_node.node_insight_model_task_id` nullable FK 확인
- `NODE_INSIGHT` task-kind CHECK 확인
- slot 0·4 거절
- 알 수 없는 time window 거절
- 빈 title·summary·synthesis·caveat 거절
- 중복 task/window/slot 거절
- 다른 node의 search document 복합 FK 거절
- 같은 insight의 Claim 중복 거절
- display order 중복 거절
- 부모 task·Claim·검색 문서 DELETE RESTRICT

## 12. transaction test

- 인사이트 3개 중 두 번째 저장 실패 시 모든 insight·basis rollback
- basis 하나가 입력 Claim 집합 밖이면 task SUCCESS 금지
- KEY_CLAIM 없는 insight 거절
- 한 window 누락 출력 거절
- 0개 결과 두 window 성공 허용
- 잘못된 node·검색 문서 task를 publication이 선택하면 READY 거절
- insight task FINAL_FAILED이면 이전 READY 유지
- Claim 거절과 같은 트랜잭션 뒤 stale insight 조회 제외
- 새 insight 준비 성공 후 다음 READY에서 교체

## 13. query test

- 목록은 slot 1→3 순서
- time window에 맞는 insight만 반환
- evidence group 중복 제거
- KEY_CLAIM의 Claim 문장과 Evidence Trace 조회
- supporting·contrasting role 구분
- Relation은 claim_relation 경로로 조회
- 저장된 evidence count·verified fact 문자열을 참조하지 않음
- 클릭 시 모델 작업 생성 없음

## 14. HBF end-to-end v2

```text
source_document
→ observation
→ Claim·Relation
→ promotion COMMITTED
→ node_search_document
→ Qwen embedding
→ node_context·질문
→ NODE_INSIGHT 작업
→ node_insight·node_insight_claim
→ publication READY
→ 인사이트 제목·근거 묶음 수
→ 상세 dialog의 분석·Claim·Evidence Trace
```

필수 시나리오:

1. SK하이닉스 90일 인사이트 최대 3개가 slot 순서로 표시된다.
2. 1년 범위는 같은 task의 별도 결과를 반환한다.
3. `HBF 표준화 참여가 협상 범위를 넓힐 수 있다`는 synthesis가 실제 계약 성과 Claim으로 표시되지 않는다.
4. `KEY_CLAIM`만 `근거로 확인된 내용`에 표시된다.
5. `caveat_text`는 모델 해석 한계를 밝힌다.
6. 같은 보도자료 재게시 세 건은 근거 묶음 하나로 센다.
7. basis Claim을 거절하면 해당 insight 전체가 즉시 사라진다.
8. 새 insight 작업이 실패하면 이전 READY 결과를 유지한다.
9. 결과가 없는 노드도 가짜 insight 행 없이 정상 empty state를 표시한다.

## 15. 비차단 후속 작업

- #64: 준비 문서의 evidence group 판정 전략
- #81: exact vector search의 ANN 전환 기준
- #68 후속 구현: 실제 API·모델 prompt·UI fixture 교체
- actor 계약: 두 상태 이벤트 테이블

이 항목들은 v2 물리 스키마 문서 완료를 막지 않는다.
