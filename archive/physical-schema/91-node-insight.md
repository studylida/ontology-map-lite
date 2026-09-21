# #91 node 인사이트의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #91
- 제품 기능: #68
- 논리 확장: [`../logical-schema/91-node-insight.md`](../logical-schema/91-node-insight.md)
- 공통 규칙: [`../physical-data-schema.md`](../../docs/data/physical-schema.md)
- 선행 매핑: #43, #44, #46, #47
- 범위: `node_insight`, `node_insight_claim`, `NODE_INSIGHT` 작업과 공개 선택 FK

## 1. 공통 결정

- 인사이트는 `node` 컬럼이나 JSON이 아니라 불변 모델 산출물 행으로 저장한다.
- 모델이 만든 제목·요약·종합 해석·유의점만 새 문자열로 저장한다.
- 사실·관계·인용문·출처는 기존 Claim과 Evidence Trace를 참조한다.
- 한 `NODE_INSIGHT` 작업은 한 node와 한 검색 문서에서 90일·1년 결과를 함께 만든다.
- 각 시간 범위에는 인사이트를 최대 세 개 허용한다.
- 의도적인 결과 없음은 성공한 model task와 해당 범위의 0개 행으로 표현한다.
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.
- `node_insight`와 `node_insight_claim`에는 UPDATE·DELETE runtime 경로를 제공하지 않는다.

## 2. `node_insight`

### 2.1 책임

한 node의 공개 검색 문서와 Claim·Relation·근거를 모델이 미리 종합한 제목 있는 분석 리포트 한 건이다. 클릭 시 새 모델 호출을 수행하지 않는다.

### 2.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `node_insight_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `node_id` | `bigint` | 불가 | 없음 | 불변 |
| `node_search_document_id` | `bigint` | 불가 | 없음 | 불변 |
| `model_task_id` | `bigint` | 불가 | 없음 | 불변 |
| `time_window` | `text` | 불가 | 없음 | 불변 |
| `as_of_at` | `timestamptz` | 불가 | 없음 | 불변 |
| `slot` | `smallint` | 불가 | 없음 | 불변 |
| `title` | `text` | 불가 | 없음 | 불변 |
| `summary_text` | `text` | 불가 | 없음 | 불변 |
| `synthesis_text` | `text` | 불가 | 없음 | 불변 |
| `caveat_text` | `text` | 불가 | 없음 | 불변 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

`as_of_at`은 모델 호출 시각이나 공개 완료 시각이 아니라 90일·1년 입력 범위를 계산한 기준 시각이다.

### 2.3 PK·FK·고유성

- PK `pk_node_insight (node_insight_id)`
- 복합 FK:

```text
(node_search_document_id, node_id)
→ node_search_document(node_search_document_id, node_id)
```

- FK `fk_node_insight__model_task`
  - `model_task_id → model_task.model_task_id`
- UNIQUE `uq_node_insight__task_window_slot`

```text
(model_task_id, time_window, slot)
```

같은 model task와 시간 범위에서 한 slot은 한 인사이트만 가진다. model task 하나가 여러 인사이트를 만들기 때문에 `model_task_id` 단독 UNIQUE는 두지 않는다.

### 2.4 CHECK

```text
time_window IN ('RECENT_90_DAYS', 'RECENT_1_YEAR')
slot IN (1, 2, 3)
btrim(title) <> ''
btrim(summary_text) <> ''
btrim(synthesis_text) <> ''
btrim(caveat_text) <> ''
isfinite(as_of_at)
isfinite(created_at)
```

임의 길이 제한은 두지 않는다. 모델 출력 계약과 UI 검증에서 적절한 표시 길이를 관리한다.

### 2.5 인덱스

```sql
CREATE INDEX ix_node_insight__node_window
ON node_insight (
    node_id,
    node_search_document_id,
    time_window,
    model_task_id,
    slot
);
```

지원 조회:

- 현재 publication이 선택한 node·검색 문서·작업의 시간 범위별 제목 목록
- 같은 node의 이전 인사이트 작업 이력
- 선택한 시간 범위의 slot 순서 조회

`model_task_id, time_window, slot` 방향은 UNIQUE 인덱스가 지원한다.

### 2.6 DB 주석

