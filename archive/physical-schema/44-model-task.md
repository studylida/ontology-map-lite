# #44 모델 작업과 반복 차단의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #44
- 범위: `output_schema_definition`, `model_task`, `agent_attempt`, `blocked_fingerprint`
- 별도 문서: 저장 그래프 lint

## 1. 공통 원칙

- JSON Schema 계약은 저장하지만 실제 Agent 응답 JSON·후보 payload·provider 원문은 저장하지 않는다.
- 하나의 `cache_key`는 하나의 내구성 있는 논리 작업을 식별한다.
- 모델 호출과 메모리 검사는 DB 장기 트랜잭션 밖에서 수행한다.
- 결과를 내구성 있게 연결한 뒤에만 작업을 `SUCCESS`로 바꾼다.
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.

## 2. `output_schema_definition`

### 2.1 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `output_schema_definition_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `task_kind` | `text` | 불가 | 없음 | 불변 |
| `version_no` | `integer` | 불가 | 없음 | 불변 |
| `schema_json` | `jsonb` | 불가 | 없음 | 불변 |
| `is_active` | `boolean` | 불가 | `false` | 새 작업 선택 허용 전환 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

### 2.2 제약과 인덱스

- PK `pk_output_schema_definition`
- UNIQUE `uq_output_schema_definition__version (task_kind, version_no)`
- active contract 최대 하나:

```sql
CREATE UNIQUE INDEX uq_output_schema_definition__active
ON output_schema_definition (task_kind)
WHERE is_active;
```

- `version_no >= 1`
- `jsonb_typeof(schema_json) = 'object'`
- `task_kind IN ('KNOWLEDGE_EXTRACTION', 'ENTITY_RESOLUTION_PROPOSAL', 'EVIDENCE_LINEAGE_PROPOSAL', 'CONFLICT_SUMMARY', 'NODE_CONTEXT', 'FOLLOWUP_QUESTIONS')`
- `EMBEDDING`은 JSON Structured Output 계약을 갖지 않는다.
- `isfinite(created_at)`

### 2.3 불변성

- 새 작업은 active contract만 선택한다.
- `model_task`가 한 번이라도 참조한 `task_kind`, `version_no`, `schema_json`은 수정하지 않는다.
- 계약 변경은 새 version 행으로 추가한다.
- runtime에는 계약 본문을 수정하는 일반 UPDATE 경로를 제공하지 않는다.

### 2.4 주석

```sql
COMMENT ON TABLE output_schema_definition IS
'모델이 반환해야 할 JSON Schema 계약의 불변 버전. 응답 인스턴스나 provider 원문을 저장하지 않는다.';
```

## 3. `model_task`

### 3.1 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `model_task_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `task_kind` | `text` | 불가 | 없음 | 불변 |
| `source_document_id` | `bigint` | 허용 | 없음 | 불변 |
| `input_hash` | `bytea` | 불가 | 없음 | 불변 |
| `output_schema_definition_id` | `bigint` | 허용 | 없음 | 불변 |
| `model_version` | `text` | 불가 | 없음 | 불변 |
| `prompt_version` | `text` | 허용 | 없음 | 불변 |
| `cache_key` | `bytea` | 불가 | 없음 | 불변 |
| `status` | `text` | 불가 | `'PENDING'` | 상태 전이만 허용 |
| `attempt_count` | `integer` | 불가 | `0` | 호출 시 원자 증가 |
| `next_attempt_at` | `timestamptz` | 허용 | 없음 | retry 대기에서만 사용 |
| `lease_owner` | `text` | 허용 | 없음 | 실행 lease 동안만 사용 |
| `lease_expires_at` | `timestamptz` | 허용 | 없음 | 실행 lease 동안만 사용 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |
| `finished_at` | `timestamptz` | 허용 | 없음 | 종료 전이 시 한 번 채움 |

### 3.2 키·FK·기본 CHECK

- PK `pk_model_task`
- FK `source_document_id → source_document`
- FK `output_schema_definition_id → output_schema_definition`
- UNIQUE `uq_model_task__cache_key (cache_key)`
- `octet_length(input_hash) = 32`
- `octet_length(cache_key) = 32`
- `model_version` nonblank
- `prompt_version`이 있으면 nonblank
- `attempt_count BETWEEN 0 AND 5`
- 시각 값은 모두 finite
- `task_kind IN ('KNOWLEDGE_EXTRACTION', 'ENTITY_RESOLUTION_PROPOSAL', 'EVIDENCE_LINEAGE_PROPOSAL', 'CONFLICT_SUMMARY', 'NODE_CONTEXT', 'FOLLOWUP_QUESTIONS', 'EMBEDDING')`
- `status IN ('PENDING', 'RUNNING', 'SUCCESS', 'RETRY_WAIT', 'VALIDATION_BLOCKED', 'FINAL_FAILED')`

