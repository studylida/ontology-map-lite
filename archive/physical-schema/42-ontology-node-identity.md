# #42 온톨로지와 노드 정체성의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #42
- 선행 결정: #40, #41, #69
- 공통 규칙: [`physical-data-schema.md`](../../docs/data/physical-schema.md)
- 현재 커밋 범위: 온톨로지 코드·revision, node·alias·외부 식별자

## 1. 공통 결정

- `ontology_version`, `ontology_member`는 만들지 않는다.
- `node_type`, `relation_type`, `attribute`는 확장 가능한 코드 테이블이다.
- 닫힌 저장 구조인 방향성·값 종류만 `text + CHECK`로 제한한다.
- relation·attribute revision은 의미 필드가 바뀌면 새 행을 만들며, 참조된 행을 수정하지 않는다.
- `is_active`는 새 지식 생성 허용 여부만 뜻한다. 비활성화된 유형·revision을 참조하는 기존 지식은 계속 유효하고 조회 가능하다.
- 다른 행을 읽어야 하는 활성화·역관계·endpoint 전체 검증은 서비스 트랜잭션이 한 곳에서 책임진다. 같은 검사를 DB custom trigger로 복제하지 않는다.
- 모든 FK는 별도 언급이 없으면 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.

## 2. `node_type`

### 2.1 책임

사람·회사·기술·주제·사건처럼 노드가 속할 수 있는 안정된 유형과 생성 근거 규칙을 관리한다. 별도 revision 테이블은 만들지 않는다.

### 2.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `node_type_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `node_type_code` | `text` | 불가 | 없음 | 사용 후 불변 |
| `display_name` | `text` | 불가 | 없음 | 통제된 표시 수정 가능 |
| `creation_rule` | `text` | 불가 | 없음 | 사용 후 불변 |
| `is_active` | `boolean` | 불가 | `false` | 새 노드 생성 허용 전환 |

### 2.3 제약과 인덱스

- PK `pk_node_type (node_type_id)`
- UNIQUE `uq_node_type__code (node_type_code)`
- `node_type_code`, `display_name`, `creation_rule`은 nonblank
- 초기 seed는 `PERSON`, `COMPANY`, `TECHNOLOGY`, `TOPIC`, `EVENT`지만 코드 추가를 막는 `CHECK IN`은 두지 않는다.
- 코드 테이블 크기가 작으므로 `is_active` 단독 인덱스는 만들지 않는다.

### 2.4 서비스 규칙

- `is_active = true`인 유형만 새 node에 사용할 수 있다.
- 한 번이라도 node가 참조한 `node_type_code`, `creation_rule`은 수정하지 않는다.
- 의미가 달라지면 새 `node_type` code를 만든다.
- 비활성화는 기존 node의 상태나 공개 여부를 바꾸지 않는다.

### 2.5 주석

```sql
COMMENT ON TABLE node_type IS
'노드의 안정된 유형 코드와 생성 근거 규칙. is_active는 새 노드 생성 허용 여부이며 기존 노드의 공개 상태가 아니다.';
```

## 3. 관계 유형

### 3.1 `relation_type`

#### 책임

관계 의미를 식별하는 불변 `relation_code`를 관리한다. 표시 이름·방향·endpoint 규칙은 revision이 소유한다.

#### 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `relation_type_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `relation_code` | `text` | 불가 | 없음 | 사용 후 불변 |

#### 제약

- PK `pk_relation_type (relation_type_id)`
- UNIQUE `uq_relation_type__code (relation_code)`
- `btrim(relation_code) <> ''`
- 활성 플래그를 두지 않는다. 활성 revision이 없으면 새 관계 생성에 사용할 수 없다.

### 3.2 `relation_type_revision`

#### 책임

같은 관계 의미에 적용하는 표시 이름, 방향성과 inverse 계약의 불변 버전이다.

#### 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `relation_type_revision_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `relation_type_id` | `bigint` | 불가 | 없음 | 불변 |
| `version_no` | `integer` | 불가 | 없음 | 불변 |
| `display_name` | `text` | 불가 | 없음 | 사용 후 불변 |
| `directionality` | `text` | 불가 | 없음 | 사용 후 불변 |
| `inverse_relation_type_revision_id` | `bigint` | 허용 | 없음 | 사용 후 불변 |
| `is_active` | `boolean` | 불가 | `false` | 새 관계 생성 허용 전환 |

#### 키와 제약

- PK `pk_relation_type_revision (relation_type_revision_id)`
- FK `fk_relation_type_revision__relation_type`
- self FK `fk_relation_type_revision__inverse`
- UNIQUE `uq_relation_type_revision__version (relation_type_id, version_no)`
- partial UNIQUE index:

```sql
CREATE UNIQUE INDEX uq_relation_type_revision__active
ON relation_type_revision (relation_type_id)
WHERE is_active;
```

