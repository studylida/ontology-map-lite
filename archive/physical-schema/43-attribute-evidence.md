# #43 구조화 값과 Evidence Trace의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #43
- 주 문서: [`43-knowledge-relation-claim.md`](43-knowledge-relation-claim.md)
- 범위: `claim_attribute_value`, `event_temporal_basis`, `claim_observation`

## 1. 공통 원칙

- Claim의 의미 대상은 typed 연결 테이블로 표현한다. 범용 `claim_target`이나 JSON target을 추가하지 않는다.
- Claim은 관계·구조화 속성값·사건 시간 중 최소 한 의미 연결과 observation 최소 한 건을 가진다.
- 값 종류와 사용하지 않는 값 컬럼의 `NULL` 조합은 DB CHECK로 막는다.
- target node type, NUMBER 단위, precision-aware 순서와 최소 연결 수처럼 다른 행을 읽는 검사는 승격 서비스가 담당한다.
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.

## 2. `claim_attribute_value`

### 2.1 책임

Claim이 한 node의 승인된 속성에 관해 주장하는 구조화 값을 tagged union으로 저장한다. 실제 사실 확정값이나 node 프로필 캐시가 아니다.

### 2.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `claim_attribute_value_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `claim_id` | `bigint` | 불가 | 없음 | 불변 |
| `target_node_id` | `bigint` | 불가 | 없음 | 불변 |
| `attribute_revision_id` | `bigint` | 불가 | 없음 | 불변 |
| `value_kind` | `text` | 불가 | 없음 | 불변 |
| `string_value` | `text` | 허용 | 없음 | 불변 |
| `number_value` | `numeric` | 허용 | 없음 | 불변 |
| `unit_code` | `text` | 허용 | 없음 | 불변 |
| `date_from` | `date` | 허용 | 없음 | 불변 |
| `date_to` | `date` | 허용 | 없음 | 불변 |
| `date_from_precision` | `text` | 불가 | 없음 | 불변 |
| `date_to_precision` | `text` | 불가 | 없음 | 불변 |
| `boolean_value` | `boolean` | 허용 | 없음 | 불변 |

### 2.3 PK·FK

- PK `pk_claim_attribute_value (claim_attribute_value_id)`
- FK `claim_id → claim.claim_id`
- FK `target_node_id → node.node_id`
- 복합 FK:

```text
(attribute_revision_id, value_kind)
→ attribute_revision(attribute_revision_id, allowed_value_kind)
```

#42가 참조 대상 `UNIQUE (attribute_revision_id, allowed_value_kind)`를 제공한다.

### 2.4 공통 CHECK

- `value_kind IN ('STRING', 'NUMBER', 'DATE', 'PERIOD', 'BOOLEAN')`
- `date_from_precision`, `date_to_precision IN ('DAY', 'MONTH', 'YEAR', 'UNKNOWN')`
  - 구조화 속성 날짜는 시각을 저장하지 않으므로 `INSTANT`를 허용하지 않는다.
- `number_value`가 있으면 `NaN`, `Infinity`, `-Infinity`가 아니다.
- `string_value`, `unit_code`가 있으면 nonblank다.

### 2.5 tagged-union CHECK

하나의 명명된 CHECK에서 다음 다섯 형태 중 정확히 하나만 허용한다.

#### `STRING`

```text
value_kind = 'STRING'
string_value IS NOT NULL
number_value, unit_code, date_from, date_to, boolean_value IS NULL
date_from_precision = 'UNKNOWN'
date_to_precision = 'UNKNOWN'
```

#### `NUMBER`

```text
value_kind = 'NUMBER'
number_value IS NOT NULL
unit_code IS NOT NULL
string_value, date_from, date_to, boolean_value IS NULL
date_from_precision = 'UNKNOWN'
date_to_precision = 'UNKNOWN'
```

#### `DATE`