### 3.3 EMBEDDING 예외

```text
task_kind = 'EMBEDDING'
→ output_schema_definition_id IS NULL
→ prompt_version IS NULL

그 외 task_kind
→ output_schema_definition_id IS NOT NULL
→ prompt_version IS NOT NULL
```

비EMBEDDING 작업에서는 service가 계약의 `task_kind`와 작업 종류가 같은지 확인한다. 이 교차 행 비교를 DB trigger로 중복 구현하지 않는다.

### 3.4 상태별 행 내부 조합

#### `PENDING`

```text
finished_at IS NULL
next_attempt_at IS NULL
lease_owner IS NULL
lease_expires_at IS NULL
```

#### `RUNNING`

```text
finished_at IS NULL
next_attempt_at IS NULL
lease_owner IS NOT NULL AND nonblank
lease_expires_at IS NOT NULL
```

#### `RETRY_WAIT`

```text
finished_at IS NULL
next_attempt_at IS NOT NULL
lease_owner IS NULL
lease_expires_at IS NULL
attempt_count < 5
```

#### 종료 상태

```text
status IN ('SUCCESS', 'VALIDATION_BLOCKED', 'FINAL_FAILED')
finished_at IS NOT NULL
next_attempt_at IS NULL
lease_owner IS NULL
lease_expires_at IS NULL
```

`FINAL_FAILED`는 최대 시도 소진뿐 아니라 인증·잘못된 요청처럼 재시도하지 않는 영구 오류도 포함하므로 `attempt_count = 5`를 강제하지 않는다.

### 3.5 cache key canonical input

`cache_key`는 다음 고정 순서의 길이 prefix binary encoding에 SHA-256을 적용한다.

```text
ASCII "TASK1"
+ task_kind UTF-8 length+bytes
+ input_hash 32 bytes
+ output_schema_definition_id int64 big-endian
  - EMBEDDING은 0
+ model_version UTF-8 length+bytes
+ prompt_version UTF-8 length+bytes
  - EMBEDDING은 길이 0
```

`input_hash`는 실제 모델 입력 전체의 canonical bytes에서 계산한다. 다중 문서 목록은 제품 DB에 별도 관계로 저장하지 않지만 정렬된 문서 ID와 입력 텍스트는 hash 입력에 포함한다.

### 3.6 작업 획득과 완료 트랜잭션

#### 획득

- `PENDING` 또는 실행 시각이 지난 `RETRY_WAIT`만 `FOR UPDATE SKIP LOCKED`로 획득한다.
- `RUNNING`, lease owner·만료 시각을 한 트랜잭션에서 기록한 뒤 외부 모델을 호출한다.

#### 호출 결과 반영

1. 작업 행을 다시 잠근다.
2. 이미 종료 상태면 중복 결과를 버린다.
3. `agent_attempt`을 추가한다.
4. 같은 트랜잭션에서 `attempt_count`를 증가시킨다.
5. 성공 결과가 있으면 결과 행과 FK를 내구성 있게 연결한다.
6. 그 뒤에만 `SUCCESS`와 `finished_at`을 기록한다.
7. 일시 오류면 `RETRY_WAIT`와 다음 시각, 영구 오류면 `FINAL_FAILED`를 기록한다.

`attempt_count = agent_attempt 행 수 = MAX(attempt_no)`는 서비스 트랜잭션 불변식이다.

### 3.7 인덱스

```sql
CREATE INDEX ix_model_task__runnable
ON model_task (status, next_attempt_at, created_at, model_task_id)
WHERE status IN ('PENDING', 'RETRY_WAIT');

CREATE INDEX ix_model_task__expired_lease
ON model_task (lease_expires_at, model_task_id)
WHERE status = 'RUNNING';

CREATE INDEX ix_model_task__source_document
ON model_task (source_document_id, model_task_id)
WHERE source_document_id IS NOT NULL;
```

contract FK 조회는 `ix_model_task__contract (output_schema_definition_id, model_task_id)`를 둔다.

### 3.8 주석

```sql
COMMENT ON COLUMN model_task.attempt_count IS
'이 논리 작업에서 실제 provider를 호출한 누계. cache 적중은 증가시키지 않으며 agent_attempt 행과 같은 트랜잭션에서 유지한다.';
```

