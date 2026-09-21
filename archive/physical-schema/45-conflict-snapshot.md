# #45 불변 충돌 스냅샷의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #45
- 선행 매핑: #43, #44
- 범위: `conflict_set`, `conflict_member`, `conflict_state_event` 보류 계약, `conflict_summary`

## 1. 설계 원칙

- 충돌은 어느 Claim이 참인지 판정하는 데이터가 아니라 비교 가능한 Claim 구성을 보존하는 불변 snapshot이다.
- 관계, 한 노드의 한 속성, 한 사건 시간 가운데 정확히 하나만 비교 대상으로 가진다.
- 대상·modality·구성 Claim·`position_key`가 바뀌면 기존 행을 수정하지 않고 새 set과 전체 member를 만든다.
- 같은 set에 모델·prompt만 다른 summary를 여러 개 추가할 수 있다.
- 사람의 확인·거절은 conflict 표현만 바꾸며 member Claim의 상태와 Evidence Trace를 수정하지 않는다.
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.
- 구성 불변성을 서비스 repository와 runtime 권한으로 보장하고 같은 규칙을 DB custom trigger로 중복 구현하지 않는다.

## 2. `conflict_set`

### 2.1 책임

한 의미 대상과 하나의 Claim modality를 비교하는 충돌 snapshot의 정체성과 현재 검토 상태를 관리한다.

### 2.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `conflict_set_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `relation_id` | `bigint` | 허용 | 없음 | 불변 |
| `target_node_id` | `bigint` | 허용 | 없음 | 불변 |
| `attribute_revision_id` | `bigint` | 허용 | 없음 | 불변 |
| `event_node_id` | `bigint` | 허용 | 없음 | 불변 |
| `modality` | `text` | 불가 | 없음 | 불변 |
| `current_state` | `text` | 불가 | 없음 | 허용된 사람 전이만 수정 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

`current_state`에는 편의 기본값을 두지 않는다. 일반 코드가 검증된 최초 set을 만들 때 `AGENT_PROPOSED`를 명시한다.

### 2.3 PK·FK

- PK `pk_conflict_set (conflict_set_id)`
- FK `relation_id → relation.relation_id`
- FK `target_node_id → node.node_id`
- FK `attribute_revision_id → attribute_revision.attribute_revision_id`
- FK `event_node_id → event_temporal_extent.event_node_id`

사건 시간 충돌은 채택 시간 행이 실제로 존재하는 사건만 대상으로 하므로 `event_node_id`는 `node`가 아니라 `event_temporal_extent`를 참조한다.

### 2.4 대상 형태 CHECK

명명된 CHECK `ck_conflict_set__target_shape`는 다음 세 형태 중 정확히 하나만 허용한다.

#### 관계 충돌

```text
relation_id IS NOT NULL
target_node_id IS NULL
attribute_revision_id IS NULL
event_node_id IS NULL
```

#### 구조화 속성값 충돌

```text
relation_id IS NULL
target_node_id IS NOT NULL
attribute_revision_id IS NOT NULL
event_node_id IS NULL
```

#### 사건 시간 충돌

```text
relation_id IS NULL
target_node_id IS NULL
attribute_revision_id IS NULL
event_node_id IS NOT NULL
```

따라서 속성 대상의 두 FK 중 하나만 채우거나 모든 대상을 비우거나 둘 이상의 대상 형태를 동시에 채울 수 없다.

### 2.5 나머지 CHECK

- `modality IN ('FACT', 'PLAN_OR_TARGET', 'PREDICTION_OR_ESTIMATE', 'OPINION_OR_EVALUATION')`
- `current_state IN ('AGENT_PROPOSED', 'HUMAN_CONFIRMED', 'REJECTED')`
- `isfinite(created_at)`

### 2.6 서비스 생성 검증

일반 코드는 새 set과 전체 member를 한 트랜잭션에서 만든다.

1. 대상 형태를 하나 선택한다.
2. 모든 member Claim이 같은 의미 대상을 가리키는지 확인한다.
3. 모든 member Claim의 `modality`가 set의 modality와 같은지 확인한다.
4. Claim마다 observation이 최소 하나 있고 현재 상태가 `EVIDENCE_VERIFIED` 또는 `HUMAN_VERIFIED`인지 확인한다.
5. 관계 충돌은 member Claim이 해당 relation을 지지하거나 반박하는지 확인한다.
6. 속성 충돌은 같은 `target_node_id + attribute_revision_id`를 주장하는지 확인한다.
7. 사건 시간 충돌은 같은 event node의 시간을 주장하는지 확인한다.
8. 시간·정밀도와 표현 성격이 실제로 비교 가능한지 확인한다.
9. member가 최소 두 개이고 서로 다른 `position_key`가 최소 두 개 있는지 확인한다.
10. 대상 행을 잠근 뒤 같은 target·modality·정렬된 member/position 구성이 이미 있으면 기존 set을 재사용한다.