```text
value_kind = 'DATE'
date_from IS NOT NULL
date_from_precision IN ('DAY', 'MONTH', 'YEAR')
date_to IS NULL
date_to_precision = 'UNKNOWN'
string_value, number_value, unit_code, boolean_value IS NULL
```

#### `PERIOD`

```text
value_kind = 'PERIOD'
date_from IS NOT NULL
date_from_precision IN ('DAY', 'MONTH', 'YEAR')
string_value, number_value, unit_code, boolean_value IS NULL

그리고
- date_to IS NULL     ↔ date_to_precision = 'UNKNOWN'
- date_to IS NOT NULL ↔ date_to_precision IN ('DAY', 'MONTH', 'YEAR')
```

#### `BOOLEAN`

```text
value_kind = 'BOOLEAN'
boolean_value IS NOT NULL
string_value, number_value, unit_code, date_from, date_to IS NULL
date_from_precision = 'UNKNOWN'
date_to_precision = 'UNKNOWN'
```

`boolean_value = false`는 근거가 있는 명시적 부정이다. 속성값 행 부재와 다르다.

### 2.6 서비스 검증

- `attribute_revision.is_active = true`인지 승격 직전에 확인한다.
- `target_node.node_type_id = attribute_revision.target_node_type_id`인지 확인한다.
- NUMBER의 `unit_code = attribute_revision.unit_rule`인지 확인한다.
- DATE·PERIOD의 월·연도 anchor가 정규화 규칙에 맞는지 확인한다.
- PERIOD 종료가 있을 때 두 precision을 의미 범위로 확장해 가능한 종료가 가능한 시작보다 완전히 앞서면 거절한다.
- 노드 사이 사실을 동적 attribute code나 Boolean으로 우회하지 않는다.

### 2.7 인덱스

- `ix_claim_attribute_value__claim (claim_id, claim_attribute_value_id)`
- `ix_claim_attribute_value__target (target_node_id, attribute_revision_id, claim_id)`
  - node 상세 패널과 같은 속성 Claim 비교
- `ix_claim_attribute_value__attribute (attribute_revision_id, target_node_id, claim_id)`
  - 속성별 충돌 후보 조회

### 2.8 주석

```sql
COMMENT ON TABLE claim_attribute_value IS
'Claim이 node 속성에 관해 주장한 구조화 값. target node의 현재 확정 프로필 값이나 사실 판정 결과가 아니다.';

COMMENT ON COLUMN claim_attribute_value.boolean_value IS
'false도 원문 근거가 있는 명시적 부정이다. 값 행이 없는 미상과 구분한다.';
```

## 3. `event_temporal_basis`

### 3.1 책임

사건 node의 채택 시간 범위를 직접 뒷받침하는 Claim을 연결한다. 다른 시간을 주장하는 Claim의 승패나 stance를 이 테이블에 넣지 않는다.

### 3.2 컬럼·키·FK

| 컬럼 | 타입 | NULL | 기본값 |
|---|---|---:|---|
| `event_node_id` | `bigint` | 불가 | 없음 |
| `claim_id` | `bigint` | 불가 | 없음 |

- PK `pk_event_temporal_basis (event_node_id, claim_id)`
- FK `event_node_id → event_temporal_extent.event_node_id`
- FK `claim_id → claim.claim_id`
- 역방향 인덱스:

```sql
CREATE INDEX ix_event_temporal_basis__claim
ON event_temporal_basis (claim_id, event_node_id);
```

### 3.3 서비스 검증

- Claim이 주장하는 시간과 채택 범위가 실제로 대응하는지 확인한다.
- Claim에는 `claim_observation`이 최소 한 건 있어야 한다.
- 사건 시간 Claim은 이 연결만 의미 대상으로 가져도 유효하다.
- 채택 범위와 다른 주장은 conflict 후보로 관리하고 이 테이블에 `DISPUTE` 컬럼을 추가하지 않는다.

## 4. `claim_observation`