```sql
COMMENT ON TABLE node_insight IS
'한 node의 공개 검색 문서와 근거 Claim을 모델이 미리 종합한 불변 분석 리포트. 클릭 시 생성하지 않으며 사실·관계·원문 사본을 저장하지 않는다.';

COMMENT ON COLUMN node_insight.as_of_at IS
'RECENT_90_DAYS와 RECENT_1_YEAR 입력 범위를 계산한 기준 시각. 모델 호출 시각이나 공개 완료 시각이 아니다.';

COMMENT ON COLUMN node_insight.synthesis_text IS
'여러 근거를 연결한 모델의 종합 해석. 원문에서 직접 확인된 사실 문장으로 표시해서는 안 된다.';
```

## 3. `node_insight_claim`

### 3.1 책임

한 인사이트가 사용한 기존 Claim과 화면 역할·표시 순서를 연결한다. Claim 문장, modality, Relation과 Evidence Trace를 복사하지 않는다.

### 3.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `node_insight_id` | `bigint` | 불가 | 없음 | 불변 |
| `claim_id` | `bigint` | 불가 | 없음 | 불변 |
| `role` | `text` | 불가 | 없음 | 불변 |
| `display_order` | `smallint` | 불가 | 없음 | 불변 |

### 3.3 PK·FK·UNIQUE·CHECK

- PK `pk_node_insight_claim (node_insight_id, claim_id)`
- FK `node_insight_id → node_insight.node_insight_id`
- FK `claim_id → claim.claim_id`
- UNIQUE `uq_node_insight_claim__display_order (node_insight_id, display_order)`
- `role IN ('KEY_CLAIM', 'SUPPORTING_CLAIM', 'CONTRASTING_CLAIM')`
- `display_order >= 1`

한 Claim은 한 인사이트에서 역할 하나만 가진다. 같은 Claim은 여러 인사이트에서 사용할 수 있다.

### 3.4 인덱스

```sql
CREATE INDEX ix_node_insight_claim__claim
ON node_insight_claim (claim_id, node_insight_id);
```

이 인덱스는 Claim 거절·BLOCKING lint 뒤 영향을 받는 인사이트를 찾는 역방향 조회를 지원한다. 한 인사이트의 Claim 목록은 PK와 display-order UNIQUE 인덱스가 지원한다.

### 3.5 DB 주석

```sql
COMMENT ON TABLE node_insight_claim IS
'인사이트가 사용한 기존 Claim과 화면 역할을 연결한다. 확인된 사실·원문·Relation 사본이 아니며 Evidence Trace는 Claim에서 조회한다.';

COMMENT ON COLUMN node_insight_claim.role IS
'KEY_CLAIM은 근거로 확인된 내용, SUPPORTING_CLAIM은 보조 근거, CONTRASTING_CLAIM은 엇갈리는 관점이나 유의점의 근거다.';
```

## 4. `NODE_INSIGHT` model task 계약

### 4.1 코드 목록 변경

`output_schema_definition.task_kind`와 `model_task.task_kind`의 닫힌 CHECK에 다음 값을 추가한다.

```text
NODE_INSIGHT
```

`NODE_INSIGHT`는 JSON Structured Output 작업이므로 다음 필드는 필수다.

```text
output_schema_definition_id NOT NULL
prompt_version NOT NULL
```

`EMBEDDING` 예외에는 포함하지 않는다.

### 4.2 작업 단위

한 model task는 다음 하나의 bundle을 만든다.

```text
node 한 개
+ node_search_document 한 개
+ as_of_at 한 개
+ RECENT_90_DAYS 결과 0~3개
+ RECENT_1_YEAR 결과 0~3개
```

모델 작업의 성공 결과는 `node_insight` 여러 행과 `node_insight_claim` 여러 행 또는 두 범위 모두의 의도적인 0개 결과다.

실제 Structured Output JSON은 현재 공통 정책대로 저장하지 않는다. 서비스가 계약을 검증하고 모든 결과 행을 같은 트랜잭션에서 insert한 뒤에만 `model_task.status = 'SUCCESS'`와 `finished_at`을 기록한다.

### 4.3 입력 hash

`model_task.input_hash`는 다음 고정 binary framing의 SHA-256으로 계산한다. 모델·프롬프트·출력 계약은 기존 `cache_key` 입력에서 별도로 포함한다.

