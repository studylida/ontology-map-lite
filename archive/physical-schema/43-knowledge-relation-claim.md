# #43 공통 지식·Relation·Claim의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #43
- 선행 매핑: #41, #42
- 범위: `knowledge_item`, `knowledge_state_event` 보류 계약, `relation`, `claim`, `claim_relation`
- 별도 문서: 구조화 값과 Evidence Trace 연결

## 1. 공통 결정

- `knowledge_item`이 node·relation·claim의 공통 ID와 현재 상태·생성 batch를 소유한다.
- `node`, `relation`, `claim`은 `knowledge_item_id`를 그대로 공유 기본 키로 사용한다.
- 정확히 한 subtype, Claim의 최소 의미 연결, 관계의 최소 지지 근거처럼 다른 행을 세어야 하는 조건은 짧은 승격 서비스 트랜잭션이 검사한다.
- 같은 조건을 DB custom trigger로 중복 구현하지 않는다.
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.
- 기준 지식과 Evidence Trace는 물리 삭제하지 않는다.

## 2. `knowledge_item`

### 2.1 책임

노드·관계·Claim에 공통인 내부 식별자, 현재 검증 상태, 생성 batch와 DB 생성 시점을 관리한다.

### 2.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `knowledge_item_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `item_kind` | `text` | 불가 | 없음 | 불변 |
| `current_state` | `text` | 불가 | 없음 | 허용 전이만 수정 |
| `promotion_batch_id` | `bigint` | 불가 | 없음 | 불변 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

### 2.3 키와 CHECK

- PK `pk_knowledge_item (knowledge_item_id)`
- 후속 FK `fk_knowledge_item__promotion_batch`
  - #46에서 `promotion_batch`가 정의된 뒤 post-creation constraint 단계에 추가
- `item_kind IN ('NODE', 'RELATION', 'CLAIM')`
- `current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED', 'ON_HOLD', 'REJECTED')`
- `isfinite(created_at)`
- 상태나 item kind에 편의 기본값을 두지 않는다. 승격 서비스가 의도를 명시한다.

### 2.4 인덱스

- `ix_knowledge_item__promotion_batch (promotion_batch_id, knowledge_item_id)`
- 상태 단독 인덱스는 만들지 않는다. 공개 조회의 실제 조건은 READY batch·열린 lint·subtype 조인을 함께 사용하므로 #46·#48에서 복합 조회 계획을 검토한다.

### 2.5 shared subtype

```text
knowledge_item.knowledge_item_id
= node.node_id
= relation.relation_id
= claim.claim_id
```

- `knowledge_item`만 identity를 가진다.
- subtype은 상위 ID를 전달받아 같은 승격 트랜잭션에서 생성한다.
- `item_kind`와 정확히 한 subtype의 존재 일치는 커밋 전 서비스가 검사한다.
- #42에서 먼저 정의한 `node.node_id` FK는 migration 후속 제약 단계에 추가한다.

### 2.6 주석

```sql
COMMENT ON TABLE knowledge_item IS
'node·relation·claim의 공유 ID, 현재 지식 상태와 생성 batch를 관리하는 상위 엔터티. 정확히 한 subtype은 승격 서비스가 커밋 전에 검증한다.';

COMMENT ON COLUMN knowledge_item.current_state IS
'EVIDENCE_VERIFIED는 출처와 구조 검사를 통과했다는 뜻이며 객관적 사실 확정이나 사람 승인을 뜻하지 않는다.';
```

## 3. `knowledge_state_event` — 비차단 보류

### 3.1 이유

사람 상태 변경에는 검증 가능한 actor FK가 필수지만 현재 POC에는 user·actor·인증 테이블이 없다. 자유 문자열 actor나 존재하지 않는 FK를 넣지 않는다.

### 3.2 초기 migration

- `knowledge_state_event`는 초기 migration에서 제외할 수 있다.
- 초기 승격은 `EVIDENCE_VERIFIED`를 `knowledge_item.current_state`에 직접 명시하고 사람 이벤트를 만들지 않는다.
- `HUMAN_VERIFIED`, `ON_HOLD`, `REJECTED` 관리자 기능은 actor 계약이 생길 때 활성화한다.

### 3.3 후속 물리 계약

actor 모델이 확정되면 다음 필드를 가진 append-only 테이블을 만든다.