- `version_no >= 1`
- `btrim(display_name) <> ''`
- `directionality IN ('DIRECTED', 'SYMMETRIC')`
- `inverse_relation_type_revision_id IS NULL OR inverse_relation_type_revision_id <> relation_type_revision_id`
- `directionality = 'SYMMETRIC'`이면 inverse는 반드시 `NULL`

#### 인덱스

- `ix_relation_type_revision__inverse (inverse_relation_type_revision_id)` WHERE inverse가 값 있음
- 관계 코드의 revision 조회는 `uq_relation_type_revision__version`이 지원한다.

#### 서비스 규칙

활성화 트랜잭션은 다음을 원자적으로 수행한다.

1. revision의 endpoint 규칙 전체를 검증한다.
2. inverse가 있으면 대상도 `DIRECTED`인지 확인한다.
3. `A.inverse = B`이면 `B.inverse = A`인지 확인한다.
4. 같은 `relation_type_id`의 기존 활성 revision을 비활성화한다.
5. 새 revision을 활성화한다.

승격 트랜잭션은 저장 직전에 exact revision이 여전히 활성인지 다시 읽는다.

### 3.3 `relation_endpoint_rule`

#### 책임

관계 revision이 허용하는 시작·도착 node type 쌍을 저장한다.

#### 컬럼과 키

| 컬럼 | 타입 | NULL | 기본값 |
|---|---|---:|---|
| `relation_type_revision_id` | `bigint` | 불가 | 없음 |
| `source_node_type_id` | `bigint` | 불가 | 없음 |
| `target_node_type_id` | `bigint` | 불가 | 없음 |

- PK `pk_relation_endpoint_rule (relation_type_revision_id, source_node_type_id, target_node_type_id)`
- 세 컬럼은 각각 revision과 `node_type`을 참조하는 FK다.
- 별도 대리 ID는 두지 않는다.

#### 방향별 저장 규칙

- `DIRECTED`: 허용 방향 그대로 저장한다.
- `SYMMETRIC`: 두 유형 ID를 오름차순으로 정규화하고 `source_node_type_id <= target_node_type_id` CHECK를 적용한다.
- 반대 방향 행을 함께 만들지 않는다.

revision과 두 endpoint type을 모두 알고 검증하므로 복합 PK가 승인 검사를 지원한다. node type만으로 관계 revision을 역탐색하는 조회가 실제 API에서 필요해질 때만 후속 인덱스를 추가한다.

### 3.4 관계 유형 주석

```sql
COMMENT ON TABLE relation_type IS
'관계 의미를 식별하는 안정된 코드 테이블. 활성 상태와 방향·endpoint 규칙은 revision이 소유한다.';

COMMENT ON COLUMN relation_type_revision.is_active IS
'새 관계를 생성할 때 사용할 수 있는 exact revision인지 표시한다. 기존 관계를 숨기거나 재해석하지 않는다.';

COMMENT ON TABLE relation_endpoint_rule IS
'관계 revision이 허용하는 시작·도착 node type 쌍. 실제 관계 endpoint 검증은 승격 서비스가 수행한다.';
```

## 4. 속성 정의

### 4.1 `attribute`

#### 책임

구조화된 노드 속성의 안정된 code를 관리한다. 실제 값이나 대상 노드는 저장하지 않는다.

#### 컬럼과 제약

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `attribute_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `attribute_code` | `text` | 불가 | 없음 | 사용 후 불변 |

- PK `pk_attribute (attribute_id)`
- UNIQUE `uq_attribute__code (attribute_code)`
- `btrim(attribute_code) <> ''`
- 활성 플래그를 두지 않는다. 활성 revision이 없으면 새 속성값에 사용할 수 없다.

### 4.2 `attribute_revision`

#### 책임

속성의 표시 이름, 단일 대상 node type, 허용 값 종류와 NUMBER의 canonical 단위를 보존하는 불변 규칙 버전이다.

#### 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `attribute_revision_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `attribute_id` | `bigint` | 불가 | 없음 | 불변 |
| `version_no` | `integer` | 불가 | 없음 | 불변 |
| `display_name` | `text` | 불가 | 없음 | 사용 후 불변 |
| `target_node_type_id` | `bigint` | 불가 | 없음 | 사용 후 불변 |
| `allowed_value_kind` | `text` | 불가 | 없음 | 사용 후 불변 |
| `unit_rule` | `text` | 허용 | 없음 | 사용 후 불변 |
| `is_active` | `boolean` | 불가 | `false` | 새 속성값 생성 허용 전환 |

#### 키와 제약