## 4. `agent_attempt`

### 4.1 컬럼

| 컬럼 | 타입 | NULL | 기본값 |
|---|---|---:|---|
| `agent_attempt_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` |
| `model_task_id` | `bigint` | 불가 | 없음 |
| `attempt_no` | `integer` | 불가 | 없음 |
| `outcome` | `text` | 불가 | 없음 |
| `failure_reason` | `text` | 허용 | 없음 |
| `attempted_at` | `timestamptz` | 불가 | 없음 |

### 4.2 제약과 인덱스

- PK `pk_agent_attempt`
- FK `model_task_id → model_task`
- UNIQUE `uq_agent_attempt__number (model_task_id, attempt_no)`
- `attempt_no BETWEEN 1 AND 5`
- `outcome IN ('SUCCESS', 'TIMEOUT', 'RATE_LIMITED', 'PROVIDER_ERROR', 'AUTHENTICATION_ERROR', 'INVALID_REQUEST', 'OUTPUT_CONTRACT_ERROR')`
- `outcome = 'SUCCESS' ↔ failure_reason IS NULL`
- 실패 outcome이면 `failure_reason` nonblank
- `isfinite(attempted_at)`
- `ix_agent_attempt__task (model_task_id, attempt_no DESC)`는 UNIQUE 인덱스의 순서가 오름차순만 지원해 최근 시도 조회가 병목일 때만 추가하며 #48에서 최종 결정한다.

시도별 모델·프롬프트·계약·토큰·비용·provider 응답 ID를 중복 저장하지 않는다.

## 5. `blocked_fingerprint`

### 5.1 책임

계약은 유효하지만 승격 전 `BLOCKING` 규칙에 실패한 같은 후보의 반복 검증·승격을 억제한다. 후보 본문을 저장하지 않는다.

### 5.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 |
|---|---|---:|---|
| `blocked_fingerprint_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` |
| `fingerprint` | `bytea` | 불가 | 없음 |
| `source_document_id` | `bigint` | 불가 | 없음 |
| `output_schema_definition_id` | `bigint` | 불가 | 없음 |
| `lint_policy_rule_id` | `bigint` | 불가 | 없음 |
| `first_blocked_at` | `timestamptz` | 불가 | 없음 |
| `last_blocked_at` | `timestamptz` | 불가 | 없음 |
| `blocked_count` | `integer` | 불가 | `1` |

### 5.3 키·FK·CHECK

- PK `pk_blocked_fingerprint`
- FK 세 개는 문서·계약·정확한 policy rule을 참조한다.
- UNIQUE:

```text
(fingerprint, source_document_id, output_schema_definition_id, lint_policy_rule_id)
```

- `octet_length(fingerprint) = 32`
- `blocked_count >= 1`
- 시각은 finite이며 `last_blocked_at >= first_blocked_at`

### 5.4 서비스 규칙

- 참조 policy rule의 severity가 `BLOCKING`이어야 한다.
- lint rule scope가 `PRE_PROMOTION` 또는 `BOTH`여야 한다.
- 계약 위반, 모델 장애, WARNING과 자유 payload는 기록하지 않는다.
- 같은 조합을 다시 발견하면 `last_blocked_at`, `blocked_count`만 갱신한다.
- 새 문서 버전·계약·정책 규칙에서는 동일한 후보라도 다시 검사한다.

### 5.5 인덱스

- UNIQUE 인덱스가 반복 차단 조회를 지원한다.
- `ix_blocked_fingerprint__source (source_document_id, last_blocked_at DESC)`
- `ix_blocked_fingerprint__policy_rule (lint_policy_rule_id, last_blocked_at DESC)`

## 6. 수명주기와 되돌리기

| 대상 | 분류 | 수정 가능 값 |
|---|---|---|
| output contract | 불변 버전 | `is_active`만 전환 |
| model task | 운영 상태 행 | 상태·lease·retry·count·finished |
| agent attempt | append-only | 없음 |
| blocked fingerprint | 반복 집계 | 최근 시각·횟수 |

### 대안으로 돌아가기

- 시도 이력을 제거하려면 마지막 outcome·reason·시각을 task에 backfill하고 scheduler·감사 조회를 전환한 뒤 table을 제거한다.
- 실제 응답 보존이 필요하면 민감정보·저작권·retention을 승인한 별도 불변 artifact를 만든다. 이 테이블에 JSON 컬럼을 추가하지 않는다.
- cache key 입력을 바꾸면 기존 key를 UPDATE하지 않고 `cache_key_version`을 포함한 새 작업 namespace를 도입한다.