| 컬럼 | 예정 타입·규칙 |
|---|---|
| `knowledge_state_event_id` | `bigint GENERATED ALWAYS AS IDENTITY` PK |
| `knowledge_item_id` | `bigint NOT NULL` FK |
| `from_state`, `to_state` | 허용 상태 `text NOT NULL` |
| `reason` | nonblank `text NOT NULL` |
| `actor_id` | 확정된 actor PK와 같은 타입의 필수 FK |
| `changed_at` | finite `timestamptz NOT NULL` |

서비스는 item row를 잠그고 현재 상태와 `from_state`가 같은지 확인한 뒤 이벤트 insert와 `current_state` update를 한 트랜잭션에서 처리한다.

허용 전이:

```text
EVIDENCE_VERIFIED → HUMAN_VERIFIED | ON_HOLD | REJECTED
HUMAN_VERIFIED    → ON_HOLD | REJECTED
ON_HOLD           → EVIDENCE_VERIFIED | HUMAN_VERIFIED | REJECTED
REJECTED          → 종료
```

## 4. `relation`

### 4.1 책임

정확한 relation revision으로 두 node를 연결하는 기준 연결이다. 출처·시간·사건 맥락을 직접 저장하지 않으며 Claim이 의미와 Evidence Trace를 제공한다.

### 4.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `relation_id` | `bigint` | 불가 | 없음 | shared PK, 불변 |
| `source_node_id` | `bigint` | 불가 | 없음 | 불변 |
| `target_node_id` | `bigint` | 불가 | 없음 | 불변 |
| `relation_type_revision_id` | `bigint` | 불가 | 없음 | 불변 |
| `relation_identity_key` | `bytea` | 불가 | 없음 | 불변 |

### 4.3 PK·FK·UNIQUE·CHECK

- PK `pk_relation (relation_id)`
- FK `relation_id → knowledge_item.knowledge_item_id`
- FK `source_node_id`, `target_node_id → node.node_id`
- FK `relation_type_revision_id → relation_type_revision`
- UNIQUE `uq_relation__identity (relation_identity_key)`
- `octet_length(relation_identity_key) = 32`

모든 endpoint는 명시적 node다. `event_context_node_id`, relation 유효 기간과 hidden context 컬럼을 추가하지 않는다.

### 4.4 결정적 identity 입력

서비스는 exact revision의 directionality를 확인한 뒤 endpoint를 정규화한다.

```text
DIRECTED
→ canonical_source = source_node_id
→ canonical_target = target_node_id

SYMMETRIC
→ canonical_source = min(source_node_id, target_node_id)
→ canonical_target = max(source_node_id, target_node_id)
```

SHA-256 입력은 문자열 이어붙이기가 아니라 다음 고정 바이너리 구조다.

```text
ASCII "REL1"
+ canonical_source의 signed int64 big-endian 8 bytes
+ relation_type_revision_id의 signed int64 big-endian 8 bytes
+ canonical_target의 signed int64 big-endian 8 bytes
```

DB insert에는 정규화한 endpoint와 계산한 32바이트 hash를 함께 전달한다. 같은 key가 이미 있으면 기존 relation을 읽고 endpoint·revision도 다시 비교한 뒤 재사용한다.

### 4.5 서비스 검증

- exact revision이 활성 상태인지 확인한다.
- endpoint node type 쌍이 `relation_endpoint_rule`에 존재하는지 확인한다.
- `SYMMETRIC` endpoint는 node ID 오름차순으로 저장한다.
- 관계 생성과 같은 트랜잭션 안에 observation이 연결된 `SUPPORT` Claim을 최소 하나 만든다.
- 반박 Claim만 있는 relation은 승격하지 않는다.
- 사건 경로에서 비사건 node 사이 직접 relation을 추론하지 않는다.

### 4.6 인덱스

- `ix_relation__source (source_node_id, relation_type_revision_id, target_node_id)`
- `ix_relation__target (target_node_id, relation_type_revision_id, source_node_id)`
- identity lookup은 UNIQUE 인덱스가 지원한다.

### 4.7 주석

```sql
COMMENT ON TABLE relation IS
'정확한 relation revision으로 두 node를 연결하는 기준 연결. 출처·사건 맥락·유효 기간은 직접 저장하지 않고 Claim과 명시적 사건 endpoint로 표현한다.';
```

## 5. `claim`

### 5.1 책임

출처가 주장한 하나의 원자적 판단 단위를 원문 언어로 보존하고 표현 성격과 주장 시간 정밀도를 관리한다.