- PK `pk_attribute_revision (attribute_revision_id)`
- FK `attribute_id → attribute`
- FK `target_node_type_id → node_type`
- UNIQUE `uq_attribute_revision__version (attribute_id, version_no)`
- UNIQUE `uq_attribute_revision__kind (attribute_revision_id, allowed_value_kind)`
  - #43의 `(attribute_revision_id, value_kind)` 복합 FK 대상
- partial UNIQUE index:

```sql
CREATE UNIQUE INDEX uq_attribute_revision__active
ON attribute_revision (attribute_id)
WHERE is_active;
```

- `version_no >= 1`
- `btrim(display_name) <> ''`
- `allowed_value_kind IN ('STRING', 'NUMBER', 'DATE', 'PERIOD', 'BOOLEAN')`
- `allowed_value_kind = 'NUMBER'`일 때만 nonblank `unit_rule`이 필수다.
- 다른 값 종류에서는 `unit_rule IS NULL`이어야 한다.

`unit_rule`은 POC에서 한 revision이 허용하는 canonical 단위 code 하나다. 자동 환산식이나 단위 목록 JSON이 아니다.

#### 인덱스

- `ix_attribute_revision__target_type (target_node_type_id, attribute_revision_id)`
  - 한 node type에 사용 가능한 속성 revision 조회
- revision 버전과 현재 활성 조회는 고유 인덱스가 지원한다.

#### 서비스 규칙

- 새 revision 활성화 전에 target node type, 값 종류와 단위 계약을 함께 검증한다.
- 같은 attribute의 기존 활성 revision 비활성화와 새 revision 활성화를 한 트랜잭션에서 처리한다.
- 승격 시 target node의 실제 type, exact revision의 `allowed_value_kind`, `is_active`를 다시 검증한다.
- 여러 대상 유형이 실제 요구가 되기 전에는 `attribute_target_rule`을 추가하지 않는다.

### 4.3 속성 주석

```sql
COMMENT ON TABLE attribute IS
'구조화된 Claim 속성의 안정된 코드 테이블. 실제 값과 사용 규칙은 attribute_revision과 claim_attribute_value가 소유한다.';

COMMENT ON COLUMN attribute_revision.unit_rule IS
'NUMBER revision이 허용하는 canonical 단위 code 하나. 자동 단위 환산 규칙이나 표시 문자열 목록이 아니다.';
```

## 5. `node`

### 5.1 책임

사람·회사·기술·주제·사건의 불변 정체성과 안정된 node type만 저장한다. 이름과 세부 사실은 각각 alias와 Claim이 소유한다.

### 5.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `node_id` | `bigint` | 불가 | 없음 | 불변 |
| `node_type_id` | `bigint` | 불가 | 없음 | 불변 |

### 5.3 키·FK·인덱스

- PK `pk_node (node_id)`
- FK `fk_node__node_type (node_type_id → node_type.node_type_id)`
- `node_id`에는 identity를 두지 않는다.
- #43에서 `node.node_id → knowledge_item.knowledge_item_id` 공유 기본 키 FK를 후속 제약 단계에 추가한다.
- `ix_node__type (node_type_id, node_id)`는 유형별 노드 조회를 지원한다.

### 5.4 서비스 규칙

- 새 node를 만들 때 `node_type.is_active = true`를 확인한다.
- 생성 후 `node_type_id`를 바꾸지 않는다. 다른 유형으로 판정해야 하면 기존 지식을 덮어쓰지 않고 정정 요구를 별도 처리한다.
- 공개 가능 여부와 상태는 `knowledge_item`이 소유한다.

### 5.5 주석

```sql
COMMENT ON TABLE node IS
'지식그래프 대상의 불변 정체성과 안정된 node type만 저장하는 knowledge_item subtype. 이름과 세부 사실을 직접 저장하지 않는다.';
```

## 6. `node_alias`

### 6.1 책임

대표 이름과 검색 가능한 모든 별칭을 불변 node ID에 연결한다. 이름 사용 기간과 종류는 저장하지 않는다.

### 6.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `node_alias_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `node_id` | `bigint` | 불가 | 없음 | 불변 |
| `alias_text` | `text` | 불가 | 없음 | 불변 |
| `language` | `text` | 불가 | 없음 | 불변 |
| `is_preferred` | `boolean` | 불가 | `false` | 대표 alias 전환 시 수정 가능 |

### 6.3 제약

- PK `pk_node_alias (node_alias_id)`
- FK `fk_node_alias__node`
- UNIQUE `uq_node_alias__value (node_id, alias_text, language)`
- `alias_text`, `language`은 nonblank
- 노드당 대표 alias 최대 하나:

```sql
CREATE UNIQUE INDEX uq_node_alias__preferred
ON node_alias (node_id)
WHERE is_preferred;
```

DB는 최대 한 건만 보장한다. 공개 전 서비스는 대표 alias가 **정확히 한 건**인지 확인한다.

### 6.4 인덱스와 조회

