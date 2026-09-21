# #44 저장 그래프 lint의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #44
- 주 문서: [`44-model-task.md`](44-model-task.md)
- 범위: `lint_rule`, `lint_policy_version`, `lint_policy_rule`, `lint_run`, `lint_finding`

## 1. 공통 원칙

- 승격 전 후보 검사는 메모리에서 수행하고 차단 반복 정보만 `blocked_fingerprint`에 남긴다.
- `lint_run`과 `lint_finding`은 이미 저장된 기준 지식그래프의 재검사만 나타낸다.
- rule의 안정된 의미와 policy별 선택·심각도를 분리한다.
- 실패하거나 불완전한 run은 기존 finding을 해결하지 않는다.
- lint finding은 사람의 `knowledge_item.current_state`를 자동으로 바꾸지 않는다.
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.

## 2. `lint_rule`

### 2.1 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `lint_rule_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `rule_code` | `text` | 불가 | 없음 | 사용 후 불변 |
| `display_name` | `text` | 불가 | 없음 | 통제된 표시 수정 가능 |
| `description` | `text` | 불가 | 없음 | 의미를 바꾸지 않는 설명 수정 가능 |
| `evaluation_scope` | `text` | 불가 | 없음 | 사용 후 불변 |

### 2.2 제약

- PK `pk_lint_rule`
- UNIQUE `uq_lint_rule__code (rule_code)`
- text 필드는 nonblank
- `evaluation_scope IN ('PRE_PROMOTION', 'PERSISTED_GRAPH', 'BOTH')`

규칙 알고리즘이나 판정 의미가 달라지면 같은 행을 수정하지 않고 새 `rule_code`를 만들거나 validator·policy version을 올린다. 단순 설명 오탈자는 의미 변경이 아니므로 수정할 수 있다.

### 2.3 주석

```sql
COMMENT ON TABLE lint_rule IS
'안정된 lint 규칙 정의와 평가 범위. 정책별 사용 여부와 BLOCKING/WARNING 심각도는 lint_policy_rule이 소유한다.';
```

## 3. `lint_policy_version`

### 3.1 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `lint_policy_version_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `version_no` | `integer` | 불가 | 없음 | 불변 |
| `validator_version` | `text` | 불가 | 없음 | 불변 |
| `is_active` | `boolean` | 불가 | `false` | 새 검증 선택 허용 전환 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |
| `activated_at` | `timestamptz` | 허용 | 없음 | 첫 활성화 시 한 번 채움 |

### 3.2 제약·인덱스

- PK `pk_lint_policy_version`
- UNIQUE `uq_lint_policy_version__number (version_no)`
- `version_no >= 1`
- `validator_version` nonblank
- 시각 finite
- `is_active = false`일 때 `activated_at`은 `NULL`일 수 있다.
- `is_active = true`이면 `activated_at` 필수
- 활성 정책 최대 하나:

```sql
CREATE UNIQUE INDEX uq_lint_policy_version__active
ON lint_policy_version ((true))
WHERE is_active;
```

과거에 활성화됐다가 비활성화된 정책은 `activated_at`을 유지한다. 따라서 역방향 `is_active = false → activated_at IS NULL`은 강제하지 않는다.

### 3.3 서비스 규칙

- 모든 `lint_policy_rule` 구성을 만든 뒤에만 활성화한다.
- 판정 결과가 달라질 수 있는 새 정책 활성화는 full graph `lint_run` 하나를 생성한다.
- 설명만 바뀌어 결과가 같다면 새 policy version을 만들지 않는다.
- 이미 사용된 정책의 validator와 구성은 수정하지 않는다.

## 4. `lint_policy_rule`

### 4.1 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `lint_policy_rule_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `lint_policy_version_id` | `bigint` | 불가 | 없음 | 불변 |
| `lint_rule_id` | `bigint` | 불가 | 없음 | 불변 |
| `severity` | `text` | 불가 | 없음 | 불변 |

### 4.2 제약·인덱스

- PK `pk_lint_policy_rule`
- FK policy와 rule
- UNIQUE `uq_lint_policy_rule__selection (lint_policy_version_id, lint_rule_id)`
- `severity IN ('BLOCKING', 'WARNING')`
- `ix_lint_policy_rule__rule (lint_rule_id, lint_policy_version_id)`