### 5.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `claim_id` | `bigint` | 불가 | 없음 | shared PK, 불변 |
| `statement_text` | `text` | 불가 | 없음 | 불변 |
| `language` | `text` | 불가 | 없음 | 불변 |
| `modality` | `text` | 불가 | 없음 | 불변 |
| `asserted_from` | `timestamptz` | 허용 | 없음 | 불변 |
| `asserted_to` | `timestamptz` | 허용 | 없음 | 불변 |
| `asserted_from_precision` | `text` | 불가 | 없음 | 불변 |
| `asserted_to_precision` | `text` | 불가 | 없음 | 불변 |

### 5.3 PK·FK·CHECK

- PK `pk_claim (claim_id)`
- FK `claim_id → knowledge_item.knowledge_item_id`
- `statement_text`, `language`은 nonblank
- `modality IN ('FACT', 'PLAN_OR_TARGET', 'PREDICTION_OR_ESTIMATE', 'OPINION_OR_EVALUATION')`
- 두 precision은 `INSTANT`, `DAY`, `MONTH`, `YEAR`, `UNKNOWN` 중 하나
- 각 경계는 값 `NULL`과 `UNKNOWN` precision이 일치해야 한다.
- 값이 있는 timestamp는 finite다.

서로 다른 precision의 순서 검사는 anchor 단순 비교로 처리하지 않고 서비스가 의미 범위를 계산한다.

### 5.4 서비스 원자성

- 독립 판단 가능한 사실은 별도 Claim으로 나눈다.
- 분리하면 의미가 손실되는 경우에만 여러 의미 연결을 허용한다.
- 승격 커밋 전 다음이 필요하다.
  - `claim_observation` 최소 한 행
  - `claim_relation`, `claim_attribute_value`, `event_temporal_basis` 중 최소 한 행
- Agent 응답 JSON과 후보 payload는 저장하지 않는다.

### 5.5 주석

```sql
COMMENT ON COLUMN claim.modality IS
'원문 표현이 사실 주장, 계획·목표, 예측·추정, 의견·평가 중 무엇인지 나타낸다. PLAN_OR_TARGET 값을 확정 사실로 표시해서는 안 된다.';
```

## 6. `claim_relation`

### 6.1 컬럼과 키

| 컬럼 | 타입 | NULL | 기본값 |
|---|---|---:|---|
| `claim_id` | `bigint` | 불가 | 없음 |
| `relation_id` | `bigint` | 불가 | 없음 |
| `stance` | `text` | 불가 | 없음 |

- PK `pk_claim_relation (claim_id, relation_id)`
- FK `claim_id → claim`, `relation_id → relation`
- `stance IN ('SUPPORT', 'DISPUTE')`
- 별도 대리 ID를 두지 않는다.

### 6.2 인덱스

```sql
CREATE INDEX ix_claim_relation__relation
ON claim_relation (relation_id, stance, claim_id);
```

- Claim의 관계 목록은 PK가 지원한다.
- 관계별 지지·반박 Claim과 독립 근거 수 계산은 역방향 인덱스가 지원한다.

### 6.3 의미

`DISPUTE` 근거 수를 관계 굵기에서 빼지 않는다. 굵기는 `SUPPORT` Claim에서 도달하는 독립 `evidence_group_id` 수이며, 비교 가능한 엇갈림은 conflict 구조와 호박색 점선으로 별도 표현한다.

## 7. 수명주기

| 테이블 | 분류 | 수정 가능 컬럼 |
|---|---|---|
| `knowledge_item` | 현재 상태를 가진 기준 식별자 | `current_state`만 허용 전이로 수정 |
| `relation` | 불변 기준 의미 | 없음 |
| `claim` | 불변 원자 Claim | 없음 |
| `claim_relation` | 불변 의미 연결 | 없음 |
| `knowledge_state_event` | 후속 append-only 이력 | 기존 행 수정 없음 |

## 8. 선택과 되돌리기

### shared PK

독립 subtype ID가 필요해지면 새 ID를 단순 추가하는 것이 아니라 모든 공통 상태·lint·conflict·검색 basis·publication FK를 함께 migration해야 한다.

### relation identity hash

hash를 제거하려면 정규화 endpoint와 exact revision의 복합 UNIQUE를 authoritative identity로 바꾸고, 캐시·upsert가 hash를 참조하는지 확인한다. 기존 hash는 검증 기간 뒤 제거한다.

### 사람 상태 이벤트

관리자 기능을 구현할 때 실제 actor table과 PK를 먼저 승인하고 이벤트 table을 추가한다. 자유 문자열 actor를 임시 호환층으로 사용하지 않는다.