POC에는 별도 `composition_hash`를 추가하지 않는다. 대상별 직렬 트랜잭션과 완전한 member 비교로 같은 snapshot을 재사용한다.

### 2.7 인덱스

```sql
CREATE INDEX ix_conflict_set__relation
ON conflict_set (relation_id, current_state, conflict_set_id)
WHERE relation_id IS NOT NULL;

CREATE INDEX ix_conflict_set__attribute
ON conflict_set (
    target_node_id,
    attribute_revision_id,
    current_state,
    conflict_set_id
)
WHERE target_node_id IS NOT NULL;

CREATE INDEX ix_conflict_set__event
ON conflict_set (event_node_id, current_state, conflict_set_id)
WHERE event_node_id IS NOT NULL;
```

`current_state` 단독 인덱스는 만들지 않는다. 실제 조회는 항상 대상과 함께 수행한다.

### 2.8 주석

```sql
COMMENT ON TABLE conflict_set IS
'관계·노드 속성·사건 시간 중 한 의미 대상을 비교하는 불변 Claim snapshot. 어느 Claim이 참인지 자동 판정하지 않는다.';

COMMENT ON COLUMN conflict_set.current_state IS
'Agent 제안에 대한 사람의 선택적 확인·거절 상태. member Claim의 knowledge state를 변경하지 않는다.';
```

## 3. `conflict_member`

### 3.1 책임

한 conflict set에 포함된 정확한 Claim과 같은 관점을 묶는 안정된 position code를 저장한다.

### 3.2 컬럼·키·FK

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `conflict_set_id` | `bigint` | 불가 | 없음 | 불변 |
| `claim_id` | `bigint` | 불가 | 없음 | 불변 |
| `position_key` | `text` | 불가 | 없음 | 불변 |

- PK `pk_conflict_member (conflict_set_id, claim_id)`
- FK `conflict_set_id → conflict_set.conflict_set_id`
- FK `claim_id → claim.claim_id`
- `btrim(position_key) <> ''`

같은 Claim은 한 set에 한 번만 포함된다. 하나의 position에는 여러 Claim이 들어갈 수 있다.

### 3.3 인덱스

```sql
CREATE INDEX ix_conflict_member__claim
ON conflict_member (claim_id, conflict_set_id);

CREATE INDEX ix_conflict_member__position
ON conflict_member (conflict_set_id, position_key, claim_id);
```

PK가 set의 전체 member 조회를 지원하고, 역방향 인덱스는 Claim이 속한 conflict 조회를 지원한다. position 인덱스는 관점별 상세 패널 구성을 지원한다.

### 3.4 불변성

- set 생성 이후 member insert·update·delete를 허용하지 않는다.
- Claim 추가·제거 또는 position 변경은 새 `conflict_set`과 전체 member snapshot을 만든다.
- runtime repository에는 `replace_members`, `update_position`, 범용 delete 함수를 제공하지 않는다.
- migration·maintenance role 외에는 member UPDATE·DELETE 권한을 주지 않는다.

## 4. `conflict_state_event` — 비차단 보류

### 4.1 초기 POC

- 일반 코드가 `AGENT_PROPOSED` set을 만들 때 사람 이벤트를 생성하지 않는다.
- 관리자·인증 actor 계약이 없으므로 초기 migration에서 `conflict_state_event`를 제외할 수 있다.
- 사람 확인·거절 UI도 actor FK와 함께 후속 구현한다.

### 4.2 후속 테이블 계약

| 컬럼 | 예정 타입·규칙 |
|---|---|
| `conflict_state_event_id` | `bigint GENERATED ALWAYS AS IDENTITY` PK |
| `conflict_set_id` | `bigint NOT NULL` FK |
| `from_state`, `to_state` | 허용 상태 `text NOT NULL` |
| `reason` | nonblank `text NOT NULL` |
| `actor_id` | 확정된 actor PK 타입의 필수 FK |
| `changed_at` | finite `timestamptz NOT NULL` |

허용 전이:

```text
AGENT_PROPOSED → HUMAN_CONFIRMED | REJECTED
HUMAN_CONFIRMED, REJECTED → 종료
```

서비스는 set row를 잠그고 `from_state`와 현재 상태가 같은지 확인한 뒤 이벤트 insert와 `current_state` update를 한 트랜잭션에서 수행한다.

## 5. `conflict_summary`

### 5.1 책임

