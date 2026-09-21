# ontology-map PostgreSQL 물리 데이터 스키마

## 문서 상태

- 상태: Physical Schema v2.2 — 동결 및 migration 구현 완료
- 확인일: 2026-09-20
- 관련 Issue: [#40 Define PostgreSQL physical schema conventions](https://github.com/studylida/ontology-map/issues/40), [#200 NUMBER attribute 복수 허용 단위 지원](https://github.com/studylida/ontology-map/issues/200), [#203 Topic reference lifecycle 지원](https://github.com/studylida/ontology-map/issues/203), [#216 promotion canonical change provenance](https://github.com/studylida/ontology-map/issues/216), [#247 canonical Claim 검토와 promotion 간소화](https://github.com/studylida/ontology-map/issues/247), 사용자 업로드 source intake
- 논리 모델: [논리 스키마](logical-schema.md)
- 생성 목록: [스키마 참고 문서](schema-reference.md)
- 구현 스택: [구현 스택](../development/implementation-stack.md)
- 코드·migration 규칙: [코드 규칙](../development/code-conventions.md)
- 구현: #95, `server/migrations/versions/0001_create_frozen_schema.py`; #200, `server/migrations/versions/0003_support_multiple_number_attribute_units.py`; #203, `server/migrations/versions/0004_support_topic_reference_lifecycle.py`; #216, `server/migrations/versions/0005_add_promotion_canonical_change.py`

이 문서는 Logical Schema v1.3의 의미를 PostgreSQL로 옮기는 공통 표현 규칙을 정의한다. 실제 table, column, constraint와 index 목록은 SQLAlchemy metadata에서 생성한 [스키마 참고 문서](schema-reference.md)가 소유한다.

#41–#48에서 기본 매핑을 확정했고 #91에서 node 인사이트 확장을 추가했다. #95는 이 결과를 SQLAlchemy metadata와 Alembic migration으로 구현했으며, #78의 embedding 계약은 [#121](https://github.com/studylida/ontology-map/issues/121)에서 폐기했다. #200은 NUMBER revision의 단일 `unit_rule`을 명시적인 허용 단위 집합으로 확장한다. migration과 metadata가 이 문서와 다르면 임의로 한쪽을 정답으로 바꾸지 않고 #116에서 의미 차이인지 구현 오류인지 감사한다.

## 현재 구현 기준

- SQLAlchemy complete metadata: `server/src/ontology_map/db/metadata.py` (`schema.py`의 기존 metadata에 #216 provenance table을 등록)
- Alembic revision: `0001_create_frozen_schema.py` → `0002_add_panel_reading_contracts.py` → `0003_support_multiple_number_attribute_units.py` → `0004_support_topic_reference_lifecycle.py` → `0005_add_promotion_canonical_change.py` → `0006_add_provider_call_slot.py` → `0007_add_source_claim_projection.py` → `0008_bundle_followup_questions.py` → `0009_allow_uploads_without_url.py` → `0010_add_source_processing_job.py`
- 개발 fixture: `server/src/ontology_map/db/fixture.py`
- PostgreSQL namespace: `public`
- 현재 metadata에 구현된 table 수와 각 객체의 세부 정의는 [스키마 참고 문서](schema-reference.md)에서 확인한다.
- 논리 모델에는 있지만 actor 계약이 없어 구현을 보류한 table은 `knowledge_state_event`와 `conflict_state_event`다.

Evidence Trace는 새 사본을 만들지 않고 다음 경로를 사용한다.

```text
claim
→ claim_observation
→ observation
→ source_document
→ evidence_group
```

중단된 Source Claim 실험의 기존 행은 다음 호환 경로에 남아 있다.

```text
source_claim
→ source_claim_observation
→ observation
→ source_document
```

현재 extraction runtime은 `source_claim*` table에 새 행을 쓰지 않는다. 기존 행은 자동 변환·삭제하지 않고 읽기 전용 호환 자료로 보존하며, migration `0007` downgrade는 Source Claim이 하나라도 있으면 데이터 손실을 막기 위해 중단한다.

Migration `0008`은 물리 table을 바꾸지 않고 활성 `FOLLOWUP_QUESTIONS` output schema를 90일·1년 묶음 계약으로 교체한다. `PREPARING` publication이나 nonterminal FOLLOWUP task가 있으면 upgrade를 중단한다. 새 schema를 참조하는 task가 하나라도 생긴 뒤에는 downgrade를 중단한다. 이전 schema와 READY 결과는 삭제하거나 변환하지 않는다.

Migration `0009`는 외부 URL이 없는 사용자 업로드를 허용하기 위해 `source_document.canonical_url`을 nullable로 바꾸고 NULL 허용 CHECK를 재생성한다. NULL 문서가 있으면 downgrade를 중단한다.

Migration `0010`은 `source_processing_job`을 추가한다. 상태·단계·안전한 오류 코드와 task·batch 참조의 조합을 CHECK로 제한하고, 문서별 최신 조회 인덱스와 `QUEUED`·`RUNNING`·`READY` 한 건만 허용하는 partial unique index를 둔다. API 런타임은 이 migration을 자동 적용하지 않는다.

Relation의 stance는 `claim_relation`, 구조화 속성값은 `claim_attribute_value`, 사건 시간 근거는 `event_temporal_basis`가 Claim에 연결한다. node 인사이트도 `node_insight_claim`에서 기존 Claim으로 이어져 같은 Evidence Trace를 재사용한다.

### Promotion canonical-change provenance

#216의 `promotion_canonical_change`는 identity PK, NOT NULL `promotion_batch_id`, closed `change_kind`, 그리고 `node_alias_id`, `observation_id`, `claim_id`, `relation_id`, `claim_attribute_value_id`, `event_node_id` 전용 nullable target column만 가진다. JSON payload, `affected_node_id`, workflow/publication 상태 column은 두지 않는다.

`NODE_ALIAS_EVIDENCE_ADDED`, `CLAIM_OBSERVATION_ADDED`, `CLAIM_RELATION_ADDED`, `EVENT_TEMPORAL_BASIS_ADDED`는 각각 원본 association의 composite PK를 composite FK로 직접 참조한다. `NODE_ALIAS_CHANGED`와 `CLAIM_ATTRIBUTE_VALUE_ADDED`는 exact target row를 단일 FK로 참조한다. 하나의 CHECK가 kind별 필요한 target만 non-NULL이고 관계없는 target은 NULL임을 강제하며, 별도 closed-kind CHECK가 여섯 종류 밖의 값을 거부한다.

Idempotency는 nullable 전체 UNIQUE에 의존하지 않고 kind별 partial unique index 여섯 개로 `promotion_batch + change_kind + exact target`을 고정한다. `(promotion_batch_id, promotion_canonical_change_id)` index는 향후 coordinator가 batch 기준으로 provenance를 읽을 최소 access path다.

migration `0005`는 historical association을 backfill하지 않는다. `ontology_map.db.promotion_provenance`의 production write boundary는 caller가 연 promotion transaction 안에서만 동작하고 association insert는 `INSERT ... ON CONFLICT DO NOTHING RETURNING ...` 결과로 실제 mutation 여부를 판별한다. `claim_attribute_value`에는 #216이 새로운 semantic equality나 UNIQUE 규칙을 추가하지 않는다. caller가 canonical insert를 요청하면 `add_claim_attribute_value()`는 새 row를 INSERT하고 반환된 exact `claim_attribute_value_id`에 provenance를 기록하며, 동일 입력의 재처리 억제나 canonical reuse 여부는 #125 task/cache와 #127 canonicalization/write path가 소유한다. merged #128 Entity Resolution의 alias/alias-evidence path는 이 boundary에 직접 연결되어 실제 INSERT winner만 provenance를 남긴다. Claim/Relation/attribute/event canonical service도 같은 transaction-local boundary를 제공하며, 최종 KNOWLEDGE_EXTRACTION orchestration은 별도 #127 책임이다. `mark_promotion_committed`는 canonical writes와 provenance 뒤 같은 caller transaction에서 `COMMITTED`를 확정하지만 commit 자체는 호출하지 않는다. #215 initial publication coordinator와 #180 recovery는 별도 책임으로 남는다.

### Durable provider 호출 슬롯

`provider_call_slot`은 `(model_task_id, slot_no)` 복합 PK와 RESTRICT model_task FK만 사용한다. slot_no 1..3, RESERVED/COMPLETED/UNKNOWN, 유한 시각, 종료 시각의 상태별 NULL 규칙과 reserved_at 이후 조건을 CHECK로 제한한다. 새로운 result/staging payload나 task→promotion FK는 없다. `agent_attempt.outcome`은 기존 terminal 값만 유지하고 attempt_no는 slot_no이므로 gap을 허용한다. `model_task.attempt_count`는 durable terminal agent_attempt 행 수이며 기존 BETWEEN 0 AND 5 CHECK는 유지한다.

예약·reclaim·terminal 기록은 model_task row lock과 현재 lease token·만료 시각 검사로 직렬화한다. application은 clock_timestamp를 lock 획득 뒤 읽고 UNKNOWN 또는 COMPLETED slot의 재사용을 거부한다. 예약·전송 사이에는 장기 DB transaction을 유지하지 않는다. 확정 결과 기록은 attempt append, count 증가, COMPLETED와 가능한 task 상태 변경을 원자적으로 commit한다. migration downgrade는 소비된 slot이 있으면 삭제 전에 중단하여 무료 재호출을 허용하지 않는다.

`publication_affected_node`는 한 batch가 영향을 준 node와 선택한 검색 문서, context와 `NODE_INSIGHT` 작업을 가리킨다. 이 행은 지도 구성원, 좌표나 전체 graph snapshot이 아니다.

### Publication과 공개 조회

`promotion_batch.promotion_status`는 기준 지식의 원자 저장 결과이고 `publication_status`는 파생 결과 준비 상태다. 둘을 하나의 상태로 합치지 않는다.

```text
promotion_status:   PENDING → COMMITTED | FAILED
publication_status: NOT_STARTED → PREPARING → READY
                                          └→ FAILED → PREPARING
```

READY 전환은 같은 transaction에서 모든 영향 node를 검사한다. 새 패널 계약에서는 각 node의 검색 문서·context와 두 기간별 node_question_set 및 성공한 NODE_INSIGHT 작업의 node_insight_window가 필요하다. 질문 묶음은 0개 이상의 답변, 인사이트 window는 0개 또는 1개 보고서를 선택한다. 기존 followup_question과 slot 1–3 인사이트는 호환 데이터로 보존한다. 현재 공개 가능한 basis 지식과 열린 `BLOCKING` lint 부재도 함께 검사한다.

이 완결성은 여러 table의 개수, task kind와 상태를 함께 읽어야 하므로 DB의 nullable column만으로 보장하지 않고 publication application service가 짧은 transaction 안에서 보장한다. `READY` 뒤 선택 pointer와 산출물은 바꾸지 않는다.

일반 사용자 조회는 node별 `ready_at DESC, promotion_batch_id DESC` 순서의 최신 `COMMITTED + READY`를 선택한다. node, Relation, Claim과 파생 결과의 basis 지식이 `EVIDENCE_VERIFIED | HUMAN_VERIFIED` 상태이고 열린 `BLOCKING` lint가 없는지 read-time에서 다시 확인한다. 새 publication이 실패하면 기준 지식과 과거 산출물을 삭제하지 않고 이전 READY를 계속 제공한다. 이전 READY가 전혀 없으면 현재 exploration 계열 API는 `503 PUBLICATION_NOT_READY`를 반환한다.

제품 Reference Topic은 일반 READY를 흉내 내는 예외가 아니라 별도 lifecycle/read contract다. `PRODUCT_REFERENCE` row에는 일반 state/promotion을 넣지 않고 `node_search_document`와 `publication_affected_node`도 금지한다. Topic 중심 조회는 `topic_reference` canonical 이름과 공개 가능한 direct `HAS_TOPIC` Relation을 역조회하며, 그 Relation과 지지 Claim은 계속 `EVIDENCE_BACKED` lifecycle·Evidence Trace·lint·publication basis 검증을 따라야 한다.

관계가 없는 공개 node도 각자 완전한 READY 결과를 가지면 검색과 주변부 조회에 포함할 수 있다. 검색, node 선택, 후속 질문과 주변부 이동은 Relation을 새로 만들지 않는다.

### 검색과 파생 산출물

`node_search_document`는 `identity_text`와 `knowledge_text`를 분리하고 `search_document_basis`가 사용한 공개 `knowledge_item`을 연결한다. migration은 다음 expression GIN을 구현한다.

```sql
setweight(to_tsvector('simple', identity_text), 'A')
|| setweight(to_tsvector('simple', knowledge_text), 'B')
```

사용자 검색은 이 저장 문서에서 exact alias → `identity_text` FTS → `knowledge_text` FTS를 별도 bucket으로 평가한다. exact alias는 `node_id ASC`, 각 FTS bucket은 `ts_rank_cd DESC, node_id ASC`로 정렬하고 canonical Node를 전체 결과에서 한 번만 반환한다. `node_context.context_text`는 검색 입력으로 사용하지 않는다. 이 검색 순위 변경은 table, column, migration이나 위 expression GIN을 변경하지 않는다.

`node_context`, `followup_question`과 `node_insight`는 같은 검색 문서·node 조합을 물리 FK로 고정한다. `followup_question.slot`은 1 또는 2이고 `target_node_id`는 필수지만 target과 중심 사이 Relation을 뜻하지 않는다.

`node_insight`는 `RECENT_90_DAYS | RECENT_1_YEAR`, slot 1–3, `as_of_at`, 제목, 요약, 종합 해석과 유의점을 가진 불변 행이다. `node_insight_claim`은 기존 Claim을 `KEY_CLAIM | SUPPORTING_CLAIM | CONTRASTING_CLAIM`으로 연결한다. 인사이트 근거 수는 column으로 저장하지 않고 Evidence Trace에서 `COUNT(DISTINCT evidence_group_id)`로 계산한다.

지도 좌표, 카메라, node 밝기와 opacity, 화면 표시 단계, 전역 graph/map version, 검색 rank와 의미가 섞인 confidence는 저장하지 않는다.

## 1. 설계 경계

### 1.1 이 문서가 결정하는 것

- PostgreSQL 기준
- PostgreSQL schema namespace와 객체 소유 경계
- 내부 식별자와 공유 기본 키 전략
- 데이터베이스 객체 이름 규칙
- 공통 문자열, JSON, 해시, 시간, 숫자, Boolean과 벡터 표현
- 닫힌 상태·코드와 확장 가능한 참조 목록의 표현
- `NULL`, 빈 문자열과 기본값 정책
- 외래 키 삭제·갱신 정책
- 불변 버전, append-only 이력과 운영 상태의 수명주기 분류
- 공통 인덱스 원칙
- PostgreSQL `COMMENT ON` 작성 기준
- blocker와 후속 결정 기록 방식

### 1.2 이 문서가 결정하지 않는 것

- HTTP DTO와 화면용 표현
- 외부 source 선정, raw snapshot과 수집 manifest
- 실제 lint 정책에 포함할 entity·event·relation 범위
- 다른 행이나 테이블을 함께 검사하는 application-service 함수의 구현 세부사항
- 사용자·관리자 인증과 권한 모델
- 운영 규모의 성능 benchmark

## 2. PostgreSQL 기준과 extension

### 2.1 승인된 기준

| 항목 | 기준 |
|---|---|
| 데이터베이스 | PostgreSQL 18.6 |
| 필수 외부 extension | 없음 |
| 데이터베이스 이름 | `ontology_map` |
| 애플리케이션 schema | `public` |

PostgreSQL의 정확한 버전은 [구현 스택](../development/implementation-stack.md)을 따른다. 버전 갱신은 별도 Issue와 호환성 검증 없이 이루어지지 않는다.

### 2.2 extension 최소화

현재 필수 외부 PostgreSQL extension은 없다.

다음 extension은 현재 물리 스키마의 선행 요구사항이 아니다.

- `uuid-ossp`
- `pgcrypto`
- `pg_trgm`
- `unaccent`
- `btree_gist`

내부 식별자는 `bigint` identity를 사용하므로 UUID 생성 extension을 추가하지 않는다. 오타 검색, 문자 정규화나 고급 제약에 별도 extension이 실제로 필요해지면 해당 조회나 무결성을 소유하는 후속 Issue에서 근거와 함께 승인한다.

## 3. PostgreSQL namespace와 객체 소유

### 3.1 단일 `public` schema

POC는 전용 PostgreSQL 데이터베이스 하나를 하나의 애플리케이션이 사용하므로, 다음 객체를 모두 `public` schema에 둔다.

- 애플리케이션 테이블과 뷰
- PK·FK·UNIQUE·CHECK
- 인덱스
- 승인된 함수와 trigger

별도의 `ontology_map`, `extensions` 또는 도메인별 PostgreSQL schema를 만들지 않는다. 이 선택은 논리 모델의 영역 구분을 없애는 것이 아니라, 현재 규모에서 불필요한 `search_path`, SQLAlchemy, Alembic과 테스트 설정 복잡도를 추가하지 않기 위한 것이다.

`public`은 PostgreSQL namespace 이름이며 네트워크 공개나 모든 사용자의 데이터 접근을 의미하지 않는다.

### 3.2 변경 책임

지속되는 데이터베이스 객체의 생성·변경·삭제는 승인된 migration 경로만 수행한다.

API와 worker 런타임은 다음 작업을 수행하지 않는다.

- `CREATE`
- `ALTER`
- `DROP`
- 일반적인 `TRUNCATE`
- 기준·근거·이력 데이터를 대상으로 한 범용 물리 `DELETE`

실제 PostgreSQL role과 `GRANT` 구성은 실행 환경 Issue에서 구현하더라도, 논리적인 책임은 다음처럼 분리한다.

```text
migration owner
→ schema, extension, table, constraint, index, function과 trigger 변경

API / worker runtime
→ 승인된 조회와 데이터 상태 전이만 수행
```

데이터베이스가 여러 애플리케이션에 공유되거나 객체 소유 경계가 실제로 달라질 때만 전용 schema 도입을 다시 검토한다.

## 4. 내부 식별자

### 4.1 대리 기본 키

독립적인 수명주기를 가진 엔터티의 대리 기본 키는 PostgreSQL `bigint`를 사용한다.

새 식별자는 다음 원칙으로 생성한다.

```text
bigint GENERATED ALWAYS AS IDENTITY
```

Agent 출력과 애플리케이션 입력은 내부 기본 키 값을 선택하거나 재사용하지 않는다.

숫자 식별자의 대소나 정렬 순서는 다음 의미를 갖지 않는다.

- 업무 발생 시각
- 공개 순서
- 상태 변경 순서
- 근거 강도
- 의미상 우선순위

이 의미에는 각각 명시적인 시각, 상태와 정렬 규칙을 사용한다.

### 4.2 공유 기본 키 subtype

`knowledge_item`이 `node`, `relation`, `claim`의 공통 식별자를 발급한다.

```text
knowledge_item.knowledge_item_id
= node.node_id
= relation.relation_id
= claim.claim_id
```

하나의 실제 행이 세 subtype을 동시에 가진다는 뜻이 아니다. 하나의 `knowledge_item`은 정확히 하나의 subtype만 가져야 한다.

- `knowledge_item`의 기본 키만 identity를 가진다.
- `node`, `relation`, `claim`의 공유 기본 키에는 별도의 identity나 default를 두지 않는다.
- subtype 행은 같은 트랜잭션에서 상위 식별자를 전달받아 생성한다.

“정확히 한 subtype”의 교차 행 검증은 짧은 승격 transaction이 담당하며 공유 PK와 item kind의 로컬 조건은 DB가 보장한다.

#203의 `knowledge_item.lifecycle_kind`는 공유 ID를 유지하면서 `EVIDENCE_BACKED`와 `PRODUCT_REFERENCE`를 구분한다. 전자는 `current_state`와 `promotion_batch_id`를 필수로 유지하고 후자는 `NODE + NULL state/batch` 형태만 허용한다. `topic_reference`와 deferred constraint trigger는 product-reference row가 실제 TOPIC node와 1:1인지 검증한다. 기존 evidence-backed TOPIC은 자동 변환하지 않는다. 새 `HAS_TOPIC` INSERT/target 변경은 active reference target만 허용하며 reference 비활성화는 기존 Relation을 손대지 않는다.

### 4.3 연결 테이블과 순번

순수 N:M 연결 테이블은 다른 엔터티가 그 연결 행 자체를 참조해야 하는 승인된 요구가 없는 한 관련 FK 조합을 복합 기본 키로 사용한다.

예:

```text
claim_observation
→ (claim_id, observation_id)

claim_relation
→ (claim_id, relation_id)
```

다음 값은 대리 기본 키와 구분한다.

| 용도 | 물리 타입 |
|---|---|
| 같은 부모 안의 버전 번호 | `integer` |
| 모델 호출 시도 순번 | `integer` |
| 후속 질문 슬롯 1·2 | `smallint` |
| 안정된 온톨로지·lint 코드 | 고유 `text` |

현재 단일 PostgreSQL POC에는 UUID를 도입하지 않는다. 여러 독립 데이터베이스의 병합, 분산 ID 생성이나 불투명 공개 식별자가 실제 요구가 되면 별도 설계로 검토한다.

## 5. 데이터베이스 객체 이름

### 5.1 공통 형식

모든 데이터베이스 객체는 큰따옴표가 필요 없는 영문 소문자 `snake_case`를 사용한다.

- 테이블 이름은 단수형이다.
- 컬럼 이름은 `snake_case`다.
- 연결 테이블은 연결하는 단수 역할을 조합한다.
- CamelCase와 quoted identifier를 사용하지 않는다.
- PostgreSQL 식별자 길이 제한에 맞추며 자동 이름 절단에 의존하지 않는다.

예:

```text
source_document
evidence_group_assignment
knowledge_item
claim_observation
publication_affected_node
```

### 5.2 키와 역할 이름

- 일반 PK: `<table_name>_id`
- 일반 FK: 참조하는 PK와 같은 이름
- 같은 대상을 여러 역할로 참조하는 FK: 역할 접두어 사용

예:

```text
source_node_id
target_node_id
canonical_node_id
first_detected_run_id
latest_detected_run_id
resolved_by_run_id
```

Boolean 컬럼은 의미가 실제로 참·거짓일 때만 `is_` 또는 `has_`를 사용할 수 있다. 여러 실행 상태를 여러 Boolean으로 분해하지 않고 `status` 코드 하나로 표현한다.

### 5.3 제약과 지원 객체 이름

| 객체 | 접두어 | 예 |
|---|---|---|
| 기본 키 | `pk_` | `pk_source_document` |
| 외래 키 | `fk_` | `fk_observation__source_document` |
| 고유 제약 | `uq_` | `uq_model_task__cache_key` |
| CHECK | `ck_` | `ck_model_task__terminal_finished` |
| 일반 인덱스 | `ix_` | `ix_claim_observation__observation` |
| trigger | `trg_` | `trg_conflict_set__prevent_update` |
| 함수 | `fn_` | `fn_resolve_canonical_node` |

이름은 모든 컬럼을 기계적으로 이어 붙이지 않고, 어떤 역할·업무 키·불변식을 보장하는지 짧게 나타낸다.

SQL 문법과 Alembic migration 파일 이름은 [코드 규칙](../development/code-conventions.md)을 따른다.

## 6. 공통 자료형

### 6.1 문자열

일반 애플리케이션 문자열은 기본적으로 PostgreSQL `text`를 사용한다.

다음과 같은 추측성 길이 제한을 사용하지 않는다.

```text
varchar(255)
varchar(500)
varchar(2048)
```

실제 제품 규칙으로 최대 길이가 확정된 경우에만 명명된 `CHECK`로 제한한다.

적용 대상 예:

- 이름과 alias
- 제목·작성자·발행처
- URL
- 정규화 본문
- Claim·인용문·맥락 설명·질문
- 상태 변경 이유와 실패 사유
- 모델·프롬프트·생성기 버전
- 안정된 코드와 언어 식별값

URL과 언어 식별값도 `text`로 저장한다. 파싱, 허용 scheme과 canonicalization은 애플리케이션 경계가 담당하며, 복잡한 URL 정규식을 데이터베이스 CHECK로 복제하지 않는다.

필수 의미 텍스트에는 `NOT NULL`과 nonblank CHECK를 함께 검토한다. 다만 정규화 본문과 인용문처럼 공백과 문자 위치가 Evidence Trace에 영향을 주는 값은 데이터베이스가 자동 trim하거나 다시 쓰지 않는다.

### 6.2 JSON

`output_schema_definition.schema_json`은 `jsonb NOT NULL`로 저장한다.

`jsonb`의 정규화는 계약 버전 보존과 충돌하지 않는다. 버전 이력은 JSON 공백이나 key 순서가 아니라 다음 구조로 보존한다.

```text
task_kind
+ version_no
+ 불변 output_schema_definition 행
+ model_task의 정확한 FK
```

계약이 바뀌면 참조된 `schema_json`을 수정하지 않고 새 버전 행을 추가한다.

`jsonb`는 다음 범위로 제한한다.

- 버전이 있는 Structured Output 계약
- 관계형 필드로 고정할 필요가 없는 유연한 진단 상세

다음 데이터를 자유 JSON payload로 대체하지 않는다.

- 기준 노드·관계·Claim
- Evidence Trace
- 상태와 상태 전이
- 공개 준비 결과
- 외래 키로 검증해야 하는 의미 데이터

JSON 내부를 조회하는 실제 쿼리가 확인되기 전에는 GIN 등 JSON 인덱스를 만들지 않는다.

### 6.3 해시와 결정적 키

현재 digest와 fingerprint는 다음 물리 표현을 사용한다.

```text
SHA-256 raw digest
→ bytea
→ 정확히 32바이트
```

각 SHA-256 컬럼에는 다음 의미의 명명된 CHECK가 필요하다.

```text
octet_length(value) = 32
```

책임은 다음처럼 나눈다.

```text
애플리케이션
- canonical input의 필드와 순서 결정
- NULL과 배열의 표현 결정
- 모호하지 않은 직렬화
- 필요한 문자열의 UTF-8 encoding
- SHA-256 계산

PostgreSQL
- 32바이트 길이
- UNIQUE와 FK
- 동등 비교와 조회
```

로그와 진단에서는 `encode(value, 'hex')`로 소문자 16진수 표현을 만들 수 있지만, DB 원본 타입을 hex `text`로 바꾸지 않는다.

모든 현재 digest가 SHA-256이므로 각 행에 `hash_algorithm` 컬럼을 반복 저장하지 않는다.

다음 의미 값은 해시가 아니다.

- `source_key`
- `node_type_code`
- `relation_code`
- `attribute_code`
- `rule_code`
- 모델·프롬프트·생성기 버전

이 값은 안정된 `text`로 유지한다.

결정적 키를 추가하거나 바꾸는 migration은 다음을 반드시 문서화한다.

- 포함 필드
- 필드 순서
- `NULL` 표지
- 문자열 encoding
- 목록 정렬
- 값 경계를 모호하지 않게 만드는 직렬화 방식

단순 문자열 연결은 값 경계를 잃을 수 있으므로 허용하지 않는다.

### 6.4 시간

#### 실제 운영 시각

전 세계에서 동일한 한 순간을 나타내는 운영·감사·스케줄·lease·처리 시각은 `timestamptz`를 사용한다.

예:

- `created_at`
- `attempted_at`
- `finished_at`
- `observed_at`
- `last_checked_at`
- `next_attempt_at`
- `lease_expires_at`
- `ready_at`
- `resolved_at`
- `reversed_at`

데이터베이스, API와 worker는 UTC를 기준으로 해석한다. 화면은 필요한 사용자 시간대로 변환할 수 있다.

`timestamptz`는 원래 입력에 사용한 `Asia/Seoul` 같은 시간대 이름을 보존하지 않고 하나의 절대 시점으로 정규화한다. 원래 시간대 이름 자체가 제품 요구라면 별도 명시 필드가 필요하지만 현재 범위에는 없다.

#### 출처·사건·Claim의 부분 시간

출처 게시·수정 시간, 사건 경계와 Claim 주장 경계는 다음 조합을 사용한다.

```text
timestamptz value
+ precision text
```

허용 precision은 다음과 같다.

```text
INSTANT
DAY
MONTH
YEAR
UNKNOWN
```

| 알려진 정보 | 저장 원칙 | precision |
|---|---|---|
| 시간대까지 확인된 정확한 시각 | 정확한 절대 시각 | `INSTANT` |
| 날짜 | UTC 기준 날짜 시작 anchor | `DAY` |
| 월 | 해당 월 첫날 UTC anchor | `MONTH` |
| 연도 | 해당 연도 첫날 UTC anchor | `YEAR` |
| 미상 | `NULL` | `UNKNOWN` |

`DAY`, `MONTH`, `YEAR`의 값은 비교와 색인을 위한 anchor일 뿐 더 정밀한 사실을 주장하지 않는다. 표시와 기간 필터는 precision을 반드시 함께 해석하며, 일반적인 사용자 시간대 변환 결과를 정확한 주장 날짜로 사용하지 않는다.

행 내부에서는 최소한 다음을 검사한다.

```text
value IS NULL
↔ precision = 'UNKNOWN'

value IS NOT NULL
↔ precision <> 'UNKNOWN'
```

서로 다른 precision 사이의 순서와 기간 겹침은 정규화된 anchor만 비교하지 않고 실제 의미 범위로 확장해 검증한다. 구체적인 PostgreSQL 함수나 트랜잭션 검증 책임은 해당 도메인 매핑에서 정한다.

PostgreSQL의 `infinity`와 `-infinity`는 제품 시간 값으로 허용하지 않는다.

#### 구조화된 날짜와 기간

`claim_attribute_value`의 `DATE`와 `PERIOD`는 절대 시각이 아니라 달력 날짜이므로 다음 조합을 사용한다.

```text
date
+ precision
```

경계 미상과 precision 규칙은 시간 경계와 동일하다. `PERIOD`의 종료일이 없다는 뜻을 정확한 단일 날짜로 축소하지 않고, `DATE`를 종료 미상 기간으로 확장하지 않는다.

### 6.5 숫자와 단위

구조화된 십진 숫자는 전역 자릿수를 추측하지 않은 PostgreSQL `numeric`을 사용한다.

```text
numeric
```

다음 특수값은 제품 값으로 허용하지 않는다.

- `NaN`
- `Infinity`
- `-Infinity`

테이블별 매핑은 이를 차단하는 명명된 CHECK를 둔다. 임의의 전역 `numeric(p, s)`는 사용하지 않는다.

`NUMBER` 속성값은 다음 두 값을 함께 가진다.

```text
finite number_value
+ nonblank unit_code text
```

`number_value`와 `unit_code`는 원문에서 추출한 값을 그대로 저장한다. 자동 환산이나 canonical normalization을 적용하지 않는다.

허용 단위는 `attribute_revision_allowed_unit`이 revision별 집합으로 표현한다.

```text
attribute_revision_allowed_unit
- attribute_revision_id
- allowed_value_kind = 'NUMBER'
- unit_code
- PRIMARY KEY (attribute_revision_id, unit_code)
```

`claim_attribute_value(attribute_revision_id, unit_code)`는 위 복합 PK를 FK로 참조하므로 NUMBER Claim의 단위는 저장 시점에 해당 revision의 허용 집합에 반드시 존재해야 한다. 기존 단일 단위 NUMBER revision은 허용 단위 행 하나로 같은 의미를 유지하고, 복수 단위 revision은 같은 revision에 여러 행을 둔다. 범용 단위 사전, 환산식, canonical 단위 선정과 단위 간 자동 비교는 추가하지 않는다.

### 6.6 Boolean

구조화된 Boolean 값은 PostgreSQL `boolean`을 사용한다.

`value_kind = 'BOOLEAN'`인 행의 `boolean_value`는 반드시 `TRUE` 또는 `FALSE`여야 한다.

```text
FALSE
= 출처가 뒷받침하는 명시적 부정

해당 claim_attribute_value 행 없음
= 미상 또는 근거 있는 Claim 없음
```

두 상태를 같은 것으로 취급하지 않는다.

## 7. 닫힌 코드와 확장 가능한 참조 목록

### 7.1 닫힌 상태·기술 코드

작업 종류, 실행 상태, 지식 상태, 방향성, Claim modality, stance, 값 종류, 시간 정밀도와 같이 코드가 애플리케이션 로직과 함께 닫혀 있는 값은 다음처럼 표현한다.

```text
text NOT NULL
+ named CHECK
```

저장값은 안정된 영문 대문자 `UPPER_SNAKE_CASE`를 사용한다.

예시 형식:

```text
PENDING
VALIDATION_BLOCKED
EVIDENCE_VERIFIED
DIRECTED
PLAN_OR_TARGET
SUPPORT
PERIOD
UNKNOWN
```

위 값은 저장 형식을 설명하는 예시다. 테이블별 정확한 허용 목록은 SQLAlchemy metadata와 migration의 이름 있는 CHECK가 고정한다.

POC에서는 PostgreSQL enum 타입을 만들지 않는다. `CHECK` 값 목록의 변경은 승인된 migration으로 수행한다.

값의 허용 목록을 CHECK로 제한하는 것은 상태 전이를 보장하지 않는다. 예를 들어 `REJECTED`가 허용 값이어도 어느 상태에서 누가 그 상태로 바꿀 수 있는지는 application-service transaction이 따로 검사한다.

### 7.2 확장 가능한 의미 사전

설명, 버전, 활성 상태, 대상 유형이나 검증 메타데이터를 소유하는 개념은 참조 테이블로 유지한다.

예:

- `node_type`
- `relation_type`
- `relation_type_revision`
- `attribute`
- `attribute_revision`
- `lint_rule`
- `lint_policy_version`
- `output_schema_definition`

이 값은 단순 enum 대체 행이 아니라 제품 의미와 과거 revision을 보존하는 기준 데이터다.

## 8. `NULL`, 빈 문자열과 기본값

### 8.1 `NULL` 원칙

모든 컬럼은 기본적으로 `NOT NULL`이다.

Logical Schema v1이 다음 의미 중 하나를 명시할 때만 nullable로 둔다.

- 실제로 알 수 없음
- 해당 관계나 값이 적용되지 않음
- 현재 수명주기에서 아직 생성되지 않음
- 특정 작업 종류나 상태에서만 선택적으로 존재함

`NULL`의 의미는 테이블과 컬럼 주석에 구체적으로 기록한다.

### 8.2 빈 문자열과 sentinel

빈 문자열은 값 부재를 나타내지 않는다.

다음 sentinel 문자열도 일반 텍스트에 저장하지 않는다.

- `UNKNOWN`
- `N/A`
- `NONE`
- `NULL`

필수 의미 텍스트는 `NOT NULL`과 nonblank CHECK를 함께 사용한다. Evidence Trace의 본문과 인용문은 자동으로 trim하지 않는다.

`UNKNOWN`이 시간 정밀도처럼 승인된 실제 도메인 코드인 경우에만 코드값으로 사용할 수 있다.

함께 한 의미를 이루는 nullable 컬럼은 all-or-none 또는 상태 의존 CHECK를 사용한다.

예:

```text
attribute 충돌 대상
→ target_node_id와 attribute_revision_id가 함께 있음 또는 함께 없음

시간 경계
→ 값 NULL + UNKNOWN
→ 값 있음 + 비UNKNOWN
```

### 8.3 기본값을 허용할 범위

DB 기본값은 PostgreSQL이나 시스템 수명주기가 기계적으로 소유하는 값에만 둔다.

허용 가능한 예:

| 값 | 허용 조건 |
|---|---|
| identity | 독립 대리 PK |
| `CURRENT_TIMESTAMP` | 정확히 DB 행 생성 시각인 `created_at` |
| `0`, `1` | 의미가 하나뿐인 초기 카운터 |
| `PENDING`, `NOT_STARTED` | 합법적인 초기 상태가 정확히 하나인 운영 행 |
| `FALSE` | “선택되지 않음·활성 아님”의 중립 의미가 명확한 Boolean |

기본값을 두지 않는 예:

- 지식 검증·사람 검토 상태
- `item_kind`, `task_kind`, modality, stance와 value kind
- 출처가 말한 사실
- 게시·사건·Claim 시간과 precision
- 모델 결과
- 완료·실패·해결·취소 시각과 이유
- 단위 코드
- 언어와 임의 날짜

실제 호출 시각인 `attempted_at`이나 시스템이 근거를 발견한 `observed_at`은 DB INSERT 시각과 다를 수 있으므로 담당 로직이 명시적으로 전달한다.

## 9. 외래 키와 물리 삭제

### 9.1 기본 외래 키 동작

기준 지식, Evidence Trace, 불변 버전, 실행 이력, 사람 검토 이력과 연결 행의 기본 삭제 정책은 다음과 같다.

```text
ON DELETE RESTRICT
ON UPDATE RESTRICT
```

초기 POC에서는 다음 동작을 사용하지 않는다.

- `ON DELETE CASCADE`
- `ON DELETE SET NULL`
- `ON DELETE SET DEFAULT`

이 원칙은 `source_document`, `observation`, `claim_observation`처럼 공유되는 근거의 일부를 실수로 삭제해 다른 Claim이나 alias의 Evidence Trace까지 잃는 일을 막는다.

거절, 폐기, 병합 취소, lint 차단과 공개 무효화는 물리 삭제가 아니라 도메인 상태와 append-only 이력으로 표현한다. 실패한 트랜잭션은 cleanup DELETE가 아니라 rollback으로 처리한다.

### 9.2 의도적인 전체 삭제

특정 출처와 모든 종속 데이터를 의도적으로 제거하는 기능은 FK cascade로 추론하지 않는다.

향후 controlled purge 또는 retention 기능은 다음을 별도 설계해야 한다.

- 삭제 범위와 dependency preview
- 공유 observation과 Claim의 영향 분석
- 권한과 승인
- 감사·법적 보존 정책
- 명시적인 삭제 순서
- 공개 결과 무효화와 재생성
- 원자성·복구와 테스트

이 기능은 POC 범위가 아니다.

## 10. 데이터 수명주기와 불변성

각 테이블 또는 관련 컬럼 묶음은 다음 중 하나로 분류한다.

| 분류 | 의미 | 대표 예 |
|---|---|---|
| 불변 버전·산출물 | 의미가 바뀌면 UPDATE 대신 새 행 생성 | 문서 버전, relation·claim, ontology revision, 검색 문서, context, question, insight, conflict summary |
| append-only 사건·이력 | 기존 행을 수정·삭제하지 않고 새 사건만 추가 | `agent_attempt`, 사람 상태 변경 이력 |
| 수정 가능한 운영 상태 | 정해진 상태 전이·lease·재시도·카운터만 갱신 | `model_task`, `promotion_batch`, finding 감지 메타데이터 |
| 한 방향 종료·취소 | `NULL`에서 종료값으로 닫히며 되돌리지 않음 | `valid_to`, `reversed_at`, `resolved_at` |

한 테이블 안에서도 컬럼 묶음별 분류가 다를 수 있다.

예:

```text
source_document
- 본문·버전 메타데이터: 불변
- last_checked_at·last_check_status: 승인된 운영 갱신

output_schema_definition
- task_kind·version_no·schema_json: 참조 후 불변
- is_active: 승인된 활성 선택 갱신
```

다음 범용 컬럼을 모든 테이블에 기계적으로 추가하지 않는다.

- `updated_at`
- `deleted_at`
- `is_deleted`

대신 `finished_at`, `ready_at`, `resolved_at`, `reversed_at`처럼 실제 도메인 사건을 나타내는 필드를 사용한다.

불변성 보장 우선순위는 다음과 같다.

1. runtime role 권한과 코드 경계
2. PK·FK·UNIQUE·CHECK
3. 위험도가 높은 불변식에 한정한 좁은 trigger
4. 짧은 트랜잭션 안의 서비스 검증

모든 불변 행에 범용 UPDATE 차단 trigger를 자동 생성하지 않는다. 위험과 실제 변경 경로를 확인한 좁은 제약만 migration에 추가한다.

## 11. 인덱스 정책

### 11.1 기본 원칙

- PK와 UNIQUE가 이미 생성한 인덱스를 중복 생성하지 않는다.
- PostgreSQL이 자식 FK 인덱스를 자동 생성한다고 가정하지 않는다.
- FK 자식 컬럼은 실제 조인, 역방향 조회, Evidence Trace, 그래프 탐색, scheduling 또는 부모 유지 경로가 있을 때 B-tree 인덱스를 추가한다.
- 모든 비제약 인덱스에는 지원하는 쿼리 또는 무결성 경로를 문서화한다.
- 낮은 선택도의 Boolean이나 status 컬럼 하나만을 위한 추측성 인덱스를 만들지 않는다.
- covering index와 `INCLUDE`는 실제 실행 계획으로 필요성이 확인된 후에만 추가한다.

### 11.2 양방향 연결 조회

복합 PK가 한쪽 탐색만 지원하면 반대 방향 복합 인덱스를 추가한다.

예:

```text
PRIMARY KEY (claim_id, observation_id)
→ Claim에서 observation 조회 지원

INDEX (observation_id, claim_id)
→ observation을 공유하는 Claim 조회 지원
```

이 원칙의 대상 여부는 각 연결 table을 사용하는 실제 query를 확인해 결정한다.

- `claim_observation`
- `claim_relation`
- `node_alias_evidence`
- `event_temporal_basis`
- `search_document_basis`
- `conflict_member`

### 11.3 조건부 고유성

조건부 “최대 하나” 규칙은 partial unique index를 사용한다.

대표 후보:

```text
노드당 preferred alias 최대 하나
WHERE is_preferred

원본 노드당 활성 merge 최대 하나
WHERE reversed_at IS NULL

finding key당 열린 finding 최대 하나
WHERE resolved_at IS NULL
```

partial unique index가 “최대 하나”를 보장해도 “공개 시 정확히 하나” 같은 최소 개수 규칙까지 보장하지는 않는다. 정확한 개수는 publication 검증 등 해당 트랜잭션이 담당한다.

### 11.4 특수 인덱스

현재 migration은 `node_search_document`의 `simple` A/B `tsvector` 표현식 GIN만 구현한다. JSON GIN과 대규모 covering index는 없다.

새 특수 인덱스는 실제 query와 측정 결과를 소유하는 Issue에서만 결정한다.

## 12. 데이터베이스 주석

### 12.1 필수 범위

모든 애플리케이션 테이블에 `COMMENT ON TABLE`을 작성한다.

다음 객체에는 의미가 자명하지 않을 때 주석을 작성한다.

- 컬럼
- 복잡한 CHECK
- trigger
- 함수
- 비직관적인 인덱스와 constraint

데이터베이스 객체 이름은 영어로 유지하고 주석은 팀이 직접 읽을 수 있는 한국어로 작성한다.

### 12.2 주석 내용

주석은 단순 번역보다 다음 내용을 설명한다.

- 이 값이 의미하는 것
- 어느 단계가 작성·변경하는지
- `NULL`의 의미
- 다른 비슷한 시간·상태와의 차이
- 무엇으로 해석하면 안 되는지

예시:

```text
observation.observed_at
→ 시스템이 해당 원문 범위를 근거로 식별한 시각.
  출처 게시 시점이나 노드 활동량 계산 시각이 아니다.

external_identifier.identifier_value
→ 신뢰된 자료 준비 단계가 제공한 외부 식별값.
  POC에서는 Claim·Observation Evidence Trace와 연결하지 않는다.

promotion_batch.publication_status
→ 검색 문서·임베딩·맥락·후속 질문·인사이트의 공개 준비 상태.
  기준 지식 저장 결과인 promotion_status와 별개다.

conflict_set
→ 대상과 Claim 구성의 불변 비교 snapshot.
  구성원이 달라지면 기존 행을 수정하지 않고 새 묶음을 만든다.
```

주석은 제약, 권한과 트랜잭션 검증을 대신하지 않는다. 자격 증명, API key, token, 비밀값과 민감한 운영 정보를 주석에 넣지 않는다.

## 13. 무결성 보장 책임

모든 table mapping은 각 불변식을 다음 중 정확한 보장 주체에 배정한다.

| 보장 수단 | 사용 대상 |
|---|---|
| `NOT NULL`, row-local CHECK | 한 행의 값·상태·nullable 조합 |
| PK·FK | 식별과 참조 무결성 |
| UNIQUE·partial unique index | 일반·조건부 중복 금지 |
| 좁은 trigger 또는 지연 가능한 DB 메커니즘 | 고위험 불변성과 commit 시점의 행 간 조건 |
| 짧은 트랜잭션 서비스 검증 | 여러 테이블 집계, 후보·공개 완결성과 정밀도-aware 계산 |
| read-time filter | 현재 상태·열린 lint·READY 결과를 조합한 공개 선택 |

한 규칙을 설명문에만 남기지 않는다. 반대로 모든 교차 테이블 규칙을 무거운 범용 trigger로 만들지도 않는다.

애플리케이션이 canonical input을 계산하는 결정적 키는 PostgreSQL UNIQUE와 함께 사용한다. 애플리케이션 계산만 믿거나 DB 해시만으로 비즈니스 의미를 추론하지 않는다.

## 14. Deferred Decisions

| ID | 영향 객체 | 현재 결정 | 다시 여는 조건 |
|---|---|---|---|
| `PHY-DEFER-001` | `knowledge_state_event`, `conflict_state_event` | actor·principal FK를 임의로 만들지 않고 두 table 전체를 초기 migration에서 제외 | 관리자·인증과 사람 상태 변경 기능의 actor 계약 승인 |

의미가 불명확한 `TBD`, placeholder 값과 가짜 actor FK는 frozen schema에 넣지 않는다.

## 15. Migration과 변경 기준

#41–#48의 table mapping과 #91의 인사이트 확장은 `0001_create_frozen_schema.py`에 통합되어 있다. #121은 현재 POC에서 사용하지 않는 #78의 embedding 계약을 제거하기 위해 예외적으로 이 frozen baseline을 교체했다. `server/src/ontology_map/db/schema.py`는 Alembic 비교와 query 작성에 쓰는 같은 metadata를 제공한다.

이미 `main`에 병합된 revision을 수정하거나 순서를 다시 쓰지 않는 것이 원칙이다. 다만 #121은 빈 환경에서도 pgvector가 필요하지 않도록 기존 개발 DB 재생성을 전제로 `0001` baseline 교체를 명시적으로 승인한 예외다. #200은 이 예외를 재사용하지 않고 현재 head `0002` 뒤에 forward migration `0003`을 추가한다. 저장 의미, 제약이나 publication 계약이 바뀌면 먼저 logical·physical 결정 Issue를 승인하고 새 Alembic revision으로 변경한다. HTTP DTO나 화면 전용 상태는 DB column을 추가하지 않고 [제품 설계](../product/design.md)의 API 경계에 기록한다.

migration 변경은 논리 필드와 물리 컬럼, PostgreSQL type, `NULL`과 default, PK·FK·UNIQUE·CHECK, 삭제·갱신 동작, lifecycle, index, 한국어 DB comment, DB와 service의 무결성 책임 및 정상·실패 검증을 함께 설명해야 한다.

## 기간별 패널 결과 추가 계약

`0002_add_panel_reading_contracts.py`는 기존 43개 테이블을 수정하지 않고 6개 테이블을 추가한다. `node_question_set`의 context·기간, `node_question`의 묶음·표시 순서, `node_insight_window`의 작업·기간, `node_insight_section`의 보고서·표시 순서가 각각 유일하다. `node_question_claim`과 `node_insight_section_claim`은 부모·Claim 복합 PK와 부모·표시 순서 UNIQUE를 갖는다. FK는 RESTRICT를 사용하고 양의 순서·비어 있지 않은 본문·허용 기간·역할·유한 기준 시각은 CHECK로 검사한다.

선택된 context·작업과 성공 상태, 전체 공개 basis, Claim 원문 연결, 보고서와 window의 node·검색 문서·작업·기간·기준 시각 일치 및 절별 Claim의 상위 보고서 포함 여부는 읽기 transaction에서 검사한다. 새 결과가 없던 기존 READY는 503으로 구분하고 기존 데이터를 생성 결과처럼 backfill하지 않는다. 생성 worker와 실제 출력 저장 검증기는 후속 구현 범위다. 기존 필드와 추가 구조의 의미는 [논리 스키마](logical-schema.md#512-기간별-질문답변과-종합보고서)를 따른다.

추가 테이블에 결과가 존재하면 downgrade는 오류로 중단한다. 일반 되돌리기는 새 스키마와 데이터를 유지하고 기존 앱·API로 복귀하는 방식이며, 데이터 삭제가 필요한 downgrade를 자동 실행하지 않는다.

## NUMBER attribute 허용 단위 확장 계약

`0003_support_multiple_number_attribute_units.py`는 기존 `attribute_revision.unit_rule` 단일 문자열을 `attribute_revision_allowed_unit`의 행 집합으로 옮긴다. 기존 NUMBER revision의 `unit_rule`은 정확히 한 허용 단위 행으로 backfill되어 단일 단위 의미가 유지된다. migration 시 기존 NUMBER Claim의 `unit_code`가 그 legacy `unit_rule`과 다르면 값을 환산하거나 고치지 않고 upgrade를 실패시킨다.

새 테이블은 `(attribute_revision_id, unit_code)`를 PK로 사용하고 `(attribute_revision_id, allowed_value_kind)`를 parent revision에 FK로 연결하며 `allowed_value_kind = 'NUMBER'`를 CHECK한다. `claim_attribute_value(attribute_revision_id, unit_code)`도 허용 단위 PK를 FK로 참조하므로 승인되지 않은 단위는 저장 단계에서 결정적으로 거부된다. active revision partial unique index는 바꾸지 않는다.

이 구조는 canonical 단위, 범용 unit registry, 환산식, 값 normalization과 cross-unit 비교를 도입하지 않는다. `number_value`와 `unit_code`는 입력 의미를 그대로 보존한다. downgrade는 모든 NUMBER revision이 정확히 한 허용 단위만 가질 때에만 legacy `unit_rule`로 복원할 수 있으며, 복수 단위 또는 단위가 없는 revision이 있으면 의미 손실을 막기 위해 중단한다.