- `ix_node_alias__text (alias_text, node_id)`
  - 정확 alias 조회와 후보 node 식별
- 대소문자·한국어 형태소·오타 검색은 #47의 검색 품질 결정이다. `lower(alias_text)`나 `pg_trgm` 인덱스를 미리 추가하지 않는다.

검색 순서는 다음과 같다.

```text
모든 alias에서 입력 일치
→ node_id 획득
→ 활성 node_merge를 따라 최종 canonical node 조회
→ canonical node의 is_preferred alias 한 건을 화면에 표시
```

처음부터 `is_preferred`만 검색하지 않는다. 그러면 과거 이름과 번역 alias를 찾을 수 없기 때문이다.

### 6.5 주석

```sql
COMMENT ON COLUMN node_alias.is_preferred IS
'현재 화면 대표 이름으로 선택된 alias인지 표시한다. 유일한 공식 명칭이나 유일한 검색 이름이라는 뜻이 아니다.';
```

## 7. `node_alias_evidence`

### 7.1 책임과 컬럼

별칭이 어느 원문 위치에서 확인되었는지 alias와 observation을 다대다로 연결한다.

| 컬럼 | 타입 | NULL | 기본값 |
|---|---|---:|---|
| `node_alias_id` | `bigint` | 불가 | 없음 |
| `observation_id` | `bigint` | 불가 | 없음 |

### 7.2 키·FK·인덱스

- PK `pk_node_alias_evidence (node_alias_id, observation_id)`
- FK `node_alias_id → node_alias`
- FK `observation_id → observation`
- 역방향 조회용 `ix_node_alias_evidence__observation (observation_id, node_alias_id)`

alias 하나는 여러 observation을 가질 수 있고, observation 하나가 여러 alias를 뒷받침할 수 있다.

## 8. `external_identifier`

### 8.1 책임

신뢰된 자료 준비 레이어가 제공한 외부 식별 체계와 값을 node에 연결한다. 일반 기사 문장에서 Agent가 추출한 문자열은 검증 없이 이 테이블에 넣지 않는다.

### 8.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `external_identifier_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `node_id` | `bigint` | 불가 | 없음 | 불변 |
| `identifier_system` | `text` | 불가 | 없음 | 불변 |
| `identifier_value` | `text` | 불가 | 없음 | 불변 |

### 8.3 키와 제약

- PK `pk_external_identifier (external_identifier_id)`
- FK `fk_external_identifier__node`
- UNIQUE `uq_external_identifier__business (identifier_system, identifier_value)`
- `identifier_system`, `identifier_value`는 nonblank
- POC의 허용 체계:

```text
identifier_system IN ('KRX', 'WIKIDATA', 'ORCID', 'LEI')
```

`identifier_value`는 text로 저장해 `KRX 000660` 같은 선행 0을 보존한다. 시스템이 다르면 같은 value를 사용할 수 있다.

### 8.4 서비스 규칙

- `identifier_system`은 대문자 canonical code로 정규화한다.
- value 형식은 KRX·Wikidata·ORCID·LEI별 검증 코드가 확인한다.
- `ALIAS`, 자유 회사명, 기사 URL과 문서 내부 번호를 외부 식별자로 허용하지 않는다.
- 노드 병합 전 동일 `(system, value)`가 다른 node에 있으면 자동으로 새 node를 만들지 않고 동일 대상 검토로 보낸다.

### 8.5 인덱스와 주석

- `ix_external_identifier__node (node_id, external_identifier_id)`
- 업무 UNIQUE가 system+value 조회를 지원한다.

```sql
COMMENT ON TABLE external_identifier IS
'신뢰된 자료 준비 단계가 제공한 외부 식별 체계와 값. 일반 Agent 본문 추출이나 Claim·observation Evidence Trace를 저장하는 곳이 아니다.';
```

## 9. 현재 단계의 되돌리기 지점

- node 프로필 전용 컬럼이 실제로 필요해지면 Claim과 중복되는지 먼저 검토한 뒤 새 Issue로 추가한다.
- alias 사용 기간이 제품 요구가 되면 기존 alias 행을 수정하지 않고 기간 Claim 또는 별도 alias revision 구조 중 하나를 선택한다.
- 외부 식별 체계가 자주 추가되면 `external_identifier_system` 코드 테이블을 만들고 `identifier_system` CHECK를 FK로 전환한다. 기존 문자열을 backfill한 뒤 CHECK를 제거한다.
- 전체 활성 규칙 snapshot이 필요하면 #69의 manifest 구조를 전부 복원한다.
- DB가 교차 행 규칙을 단독으로 방어해야 한다는 운영 요구가 생기면 relation revision 활성화용 좁은 deferred constraint trigger를 별도 Issue로 추가한다. 서비스와 같은 검사를 중복 유지하지 않는다.