### 4.1 책임

Claim을 정확한 원문 위치와 다대다로 연결한다. 같은 observation은 한 문장에서 분리된 여러 원자적 Claim을 뒷받침할 수 있고, Claim 하나는 여러 문서 근거를 가질 수 있다.

### 4.2 컬럼·키·FK

| 컬럼 | 타입 | NULL | 기본값 |
|---|---|---:|---|
| `claim_id` | `bigint` | 불가 | 없음 |
| `observation_id` | `bigint` | 불가 | 없음 |

- PK `pk_claim_observation (claim_id, observation_id)`
- FK `claim_id → claim.claim_id`
- FK `observation_id → observation.observation_id`
- 역방향 인덱스:

```sql
CREATE INDEX ix_claim_observation__observation
ON claim_observation (observation_id, claim_id);
```

### 4.3 Evidence Trace

```text
relation
← claim_relation
← claim
→ claim_observation
→ observation
→ source_document
→ evidence_group
```

```text
node
← claim_attribute_value
← claim
→ claim_observation
→ observation
→ source_document
```

```text
event_temporal_extent
← event_temporal_basis
← claim
→ claim_observation
→ observation
→ source_document
```

DB FK는 경로의 존재를 보장하고, 서비스는 승격 커밋 전에 Claim마다 observation이 최소 하나인지 검사한다.

## 5. 승격 트랜잭션 완결성

새 기준 지식을 쓰는 트랜잭션은 다음 순서로 검증한다.

1. `promotion_batch`가 승격 가능한 상태인지 확인한다.
2. 사용할 node type·relation revision·attribute revision을 잠그거나 활성 상태를 다시 읽는다.
3. `knowledge_item`과 정확한 subtype을 만든다.
4. Claim·relation·구조화 값·event basis·observation 연결을 만든다.
5. 각 Claim에 의미 연결과 observation이 있는지 확인한다.
6. 각 relation에 observation이 있는 `SUPPORT` Claim이 있는지 확인한다.
7. 새 node의 최소 식별 근거를 확인한다.
8. EVENT node에는 temporal basis와 참여자 또는 대상 relation이 있는지 확인한다.
9. 하나라도 실패하면 전체 기준 지식 쓰기를 롤백한다.

관계 없는 일반 node도 대표 alias·근거 Claim 등 최소 근거와 이후 공개 파생 결과가 완전하면 공개할 수 있다. 가짜 relation을 만들지 않는다.

## 6. 접근 경로와 인덱스 중복 검토

- `claim_relation`과 `claim_observation`은 복합 PK가 Claim→대상 방향을 지원하고 별도 역방향 인덱스가 대상→Claim을 지원한다.
- `event_temporal_basis`도 같은 원칙을 사용한다.
- `claim_attribute_value`는 독립 엔터티 ID가 있어 PK 외에 Claim·target·attribute 방향 인덱스가 필요하다.
- #48에서 FK 인덱스와 실제 조회용 인덱스가 같은 선두 컬럼을 가질 때 중복을 제거한다.

## 7. 선택과 되돌리기

### tagged union을 분리 테이블로 바꾸는 대안

값 종류별 테이블은 각 행의 nullable 컬럼을 줄이지만 Claim 속성값 전체 조회와 공통 FK가 복잡해진다. 값 종류가 크게 늘거나 종류별 수명주기가 달라질 때만 별도 Issue에서 분리한다.

### generic meaning target

새 의미 대상 종류가 반복적으로 추가되어 typed 연결이 감당하기 어려워질 때 검토할 수 있다. 현재 세 종류에는 typed FK가 더 강한 무결성과 명확한 조인을 제공한다.

### DB trigger

최소 의미 연결과 observation 개수를 DB가 단독으로 보장해야 한다면 deferred constraint trigger를 추가할 수 있다. 전환 시 서비스의 같은 판정 로직을 제거하고 DB 오류 변환만 남긴다.