```text
ASCII "NINS1"
+ node_id signed int64 big-endian
+ node_search_document_id signed int64 big-endian
+ as_of_at UTC microseconds since Unix epoch signed int64 big-endian
+ time window count uint16 big-endian = 2
+ 각 window code의 UTF-8 length+bytes를 고정 순서로 추가
+ 공개 입력 Claim count uint64 big-endian
+ 각 Claim을 claim_id 오름차순으로:
  - claim_id int64
  - statement_text UTF-8 length+bytes
  - language, modality length+bytes
  - 주장 시간 값·precision의 고정 NULL 표식
  - 관련 relation ID의 정렬된 목록
  - 선택 범위의 observation ID와 evidence_group_id 정렬 목록
+ 비교 가능한 conflict set과 position의 정렬 목록
```

시간 범위 순서는 `RECENT_90_DAYS`, `RECENT_1_YEAR`로 고정한다. 문자열은 UTF-8, Unicode NFC, LF를 사용한다.

### 4.4 서비스 검증

결과 insert 전 다음을 검사한다.

1. task가 아직 종료되지 않았고 같은 task의 결과가 없다.
2. task kind와 output schema task kind가 `NODE_INSIGHT`로 일치한다.
3. 모든 출력 Claim ID가 입력 Claim 집합에 포함된다.
4. Claim이 현재 공개 가능하고 해당 검색 문서의 공개 basis에 포함된다.
5. Claim이 중심 node의 속성·사건 시간·관계 또는 인접 관계와 연결된다.
6. 각 비어 있지 않은 인사이트에 Claim과 `KEY_CLAIM`이 최소 한 건 있다.
7. 두 window가 모두 출력 계약에서 처리됐다.
8. 같은 task 결과의 node, 검색 문서와 `as_of_at`이 모두 같다.
9. title·summary·synthesis·caveat가 nonblank다.
10. 결과 전체를 insert한 뒤 task를 SUCCESS로 닫는다.

DB custom trigger로 이 교차 행 검증을 중복 구현하지 않는다.

## 5. `publication_affected_node` 확장

### 5.1 추가 컬럼

```text
node_insight_model_task_id bigint NULL
```

FK:

```text
node_insight_model_task_id
→ model_task.model_task_id
ON DELETE RESTRICT
ON UPDATE RESTRICT
```

이 FK는 작업 행의 존재만 보장한다. task kind·status·node·검색 문서·두 시간 범위 완결성은 READY 서비스 검증이 담당한다.

### 5.2 상태별 규칙

```text
PREPARING 또는 FAILED
→ node_insight_model_task_id가 NULL이거나 완료 결과를 가리킬 수 있음

READY
→ node_insight_model_task_id NOT NULL
→ 성공한 NODE_INSIGHT 작업
→ 같은 node와 node_search_document
→ 두 시간 범위 처리 완료
```

`node_insight_model_task_id`는 READY 이후 수정하지 않는다. 새 지식이나 새 모델·prompt 결과를 공개하려면 새 promotion batch의 publication 선택을 만든다.

### 5.3 READY 완결성 추가

기존 #46의 READY 검사에 다음을 추가한다.

1. 각 affected node가 정확한 `node_insight_model_task_id`를 가진다.
2. 작업은 `NODE_INSIGHT + SUCCESS`다.
3. 작업 결과의 모든 행이 affected row와 같은 node·검색 문서를 가리킨다.
4. 작업이 90일·1년 두 범위를 검증했다.
5. 각 비어 있지 않은 인사이트의 Claim·role이 완전하다.
6. 모든 Claim은 공개 가능하고 열린 BLOCKING lint가 없다.
7. 결과 없음은 성공 작업으로 확인되며 가짜 빈 인사이트 행이 없다.

인사이트 준비 실패는 기준 지식을 rollback하지 않는다. batch의 publication을 FAILED로 기록하고 이전 READY 결과를 계속 제공한다.

## 6. 목록 근거 수 계산

근거 개수는 컬럼으로 저장하지 않는다. 선택한 time window의 source published 범위 안에서 계산한다.

```sql
COUNT(DISTINCT sd.evidence_group_id)
```

논리적인 join 경로:

```text
node_insight ni
JOIN node_insight_claim nic
JOIN claim_observation co
JOIN observation o
JOIN source_document sd
```

- `ni.time_window`과 `ni.as_of_at`으로 source published 범위를 계산한다.
- `published_at IS NULL`인 문서는 범위별 count에서 제외한다.
- 같은 evidence group의 여러 문서·observation은 한 번만 센다.
- `DISPUTE`나 contrasting Claim의 근거를 지지 수에서 빼지 않는다. UI에서 role과 엇갈림으로 구분한다.

## 7. Evidence Trace와 Relation 조회

### 7.1 원문

```text
node_insight
→ node_insight_claim
→ claim_observation
→ observation
→ source_document
```