`blocked_fingerprint`가 참조할 수 있는지 여부는 `severity`와 연결 rule의 `evaluation_scope`를 함께 읽어야 하므로 승격 전 서비스가 검사한다.

## 5. `lint_run`

### 5.1 책임

저장된 기준 지식그래프 전체를 한 policy version으로 재검사한 실행이다. 후보 검사나 promotion transaction 실패를 기록하지 않는다.

### 5.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `lint_run_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `lint_policy_version_id` | `bigint` | 불가 | 없음 | 불변 |
| `scope_kind` | `text` | 불가 | `'FULL_GRAPH'` | 불변 |
| `status` | `text` | 불가 | `'PENDING'` | 허용 전이만 수정 |
| `started_at` | `timestamptz` | 허용 | 없음 | 실행 시작 시 한 번 채움 |
| `completed_at` | `timestamptz` | 허용 | 없음 | 종료 시 한 번 채움 |

### 5.3 제약

- PK `pk_lint_run`
- FK `lint_policy_version_id → lint_policy_version`
- `scope_kind = 'FULL_GRAPH'`
- `status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED')`
- 시각 finite, 값이 모두 있으면 `completed_at >= started_at`
- 상태 조합:

```text
PENDING → started_at, completed_at 모두 NULL
RUNNING → started_at 값 있음, completed_at NULL
SUCCESS 또는 FAILED → 두 시각 모두 값 있음
```

한 policy에 진행 중인 full run 최대 하나:

```sql
CREATE UNIQUE INDEX uq_lint_run__in_progress
ON lint_run (lint_policy_version_id)
WHERE status IN ('PENDING', 'RUNNING');
```

### 5.4 인덱스

- `ix_lint_run__status (status, lint_run_id)` WHERE status IN (`PENDING`, `RUNNING`)
- `ix_lint_run__policy (lint_policy_version_id, lint_run_id DESC)`

## 6. `lint_finding`

### 6.1 책임

저장된 한 `knowledge_item`에서 발견한 결정적 문제 인스턴스의 열린 상태·반복·해결·재발 이력을 관리한다.

### 6.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `lint_finding_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `finding_key` | `bytea` | 불가 | 없음 | 불변 |
| `knowledge_item_id` | `bigint` | 불가 | 없음 | 불변 |
| `lint_policy_rule_id` | `bigint` | 불가 | 없음 | 불변 |
| `first_detected_run_id` | `bigint` | 불가 | 없음 | 불변 |
| `latest_detected_run_id` | `bigint` | 불가 | 없음 | 반복 발견 시 갱신 |
| `first_detected_at` | `timestamptz` | 불가 | 없음 | 불변 |
| `last_detected_at` | `timestamptz` | 불가 | 없음 | 반복 발견 시 갱신 |
| `detection_count` | `integer` | 불가 | `1` | 반복 발견 시 증가 |
| `message` | `text` | 불가 | 없음 | 최근 같은 문제의 설명으로 갱신 가능 |
| `details_json` | `jsonb` | 허용 | 없음 | 최근 정형 상세로 갱신 가능 |
| `resolved_by_run_id` | `bigint` | 허용 | 없음 | 해결 시 한 번 채움 |
| `resolved_at` | `timestamptz` | 허용 | 없음 | 해결 시 한 번 채움 |
| `resolution_reason` | `text` | 허용 | 없음 | 해결 시 한 번 채움 |

### 6.3 PK·FK·고유성

- PK `pk_lint_finding`
- FK `knowledge_item_id → knowledge_item`
- FK `lint_policy_rule_id → lint_policy_rule`
- 세 run ID는 `lint_run`을 참조한다.
- 열린 finding key 최대 하나:

```sql
CREATE UNIQUE INDEX uq_lint_finding__open_key
ON lint_finding (finding_key)
WHERE resolved_at IS NULL;
```

### 6.4 행 내부 CHECK

- `octet_length(finding_key) = 32`
- `detection_count >= 1`
- `message` nonblank
- `details_json`이 있으면 JSON object
- 시각 finite, `last_detected_at >= first_detected_at`
- 해결 세 필드는 모두 `NULL`이거나 모두 값 있음
- 해결 시 `resolved_at >= last_detected_at`