불변 conflict set을 입력으로 성공한 모델 작업이 생성한 사용자용 공통점과 관점 요약 본문을 저장한다.

### 5.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `conflict_summary_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `conflict_set_id` | `bigint` | 불가 | 없음 | 불변 |
| `model_task_id` | `bigint` | 불가 | 없음 | 불변 |
| `common_ground_text` | `text` | 불가 | 없음 | 불변 |
| `viewpoint_summary_text` | `text` | 불가 | 없음 | 불변 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

### 5.3 키·FK·CHECK

- PK `pk_conflict_summary (conflict_summary_id)`
- FK `conflict_set_id → conflict_set.conflict_set_id`
- FK `model_task_id → model_task.model_task_id`
- UNIQUE `uq_conflict_summary__model_task (model_task_id)`
- 두 text 필드는 nonblank
- `isfinite(created_at)`

한 성공 작업은 conflict summary 한 행만 만든다. 같은 set에는 모델·prompt·계약 버전이 다른 작업의 summary를 여러 개 추가할 수 있다.

### 5.4 서비스 검증

summary 저장과 작업 성공 전환을 한 짧은 트랜잭션에서 처리한다.

1. `model_task.task_kind = 'CONFLICT_SUMMARY'`인지 확인한다.
2. 작업이 아직 종료되지 않았고 같은 task 결과가 없는지 확인한다.
3. 실제 모델 입력이 set의 대상·modality와 모든 member를 포함했는지 확인한다.
4. member는 `claim_id` 오름차순, 같은 position 내부에서도 ID 오름차순으로 결정적으로 정렬한다.
5. `position_key`, Claim 문장, modality, 시간과 Evidence Trace 식별자를 작업 입력 hash에 포함한다.
6. summary 행을 insert하고 결과 FK가 내구성 있게 연결된 뒤 `model_task.status = SUCCESS`로 전환한다.

`input_hash`, 모델·prompt·출력 계약은 summary에 중복 저장하지 않고 `model_task_id`로 조회한다.

### 5.5 인덱스

```sql
CREATE INDEX ix_conflict_summary__set
ON conflict_summary (
    conflict_set_id,
    created_at DESC,
    conflict_summary_id DESC
);
```

model task 방향은 UNIQUE 인덱스가 지원한다.

### 5.6 Evidence Trace 경로

```text
conflict_summary
→ conflict_set
→ conflict_member
→ claim
→ claim_observation
→ observation
→ source_document
```

별도 `conflict_summary_input`을 만들지 않는다. summary는 생성 당시 불변 set·member snapshot을 계속 참조한다.

## 6. 수명주기와 공개 효과

| 대상 | 분류 | 변경 가능 값 |
|---|---|---|
| conflict set 대상·modality | 불변 snapshot | 없음 |
| conflict set state | 선택적 사람 검토 상태 | 허용 전이만 |
| conflict member | 불변 snapshot | 없음 |
| conflict summary | 불변 모델 산출물 | 없음 |
| conflict state event | 후속 append-only | 기존 행 수정 없음 |

- 검증된 `AGENT_PROPOSED`도 사용자에게 `Agent가 발견한 엇갈림`으로 표시할 수 있다.
- `HUMAN_CONFIRMED`는 문구만 사람 확인 상태로 바꾸고 호박색 점선은 유지한다.
- `REJECTED`는 충돌 표현만 숨긴다.
- 어느 상태 전이도 member Claim을 삭제하거나 `REJECTED`로 바꾸지 않는다.
- 일반 관계선 굵기는 SUPPORT 근거 수로 계속 계산하며 충돌 상태를 굵기에 섞지 않는다.

## 7. 선택과 되돌리기

### `composition_key` 도입

동시 동일 snapshot 중복이 실제로 발생하면 다음 순서로 전환한다.

1. 대상·modality·정렬된 `(claim_id, position_key)`에서 canonical SHA-256 key를 정의한다.
2. 기존 set을 backfill하고 중복 set의 summary 참조를 보존한 채 canonical set을 선택한다.
3. target shape + composition key의 조건부 UNIQUE를 적용한다.
4. 서비스의 전체 member 비교는 collision 방어 확인으로 축소한다.

### DB trigger로 구성 불변성 강화

직접 SQL 쓰기를 DB가 단독으로 막아야 하면 set·member UPDATE·DELETE를 거절하는 좁은 trigger를 추가할 수 있다. 이 경우 repository의 동일 판정 로직을 중복 유지하지 않는다.

### actor 도입

실제 actor PK를 먼저 확정한 뒤 state event와 관리자 UI를 함께 추가한다. 자유 문자열을 임시 actor로 저장하지 않는다.