팝업에는 기존 Claim의 문장·modality·시간을 표시하고 observation의 quote·문서 title·publisher·published time·URL로 이동한다.

### 7.2 Relation

```text
node_insight_claim
→ claim_relation
→ relation
```

Claim이 여러 Relation과 연결되면 중심 node가 endpoint인 Relation과 인사이트 출력 입력에 포함된 Relation만 표시한다. 별도 `node_insight_relation`은 만들지 않는다.

## 8. 거절과 lint

연결 Claim이 다음 상태가 되면 인사이트 전체를 즉시 read-time에서 제외한다.

```text
knowledge_item.current_state IN ('ON_HOLD', 'REJECTED')
또는 열린 BLOCKING lint finding 존재
```

일부 Claim만 숨기고 기존 `summary_text`나 `synthesis_text`를 계속 표시하면 모델 문장과 근거가 달라질 수 있으므로 허용하지 않는다.

영향받은 node는 새 검색 문서·인사이트·맥락·질문 publication을 준비한다. 새 결과가 READY가 되기 전에는 이전 인사이트 중 여전히 모든 basis가 유효한 결과만 제공한다.

## 9. migration 배치

실제 DDL 구현에서는 다음 순서를 사용한다.

```text
1. model task와 output schema task-kind CHECK 확장
2. node_insight 생성
3. node_insight_claim 생성
4. publication_affected_node에 node_insight_model_task_id 추가
5. FK·UNIQUE·CHECK·인덱스·COMMENT 추가
6. READY 서비스와 테스트를 배포한 뒤 NOT NULL 성격을 서비스 불변식으로 활성화
```

`publication_affected_node`에 이미 READY 데이터가 존재할 수 있으므로 컬럼 자체는 nullable로 유지한다. 새 기능 적용 이전 READY row는 인사이트가 없는 legacy publication으로 구분하며, UI fixture를 실제 API로 전환하기 전에 새 publication을 생성한다.

## 10. 테스트 계약

### DB schema

- task-kind CHECK가 `NODE_INSIGHT`를 허용한다.
- 잘못된 time window·slot·빈 text·무한 시각을 거절한다.
- 같은 task/window/slot 중복을 거절한다.
- 같은 인사이트의 Claim 중복과 display order 중복을 거절한다.
- 다른 node의 search document를 참조하면 복합 FK가 거절한다.
- 부모 Claim·검색 문서·task 삭제는 RESTRICT된다.

### 서비스 transaction

- 한 task의 여러 인사이트와 basis가 모두 저장되기 전 SUCCESS가 되지 않는다.
- 존재하지 않거나 입력에 없던 Claim ID를 거절한다.
- 90일·1년 중 한 범위를 누락한 출력을 거절한다.
- 한 범위가 의도적으로 비어 있는 성공 결과를 허용한다.
- READY는 올바른 insight task 없이는 실패한다.
- Claim 거절 뒤 인사이트 전체가 즉시 조회에서 제외된다.

### 조회

- 목록의 count는 같은 evidence group을 한 번만 센다.
- title 목록은 slot 순서가 안정적이다.
- `KEY_CLAIM`은 기존 Claim 문장과 Evidence Trace로 표시된다.
- Relation은 `claim_relation`으로 조회되고 별도 사본을 사용하지 않는다.
- 클릭 시 모델 호출이 없다.

## 11. 되돌리기

### 인사이트를 publication 필수에서 선택 기능으로 낮추기

`publication_affected_node.node_insight_model_task_id`를 READY 필수 검증에서 제외하고 UI가 NULL을 결과 없음이 아닌 기능 미준비로 처리한다. 두 테이블은 과거 산출물 보존을 위해 유지할 수 있다.

### 독립적인 인사이트 bundle 테이블 도입

여러 모델 작업을 한 publication에서 조합하거나 시간 범위별 작업을 독립 교체해야 할 실제 요구가 생기면 `node_insight_set`을 새 Issue로 추가한다. 현재 한 작업이 두 시간 범위를 함께 만드는 POC에서는 model task 자체가 bundle 식별자 역할을 하므로 추가하지 않는다.

### 직접 Relation 연결 추가

Claim 하나가 여러 Relation을 연결해 UI가 의도한 Relation을 안정적으로 구분하지 못하는 실제 사례가 생길 때만 `node_insight_relation`을 검토한다. 현재는 Claim·중심 node·입력 Relation 집합으로 충분하므로 만들지 않는다.