### 6.5 finding key canonical input

```text
ASCII "FND1"
+ lint_policy_rule_id int64 big-endian
+ knowledge_item_id int64 big-endian
+ 규칙이 정의한 normalized identity detail의 length+bytes
```

message처럼 표현이 바뀔 수 있는 자유 문장은 key에 넣지 않는다. 규칙별 normalized detail 구성은 validator version과 함께 테스트 fixture로 고정한다.

### 6.6 반복·해결·재발

#### 반복 발견

같은 열린 key가 있으면 새 행을 만들지 않고 다음 값만 갱신한다.

- `latest_detected_run_id`
- `last_detected_at`
- `detection_count`
- 최근 `message`, `details_json`

#### 해결

`SUCCESS` full graph run에서 해당 대상과 rule이 적용 범위에 있었지만 key를 다시 발견하지 않았을 때만 해결한다. 실패 run과 부분 처리된 run은 finding을 해결하지 않는다.

새 정책에서도 같은 stable lint rule을 선택했다면 서비스가 이전 policy rule의 열린 finding과 대응해 해결할 수 있다. 단순히 policy ID가 달라졌다는 이유로 자동 해결하지 않는다.

#### 재발

해결된 finding key가 이후 다시 발견되면 기존 행을 되살리지 않고 새 finding 행을 만든다. partial unique index는 해결 행과 새 열린 행의 공존을 허용한다.

### 6.7 공개 효과

```text
공개 가능
= knowledge_item.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
  AND 공개 준비 완료
  AND 열린 BLOCKING lint finding 없음
```

열린 BLOCKING finding은 `current_state`를 `REJECTED`로 바꾸지 않는다. 해결 뒤 영향을 받은 노드의 공개 파생 결과를 다시 준비한다. WARNING은 공개를 막지 않는다.

### 6.8 인덱스

```sql
CREATE INDEX ix_lint_finding__item_open
ON lint_finding (knowledge_item_id, lint_policy_rule_id, lint_finding_id)
WHERE resolved_at IS NULL;

CREATE INDEX ix_lint_finding__blocking_open
ON lint_finding (lint_policy_rule_id, knowledge_item_id)
WHERE resolved_at IS NULL;

CREATE INDEX ix_lint_finding__latest_run
ON lint_finding (latest_detected_run_id, lint_finding_id);
```

`ix_lint_finding__blocking_open` 자체는 severity를 알지 못하므로 조회에서 `lint_policy_rule`을 join한다. severity를 finding에 중복 저장하지 않는다.

## 7. 주석

```sql
COMMENT ON TABLE lint_run IS
'이미 저장된 기준 지식그래프를 한 lint policy로 재검사한 full graph 실행. 후보 검사나 promotion 실패를 기록하지 않는다.';

COMMENT ON TABLE lint_finding IS
'저장된 knowledge item의 결정적 문제 인스턴스. 사람의 거절 상태가 아니며 열린 BLOCKING finding은 공개 조회에서만 제외한다.';
```

## 8. 수명주기와 되돌리기

| 대상 | 분류 | 변경 |
|---|---|---|
| lint rule | 안정 정의 | 의미 불변 |
| policy/version selection | 불변 버전 | `is_active` 전환만 |
| lint run | 실행 상태 | 상태·시작·완료 |
| lint finding | 열린 문제 상태 | 반복 집계·한 방향 해결 |

### `details_json` 제거

정형 상세가 실제로 사용되지 않으면 message와 finding key identity만 남기고 nullable JSON 컬럼을 제거할 수 있다. API와 validator가 JSON을 참조하는지 확인한 뒤 migration한다.

### 부분 graph run 도입

운영 규모에서 full graph 비용이 커질 때 `scope_kind`와 별도 scope table을 확장한다. 현재는 의미 없는 nullable scope FK를 미리 추가하지 않는다.

### finding이 상태를 바꾸는 모델

사람 상태와 시스템 건강도 상태를 합치려면 기존 `knowledge_state_event` 의미·관리자 권한·복구 절차를 모두 다시 설계해야 한다. 단순 컬럼 매핑으로 바꾸지 않는다.
