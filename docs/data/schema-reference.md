<!-- scripts/check_docs.py가 생성합니다. 직접 수정하지 마세요. -->

# ontology-map PostgreSQL 스키마 참고 문서

이 문서는 [완성 SQLAlchemy metadata](../../server/src/ontology_map/db/metadata.py)의 실제 table, column, constraint와 index를 이름순으로 보여 주는 생성 결과다. 데이터 의미와 수명주기는 [논리 스키마](logical-schema.md), PostgreSQL 공통 표현 규칙은 [물리 스키마](physical-schema.md)가 소유한다.

- table 수: 56
- 생성 명령: `uv run --project server --frozen python scripts/check_docs.py --write`
- 검사 명령: `uv run --project server --frozen python scripts/check_docs.py --check`

## Table 목차

- [`agent_attempt`](#agent_attempt)
- [`attribute`](#attribute)
- [`attribute_revision`](#attribute_revision)
- [`attribute_revision_allowed_unit`](#attribute_revision_allowed_unit)
- [`blocked_fingerprint`](#blocked_fingerprint)
- [`claim`](#claim)
- [`claim_attribute_value`](#claim_attribute_value)
- [`claim_observation`](#claim_observation)
- [`claim_relation`](#claim_relation)
- [`conflict_member`](#conflict_member)
- [`conflict_set`](#conflict_set)
- [`conflict_summary`](#conflict_summary)
- [`event_temporal_basis`](#event_temporal_basis)
- [`event_temporal_extent`](#event_temporal_extent)
- [`evidence_group`](#evidence_group)
- [`external_identifier`](#external_identifier)
- [`followup_question`](#followup_question)
- [`knowledge_item`](#knowledge_item)
- [`lint_finding`](#lint_finding)
- [`lint_policy_rule`](#lint_policy_rule)
- [`lint_policy_version`](#lint_policy_version)
- [`lint_rule`](#lint_rule)
- [`lint_run`](#lint_run)
- [`model_task`](#model_task)
- [`node`](#node)
- [`node_alias`](#node_alias)
- [`node_alias_evidence`](#node_alias_evidence)
- [`node_context`](#node_context)
- [`node_insight`](#node_insight)
- [`node_insight_claim`](#node_insight_claim)
- [`node_insight_section`](#node_insight_section)
- [`node_insight_section_claim`](#node_insight_section_claim)
- [`node_insight_window`](#node_insight_window)
- [`node_merge`](#node_merge)
- [`node_question`](#node_question)
- [`node_question_claim`](#node_question_claim)
- [`node_question_set`](#node_question_set)
- [`node_search_document`](#node_search_document)
- [`node_type`](#node_type)
- [`observation`](#observation)
- [`output_schema_definition`](#output_schema_definition)
- [`promotion_batch`](#promotion_batch)
- [`promotion_canonical_change`](#promotion_canonical_change)
- [`provider_call_slot`](#provider_call_slot)
- [`publication_affected_node`](#publication_affected_node)
- [`relation`](#relation)
- [`relation_endpoint_rule`](#relation_endpoint_rule)
- [`relation_type`](#relation_type)
- [`relation_type_revision`](#relation_type_revision)
- [`search_document_basis`](#search_document_basis)
- [`source_claim`](#source_claim)
- [`source_claim_observation`](#source_claim_observation)
- [`source_claim_projection`](#source_claim_projection)
- [`source_document`](#source_document)
- [`source_processing_job`](#source_processing_job)
- [`topic_reference`](#topic_reference)

## `agent_attempt`

확정된 provider terminal 결과의 append-only 이력. attempt_no는 slot_no이며 UNKNOWN slot 때문에 번호에 gap이 생길 수 있다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `agent_attempt_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `model_task_id` | `BIGINT` | 아니요 | — | — | — |
| `attempt_no` | `INTEGER` | 아니요 | — | — | — |
| `outcome` | `TEXT` | 아니요 | — | — | — |
| `failure_reason` | `TEXT` | 예 | — | — | — |
| `attempted_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_agent_attempt__attempt_no` | `CHECK (attempt_no BETWEEN 1 AND 5)` |
| CHECK | `ck_agent_attempt__attempted_at_finite` | `CHECK (isfinite(attempted_at))` |
| CHECK | `ck_agent_attempt__failure_reason` | `CHECK ((outcome = 'SUCCESS' AND failure_reason IS NULL) OR (outcome <> 'SUCCESS' AND failure_reason IS NOT NULL AND btrim(failure_reason) <> ''))` |
| CHECK | `ck_agent_attempt__outcome` | `CHECK (outcome IN ('SUCCESS', 'TIMEOUT', 'RATE_LIMITED', 'PROVIDER_ERROR', 'AUTHENTICATION_ERROR', 'INVALID_REQUEST', 'OUTPUT_CONTRACT_ERROR'))` |
| FOREIGN KEY | `fk_agent_attempt__model_task` | `FOREIGN KEY (model_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_agent_attempt` | `PRIMARY KEY (agent_attempt_id)` |
| UNIQUE | `uq_agent_attempt__number` | `UNIQUE (model_task_id, attempt_no)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `attribute`

구조화된 Claim 속성의 안정된 코드 테이블. 실제 값과 사용 규칙은 attribute_revision과 claim_attribute_value가 소유한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `attribute_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `attribute_code` | `TEXT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_attribute__code_nonblank` | `CHECK (btrim(attribute_code) <> '')` |
| PRIMARY KEY | `pk_attribute` | `PRIMARY KEY (attribute_id)` |
| UNIQUE | `uq_attribute__code` | `UNIQUE (attribute_code)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `attribute_revision`

속성의 대상 유형·값 종류와 활성 수명주기를 보존하는 불변 규칙 버전. NUMBER 허용 단위는 attribute_revision_allowed_unit이 소유한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `attribute_revision_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `attribute_id` | `BIGINT` | 아니요 | — | — | — |
| `version_no` | `INTEGER` | 아니요 | — | — | — |
| `display_name` | `TEXT` | 아니요 | — | — | — |
| `target_node_type_id` | `BIGINT` | 아니요 | — | — | — |
| `allowed_value_kind` | `TEXT` | 아니요 | — | — | — |
| `is_active` | `BOOLEAN` | 아니요 | `false` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_attribute_revision__display_name_nonblank` | `CHECK (btrim(display_name) <> '')` |
| CHECK | `ck_attribute_revision__value_kind` | `CHECK (allowed_value_kind IN ('STRING', 'NUMBER', 'DATE', 'PERIOD', 'BOOLEAN'))` |
| CHECK | `ck_attribute_revision__version_positive` | `CHECK (version_no >= 1)` |
| FOREIGN KEY | `fk_attribute_revision__attribute` | `FOREIGN KEY (attribute_id) REFERENCES attribute (attribute_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_attribute_revision__target_type` | `FOREIGN KEY (target_node_type_id) REFERENCES node_type (node_type_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_attribute_revision` | `PRIMARY KEY (attribute_revision_id)` |
| UNIQUE | `uq_attribute_revision__kind` | `UNIQUE (attribute_revision_id, allowed_value_kind)` |
| UNIQUE | `uq_attribute_revision__version` | `UNIQUE (attribute_id, version_no)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_attribute_revision__target_type` | 아니요 | `target_node_type_id, attribute_revision_id` | — |
| `uq_attribute_revision__active` | 예 | `attribute_id` | `attribute_revision.is_active` |

## `attribute_revision_allowed_unit`

NUMBER attribute revision이 허용하는 원문 단위 code 집합. 단위 환산·정규화·비교 규칙을 뜻하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `attribute_revision_id` | `BIGINT` | 아니요 | — | — | — |
| `allowed_value_kind` | `TEXT` | 아니요 | — | — | — |
| `unit_code` | `TEXT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_attribute_revision_allowed_unit__number_kind` | `CHECK (allowed_value_kind = 'NUMBER')` |
| CHECK | `ck_attribute_revision_allowed_unit__unit_nonblank` | `CHECK (btrim(unit_code) <> '')` |
| FOREIGN KEY | `fk_attribute_revision_allowed_unit__attribute_kind` | `FOREIGN KEY (attribute_revision_id, allowed_value_kind) REFERENCES attribute_revision (attribute_revision_id, allowed_value_kind) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_attribute_revision_allowed_unit` | `PRIMARY KEY (attribute_revision_id, unit_code)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `blocked_fingerprint`

후보 payload를 저장하지 않고 같은 차단 후보의 반복 검증·승격을 억제한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `blocked_fingerprint_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `fingerprint` | `BYTEA` | 아니요 | — | — | — |
| `source_document_id` | `BIGINT` | 아니요 | — | — | — |
| `output_schema_definition_id` | `BIGINT` | 아니요 | — | — | — |
| `lint_policy_rule_id` | `BIGINT` | 아니요 | — | — | — |
| `first_blocked_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | — |
| `last_blocked_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | — |
| `blocked_count` | `INTEGER` | 아니요 | `1` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_blocked_fingerprint__count_positive` | `CHECK (blocked_count >= 1)` |
| CHECK | `ck_blocked_fingerprint__length` | `CHECK (octet_length(fingerprint) = 32)` |
| CHECK | `ck_blocked_fingerprint__timestamps` | `CHECK (isfinite(first_blocked_at) AND isfinite(last_blocked_at) AND last_blocked_at >= first_blocked_at)` |
| FOREIGN KEY | `fk_blocked_fingerprint__output_contract` | `FOREIGN KEY (output_schema_definition_id) REFERENCES output_schema_definition (output_schema_definition_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_blocked_fingerprint__policy_rule` | `FOREIGN KEY (lint_policy_rule_id) REFERENCES lint_policy_rule (lint_policy_rule_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_blocked_fingerprint__source_document` | `FOREIGN KEY (source_document_id) REFERENCES source_document (source_document_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_blocked_fingerprint` | `PRIMARY KEY (blocked_fingerprint_id)` |
| UNIQUE | `uq_blocked_fingerprint__identity` | `UNIQUE (fingerprint, source_document_id, output_schema_definition_id, lint_policy_rule_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_blocked_fingerprint__policy_rule` | 아니요 | `lint_policy_rule_id, blocked_fingerprint.last_blocked_at DESC` | — |
| `ix_blocked_fingerprint__source` | 아니요 | `source_document_id, blocked_fingerprint.last_blocked_at DESC` | — |

## `claim`

출처가 주장한 원자 문장과 표현 성격·주장 시간을 보존하는 불변 knowledge_item subtype.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `claim_id` | `BIGINT` | 아니요 | — | — | — |
| `statement_text` | `TEXT` | 아니요 | — | — | — |
| `language` | `TEXT` | 아니요 | — | — | — |
| `modality` | `TEXT` | 아니요 | — | — | 원문 표현이 사실 주장, 계획·목표, 예측·추정, 의견·평가 중 무엇인지 나타낸다. PLAN_OR_TARGET 값을 확정 사실로 표시해서는 안 된다. |
| `asserted_from` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `asserted_to` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `asserted_from_precision` | `TEXT` | 아니요 | — | — | — |
| `asserted_to_precision` | `TEXT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_claim__from_precision` | `CHECK (asserted_from_precision IN ('INSTANT', 'DAY', 'MONTH', 'YEAR', 'UNKNOWN'))` |
| CHECK | `ck_claim__from_value_precision` | `CHECK ((asserted_from IS NULL AND asserted_from_precision = 'UNKNOWN') OR (asserted_from IS NOT NULL AND asserted_from_precision <> 'UNKNOWN'))` |
| CHECK | `ck_claim__language_nonblank` | `CHECK (btrim(language) <> '')` |
| CHECK | `ck_claim__modality` | `CHECK (modality IN ('FACT', 'PLAN_OR_TARGET', 'PREDICTION_OR_ESTIMATE', 'OPINION_OR_EVALUATION'))` |
| CHECK | `ck_claim__statement_nonblank` | `CHECK (btrim(statement_text) <> '')` |
| CHECK | `ck_claim__timestamps_finite` | `CHECK ((asserted_from IS NULL OR isfinite(asserted_from)) AND (asserted_to IS NULL OR isfinite(asserted_to)))` |
| CHECK | `ck_claim__to_precision` | `CHECK (asserted_to_precision IN ('INSTANT', 'DAY', 'MONTH', 'YEAR', 'UNKNOWN'))` |
| CHECK | `ck_claim__to_value_precision` | `CHECK ((asserted_to IS NULL AND asserted_to_precision = 'UNKNOWN') OR (asserted_to IS NOT NULL AND asserted_to_precision <> 'UNKNOWN'))` |
| FOREIGN KEY | `fk_claim__knowledge_item` | `FOREIGN KEY (claim_id) REFERENCES knowledge_item (knowledge_item_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_claim` | `PRIMARY KEY (claim_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `claim_attribute_value`

Claim이 node 속성에 관해 주장한 구조화 값. target node의 현재 확정 프로필 값이나 사실 판정 결과가 아니다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `claim_attribute_value_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `claim_id` | `BIGINT` | 아니요 | — | — | — |
| `target_node_id` | `BIGINT` | 아니요 | — | — | — |
| `attribute_revision_id` | `BIGINT` | 아니요 | — | — | — |
| `value_kind` | `TEXT` | 아니요 | — | — | — |
| `string_value` | `TEXT` | 예 | — | — | — |
| `number_value` | `NUMERIC` | 예 | — | — | — |
| `unit_code` | `TEXT` | 예 | — | — | — |
| `date_from` | `DATE` | 예 | — | — | — |
| `date_to` | `DATE` | 예 | — | — | — |
| `date_from_precision` | `TEXT` | 아니요 | — | — | — |
| `date_to_precision` | `TEXT` | 아니요 | — | — | — |
| `boolean_value` | `BOOLEAN` | 예 | — | — | false도 원문 근거가 있는 명시적 부정이다. 값 행이 없는 미상과 구분한다. |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_claim_attribute_value__from_precision` | `CHECK (date_from_precision IN ('DAY', 'MONTH', 'YEAR', 'UNKNOWN'))` |
| CHECK | `ck_claim_attribute_value__number_finite` | `CHECK (number_value IS NULL OR number_value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))` |
| CHECK | `ck_claim_attribute_value__string_nonblank` | `CHECK (string_value IS NULL OR btrim(string_value) <> '')` |
| CHECK | `ck_claim_attribute_value__tagged_union` | `CHECK ((value_kind = 'STRING' AND string_value IS NOT NULL AND number_value IS NULL AND unit_code IS NULL AND date_from IS NULL AND date_to IS NULL AND boolean_value IS NULL AND date_from_precision = 'UNKNOWN' AND date_to_precision = 'UNKNOWN') OR (value_kind = 'NUMBER' AND string_value IS NULL AND number_value IS NOT NULL AND unit_code IS NOT NULL AND date_from IS NULL AND date_to IS NULL AND boolean_value IS NULL AND date_from_precision = 'UNKNOWN' AND date_to_precision = 'UNKNOWN') OR (value_kind = 'DATE' AND string_value IS NULL AND number_value IS NULL AND unit_code IS NULL AND date_from IS NOT NULL AND date_to IS NULL AND boolean_value IS NULL AND date_from_precision IN ('DAY', 'MONTH', 'YEAR') AND date_to_precision = 'UNKNOWN') OR (value_kind = 'PERIOD' AND string_value IS NULL AND number_value IS NULL AND unit_code IS NULL AND date_from IS NOT NULL AND boolean_value IS NULL AND date_from_precision IN ('DAY', 'MONTH', 'YEAR') AND ((date_to IS NULL AND date_to_precision = 'UNKNOWN') OR (date_to IS NOT NULL AND date_to_precision IN ('DAY', 'MONTH', 'YEAR')))) OR (value_kind = 'BOOLEAN' AND string_value IS NULL AND number_value IS NULL AND unit_code IS NULL AND date_from IS NULL AND date_to IS NULL AND boolean_value IS NOT NULL AND date_from_precision = 'UNKNOWN' AND date_to_precision = 'UNKNOWN'))` |
| CHECK | `ck_claim_attribute_value__to_precision` | `CHECK (date_to_precision IN ('DAY', 'MONTH', 'YEAR', 'UNKNOWN'))` |
| CHECK | `ck_claim_attribute_value__unit_nonblank` | `CHECK (unit_code IS NULL OR btrim(unit_code) <> '')` |
| CHECK | `ck_claim_attribute_value__value_kind` | `CHECK (value_kind IN ('STRING', 'NUMBER', 'DATE', 'PERIOD', 'BOOLEAN'))` |
| FOREIGN KEY | `fk_claim_attribute_value__allowed_unit` | `FOREIGN KEY (attribute_revision_id, unit_code) REFERENCES attribute_revision_allowed_unit (attribute_revision_id, unit_code) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_claim_attribute_value__attribute_kind` | `FOREIGN KEY (attribute_revision_id, value_kind) REFERENCES attribute_revision (attribute_revision_id, allowed_value_kind) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_claim_attribute_value__claim` | `FOREIGN KEY (claim_id) REFERENCES claim (claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_claim_attribute_value__target_node` | `FOREIGN KEY (target_node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_claim_attribute_value` | `PRIMARY KEY (claim_attribute_value_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_claim_attribute_value__attribute` | 아니요 | `attribute_revision_id, target_node_id, claim_id` | — |
| `ix_claim_attribute_value__claim` | 아니요 | `claim_id, claim_attribute_value_id` | — |
| `ix_claim_attribute_value__target` | 아니요 | `target_node_id, attribute_revision_id, claim_id` | — |

## `claim_observation`

Claim과 정확한 원문 범위를 다대다로 연결하는 Evidence Trace다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `claim_id` | `BIGINT` | 아니요 | — | — | — |
| `observation_id` | `BIGINT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | `fk_claim_observation__claim` | `FOREIGN KEY (claim_id) REFERENCES claim (claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_claim_observation__observation` | `FOREIGN KEY (observation_id) REFERENCES observation (observation_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_claim_observation` | `PRIMARY KEY (claim_id, observation_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_claim_observation__observation` | 아니요 | `observation_id, claim_id` | — |

## `claim_relation`

Claim이 관계를 지지하거나 반박하는 의미 연결. 근거 강도와 충돌 상태를 합친 점수는 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `claim_id` | `BIGINT` | 아니요 | — | — | — |
| `relation_id` | `BIGINT` | 아니요 | — | — | — |
| `stance` | `TEXT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_claim_relation__stance` | `CHECK (stance IN ('SUPPORT', 'DISPUTE'))` |
| FOREIGN KEY | `fk_claim_relation__claim` | `FOREIGN KEY (claim_id) REFERENCES claim (claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_claim_relation__relation` | `FOREIGN KEY (relation_id) REFERENCES relation (relation_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_claim_relation` | `PRIMARY KEY (claim_id, relation_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_claim_relation__relation` | 아니요 | `relation_id, stance, claim_id` | — |

## `conflict_member`

정확한 Claim 구성과 같은 관점 그룹을 보존하는 불변 conflict snapshot member다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `conflict_set_id` | `BIGINT` | 아니요 | — | — | — |
| `claim_id` | `BIGINT` | 아니요 | — | — | — |
| `position_key` | `TEXT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_conflict_member__position_nonblank` | `CHECK (btrim(position_key) <> '')` |
| FOREIGN KEY | `fk_conflict_member__claim` | `FOREIGN KEY (claim_id) REFERENCES claim (claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_conflict_member__set` | `FOREIGN KEY (conflict_set_id) REFERENCES conflict_set (conflict_set_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_conflict_member` | `PRIMARY KEY (conflict_set_id, claim_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_conflict_member__claim` | 아니요 | `claim_id, conflict_set_id` | — |
| `ix_conflict_member__position` | 아니요 | `conflict_set_id, position_key, claim_id` | — |

## `conflict_set`

관계·노드 속성·사건 시간 중 한 의미 대상을 비교하는 불변 Claim snapshot. 어느 Claim이 참인지 자동 판정하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `conflict_set_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `relation_id` | `BIGINT` | 예 | — | — | — |
| `target_node_id` | `BIGINT` | 예 | — | — | — |
| `attribute_revision_id` | `BIGINT` | 예 | — | — | — |
| `event_node_id` | `BIGINT` | 예 | — | — | — |
| `modality` | `TEXT` | 아니요 | — | — | — |
| `current_state` | `TEXT` | 아니요 | — | — | Agent 제안에 대한 사람의 선택적 확인·거절 상태. member Claim의 knowledge state를 변경하지 않는다. |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_conflict_set__created_at_finite` | `CHECK (isfinite(created_at))` |
| CHECK | `ck_conflict_set__current_state` | `CHECK (current_state IN ('AGENT_PROPOSED', 'HUMAN_CONFIRMED', 'REJECTED'))` |
| CHECK | `ck_conflict_set__modality` | `CHECK (modality IN ('FACT', 'PLAN_OR_TARGET', 'PREDICTION_OR_ESTIMATE', 'OPINION_OR_EVALUATION'))` |
| CHECK | `ck_conflict_set__target_shape` | `CHECK ((relation_id IS NOT NULL AND target_node_id IS NULL AND attribute_revision_id IS NULL AND event_node_id IS NULL) OR (relation_id IS NULL AND target_node_id IS NOT NULL AND attribute_revision_id IS NOT NULL AND event_node_id IS NULL) OR (relation_id IS NULL AND target_node_id IS NULL AND attribute_revision_id IS NULL AND event_node_id IS NOT NULL))` |
| FOREIGN KEY | `fk_conflict_set__attribute_revision` | `FOREIGN KEY (attribute_revision_id) REFERENCES attribute_revision (attribute_revision_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_conflict_set__event` | `FOREIGN KEY (event_node_id) REFERENCES event_temporal_extent (event_node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_conflict_set__relation` | `FOREIGN KEY (relation_id) REFERENCES relation (relation_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_conflict_set__target_node` | `FOREIGN KEY (target_node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_conflict_set` | `PRIMARY KEY (conflict_set_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_conflict_set__attribute` | 아니요 | `target_node_id, attribute_revision_id, current_state, conflict_set_id` | `conflict_set.target_node_id IS NOT NULL` |
| `ix_conflict_set__event` | 아니요 | `event_node_id, current_state, conflict_set_id` | `conflict_set.event_node_id IS NOT NULL` |
| `ix_conflict_set__relation` | 아니요 | `relation_id, current_state, conflict_set_id` | `conflict_set.relation_id IS NOT NULL` |

## `conflict_summary`

불변 conflict member 집합으로 생성한 공통점과 관점 요약. 자동 승자 판정이 아니다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `conflict_summary_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `conflict_set_id` | `BIGINT` | 아니요 | — | — | — |
| `model_task_id` | `BIGINT` | 아니요 | — | — | — |
| `common_ground_text` | `TEXT` | 아니요 | — | — | — |
| `viewpoint_summary_text` | `TEXT` | 아니요 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_conflict_summary__common_ground_nonblank` | `CHECK (btrim(common_ground_text) <> '')` |
| CHECK | `ck_conflict_summary__created_at_finite` | `CHECK (isfinite(created_at))` |
| CHECK | `ck_conflict_summary__viewpoint_nonblank` | `CHECK (btrim(viewpoint_summary_text) <> '')` |
| FOREIGN KEY | `fk_conflict_summary__model_task` | `FOREIGN KEY (model_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_conflict_summary__set` | `FOREIGN KEY (conflict_set_id) REFERENCES conflict_set (conflict_set_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_conflict_summary` | `PRIMARY KEY (conflict_summary_id)` |
| UNIQUE | `uq_conflict_summary__model_task` | `UNIQUE (model_task_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_conflict_summary__set` | 아니요 | `conflict_set_id, conflict_summary.created_at DESC, conflict_summary.conflict_summary_id DESC` | — |

## `event_temporal_basis`

사건 node의 채택 시간 범위를 직접 뒷받침하는 Claim 연결이다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `event_node_id` | `BIGINT` | 아니요 | — | — | — |
| `claim_id` | `BIGINT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | `fk_event_temporal_basis__claim` | `FOREIGN KEY (claim_id) REFERENCES claim (claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_event_temporal_basis__event` | `FOREIGN KEY (event_node_id) REFERENCES event_temporal_extent (event_node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_event_temporal_basis` | `PRIMARY KEY (event_node_id, claim_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_event_temporal_basis__claim` | 아니요 | `claim_id, event_node_id` | — |

## `event_temporal_extent`

사건 node의 채택 시간 범위. 출처별 모든 주장 시간을 저장하는 곳이 아니며 근거 Claim은 event_temporal_basis로 연결한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `event_node_id` | `BIGINT` | 아니요 | — | — | — |
| `start_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | precision이 MONTH 또는 YEAR이면 범위 계산을 위한 시작 anchor다. 실제 월 1일 또는 1월 1일 발생을 뜻하지 않는다. |
| `end_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `start_precision` | `TEXT` | 아니요 | — | — | — |
| `end_precision` | `TEXT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_event_temporal_extent__end_precision` | `CHECK (end_precision IN ('INSTANT', 'DAY', 'MONTH', 'YEAR', 'UNKNOWN'))` |
| CHECK | `ck_event_temporal_extent__end_value_precision` | `CHECK ((end_at IS NULL AND end_precision = 'UNKNOWN') OR (end_at IS NOT NULL AND end_precision <> 'UNKNOWN'))` |
| CHECK | `ck_event_temporal_extent__start_precision` | `CHECK (start_precision IN ('INSTANT', 'DAY', 'MONTH', 'YEAR', 'UNKNOWN'))` |
| CHECK | `ck_event_temporal_extent__start_value_precision` | `CHECK ((start_at IS NULL AND start_precision = 'UNKNOWN') OR (start_at IS NOT NULL AND start_precision <> 'UNKNOWN'))` |
| CHECK | `ck_event_temporal_extent__timestamps_finite` | `CHECK ((start_at IS NULL OR isfinite(start_at)) AND (end_at IS NULL OR isfinite(end_at)))` |
| FOREIGN KEY | `fk_event_temporal_extent__node` | `FOREIGN KEY (event_node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_event_temporal_extent` | `PRIMARY KEY (event_node_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_event_temporal_extent__start` | 아니요 | `start_at` | `event_temporal_extent.start_at IS NOT NULL` |

## `evidence_group`

같은 원문 계보로 판단된 문서를 독립 근거 하나로 세기 위한 최소 묶음. 출처 신뢰도나 사실의 진실성을 뜻하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `evidence_group_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_evidence_group__created_at_finite` | `CHECK (isfinite(created_at))` |
| PRIMARY KEY | `pk_evidence_group` | `PRIMARY KEY (evidence_group_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `external_identifier`

신뢰된 자료 준비 단계가 제공한 외부 식별 체계와 값. 일반 Agent 본문 추출이나 Claim·observation Evidence Trace를 저장하는 곳이 아니다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `external_identifier_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `identifier_system` | `TEXT` | 아니요 | — | — | — |
| `identifier_value` | `TEXT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_external_identifier__system` | `CHECK (identifier_system IN ('KRX', 'WIKIDATA', 'ORCID', 'LEI'))` |
| CHECK | `ck_external_identifier__system_nonblank` | `CHECK (btrim(identifier_system) <> '')` |
| CHECK | `ck_external_identifier__value_nonblank` | `CHECK (btrim(identifier_value) <> '')` |
| FOREIGN KEY | `fk_external_identifier__node` | `FOREIGN KEY (node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_external_identifier` | `PRIMARY KEY (external_identifier_id)` |
| UNIQUE | `uq_external_identifier__business` | `UNIQUE (identifier_system, identifier_value)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_external_identifier__node` | 아니요 | `node_id, external_identifier_id` | — |

## `followup_question`

한 node_context에서 다음 지도 중심으로 이동할 질문 두 개와 대상 node를 저장한다. target은 Relation을 뜻하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `followup_question_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `node_context_id` | `BIGINT` | 아니요 | — | — | — |
| `model_task_id` | `BIGINT` | 아니요 | — | — | — |
| `slot` | `SMALLINT` | 아니요 | — | — | — |
| `question_text` | `TEXT` | 아니요 | — | — | — |
| `target_node_id` | `BIGINT` | 아니요 | — | — | 질문 클릭 뒤 새 중심으로 탐색할 node. 질문의 완전한 답이나 두 node 사이의 근거 있는 Relation을 뜻하지 않는다. |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_followup_question__created_at_finite` | `CHECK (isfinite(created_at))` |
| CHECK | `ck_followup_question__slot` | `CHECK (slot IN (1, 2))` |
| CHECK | `ck_followup_question__text_nonblank` | `CHECK (btrim(question_text) <> '')` |
| FOREIGN KEY | `fk_followup_question__context` | `FOREIGN KEY (node_context_id) REFERENCES node_context (node_context_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_followup_question__model_task` | `FOREIGN KEY (model_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_followup_question__target_node` | `FOREIGN KEY (target_node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_followup_question` | `PRIMARY KEY (followup_question_id)` |
| UNIQUE | `uq_followup_question__slot` | `UNIQUE (node_context_id, slot)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_followup_question__model_task` | 아니요 | `model_task_id, slot` | — |
| `ix_followup_question__target` | 아니요 | `target_node_id, followup_question_id` | — |

## `knowledge_item`

node·relation·claim의 공유 ID와 수명주기를 관리하는 상위 엔터티. EVIDENCE_BACKED는 기존 state·promotion을 필수로 사용하고, PRODUCT_REFERENCE는 승인된 reference Node에만 제한한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `knowledge_item_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `item_kind` | `TEXT` | 아니요 | — | — | — |
| `lifecycle_kind` | `TEXT` | 아니요 | `'EVIDENCE_BACKED'` | — | EVIDENCE_BACKED는 기존 promotion·state·Evidence/publication 수명주기, PRODUCT_REFERENCE는 승인된 제품 reference Node 수명주기다. |
| `current_state` | `TEXT` | 예 | — | — | EVIDENCE_VERIFIED는 출처와 구조 검사를 통과했다는 뜻이며 객관적 사실 확정이나 사람 승인을 뜻하지 않는다. |
| `promotion_batch_id` | `BIGINT` | 예 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_knowledge_item__created_at_finite` | `CHECK (isfinite(created_at))` |
| CHECK | `ck_knowledge_item__current_state` | `CHECK (current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED', 'ON_HOLD', 'REJECTED'))` |
| CHECK | `ck_knowledge_item__item_kind` | `CHECK (item_kind IN ('NODE', 'RELATION', 'CLAIM'))` |
| CHECK | `ck_knowledge_item__lifecycle_kind` | `CHECK (lifecycle_kind IN ('EVIDENCE_BACKED', 'PRODUCT_REFERENCE'))` |
| CHECK | `ck_knowledge_item__lifecycle_shape` | `CHECK ((lifecycle_kind = 'EVIDENCE_BACKED' AND current_state IS NOT NULL AND promotion_batch_id IS NOT NULL) OR (lifecycle_kind = 'PRODUCT_REFERENCE' AND item_kind = 'NODE' AND current_state IS NULL AND promotion_batch_id IS NULL))` |
| FOREIGN KEY | `fk_knowledge_item__promotion_batch` | `FOREIGN KEY (promotion_batch_id) REFERENCES promotion_batch (promotion_batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_knowledge_item` | `PRIMARY KEY (knowledge_item_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_knowledge_item__promotion_batch` | 아니요 | `promotion_batch_id, knowledge_item_id` | — |

## `lint_finding`

저장된 knowledge item의 결정적 문제 인스턴스. 사람의 거절 상태가 아니며 열린 BLOCKING finding은 공개 조회에서만 제외한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `lint_finding_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `finding_key` | `BYTEA` | 아니요 | — | — | — |
| `knowledge_item_id` | `BIGINT` | 아니요 | — | — | — |
| `lint_policy_rule_id` | `BIGINT` | 아니요 | — | — | — |
| `first_detected_run_id` | `BIGINT` | 아니요 | — | — | — |
| `latest_detected_run_id` | `BIGINT` | 아니요 | — | — | — |
| `first_detected_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | — |
| `last_detected_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | — |
| `detection_count` | `INTEGER` | 아니요 | `1` | — | — |
| `message` | `TEXT` | 아니요 | — | — | — |
| `details_json` | `JSONB` | 예 | — | — | — |
| `resolved_by_run_id` | `BIGINT` | 예 | — | — | — |
| `resolved_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `resolution_reason` | `TEXT` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_lint_finding__count_positive` | `CHECK (detection_count >= 1)` |
| CHECK | `ck_lint_finding__details_object` | `CHECK (details_json IS NULL OR jsonb_typeof(details_json) = 'object')` |
| CHECK | `ck_lint_finding__key_length` | `CHECK (octet_length(finding_key) = 32)` |
| CHECK | `ck_lint_finding__message_nonblank` | `CHECK (btrim(message) <> '')` |
| CHECK | `ck_lint_finding__resolution_shape` | `CHECK ((resolved_by_run_id IS NULL AND resolved_at IS NULL AND resolution_reason IS NULL) OR (resolved_by_run_id IS NOT NULL AND resolved_at IS NOT NULL AND resolution_reason IS NOT NULL AND btrim(resolution_reason) <> ''))` |
| CHECK | `ck_lint_finding__timestamps` | `CHECK (isfinite(first_detected_at) AND isfinite(last_detected_at) AND last_detected_at >= first_detected_at AND (resolved_at IS NULL OR (isfinite(resolved_at) AND resolved_at >= last_detected_at)))` |
| FOREIGN KEY | `fk_lint_finding__first_run` | `FOREIGN KEY (first_detected_run_id) REFERENCES lint_run (lint_run_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_lint_finding__knowledge_item` | `FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_item (knowledge_item_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_lint_finding__latest_run` | `FOREIGN KEY (latest_detected_run_id) REFERENCES lint_run (lint_run_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_lint_finding__policy_rule` | `FOREIGN KEY (lint_policy_rule_id) REFERENCES lint_policy_rule (lint_policy_rule_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_lint_finding__resolved_run` | `FOREIGN KEY (resolved_by_run_id) REFERENCES lint_run (lint_run_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_lint_finding` | `PRIMARY KEY (lint_finding_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_lint_finding__blocking_open` | 아니요 | `lint_policy_rule_id, knowledge_item_id` | `lint_finding.resolved_at IS NULL` |
| `ix_lint_finding__item_open` | 아니요 | `knowledge_item_id, lint_policy_rule_id, lint_finding_id` | `lint_finding.resolved_at IS NULL` |
| `ix_lint_finding__latest_run` | 아니요 | `latest_detected_run_id, lint_finding_id` | — |
| `uq_lint_finding__open_key` | 예 | `finding_key` | `lint_finding.resolved_at IS NULL` |

## `lint_policy_rule`

한 lint policy가 선택한 stable rule과 해당 심각도의 불변 연결.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `lint_policy_rule_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `lint_policy_version_id` | `BIGINT` | 아니요 | — | — | — |
| `lint_rule_id` | `BIGINT` | 아니요 | — | — | — |
| `severity` | `TEXT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_lint_policy_rule__severity` | `CHECK (severity IN ('BLOCKING', 'WARNING'))` |
| FOREIGN KEY | `fk_lint_policy_rule__policy` | `FOREIGN KEY (lint_policy_version_id) REFERENCES lint_policy_version (lint_policy_version_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_lint_policy_rule__rule` | `FOREIGN KEY (lint_rule_id) REFERENCES lint_rule (lint_rule_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_lint_policy_rule` | `PRIMARY KEY (lint_policy_rule_id)` |
| UNIQUE | `uq_lint_policy_rule__selection` | `UNIQUE (lint_policy_version_id, lint_rule_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_lint_policy_rule__rule` | 아니요 | `lint_rule_id, lint_policy_version_id` | — |

## `lint_policy_version`

판정 결과에 영향을 주는 validator와 규칙 선택의 불변 정책 버전.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `lint_policy_version_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `version_no` | `INTEGER` | 아니요 | — | — | — |
| `validator_version` | `TEXT` | 아니요 | — | — | — |
| `is_active` | `BOOLEAN` | 아니요 | `false` | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |
| `activated_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_lint_policy_version__active_timestamp` | `CHECK (NOT is_active OR activated_at IS NOT NULL)` |
| CHECK | `ck_lint_policy_version__timestamps_finite` | `CHECK (isfinite(created_at) AND (activated_at IS NULL OR isfinite(activated_at)))` |
| CHECK | `ck_lint_policy_version__validator_nonblank` | `CHECK (btrim(validator_version) <> '')` |
| CHECK | `ck_lint_policy_version__version_positive` | `CHECK (version_no >= 1)` |
| PRIMARY KEY | `pk_lint_policy_version` | `PRIMARY KEY (lint_policy_version_id)` |
| UNIQUE | `uq_lint_policy_version__number` | `UNIQUE (version_no)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `lint_rule`

안정된 lint 규칙 정의와 평가 범위. 정책별 사용 여부와 BLOCKING/WARNING 심각도는 lint_policy_rule이 소유한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `lint_rule_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `rule_code` | `TEXT` | 아니요 | — | — | — |
| `display_name` | `TEXT` | 아니요 | — | — | — |
| `description` | `TEXT` | 아니요 | — | — | — |
| `evaluation_scope` | `TEXT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_lint_rule__code_nonblank` | `CHECK (btrim(rule_code) <> '')` |
| CHECK | `ck_lint_rule__description_nonblank` | `CHECK (btrim(description) <> '')` |
| CHECK | `ck_lint_rule__display_name_nonblank` | `CHECK (btrim(display_name) <> '')` |
| CHECK | `ck_lint_rule__evaluation_scope` | `CHECK (evaluation_scope IN ('PRE_PROMOTION', 'PERSISTED_GRAPH', 'BOTH'))` |
| PRIMARY KEY | `pk_lint_rule` | `PRIMARY KEY (lint_rule_id)` |
| UNIQUE | `uq_lint_rule__code` | `UNIQUE (rule_code)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `lint_run`

이미 저장된 기준 지식그래프를 한 lint policy로 재검사한 full graph 실행. 후보 검사나 promotion 실패를 기록하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `lint_run_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `lint_policy_version_id` | `BIGINT` | 아니요 | — | — | — |
| `scope_kind` | `TEXT` | 아니요 | `'FULL_GRAPH'` | — | — |
| `status` | `TEXT` | 아니요 | `'PENDING'` | — | — |
| `started_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `completed_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_lint_run__scope_kind` | `CHECK (scope_kind = 'FULL_GRAPH')` |
| CHECK | `ck_lint_run__status` | `CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED'))` |
| CHECK | `ck_lint_run__status_shape` | `CHECK ((status = 'PENDING' AND started_at IS NULL AND completed_at IS NULL) OR (status = 'RUNNING' AND started_at IS NOT NULL AND completed_at IS NULL) OR (status IN ('SUCCESS', 'FAILED') AND started_at IS NOT NULL AND completed_at IS NOT NULL))` |
| CHECK | `ck_lint_run__timestamps` | `CHECK ((started_at IS NULL OR isfinite(started_at)) AND (completed_at IS NULL OR isfinite(completed_at)) AND (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at))` |
| FOREIGN KEY | `fk_lint_run__policy` | `FOREIGN KEY (lint_policy_version_id) REFERENCES lint_policy_version (lint_policy_version_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_lint_run` | `PRIMARY KEY (lint_run_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_lint_run__policy` | 아니요 | `lint_policy_version_id, lint_run.lint_run_id DESC` | — |
| `ix_lint_run__status` | 아니요 | `status, lint_run_id` | `lint_run.status IN ('PENDING', 'RUNNING')` |
| `uq_lint_run__in_progress` | 예 | `lint_policy_version_id` | `lint_run.status IN ('PENDING', 'RUNNING')` |

## `model_task`

재시도 전체를 묶는 논리 모델 작업. terminal attempt 수와 현재 실행 상태를 소유하며 모델 응답 payload를 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `model_task_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `task_kind` | `TEXT` | 아니요 | — | — | — |
| `source_document_id` | `BIGINT` | 예 | — | — | — |
| `input_hash` | `BYTEA` | 아니요 | — | — | — |
| `output_schema_definition_id` | `BIGINT` | 예 | — | — | — |
| `model_version` | `TEXT` | 아니요 | — | — | — |
| `prompt_version` | `TEXT` | 예 | — | — | — |
| `cache_key` | `BYTEA` | 아니요 | — | — | — |
| `status` | `TEXT` | 아니요 | `'PENDING'` | — | — |
| `attempt_count` | `INTEGER` | 아니요 | `0` | — | durable terminal agent_attempt 행 수. append와 같은 transaction에서 유지하며 실제 호출 hard budget은 provider_call_slot 소비 수로 판단한다. |
| `next_attempt_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `lease_owner` | `TEXT` | 예 | — | — | — |
| `lease_expires_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |
| `finished_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_model_task__attempt_count` | `CHECK (attempt_count BETWEEN 0 AND 5)` |
| CHECK | `ck_model_task__cache_key_length` | `CHECK (octet_length(cache_key) = 32)` |
| CHECK | `ck_model_task__input_hash_length` | `CHECK (octet_length(input_hash) = 32)` |
| CHECK | `ck_model_task__lease_owner_nonblank` | `CHECK (lease_owner IS NULL OR btrim(lease_owner) <> '')` |
| CHECK | `ck_model_task__model_version_nonblank` | `CHECK (btrim(model_version) <> '')` |
| CHECK | `ck_model_task__output_contract` | `CHECK (output_schema_definition_id IS NOT NULL AND prompt_version IS NOT NULL)` |
| CHECK | `ck_model_task__prompt_version_nonblank` | `CHECK (prompt_version IS NULL OR btrim(prompt_version) <> '')` |
| CHECK | `ck_model_task__status` | `CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'RETRY_WAIT', 'VALIDATION_BLOCKED', 'FINAL_FAILED'))` |
| CHECK | `ck_model_task__status_shape` | `CHECK ((status = 'PENDING' AND finished_at IS NULL AND next_attempt_at IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL) OR (status = 'RUNNING' AND finished_at IS NULL AND next_attempt_at IS NULL AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status = 'RETRY_WAIT' AND finished_at IS NULL AND next_attempt_at IS NOT NULL AND lease_owner IS NULL AND lease_expires_at IS NULL AND attempt_count < 5) OR (status IN ('SUCCESS', 'VALIDATION_BLOCKED', 'FINAL_FAILED') AND finished_at IS NOT NULL AND next_attempt_at IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL))` |
| CHECK | `ck_model_task__task_kind` | `CHECK (task_kind IN ('KNOWLEDGE_EXTRACTION', 'ENTITY_RESOLUTION_PROPOSAL', 'EVIDENCE_LINEAGE_PROPOSAL', 'CONFLICT_SUMMARY', 'NODE_CONTEXT', 'FOLLOWUP_QUESTIONS', 'NODE_INSIGHT'))` |
| CHECK | `ck_model_task__timestamps_finite` | `CHECK (isfinite(created_at) AND (next_attempt_at IS NULL OR isfinite(next_attempt_at)) AND (lease_expires_at IS NULL OR isfinite(lease_expires_at)) AND (finished_at IS NULL OR isfinite(finished_at)))` |
| FOREIGN KEY | `fk_model_task__output_contract` | `FOREIGN KEY (output_schema_definition_id) REFERENCES output_schema_definition (output_schema_definition_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_model_task__source_document` | `FOREIGN KEY (source_document_id) REFERENCES source_document (source_document_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_model_task` | `PRIMARY KEY (model_task_id)` |
| UNIQUE | `uq_model_task__cache_key` | `UNIQUE (cache_key)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_model_task__contract` | 아니요 | `output_schema_definition_id, model_task_id` | — |
| `ix_model_task__expired_lease` | 아니요 | `lease_expires_at, model_task_id` | `model_task.status = 'RUNNING'` |
| `ix_model_task__runnable` | 아니요 | `status, next_attempt_at, created_at, model_task_id` | `model_task.status IN ('PENDING', 'RETRY_WAIT')` |
| `ix_model_task__source_document` | 아니요 | `source_document_id, model_task_id` | `model_task.source_document_id IS NOT NULL` |

## `node`

지식그래프 대상의 불변 정체성과 안정된 node type만 저장하는 knowledge_item subtype. 이름과 세부 사실을 직접 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `node_type_id` | `BIGINT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | `fk_node__knowledge_item` | `FOREIGN KEY (node_id) REFERENCES knowledge_item (knowledge_item_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node__node_type` | `FOREIGN KEY (node_type_id) REFERENCES node_type (node_type_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node` | `PRIMARY KEY (node_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_node__type` | 아니요 | `node_type_id, node_id` | — |

## `node_alias`

대표 이름과 검색 alias를 불변 node ID에 연결한다. 기간과 이름 종류는 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_alias_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `alias_text` | `TEXT` | 아니요 | — | — | — |
| `language` | `TEXT` | 아니요 | — | — | — |
| `is_preferred` | `BOOLEAN` | 아니요 | `false` | — | 현재 화면 대표 이름으로 선택된 alias인지 표시한다. 유일한 공식 명칭이나 유일한 검색 이름이라는 뜻이 아니다. |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_alias__alias_nonblank` | `CHECK (btrim(alias_text) <> '')` |
| CHECK | `ck_node_alias__language_nonblank` | `CHECK (btrim(language) <> '')` |
| FOREIGN KEY | `fk_node_alias__node` | `FOREIGN KEY (node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_alias` | `PRIMARY KEY (node_alias_id)` |
| UNIQUE | `uq_node_alias__value` | `UNIQUE (node_id, alias_text, language)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_node_alias__text` | 아니요 | `alias_text, node_id` | — |
| `uq_node_alias__preferred` | 예 | `node_id` | `node_alias.is_preferred` |

## `node_alias_evidence`

alias가 확인된 원문 위치를 다대다로 연결한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_alias_id` | `BIGINT` | 아니요 | — | — | — |
| `observation_id` | `BIGINT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | `fk_node_alias_evidence__alias` | `FOREIGN KEY (node_alias_id) REFERENCES node_alias (node_alias_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_alias_evidence__observation` | `FOREIGN KEY (observation_id) REFERENCES observation (observation_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_alias_evidence` | `PRIMARY KEY (node_alias_id, observation_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_node_alias_evidence__observation` | 아니요 | `observation_id, node_alias_id` | — |

## `node_context`

정확한 검색 문서에서 사전 생성한 사용자용 맥락 설명. 검색 문서 입력으로 되돌려 넣지 않으며 클릭 시 모델을 호출하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_context_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `node_search_document_id` | `BIGINT` | 아니요 | — | — | — |
| `model_task_id` | `BIGINT` | 아니요 | — | — | — |
| `language` | `TEXT` | 아니요 | — | — | — |
| `context_text` | `TEXT` | 아니요 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_context__context_nonblank` | `CHECK (btrim(context_text) <> '')` |
| CHECK | `ck_node_context__created_at_finite` | `CHECK (isfinite(created_at))` |
| CHECK | `ck_node_context__language_nonblank` | `CHECK (btrim(language) <> '')` |
| FOREIGN KEY | `fk_node_context__model_task` | `FOREIGN KEY (model_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_context__search_document` | `FOREIGN KEY (node_search_document_id, node_id) REFERENCES node_search_document (node_search_document_id, node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_context` | `PRIMARY KEY (node_context_id)` |
| UNIQUE | `uq_node_context__model_task` | `UNIQUE (model_task_id)` |
| UNIQUE | `uq_node_context__publication_reference` | `UNIQUE (node_context_id, node_search_document_id, node_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_node_context__search_document` | 아니요 | `node_search_document_id, node_id, node_context_id` | — |

## `node_insight`

한 node의 공개 검색 문서와 근거 Claim을 모델이 미리 종합한 불변 분석 리포트. 클릭 시 생성하지 않으며 사실·관계·원문 사본을 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_insight_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `node_search_document_id` | `BIGINT` | 아니요 | — | — | — |
| `model_task_id` | `BIGINT` | 아니요 | — | — | — |
| `time_window` | `TEXT` | 아니요 | — | — | — |
| `as_of_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | RECENT_90_DAYS와 RECENT_1_YEAR 입력 범위를 계산한 기준 시각. 모델 호출 시각이나 공개 완료 시각이 아니다. |
| `slot` | `SMALLINT` | 아니요 | — | — | — |
| `title` | `TEXT` | 아니요 | — | — | — |
| `summary_text` | `TEXT` | 아니요 | — | — | — |
| `synthesis_text` | `TEXT` | 아니요 | — | — | 여러 근거를 연결한 모델의 종합 해석. 원문에서 직접 확인된 사실 문장으로 표시해서는 안 된다. |
| `caveat_text` | `TEXT` | 아니요 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_insight__caveat_nonblank` | `CHECK (btrim(caveat_text) <> '')` |
| CHECK | `ck_node_insight__slot` | `CHECK (slot IN (1, 2, 3))` |
| CHECK | `ck_node_insight__summary_nonblank` | `CHECK (btrim(summary_text) <> '')` |
| CHECK | `ck_node_insight__synthesis_nonblank` | `CHECK (btrim(synthesis_text) <> '')` |
| CHECK | `ck_node_insight__time_window` | `CHECK (time_window IN ('RECENT_90_DAYS', 'RECENT_1_YEAR'))` |
| CHECK | `ck_node_insight__timestamps_finite` | `CHECK (isfinite(as_of_at) AND isfinite(created_at))` |
| CHECK | `ck_node_insight__title_nonblank` | `CHECK (btrim(title) <> '')` |
| FOREIGN KEY | `fk_node_insight__model_task` | `FOREIGN KEY (model_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_insight__search_document` | `FOREIGN KEY (node_search_document_id, node_id) REFERENCES node_search_document (node_search_document_id, node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_insight` | `PRIMARY KEY (node_insight_id)` |
| UNIQUE | `uq_node_insight__task_window_slot` | `UNIQUE (model_task_id, time_window, slot)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_node_insight__node_window` | 아니요 | `node_id, node_search_document_id, time_window, model_task_id, slot` | — |

## `node_insight_claim`

인사이트가 사용한 기존 Claim과 화면 역할을 연결한다. 확인된 사실·원문·Relation 사본이 아니며 Evidence Trace는 Claim에서 조회한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_insight_id` | `BIGINT` | 아니요 | — | — | — |
| `claim_id` | `BIGINT` | 아니요 | — | — | — |
| `role` | `TEXT` | 아니요 | — | — | KEY_CLAIM은 근거로 확인된 내용, SUPPORTING_CLAIM은 보조 근거, CONTRASTING_CLAIM은 엇갈리는 관점이나 유의점의 근거다. |
| `display_order` | `SMALLINT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_insight_claim__display_order_positive` | `CHECK (display_order >= 1)` |
| CHECK | `ck_node_insight_claim__role` | `CHECK (role IN ('KEY_CLAIM', 'SUPPORTING_CLAIM', 'CONTRASTING_CLAIM'))` |
| FOREIGN KEY | `fk_node_insight_claim__claim` | `FOREIGN KEY (claim_id) REFERENCES claim (claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_insight_claim__insight` | `FOREIGN KEY (node_insight_id) REFERENCES node_insight (node_insight_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_insight_claim` | `PRIMARY KEY (node_insight_id, claim_id)` |
| UNIQUE | `uq_node_insight_claim__display_order` | `UNIQUE (node_insight_id, display_order)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_node_insight_claim__claim` | 아니요 | `claim_id, node_insight_id` | — |

## `node_insight_section`

종합보고서의 주요 발견과 해석. 사실은 기존 Claim을 참조한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `section_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `node_insight_id` | `BIGINT` | 아니요 | — | — | — |
| `display_order` | `INTEGER` | 아니요 | — | — | — |
| `title` | `TEXT` | 아니요 | — | — | — |
| `synthesis_text` | `TEXT` | 아니요 | — | — | — |
| `caveat_text` | `TEXT` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_insight_section__caveat` | `CHECK (caveat_text IS NULL OR btrim(caveat_text) <> '')` |
| CHECK | `ck_node_insight_section__order` | `CHECK (display_order > 0)` |
| CHECK | `ck_node_insight_section__text` | `CHECK (btrim(title) <> '' AND btrim(synthesis_text) <> '')` |
| FOREIGN KEY | `fk_node_insight_section__node_insight` | `FOREIGN KEY (node_insight_id) REFERENCES node_insight (node_insight_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_insight_section` | `PRIMARY KEY (section_id)` |
| UNIQUE | `uq_node_insight_section__order` | `UNIQUE (node_insight_id, display_order)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `node_insight_section_claim`

생성물이 사용하는 기존 Claim과 역할·순서. 원문과 사실의 사본을 만들지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `section_id` | `BIGINT` | 아니요 | — | — | — |
| `claim_id` | `BIGINT` | 아니요 | — | — | — |
| `role` | `TEXT` | 아니요 | — | — | — |
| `display_order` | `INTEGER` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_insight_section_claim__order` | `CHECK (display_order > 0)` |
| CHECK | `ck_node_insight_section_claim__role` | `CHECK (role IN ('KEY_CLAIM', 'SUPPORTING_CLAIM', 'CONTRASTING_CLAIM'))` |
| FOREIGN KEY | `fk_node_insight_section_claim__claim` | `FOREIGN KEY (claim_id) REFERENCES claim (claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_insight_section_claim__node_insight_section` | `FOREIGN KEY (section_id) REFERENCES node_insight_section (section_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_insight_section_claim` | `PRIMARY KEY (section_id, claim_id)` |
| UNIQUE | `uq_node_insight_section_claim__order` | `UNIQUE (section_id, display_order)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `node_insight_window`

선택된 작업의 기간별 종합보고서 준비 결과. NULL 보고서는 정상 0개이며 빈 분석을 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `insight_window_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `node_search_document_id` | `BIGINT` | 아니요 | — | — | — |
| `model_task_id` | `BIGINT` | 아니요 | — | — | — |
| `time_window` | `TEXT` | 아니요 | — | — | — |
| `as_of_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | — |
| `node_insight_id` | `BIGINT` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_insight_window__as_of` | `CHECK (isfinite(as_of_at))` |
| CHECK | `ck_node_insight_window__window` | `CHECK (time_window IN ('RECENT_90_DAYS', 'RECENT_1_YEAR'))` |
| FOREIGN KEY | `fk_node_insight_window__model_task` | `FOREIGN KEY (model_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_insight_window__node` | `FOREIGN KEY (node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_insight_window__node_insight` | `FOREIGN KEY (node_insight_id) REFERENCES node_insight (node_insight_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_insight_window__node_search_document` | `FOREIGN KEY (node_search_document_id) REFERENCES node_search_document (node_search_document_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_insight_window` | `PRIMARY KEY (insight_window_id)` |
| UNIQUE | `uq_node_insight_window__task_window` | `UNIQUE (model_task_id, time_window)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `node_merge`

동일 대상으로 확인된 source node ID를 canonical node ID로 해석하는 리디렉션 이력. alias 변경이나 기존 근거의 물리 이동이 아니다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_merge_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `source_node_id` | `BIGINT` | 아니요 | — | — | — |
| `canonical_node_id` | `BIGINT` | 아니요 | — | — | — |
| `merge_reason` | `TEXT` | 아니요 | — | — | — |
| `merged_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | — |
| `reversed_reason` | `TEXT` | 예 | — | — | — |
| `reversed_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | 값이 없으면 활성 병합, 값이 있으면 취소된 과거 병합이다. 취소 행을 삭제하거나 다시 활성화하지 않는다. |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_merge__not_self` | `CHECK (source_node_id <> canonical_node_id)` |
| CHECK | `ck_node_merge__reason_nonblank` | `CHECK (btrim(merge_reason) <> '')` |
| CHECK | `ck_node_merge__reversal_shape` | `CHECK ((reversed_at IS NULL AND reversed_reason IS NULL) OR (reversed_at IS NOT NULL AND reversed_reason IS NOT NULL AND btrim(reversed_reason) <> ''))` |
| CHECK | `ck_node_merge__timestamps` | `CHECK (isfinite(merged_at) AND (reversed_at IS NULL OR (isfinite(reversed_at) AND reversed_at >= merged_at)))` |
| FOREIGN KEY | `fk_node_merge__canonical` | `FOREIGN KEY (canonical_node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_merge__source` | `FOREIGN KEY (source_node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_merge` | `PRIMARY KEY (node_merge_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_node_merge__active_canonical` | 아니요 | `canonical_node_id, source_node_id` | `node_merge.reversed_at IS NULL` |
| `ix_node_merge__source_history` | 아니요 | `source_node_id, node_merge.merged_at DESC` | — |
| `uq_node_merge__active_source` | 예 | `source_node_id` | `node_merge.reversed_at IS NULL` |

## `node_question`

현재 노드를 이해하는 질문과 저장 답변. 클릭으로 지도의 중심을 바꾸지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `question_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `question_set_id` | `BIGINT` | 아니요 | — | — | — |
| `display_order` | `INTEGER` | 아니요 | — | — | — |
| `question_text` | `TEXT` | 아니요 | — | — | — |
| `answer_text` | `TEXT` | 아니요 | — | — | — |
| `caveat_text` | `TEXT` | 예 | — | — | — |
| `section_id` | `BIGINT` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_question__caveat` | `CHECK (caveat_text IS NULL OR btrim(caveat_text) <> '')` |
| CHECK | `ck_node_question__order` | `CHECK (display_order > 0)` |
| CHECK | `ck_node_question__text` | `CHECK (btrim(question_text) <> '' AND btrim(answer_text) <> '')` |
| FOREIGN KEY | `fk_node_question__node_insight_section` | `FOREIGN KEY (section_id) REFERENCES node_insight_section (section_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_question__node_question_set` | `FOREIGN KEY (question_set_id) REFERENCES node_question_set (question_set_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_question` | `PRIMARY KEY (question_id)` |
| UNIQUE | `uq_node_question__order` | `UNIQUE (question_set_id, display_order)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `node_question_claim`

생성물이 사용하는 기존 Claim과 역할·순서. 원문과 사실의 사본을 만들지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `question_id` | `BIGINT` | 아니요 | — | — | — |
| `claim_id` | `BIGINT` | 아니요 | — | — | — |
| `role` | `TEXT` | 아니요 | — | — | — |
| `display_order` | `INTEGER` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_question_claim__order` | `CHECK (display_order > 0)` |
| CHECK | `ck_node_question_claim__role` | `CHECK (role IN ('KEY_CLAIM', 'SUPPORTING_CLAIM', 'CONTRASTING_CLAIM'))` |
| FOREIGN KEY | `fk_node_question_claim__claim` | `FOREIGN KEY (claim_id) REFERENCES claim (claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_question_claim__node_question` | `FOREIGN KEY (question_id) REFERENCES node_question (question_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_question_claim` | `PRIMARY KEY (question_id, claim_id)` |
| UNIQUE | `uq_node_question_claim__order` | `UNIQUE (question_id, display_order)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `node_question_set`

공개 context의 기간별 불변 질문 묶음. 성공한 빈 묶음과 미준비를 구분한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `question_set_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `node_context_id` | `BIGINT` | 아니요 | — | — | — |
| `model_task_id` | `BIGINT` | 아니요 | — | — | — |
| `time_window` | `TEXT` | 아니요 | — | — | — |
| `as_of_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_question_set__as_of` | `CHECK (isfinite(as_of_at))` |
| CHECK | `ck_node_question_set__window` | `CHECK (time_window IN ('RECENT_90_DAYS', 'RECENT_1_YEAR'))` |
| FOREIGN KEY | `fk_node_question_set__model_task` | `FOREIGN KEY (model_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_node_question_set__node_context` | `FOREIGN KEY (node_context_id) REFERENCES node_context (node_context_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_question_set` | `PRIMARY KEY (question_set_id)` |
| UNIQUE | `uq_node_question_set__context_window` | `UNIQUE (node_context_id, time_window)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `node_search_document`

공개 가능한 한 node를 키워드 검색 대상으로 만드는 불변 텍스트 버전. 생성된 node_context를 입력으로 되돌려 넣지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_search_document_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `identity_text` | `TEXT` | 아니요 | — | — | — |
| `knowledge_text` | `TEXT` | 아니요 | — | — | 결정적으로 정렬한 공개 기준 지식의 검색 표현. source 문서 전체나 생성된 맥락 설명을 복사한 필드가 아니다. |
| `input_hash` | `BYTEA` | 아니요 | — | — | — |
| `generator_version` | `TEXT` | 아니요 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_search_document__created_at_finite` | `CHECK (isfinite(created_at))` |
| CHECK | `ck_node_search_document__generator_nonblank` | `CHECK (btrim(generator_version) <> '')` |
| CHECK | `ck_node_search_document__identity_nonempty` | `CHECK (char_length(identity_text) > 0)` |
| CHECK | `ck_node_search_document__input_hash_length` | `CHECK (octet_length(input_hash) = 32)` |
| CHECK | `ck_node_search_document__knowledge_nonempty` | `CHECK (char_length(knowledge_text) > 0)` |
| FOREIGN KEY | `fk_node_search_document__node` | `FOREIGN KEY (node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_node_search_document` | `PRIMARY KEY (node_search_document_id)` |
| UNIQUE | `uq_node_search_document__node_reference` | `UNIQUE (node_search_document_id, node_id)` |
| UNIQUE | `uq_node_search_document__version` | `UNIQUE (node_id, input_hash, generator_version)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_node_search_document__node` | 아니요 | `node_id, node_search_document.created_at DESC, node_search_document.node_search_document_id DESC` | — |

## `node_type`

노드의 안정된 유형 코드와 생성 근거 규칙. is_active는 새 노드 생성 허용 여부이며 기존 노드의 공개 상태가 아니다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_type_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `node_type_code` | `TEXT` | 아니요 | — | — | — |
| `display_name` | `TEXT` | 아니요 | — | — | — |
| `creation_rule` | `TEXT` | 아니요 | — | — | — |
| `is_active` | `BOOLEAN` | 아니요 | `false` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_node_type__code_nonblank` | `CHECK (btrim(node_type_code) <> '')` |
| CHECK | `ck_node_type__creation_rule_nonblank` | `CHECK (btrim(creation_rule) <> '')` |
| CHECK | `ck_node_type__display_name_nonblank` | `CHECK (btrim(display_name) <> '')` |
| PRIMARY KEY | `pk_node_type` | `PRIMARY KEY (node_type_id)` |
| UNIQUE | `uq_node_type__code` | `UNIQUE (node_type_code)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `observation`

불변 source_document의 정확한 Unicode 문자 범위에서 근거를 식별한 기록. 출처의 발화를 증명하지만 객관적 진실을 증명하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `observation_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `source_document_id` | `BIGINT` | 아니요 | — | — | — |
| `start_char` | `INTEGER` | 아니요 | — | — | — |
| `end_char` | `INTEGER` | 아니요 | — | — | — |
| `quote_text` | `TEXT` | 아니요 | — | — | — |
| `quote_hash` | `BYTEA` | 아니요 | — | — | — |
| `paragraph_number` | `INTEGER` | 예 | — | — | — |
| `observed_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | 시스템이 원문 범위를 근거로 식별한 시점. 출처 게시 시점이나 노드 활동량 계산 시점이 아니다. |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_observation__observed_at_finite` | `CHECK (isfinite(observed_at))` |
| CHECK | `ck_observation__paragraph_positive` | `CHECK (paragraph_number IS NULL OR paragraph_number >= 1)` |
| CHECK | `ck_observation__quote_hash_length` | `CHECK (octet_length(quote_hash) = 32)` |
| CHECK | `ck_observation__quote_length` | `CHECK (char_length(quote_text) = end_char - start_char)` |
| CHECK | `ck_observation__range` | `CHECK (end_char > start_char)` |
| CHECK | `ck_observation__start_nonnegative` | `CHECK (start_char >= 0)` |
| FOREIGN KEY | `fk_observation__source_document` | `FOREIGN KEY (source_document_id) REFERENCES source_document (source_document_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_observation` | `PRIMARY KEY (observation_id)` |
| UNIQUE | `uq_observation__document_range` | `UNIQUE (source_document_id, start_char, end_char)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `output_schema_definition`

모델이 반환해야 할 JSON Schema 계약의 불변 버전. 응답 인스턴스나 provider 원문을 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `output_schema_definition_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `task_kind` | `TEXT` | 아니요 | — | — | — |
| `version_no` | `INTEGER` | 아니요 | — | — | — |
| `schema_json` | `JSONB` | 아니요 | — | — | — |
| `is_active` | `BOOLEAN` | 아니요 | `false` | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_output_schema_definition__created_at_finite` | `CHECK (isfinite(created_at))` |
| CHECK | `ck_output_schema_definition__schema_object` | `CHECK (jsonb_typeof(schema_json) = 'object')` |
| CHECK | `ck_output_schema_definition__task_kind` | `CHECK (task_kind IN ('KNOWLEDGE_EXTRACTION', 'ENTITY_RESOLUTION_PROPOSAL', 'EVIDENCE_LINEAGE_PROPOSAL', 'CONFLICT_SUMMARY', 'NODE_CONTEXT', 'FOLLOWUP_QUESTIONS', 'NODE_INSIGHT'))` |
| CHECK | `ck_output_schema_definition__version_positive` | `CHECK (version_no >= 1)` |
| PRIMARY KEY | `pk_output_schema_definition` | `PRIMARY KEY (output_schema_definition_id)` |
| UNIQUE | `uq_output_schema_definition__version` | `UNIQUE (task_kind, version_no)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `uq_output_schema_definition__active` | 예 | `task_kind` | `output_schema_definition.is_active` |

## `promotion_batch`

기준 지식의 원자 승격 결과와 그 변경의 공개 준비 상태를 분리해 관리한다. 전체 활성 온톨로지 snapshot이나 지도 버전을 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `promotion_batch_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `lint_policy_version_id` | `BIGINT` | 아니요 | — | — | — |
| `promotion_status` | `TEXT` | 아니요 | `'PENDING'` | — | — |
| `publication_status` | `TEXT` | 아니요 | `'NOT_STARTED'` | — | 검색 문서·맥락·질문·인사이트의 공개 준비 상태. 기준 그래프 저장 결과인 promotion_status와 별개다. |
| `started_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |
| `committed_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `ready_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `promotion_failure_reason` | `TEXT` | 예 | — | — | — |
| `publication_failure_reason` | `TEXT` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_promotion_batch__committed_order` | `CHECK (committed_at IS NULL OR committed_at >= started_at)` |
| CHECK | `ck_promotion_batch__promotion_failure_nonblank` | `CHECK (promotion_failure_reason IS NULL OR btrim(promotion_failure_reason) <> '')` |
| CHECK | `ck_promotion_batch__promotion_status` | `CHECK (promotion_status IN ('PENDING', 'COMMITTED', 'FAILED'))` |
| CHECK | `ck_promotion_batch__publication_failure_nonblank` | `CHECK (publication_failure_reason IS NULL OR btrim(publication_failure_reason) <> '')` |
| CHECK | `ck_promotion_batch__publication_status` | `CHECK (publication_status IN ('NOT_STARTED', 'PREPARING', 'READY', 'FAILED'))` |
| CHECK | `ck_promotion_batch__ready_order` | `CHECK (ready_at IS NULL OR (committed_at IS NOT NULL AND ready_at >= committed_at))` |
| CHECK | `ck_promotion_batch__state_shape` | `CHECK ((promotion_status = 'PENDING' AND publication_status = 'NOT_STARTED' AND committed_at IS NULL AND ready_at IS NULL AND promotion_failure_reason IS NULL AND publication_failure_reason IS NULL) OR (promotion_status = 'FAILED' AND publication_status = 'NOT_STARTED' AND committed_at IS NULL AND ready_at IS NULL AND promotion_failure_reason IS NOT NULL AND publication_failure_reason IS NULL) OR (promotion_status = 'COMMITTED' AND publication_status = 'NOT_STARTED' AND committed_at IS NOT NULL AND ready_at IS NULL AND promotion_failure_reason IS NULL AND publication_failure_reason IS NULL) OR (promotion_status = 'COMMITTED' AND publication_status = 'PREPARING' AND committed_at IS NOT NULL AND ready_at IS NULL AND promotion_failure_reason IS NULL AND publication_failure_reason IS NULL) OR (promotion_status = 'COMMITTED' AND publication_status = 'FAILED' AND committed_at IS NOT NULL AND ready_at IS NULL AND promotion_failure_reason IS NULL AND publication_failure_reason IS NOT NULL) OR (promotion_status = 'COMMITTED' AND publication_status = 'READY' AND committed_at IS NOT NULL AND ready_at IS NOT NULL AND promotion_failure_reason IS NULL AND publication_failure_reason IS NULL))` |
| CHECK | `ck_promotion_batch__timestamps_finite` | `CHECK (isfinite(started_at) AND (committed_at IS NULL OR isfinite(committed_at)) AND (ready_at IS NULL OR isfinite(ready_at)))` |
| FOREIGN KEY | `fk_promotion_batch__lint_policy_version` | `FOREIGN KEY (lint_policy_version_id) REFERENCES lint_policy_version (lint_policy_version_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_promotion_batch` | `PRIMARY KEY (promotion_batch_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_promotion_batch__promotion_pending` | 아니요 | `started_at, promotion_batch_id` | `promotion_batch.promotion_status = 'PENDING'` |
| `ix_promotion_batch__publication_work` | 아니요 | `publication_status, promotion_batch_id` | `promotion_batch.promotion_status = 'COMMITTED' AND promotion_batch.publication_status IN ('NOT_STARTED', 'PREPARING', 'FAILED')` |
| `ix_promotion_batch__ready` | 아니요 | `promotion_batch.ready_at DESC, promotion_batch.promotion_batch_id DESC` | `promotion_batch.promotion_status = 'COMMITTED' AND promotion_batch.publication_status = 'READY'` |

## `promotion_canonical_change`

promotion transaction이 기존 canonical object를 재사용하면서 실제로 추가한 association/change의 불변 batch provenance. publication 상태나 affected node, workflow attempt, 임의 payload를 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `promotion_canonical_change_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `promotion_batch_id` | `BIGINT` | 아니요 | — | — | — |
| `change_kind` | `TEXT` | 아니요 | — | — | — |
| `node_alias_id` | `BIGINT` | 예 | — | — | — |
| `observation_id` | `BIGINT` | 예 | — | — | — |
| `claim_id` | `BIGINT` | 예 | — | — | — |
| `relation_id` | `BIGINT` | 예 | — | — | — |
| `claim_attribute_value_id` | `BIGINT` | 예 | — | — | — |
| `event_node_id` | `BIGINT` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_promotion_canonical_change__kind` | `CHECK (change_kind IN ('NODE_ALIAS_CHANGED', 'NODE_ALIAS_EVIDENCE_ADDED', 'CLAIM_OBSERVATION_ADDED', 'CLAIM_RELATION_ADDED', 'CLAIM_ATTRIBUTE_VALUE_ADDED', 'EVENT_TEMPORAL_BASIS_ADDED'))` |
| CHECK | `ck_promotion_canonical_change__target_shape` | `CHECK ((change_kind = 'NODE_ALIAS_CHANGED' AND node_alias_id IS NOT NULL AND observation_id IS NULL AND claim_id IS NULL AND relation_id IS NULL AND claim_attribute_value_id IS NULL AND event_node_id IS NULL) OR (change_kind = 'NODE_ALIAS_EVIDENCE_ADDED' AND node_alias_id IS NOT NULL AND observation_id IS NOT NULL AND claim_id IS NULL AND relation_id IS NULL AND claim_attribute_value_id IS NULL AND event_node_id IS NULL) OR (change_kind = 'CLAIM_OBSERVATION_ADDED' AND node_alias_id IS NULL AND observation_id IS NOT NULL AND claim_id IS NOT NULL AND relation_id IS NULL AND claim_attribute_value_id IS NULL AND event_node_id IS NULL) OR (change_kind = 'CLAIM_RELATION_ADDED' AND node_alias_id IS NULL AND observation_id IS NULL AND claim_id IS NOT NULL AND relation_id IS NOT NULL AND claim_attribute_value_id IS NULL AND event_node_id IS NULL) OR (change_kind = 'CLAIM_ATTRIBUTE_VALUE_ADDED' AND node_alias_id IS NULL AND observation_id IS NULL AND claim_id IS NULL AND relation_id IS NULL AND claim_attribute_value_id IS NOT NULL AND event_node_id IS NULL) OR (change_kind = 'EVENT_TEMPORAL_BASIS_ADDED' AND node_alias_id IS NULL AND observation_id IS NULL AND claim_id IS NOT NULL AND relation_id IS NULL AND claim_attribute_value_id IS NULL AND event_node_id IS NOT NULL))` |
| FOREIGN KEY | `fk_promotion_canonical_change__attribute_value` | `FOREIGN KEY (claim_attribute_value_id) REFERENCES claim_attribute_value (claim_attribute_value_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_promotion_canonical_change__claim_observation` | `FOREIGN KEY (claim_id, observation_id) REFERENCES claim_observation (claim_id, observation_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_promotion_canonical_change__claim_relation` | `FOREIGN KEY (claim_id, relation_id) REFERENCES claim_relation (claim_id, relation_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_promotion_canonical_change__event_temporal_basis` | `FOREIGN KEY (event_node_id, claim_id) REFERENCES event_temporal_basis (event_node_id, claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_promotion_canonical_change__node_alias` | `FOREIGN KEY (node_alias_id) REFERENCES node_alias (node_alias_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_promotion_canonical_change__node_alias_evidence` | `FOREIGN KEY (node_alias_id, observation_id) REFERENCES node_alias_evidence (node_alias_id, observation_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_promotion_canonical_change__promotion_batch` | `FOREIGN KEY (promotion_batch_id) REFERENCES promotion_batch (promotion_batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_promotion_canonical_change` | `PRIMARY KEY (promotion_canonical_change_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_promotion_canonical_change__batch` | 아니요 | `promotion_batch_id, promotion_canonical_change_id` | — |
| `uq_promotion_canonical_change__claim_attribute_value_added` | 예 | `promotion_batch_id, claim_attribute_value_id` | `change_kind = 'CLAIM_ATTRIBUTE_VALUE_ADDED'` |
| `uq_promotion_canonical_change__claim_observation_added` | 예 | `promotion_batch_id, claim_id, observation_id` | `change_kind = 'CLAIM_OBSERVATION_ADDED'` |
| `uq_promotion_canonical_change__claim_relation_added` | 예 | `promotion_batch_id, claim_id, relation_id` | `change_kind = 'CLAIM_RELATION_ADDED'` |
| `uq_promotion_canonical_change__event_temporal_basis_added` | 예 | `promotion_batch_id, event_node_id, claim_id` | `change_kind = 'EVENT_TEMPORAL_BASIS_ADDED'` |
| `uq_promotion_canonical_change__node_alias_changed` | 예 | `promotion_batch_id, node_alias_id` | `change_kind = 'NODE_ALIAS_CHANGED'` |
| `uq_promotion_canonical_change__node_alias_evidence_added` | 예 | `promotion_batch_id, node_alias_id, observation_id` | `change_kind = 'NODE_ALIAS_EVIDENCE_ADDED'` |

## `provider_call_slot`

전송 전 durable하게 소비하는 제품 provider 호출 슬롯. UNKNOWN도 예산을 소비하며 재사용하지 않는다. 모델 입력·응답·결과 payload는 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `model_task_id` | `BIGINT` | 아니요 | — | — | — |
| `slot_no` | `INTEGER` | 아니요 | — | — | — |
| `state` | `TEXT` | 아니요 | — | — | — |
| `reserved_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | — |
| `resolved_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_provider_call_slot__number` | `CHECK (slot_no BETWEEN 1 AND 3)` |
| CHECK | `ck_provider_call_slot__state` | `CHECK (state IN ('RESERVED', 'COMPLETED', 'UNKNOWN'))` |
| CHECK | `ck_provider_call_slot__state_shape` | `CHECK ((state = 'RESERVED' AND resolved_at IS NULL) OR (state IN ('COMPLETED', 'UNKNOWN') AND resolved_at IS NOT NULL))` |
| CHECK | `ck_provider_call_slot__timestamps` | `CHECK (isfinite(reserved_at) AND (resolved_at IS NULL OR (isfinite(resolved_at) AND resolved_at >= reserved_at)))` |
| FOREIGN KEY | `fk_provider_call_slot__model_task` | `FOREIGN KEY (model_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_provider_call_slot` | `PRIMARY KEY (model_task_id, slot_no)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `publication_affected_node`

한 batch의 공개 준비 영향 범위와 최종 선택 artifact·인사이트 작업을 저장한다. 지도 구성원·좌표·전체 공개 그래프 snapshot이 아니다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `promotion_batch_id` | `BIGINT` | 아니요 | — | — | — |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `node_search_document_id` | `BIGINT` | 예 | — | — | — |
| `node_context_id` | `BIGINT` | 예 | — | — | — |
| `node_insight_model_task_id` | `BIGINT` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_publication_affected_node__context_document` | `CHECK (node_context_id IS NULL OR node_search_document_id IS NOT NULL)` |
| FOREIGN KEY | `fk_publication_affected_node__batch` | `FOREIGN KEY (promotion_batch_id) REFERENCES promotion_batch (promotion_batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_publication_affected_node__context` | `FOREIGN KEY (node_context_id, node_search_document_id, node_id) REFERENCES node_context (node_context_id, node_search_document_id, node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_publication_affected_node__insight_task` | `FOREIGN KEY (node_insight_model_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_publication_affected_node__node` | `FOREIGN KEY (node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_publication_affected_node__search_document` | `FOREIGN KEY (node_search_document_id, node_id) REFERENCES node_search_document (node_search_document_id, node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_publication_affected_node` | `PRIMARY KEY (promotion_batch_id, node_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_publication_affected_node__node` | 아니요 | `node_id, publication_affected_node.promotion_batch_id DESC` | — |

## `relation`

정확한 relation revision으로 두 node를 연결하는 기준 연결. 출처·사건 맥락·유효 기간은 직접 저장하지 않고 Claim과 명시적 사건 endpoint로 표현한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `relation_id` | `BIGINT` | 아니요 | — | — | — |
| `source_node_id` | `BIGINT` | 아니요 | — | — | — |
| `target_node_id` | `BIGINT` | 아니요 | — | — | — |
| `relation_type_revision_id` | `BIGINT` | 아니요 | — | — | — |
| `relation_identity_key` | `BYTEA` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_relation__identity_length` | `CHECK (octet_length(relation_identity_key) = 32)` |
| FOREIGN KEY | `fk_relation__knowledge_item` | `FOREIGN KEY (relation_id) REFERENCES knowledge_item (knowledge_item_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_relation__source_node` | `FOREIGN KEY (source_node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_relation__target_node` | `FOREIGN KEY (target_node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_relation__type_revision` | `FOREIGN KEY (relation_type_revision_id) REFERENCES relation_type_revision (relation_type_revision_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_relation` | `PRIMARY KEY (relation_id)` |
| UNIQUE | `uq_relation__identity` | `UNIQUE (relation_identity_key)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_relation__source` | 아니요 | `source_node_id, relation_type_revision_id, target_node_id` | — |
| `ix_relation__target` | 아니요 | `target_node_id, relation_type_revision_id, source_node_id` | — |

## `relation_endpoint_rule`

관계 revision이 허용하는 시작·도착 node type 쌍. 실제 관계 endpoint 검증은 승격 서비스가 수행한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `relation_type_revision_id` | `BIGINT` | 아니요 | — | — | — |
| `source_node_type_id` | `BIGINT` | 아니요 | — | — | — |
| `target_node_type_id` | `BIGINT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | `fk_relation_endpoint_rule__revision` | `FOREIGN KEY (relation_type_revision_id) REFERENCES relation_type_revision (relation_type_revision_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_relation_endpoint_rule__source_type` | `FOREIGN KEY (source_node_type_id) REFERENCES node_type (node_type_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_relation_endpoint_rule__target_type` | `FOREIGN KEY (target_node_type_id) REFERENCES node_type (node_type_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_relation_endpoint_rule` | `PRIMARY KEY (relation_type_revision_id, source_node_type_id, target_node_type_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `relation_type`

관계 의미를 식별하는 안정된 코드 테이블. 활성 상태와 방향·endpoint 규칙은 revision이 소유한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `relation_type_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `relation_code` | `TEXT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_relation_type__code_nonblank` | `CHECK (btrim(relation_code) <> '')` |
| PRIMARY KEY | `pk_relation_type` | `PRIMARY KEY (relation_type_id)` |
| UNIQUE | `uq_relation_type__code` | `UNIQUE (relation_code)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |

## `relation_type_revision`

관계 표시·방향·inverse 계약의 불변 버전. 비활성화는 기존 관계를 숨기지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `relation_type_revision_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `relation_type_id` | `BIGINT` | 아니요 | — | — | — |
| `version_no` | `INTEGER` | 아니요 | — | — | — |
| `display_name` | `TEXT` | 아니요 | — | — | — |
| `directionality` | `TEXT` | 아니요 | — | — | — |
| `inverse_relation_type_revision_id` | `BIGINT` | 예 | — | — | — |
| `is_active` | `BOOLEAN` | 아니요 | `false` | — | 새 관계를 생성할 때 사용할 수 있는 exact revision인지 표시한다. 기존 관계를 숨기거나 재해석하지 않는다. |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_relation_type_revision__directionality` | `CHECK (directionality IN ('DIRECTED', 'SYMMETRIC'))` |
| CHECK | `ck_relation_type_revision__display_name_nonblank` | `CHECK (btrim(display_name) <> '')` |
| CHECK | `ck_relation_type_revision__not_self_inverse` | `CHECK (inverse_relation_type_revision_id IS NULL OR inverse_relation_type_revision_id <> relation_type_revision_id)` |
| CHECK | `ck_relation_type_revision__symmetric_inverse` | `CHECK (directionality <> 'SYMMETRIC' OR inverse_relation_type_revision_id IS NULL)` |
| CHECK | `ck_relation_type_revision__version_positive` | `CHECK (version_no >= 1)` |
| FOREIGN KEY | `fk_relation_type_revision__inverse` | `FOREIGN KEY (inverse_relation_type_revision_id) REFERENCES relation_type_revision (relation_type_revision_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_relation_type_revision__relation_type` | `FOREIGN KEY (relation_type_id) REFERENCES relation_type (relation_type_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_relation_type_revision` | `PRIMARY KEY (relation_type_revision_id)` |
| UNIQUE | `uq_relation_type_revision__version` | `UNIQUE (relation_type_id, version_no)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_relation_type_revision__inverse` | 아니요 | `inverse_relation_type_revision_id` | `relation_type_revision.inverse_relation_type_revision_id IS NOT NULL` |
| `uq_relation_type_revision__active` | 예 | `relation_type_id` | `relation_type_revision.is_active` |

## `search_document_basis`

검색 문서 생성에 기여한 공개 기준 지식 계보. 검색 점수의 문장별 인과 설명이 아니다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_search_document_id` | `BIGINT` | 아니요 | — | — | — |
| `knowledge_item_id` | `BIGINT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | `fk_search_document_basis__knowledge_item` | `FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_item (knowledge_item_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_search_document_basis__search_document` | `FOREIGN KEY (node_search_document_id) REFERENCES node_search_document (node_search_document_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_search_document_basis` | `PRIMARY KEY (node_search_document_id, knowledge_item_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_search_document_basis__knowledge_item` | 아니요 | `knowledge_item_id, node_search_document_id` | — |

## `source_claim`

정확한 Observation이 statement와 modality를 지지한다고 검증된 불변 Claim. graph 투영 성공과 독립된 제품 결과이며 provider 원문이나 reasoning이 아니다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `source_claim_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `model_task_id` | `BIGINT` | 아니요 | — | — | — |
| `candidate_id` | `TEXT` | 아니요 | — | — | — |
| `statement_text` | `TEXT` | 아니요 | — | — | — |
| `language` | `TEXT` | 아니요 | — | — | — |
| `modality` | `TEXT` | 아니요 | — | — | — |
| `projection_status` | `TEXT` | 아니요 | — | — | — |
| `proposal_json` | `JSONB` | 아니요 | — | — | — |
| `projection_outcomes_json` | `JSONB` | 아니요 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_source_claim__candidate_nonblank` | `CHECK (btrim(candidate_id) <> '')` |
| CHECK | `ck_source_claim__created_at_finite` | `CHECK (isfinite(created_at))` |
| CHECK | `ck_source_claim__language_nonblank` | `CHECK (btrim(language) <> '')` |
| CHECK | `ck_source_claim__modality` | `CHECK (modality IN ('FACT', 'PLAN_OR_TARGET', 'PREDICTION_OR_ESTIMATE', 'OPINION_OR_EVALUATION'))` |
| CHECK | `ck_source_claim__outcomes_object` | `CHECK (jsonb_typeof(projection_outcomes_json) = 'object')` |
| CHECK | `ck_source_claim__projection_status` | `CHECK (projection_status IN ('NONE', 'PARTIAL', 'COMPLETE'))` |
| CHECK | `ck_source_claim__proposal_object` | `CHECK (jsonb_typeof(proposal_json) = 'object')` |
| CHECK | `ck_source_claim__statement_nonblank` | `CHECK (btrim(statement_text) <> '')` |
| FOREIGN KEY | `fk_source_claim__model_task` | `FOREIGN KEY (model_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_source_claim` | `PRIMARY KEY (source_claim_id)` |
| UNIQUE | `uq_source_claim__task_candidate` | `UNIQUE (model_task_id, candidate_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_source_claim__projection_status` | 아니요 | `projection_status, source_claim_id` | — |
| `ix_source_claim__task` | 아니요 | `model_task_id, source_claim_id` | — |

## `source_claim_observation`

Source Claim과 이를 직접 지지하는 정확한 원문 범위의 Evidence Trace.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `source_claim_id` | `BIGINT` | 아니요 | — | — | — |
| `observation_id` | `BIGINT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | `fk_source_claim_observation__observation` | `FOREIGN KEY (observation_id) REFERENCES observation (observation_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_source_claim_observation__source_claim` | `FOREIGN KEY (source_claim_id) REFERENCES source_claim (source_claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_source_claim_observation` | `PRIMARY KEY (source_claim_id, observation_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_source_claim_observation__observation` | 아니요 | `observation_id, source_claim_id` | — |

## `source_claim_projection`

Source Claim에서 기존 공개 graph 수명주기의 canonical Claim으로 성공한 투영 연결. NONE 상태에는 행이 없다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `source_claim_id` | `BIGINT` | 아니요 | — | — | — |
| `claim_id` | `BIGINT` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | `fk_source_claim_projection__claim` | `FOREIGN KEY (claim_id) REFERENCES claim (claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_source_claim_projection__source_claim` | `FOREIGN KEY (source_claim_id) REFERENCES source_claim (source_claim_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_source_claim_projection` | `PRIMARY KEY (source_claim_id, claim_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_source_claim_projection__claim` | 아니요 | `claim_id, source_claim_id` | — |

## `source_document`

제품 밖에서 준비한 정규화 문서의 불변 버전. 발견·GDELT·크롤링·HTML·HTTP 시도는 저장하지 않는다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `source_document_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `evidence_group_id` | `BIGINT` | 아니요 | — | — | 현재 독립 근거 계보 묶음. 재배정 이력은 보존하지 않으며 승인된 정정 경로만 수정할 수 있다. |
| `source_key` | `TEXT` | 아니요 | — | — | — |
| `version_no` | `INTEGER` | 아니요 | — | — | — |
| `canonical_url` | `TEXT` | 예 | — | — | — |
| `publisher_name` | `TEXT` | 아니요 | — | — | — |
| `title` | `TEXT` | 아니요 | — | — | — |
| `author_text` | `TEXT` | 예 | — | — | — |
| `original_language` | `TEXT` | 아니요 | — | — | — |
| `normalized_body` | `TEXT` | 아니요 | — | — | — |
| `body_hash` | `BYTEA` | 아니요 | — | — | normalized_body의 UTF-8 바이트 SHA-256. 문서 행을 합치는 키가 아니라 정확한 본문 복제 후보와 근거 묶음 판정에 사용한다. |
| `published_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `published_precision` | `TEXT` | 아니요 | — | — | — |
| `source_modified_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `modified_precision` | `TEXT` | 아니요 | — | — | — |
| `last_checked_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | — | — | 준비 레이어가 같은 자료를 마지막으로 확인한 시점. 출처 게시 시점이나 노드 활동량 계산 시점이 아니다. |
| `last_check_status` | `TEXT` | 아니요 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_source_document__author_nonblank` | `CHECK (author_text IS NULL OR btrim(author_text) <> '')` |
| CHECK | `ck_source_document__body_hash_length` | `CHECK (octet_length(body_hash) = 32)` |
| CHECK | `ck_source_document__body_nonempty` | `CHECK (char_length(normalized_body) > 0)` |
| CHECK | `ck_source_document__language_nonblank` | `CHECK (btrim(original_language) <> '')` |
| CHECK | `ck_source_document__last_check_status` | `CHECK (last_check_status IN ('SUCCESS', 'FAILED'))` |
| CHECK | `ck_source_document__modified_precision` | `CHECK (modified_precision IN ('INSTANT', 'DAY', 'MONTH', 'YEAR', 'UNKNOWN'))` |
| CHECK | `ck_source_document__modified_value_precision` | `CHECK ((source_modified_at IS NULL AND modified_precision = 'UNKNOWN') OR (source_modified_at IS NOT NULL AND modified_precision <> 'UNKNOWN'))` |
| CHECK | `ck_source_document__published_precision` | `CHECK (published_precision IN ('INSTANT', 'DAY', 'MONTH', 'YEAR', 'UNKNOWN'))` |
| CHECK | `ck_source_document__published_value_precision` | `CHECK ((published_at IS NULL AND published_precision = 'UNKNOWN') OR (published_at IS NOT NULL AND published_precision <> 'UNKNOWN'))` |
| CHECK | `ck_source_document__publisher_nonblank` | `CHECK (btrim(publisher_name) <> '')` |
| CHECK | `ck_source_document__source_key_nonblank` | `CHECK (btrim(source_key) <> '')` |
| CHECK | `ck_source_document__timestamps_finite` | `CHECK ((published_at IS NULL OR isfinite(published_at)) AND (source_modified_at IS NULL OR isfinite(source_modified_at)) AND isfinite(last_checked_at) AND isfinite(created_at))` |
| CHECK | `ck_source_document__title_nonblank` | `CHECK (btrim(title) <> '')` |
| CHECK | `ck_source_document__url_nonblank` | `CHECK (canonical_url IS NULL OR btrim(canonical_url) <> '')` |
| CHECK | `ck_source_document__version_positive` | `CHECK (version_no >= 1)` |
| FOREIGN KEY | `fk_source_document__evidence_group` | `FOREIGN KEY (evidence_group_id) REFERENCES evidence_group (evidence_group_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_source_document` | `PRIMARY KEY (source_document_id)` |
| UNIQUE | `uq_source_document__version` | `UNIQUE (source_key, version_no)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_source_document__body_hash` | 아니요 | `body_hash` | — |
| `ix_source_document__evidence_group` | 아니요 | `evidence_group_id, source_document_id` | — |

## `source_processing_job`

업로드된 source_document 하나를 추출·승격·공개하는 제품 작업. provider 응답이나 원문을 저장하지 않고 현재 단계와 안전한 실패 코드만 보존한다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `source_processing_job_id` | `BIGINT` | 아니요 | — | `GENERATED ALWAYS AS IDENTITY` | — |
| `source_document_id` | `BIGINT` | 아니요 | — | — | — |
| `status` | `TEXT` | 아니요 | `'QUEUED'` | — | — |
| `stage` | `TEXT` | 아니요 | `'QUEUED'` | — | — |
| `extraction_task_id` | `BIGINT` | 예 | — | — | — |
| `promotion_batch_id` | `BIGINT` | 예 | — | — | — |
| `error_code` | `TEXT` | 예 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `CURRENT_TIMESTAMP` | — | — |
| `started_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |
| `finished_at` | `TIMESTAMP WITH TIME ZONE` | 예 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_source_processing_job__error_code` | `CHECK (error_code IS NULL OR (btrim(error_code) <> '' AND error_code ~ '^[A-Z][A-Z0-9_]*$'))` |
| CHECK | `ck_source_processing_job__finished_order` | `CHECK (finished_at IS NULL OR finished_at >= COALESCE(started_at, created_at))` |
| CHECK | `ck_source_processing_job__stage_nonblank` | `CHECK (btrim(stage) <> '')` |
| CHECK | `ck_source_processing_job__started_order` | `CHECK (started_at IS NULL OR started_at >= created_at)` |
| CHECK | `ck_source_processing_job__state_shape` | `CHECK ((status = 'QUEUED' AND started_at IS NULL AND finished_at IS NULL AND error_code IS NULL) OR (status = 'RUNNING' AND started_at IS NOT NULL AND finished_at IS NULL AND error_code IS NULL) OR (status = 'READY' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND error_code IS NULL) OR (status = 'FAILED' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND error_code IS NOT NULL) OR (status = 'INTERRUPTED' AND finished_at IS NOT NULL AND error_code IS NOT NULL) OR (status = 'EXCLUDED_LANGUAGE' AND finished_at IS NOT NULL AND error_code IS NULL))` |
| CHECK | `ck_source_processing_job__status` | `CHECK (status IN ('QUEUED', 'RUNNING', 'READY', 'FAILED', 'INTERRUPTED', 'EXCLUDED_LANGUAGE'))` |
| CHECK | `ck_source_processing_job__timestamps_finite` | `CHECK (isfinite(created_at) AND (started_at IS NULL OR isfinite(started_at)) AND (finished_at IS NULL OR isfinite(finished_at)))` |
| FOREIGN KEY | `fk_source_processing_job__extraction_task` | `FOREIGN KEY (extraction_task_id) REFERENCES model_task (model_task_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_source_processing_job__promotion_batch` | `FOREIGN KEY (promotion_batch_id) REFERENCES promotion_batch (promotion_batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| FOREIGN KEY | `fk_source_processing_job__source_document` | `FOREIGN KEY (source_document_id) REFERENCES source_document (source_document_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_source_processing_job` | `PRIMARY KEY (source_processing_job_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_source_processing_job__latest` | 아니요 | `source_document_id, source_processing_job.source_processing_job_id DESC` | — |
| `uq_source_processing_job__reusable` | 예 | `source_document_id` | `source_processing_job.status IN ('QUEUED', 'RUNNING', 'READY', 'EXCLUDED_LANGUAGE')` |

## `topic_reference`

TOPIC Node identity에 연결된 제품 controlled vocabulary 정의. canonical 이름과 active mapping 여부의 source of truth이며 Evidence가 아니다.

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `topic_code` | `TEXT` | 아니요 | — | — | — |
| `canonical_display_name` | `TEXT` | 아니요 | — | — | — |
| `is_active` | `BOOLEAN` | 아니요 | `false` | — | 새 HAS_TOPIC mapping의 target으로 사용할 수 있는지 나타낸다. 비활성화는 기존 relation의 의미·공개 여부를 바꾸지 않는다. |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| CHECK | `ck_topic_reference__approved_definition` | `CHECK ((topic_code = 'SEMICONDUCTOR' AND canonical_display_name = '반도체') OR (topic_code = 'MEMORY_SEMICONDUCTOR' AND canonical_display_name = '메모리 반도체') OR (topic_code = 'ADVANCED_PACKAGING' AND canonical_display_name = '첨단 패키징') OR (topic_code = 'ARTIFICIAL_INTELLIGENCE' AND canonical_display_name = '인공지능') OR (topic_code = 'DATA_CENTER' AND canonical_display_name = '데이터센터') OR (topic_code = 'MANUFACTURING_PROCESS' AND canonical_display_name = '제조 공정') OR (topic_code = 'INVESTMENT' AND canonical_display_name = '투자') OR (topic_code = 'COMMERCIALIZATION' AND canonical_display_name = '상용화') OR (topic_code = 'REGULATION_POLICY' AND canonical_display_name = '규제·정책'))` |
| CHECK | `ck_topic_reference__code_nonblank` | `CHECK (btrim(topic_code) <> '')` |
| CHECK | `ck_topic_reference__display_name_nonblank` | `CHECK (btrim(canonical_display_name) <> '')` |
| FOREIGN KEY | `fk_topic_reference__node` | `FOREIGN KEY (node_id) REFERENCES node (node_id) ON DELETE RESTRICT ON UPDATE RESTRICT` |
| PRIMARY KEY | `pk_topic_reference` | `PRIMARY KEY (node_id)` |
| UNIQUE | `uq_topic_reference__code` | `UNIQUE (topic_code)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| — | — | — | — |
