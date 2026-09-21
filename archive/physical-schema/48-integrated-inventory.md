# #48 통합 PostgreSQL 테이블 inventory

## 문서 상태

- 관련 Issue: #48
- 선행 설계: #40–#47, #69, #78
- 상태: 통합 검토 기준
- 동반 문서: [`48-integrity-migration-tests.md`](48-integrity-migration-tests.md)

이 문서는 영역별 물리 설계를 하나의 구현 inventory로 합친다. 각 테이블의 세부 CHECK 문장과 한국어 `COMMENT ON` 원문은 소유 문서를 따르되, 이 문서의 테이블·컬럼·키·초기 인덱스 목록과 #48 정정 사항이 최종 구현 범위를 결정한다.

## 1. 표기

```text
!  NOT NULL
?  NULL 허용
ID GENERATED ALWAYS AS IDENTITY
D  승인된 비차단 보류로 초기 migration에서 제외 가능
```

공통 규칙:

- 별도 언급이 없는 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.
- 모든 application table에는 한국어 `COMMENT ON TABLE`을 둔다.
- 혼동 가능성이 있는 컬럼·복잡한 CHECK·서비스 경계에는 소유 문서의 `COMMENT ON COLUMN/CONSTRAINT`를 적용한다.
- PK와 UNIQUE가 이미 만든 B-tree 인덱스를 중복 생성하지 않는다.

## 2. 전체 수량

| 구분 | 수량 |
|---|---:|
| 물리 설계에 정의된 테이블 | 43 |
| 초기 migration 대상 | 41 |
| actor 계약까지 비차단 보류 | 2 |

초기 migration에서 보류 가능한 테이블은 `knowledge_state_event`, `conflict_state_event`다. 나머지 41개는 구현 대상이다.

다음 폐기 구조는 만들지 않는다.

```text
ontology_version
ontology_member
evidence_group_assignment
display_rule_version
candidate_item
candidate_state_event
structured_output
```

---

# 3. 준비 문서와 원문 근거

소유 문서: [`41-prepared-evidence.md`](41-prepared-evidence.md)

## 3.1 `evidence_group`

| 항목 | 내용 |
|---|---|
| 컬럼 | `evidence_group_id bigint! ID`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(evidence_group_id)` |
| CHECK | `created_at` finite |
| 초기 인덱스 | PK만 사용 |
| 주석 목적 | 같은 원문 계보를 독립 근거 하나로 세는 최소 묶음이며 출처 신뢰도나 진실성 점수가 아님 |

## 3.2 `source_document`

| 항목 | 내용 |
|---|---|
| 컬럼 | `source_document_id bigint! ID`, `evidence_group_id bigint!`, `source_key text!`, `version_no integer!`, `canonical_url text!`, `publisher_name text!`, `title text!`, `author_text text?`, `original_language text!`, `normalized_body text!`, `body_hash bytea!`, `published_at timestamptz?`, `published_precision text!`, `source_modified_at timestamptz?`, `modified_precision text!`, `last_checked_at timestamptz!`, `last_check_status text!`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(source_document_id)` |
| FK | `evidence_group_id → evidence_group` |
| UNIQUE | `(source_key, version_no)` |
| CHECK | `version_no >= 1`; 필수 text nonblank; body nonempty; `body_hash` 32 bytes; 게시·수정 시각과 precision의 `NULL/UNKNOWN` 일치; finite timestamp; `last_check_status IN ('SUCCESS','FAILED')` |
| 초기 인덱스 | `(body_hash)`; `(evidence_group_id, source_document_id)`; version UNIQUE 인덱스 재사용 |
| 주석 목적 | 제품 밖에서 준비한 정규화 문서의 불변 버전이며 발견·크롤링·HTTP 이력을 저장하지 않음 |

## 3.3 `observation`

| 항목 | 내용 |
|---|---|
| 컬럼 | `observation_id bigint! ID`, `source_document_id bigint!`, `start_char integer!`, `end_char integer!`, `quote_text text!`, `quote_hash bytea!`, `paragraph_number integer?`, `observed_at timestamptz!` |
| PK | `(observation_id)` |
| FK | `source_document_id → source_document` |
| UNIQUE | `(source_document_id, start_char, end_char)` |
| CHECK | `start_char >= 0`; `end_char > start_char`; quote 길이와 범위 길이 일치; `quote_hash` 32 bytes; 문단 번호 양수; `observed_at` finite |
| 초기 인덱스 | document-range UNIQUE만 사용. 별도 `(source_document_id, observation_id)` 인덱스는 초기 migration에서 제외 |
| 주석 목적 | 불변 문서의 정확한 Unicode 문자 범위에서 근거를 식별했음을 나타내며 객관적 진실 판정은 아님 |

---

# 4. 온톨로지 코드와 노드 정체성

소유 문서:

- [`42-ontology-node-identity.md`](42-ontology-node-identity.md)
- [`42-merge-event-time.md`](42-merge-event-time.md)
- [`42-validation-ownership.md`](42-validation-ownership.md)

## 4.1 `node_type`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_type_id bigint! ID`, `node_type_code text!`, `display_name text!`, `creation_rule text!`, `is_active boolean! = false` |
| PK | `(node_type_id)` |
| UNIQUE | `(node_type_code)` |
| CHECK | code·display name·creation rule nonblank |
| 초기 인덱스 | PK·code UNIQUE만 사용. `is_active` 단독 인덱스 없음 |
| 주석 목적 | 안정된 노드 유형과 생성 규칙. `is_active`는 새 노드 생성 허용 여부일 뿐 기존 노드 공개 상태가 아님 |

## 4.2 `relation_type`

| 항목 | 내용 |
|---|---|
| 컬럼 | `relation_type_id bigint! ID`, `relation_code text!` |
| PK | `(relation_type_id)` |
| UNIQUE | `(relation_code)` |
| CHECK | relation code nonblank |
| 초기 인덱스 | PK·code UNIQUE만 사용 |
| 주석 목적 | 관계 의미의 안정된 코드. 활성 상태와 방향·endpoint는 revision이 소유 |

## 4.3 `relation_type_revision`

| 항목 | 내용 |
|---|---|
| 컬럼 | `relation_type_revision_id bigint! ID`, `relation_type_id bigint!`, `version_no integer!`, `display_name text!`, `directionality text!`, `inverse_relation_type_revision_id bigint?`, `is_active boolean! = false` |
| PK | `(relation_type_revision_id)` |
| FK | `relation_type_id → relation_type`; self FK `inverse_relation_type_revision_id → relation_type_revision` |
| UNIQUE | `(relation_type_id, version_no)`; 활성 revision은 `relation_type_id`당 최대 한 건 |
| CHECK | `version_no >= 1`; display name nonblank; `directionality IN ('DIRECTED','SYMMETRIC')`; self inverse 금지; symmetric이면 inverse `NULL` |
| 초기 인덱스 | inverse 값이 있는 행의 `(inverse_relation_type_revision_id)`; version·active UNIQUE 재사용 |
| 주석 목적 | 관계 표시·방향·inverse 계약의 불변 버전이며 비활성화가 기존 관계를 숨기지 않음 |

## 4.4 `relation_endpoint_rule`

| 항목 | 내용 |
|---|---|
| 컬럼 | `relation_type_revision_id bigint!`, `source_node_type_id bigint!`, `target_node_type_id bigint!` |
| PK | `(relation_type_revision_id, source_node_type_id, target_node_type_id)` |
| FK | revision과 두 `node_type` FK |
| CHECK | 모든 행에 ID 대소 비교 CHECK를 두지 않음 |
| 초기 인덱스 | PK만 사용. node type 역탐색 인덱스는 실제 API가 생길 때 검토 |
| 주석 목적 | 한 relation revision이 허용하는 endpoint 유형 쌍 |

#48 정정: symmetric endpoint의 ID 정렬과 반대 방향 중복 금지는 연결 revision의 `directionality`를 읽는 서비스가 담당한다. `source_node_type_id <= target_node_type_id`를 모든 행에 적용하는 CHECK는 만들지 않는다.

## 4.5 `attribute`

| 항목 | 내용 |
|---|---|
| 컬럼 | `attribute_id bigint! ID`, `attribute_code text!` |
| PK | `(attribute_id)` |
| UNIQUE | `(attribute_code)` |
| CHECK | attribute code nonblank |
| 초기 인덱스 | PK·code UNIQUE만 사용 |
| 주석 목적 | 구조화 Claim 속성의 안정된 코드. 값과 사용 규칙은 revision이 소유 |

## 4.6 `attribute_revision`

| 항목 | 내용 |
|---|---|
| 컬럼 | `attribute_revision_id bigint! ID`, `attribute_id bigint!`, `version_no integer!`, `display_name text!`, `target_node_type_id bigint!`, `allowed_value_kind text!`, `unit_rule text?`, `is_active boolean! = false` |
| PK | `(attribute_revision_id)` |
| FK | `attribute_id → attribute`; `target_node_type_id → node_type` |
| UNIQUE | `(attribute_id, version_no)`; `(attribute_revision_id, allowed_value_kind)`; 활성 revision은 `attribute_id`당 최대 한 건 |
| CHECK | version 양수; display nonblank; value kind 닫힌 목록; NUMBER에서만 nonblank unit 필수 |
| 초기 인덱스 | `(target_node_type_id, attribute_revision_id)`; version·kind·active UNIQUE 재사용 |
| 주석 목적 | 한 속성의 대상 유형·값 종류·canonical unit을 보존하는 불변 규칙 버전 |

## 4.7 `node`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_id bigint!`, `node_type_id bigint!` |
| PK | `(node_id)` shared PK |
| FK | `node_id → knowledge_item`; `node_type_id → node_type` |
| CHECK | 행 내부 추가 CHECK 없음 |
| 초기 인덱스 | `(node_type_id, node_id)` |
| 주석 목적 | 사람·회사·기술·주제·사건의 불변 정체성과 유형만 저장하는 `knowledge_item` subtype |

## 4.8 `node_alias`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_alias_id bigint! ID`, `node_id bigint!`, `alias_text text!`, `language text!`, `is_preferred boolean! = false` |
| PK | `(node_alias_id)` |
| FK | `node_id → node` |
| UNIQUE | `(node_id, alias_text, language)`; 노드당 preferred 최대 한 건 |
| CHECK | alias·language nonblank |
| 초기 인덱스 | `(alias_text, node_id)`; preferred partial UNIQUE |
| 주석 목적 | 대표 이름과 검색 alias를 불변 node ID에 연결. 기간·이름 종류는 저장하지 않음 |

## 4.9 `node_alias_evidence`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_alias_id bigint!`, `observation_id bigint!` |
| PK | `(node_alias_id, observation_id)` |
| FK | alias와 observation FK |
| 초기 인덱스 | `(observation_id, node_alias_id)` |
| 주석 목적 | alias가 확인된 원문 위치를 다대다로 연결 |

## 4.10 `external_identifier`

| 항목 | 내용 |
|---|---|
| 컬럼 | `external_identifier_id bigint! ID`, `node_id bigint!`, `identifier_system text!`, `identifier_value text!` |
| PK | `(external_identifier_id)` |
| FK | `node_id → node` |
| UNIQUE | `(identifier_system, identifier_value)` |
| CHECK | system·value nonblank; POC system은 `KRX`, `WIKIDATA`, `ORCID`, `LEI` |
| 초기 인덱스 | `(node_id, external_identifier_id)`; 업무 UNIQUE 재사용 |
| 주석 목적 | 신뢰된 준비 단계가 제공한 외부 식별자이며 일반 기사 문장의 alias나 Claim 근거가 아님 |

## 4.11 `node_merge`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_merge_id bigint! ID`, `source_node_id bigint!`, `canonical_node_id bigint!`, `merge_reason text!`, `merged_at timestamptz!`, `reversed_reason text?`, `reversed_at timestamptz?` |
| PK | `(node_merge_id)` |
| FK | source·canonical 모두 `node` FK |
| UNIQUE | 활성 병합은 `source_node_id`당 최대 한 건 |
| CHECK | 자기 병합 금지; 이유 nonblank; finite 시각; reverse reason/time all-or-none; 취소 시각이 병합 이후 |
| 초기 인덱스 | 활성 canonical `(canonical_node_id, source_node_id)`; source history `(source_node_id, merged_at DESC)`; active source partial UNIQUE |
| 주석 목적 | alias 변경이나 FK 이동이 아닌 내부 node ID 리디렉션 이력 |

## 4.12 `event_temporal_extent`

| 항목 | 내용 |
|---|---|
| 컬럼 | `event_node_id bigint!`, `start_at timestamptz?`, `end_at timestamptz?`, `start_precision text!`, `end_precision text!` |
| PK | `(event_node_id)` shared 대상 |
| FK | `event_node_id → node` |
| CHECK | precision 닫힌 목록; 각 값과 `UNKNOWN`의 일치; finite timestamp |
| 초기 인덱스 | `start_at`이 있는 행의 `(start_at)` |
| 주석 목적 | 사건 node의 채택 시간 범위이며 출처별 주장 전체는 Claim·basis가 보존 |

---

# 5. 기준 지식과 Evidence Trace

소유 문서:

- [`43-knowledge-relation-claim.md`](43-knowledge-relation-claim.md)
- [`43-attribute-evidence.md`](43-attribute-evidence.md)

## 5.1 `knowledge_item`

| 항목 | 내용 |
|---|---|
| 컬럼 | `knowledge_item_id bigint! ID`, `item_kind text!`, `current_state text!`, `promotion_batch_id bigint!`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(knowledge_item_id)` |
| FK | `promotion_batch_id → promotion_batch` |
| CHECK | `item_kind IN ('NODE','RELATION','CLAIM')`; state 닫힌 목록; finite created time |
| 초기 인덱스 | `(promotion_batch_id, knowledge_item_id)` |
| 주석 목적 | node·relation·claim의 공유 ID, 현재 상태와 생성 batch를 소유 |

## 5.2 `knowledge_state_event` — D

| 항목 | 내용 |
|---|---|
| 컬럼 | `knowledge_state_event_id bigint! ID`, `knowledge_item_id bigint!`, `from_state text!`, `to_state text!`, `reason text!`, `actor_id <확정 actor PK>!`, `changed_at timestamptz!` |
| PK | `(knowledge_state_event_id)` |
| FK | item과 향후 actor FK |
| 상태 | 관리자·인증 actor 계약까지 초기 migration 제외 가능 |
| 주석 목적 | 사람 상태 전이의 append-only 이력 |

## 5.3 `relation`

| 항목 | 내용 |
|---|---|
| 컬럼 | `relation_id bigint!`, `source_node_id bigint!`, `target_node_id bigint!`, `relation_type_revision_id bigint!`, `relation_identity_key bytea!` |
| PK | `(relation_id)` shared PK |
| FK | shared item, source node, target node, relation revision |
| UNIQUE | `(relation_identity_key)` |
| CHECK | identity key 32 bytes |
| 초기 인덱스 | `(source_node_id, relation_type_revision_id, target_node_id)`; 반대 방향 `(target_node_id, relation_type_revision_id, source_node_id)` |
| 주석 목적 | 정확한 revision으로 두 node를 연결하는 기준 연결. 출처·사건 맥락·유효 기간은 직접 저장하지 않음 |

## 5.4 `claim`

| 항목 | 내용 |
|---|---|
| 컬럼 | `claim_id bigint!`, `statement_text text!`, `language text!`, `modality text!`, `asserted_from timestamptz?`, `asserted_to timestamptz?`, `asserted_from_precision text!`, `asserted_to_precision text!` |
| PK | `(claim_id)` shared PK |
| FK | `claim_id → knowledge_item` |
| CHECK | statement·language nonblank; modality 닫힌 목록; 값과 precision의 `NULL/UNKNOWN` 일치; finite timestamp |
| 초기 인덱스 | PK 외 별도 없음. 의미 대상 연결 테이블에서 역조회 |
| 주석 목적 | 출처가 주장한 원자 문장과 표현 성격·주장 시간을 보존 |

## 5.5 `claim_relation`

| 항목 | 내용 |
|---|---|
| 컬럼 | `claim_id bigint!`, `relation_id bigint!`, `stance text!` |
| PK | `(claim_id, relation_id)` |
| FK | claim과 relation FK |
| CHECK | `stance IN ('SUPPORT','DISPUTE')` |
| 초기 인덱스 | `(relation_id, stance, claim_id)` |
| 주석 목적 | Claim이 관계를 지지하거나 반박하는 의미 연결 |

## 5.6 `claim_attribute_value`

| 항목 | 내용 |
|---|---|
| 컬럼 | `claim_attribute_value_id bigint! ID`, `claim_id bigint!`, `target_node_id bigint!`, `attribute_revision_id bigint!`, `value_kind text!`, `string_value text?`, `number_value numeric?`, `unit_code text?`, `date_from date?`, `date_to date?`, `date_from_precision text!`, `date_to_precision text!`, `boolean_value boolean?` |
| PK | `(claim_attribute_value_id)` |
| FK | claim, target node; `(attribute_revision_id, value_kind) → attribute_revision(attribute_revision_id, allowed_value_kind)` |
| CHECK | value kind·date precision 닫힌 목록; finite numeric; 값 종류별 tagged union 정확히 한 형태 |
| 초기 인덱스 | `(claim_id, claim_attribute_value_id)`; `(target_node_id, attribute_revision_id, claim_id)`; `(attribute_revision_id, target_node_id, claim_id)` |
| 주석 목적 | Claim이 한 node 속성에 관해 주장한 구조화 값이며 현재 프로필 확정값이 아님 |

## 5.7 `event_temporal_basis`

| 항목 | 내용 |
|---|---|
| 컬럼 | `event_node_id bigint!`, `claim_id bigint!` |
| PK | `(event_node_id, claim_id)` |
| FK | event temporal extent와 claim FK |
| 초기 인덱스 | `(claim_id, event_node_id)` |
| 주석 목적 | 채택 사건 시간을 직접 뒷받침하는 Claim 연결 |

## 5.8 `claim_observation`

| 항목 | 내용 |
|---|---|
| 컬럼 | `claim_id bigint!`, `observation_id bigint!` |
| PK | `(claim_id, observation_id)` |
| FK | claim과 observation FK |
| 초기 인덱스 | `(observation_id, claim_id)` |
| 주석 목적 | Claim과 정확한 원문 범위를 다대다로 연결하는 Evidence Trace |

---

# 6. 모델 작업과 승격 전 차단

소유 문서: [`44-model-task.md`](44-model-task.md)

## 6.1 `output_schema_definition`

| 항목 | 내용 |
|---|---|
| 컬럼 | `output_schema_definition_id bigint! ID`, `task_kind text!`, `version_no integer!`, `schema_json jsonb!`, `is_active boolean! = false`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(output_schema_definition_id)` |
| UNIQUE | `(task_kind, version_no)`; task kind별 active 최대 한 건 |
| CHECK | version 양수; JSON object; task kind 닫힌 목록; finite created time |
| 초기 인덱스 | version·active UNIQUE 재사용 |
| 주석 목적 | 실제 응답이 아닌 Structured Output JSON Schema 계약의 불변 버전 |

## 6.2 `model_task`

| 항목 | 내용 |
|---|---|
| 컬럼 | `model_task_id bigint! ID`, `task_kind text!`, `source_document_id bigint?`, `input_hash bytea!`, `output_schema_definition_id bigint?`, `model_version text!`, `prompt_version text?`, `cache_key bytea!`, `status text! = 'PENDING'`, `attempt_count integer! = 0`, `next_attempt_at timestamptz?`, `lease_owner text?`, `lease_expires_at timestamptz?`, `created_at timestamptz! = CURRENT_TIMESTAMP`, `finished_at timestamptz?` |
| PK | `(model_task_id)` |
| FK | optional source document; optional output contract |
| UNIQUE | `(cache_key)` |
| CHECK | hashes 32 bytes; model/prompt nonblank 조건; attempt 0–5; task/status 닫힌 목록; EMBEDDING 계약 예외; 상태별 retry·lease·finished 조합; finite timestamp |
| 초기 인덱스 | runnable partial; expired lease partial; optional source document; output contract |
| 주석 목적 | 재시도 전체를 묶는 논리 모델 작업이며 실제 호출 누계와 현재 실행 상태를 소유 |

## 6.3 `agent_attempt`

| 항목 | 내용 |
|---|---|
| 컬럼 | `agent_attempt_id bigint! ID`, `model_task_id bigint!`, `attempt_no integer!`, `outcome text!`, `failure_reason text?`, `attempted_at timestamptz!` |
| PK | `(agent_attempt_id)` |
| FK | `model_task_id → model_task` |
| UNIQUE | `(model_task_id, attempt_no)` |
| CHECK | attempt 1–5; outcome 닫힌 목록; 성공과 failure reason의 all-or-none; finite time |
| 초기 인덱스 | UNIQUE만 사용. 별도 `(model_task_id, attempt_no DESC)` 인덱스는 초기 migration에서 제외 |
| 주석 목적 | 한 논리 작업의 실제 provider 호출 한 번을 기록하는 append-only 이력 |

## 6.4 `blocked_fingerprint`

| 항목 | 내용 |
|---|---|
| 컬럼 | `blocked_fingerprint_id bigint! ID`, `fingerprint bytea!`, `source_document_id bigint!`, `output_schema_definition_id bigint!`, `lint_policy_rule_id bigint!`, `first_blocked_at timestamptz!`, `last_blocked_at timestamptz!`, `blocked_count integer! = 1` |
| PK | `(blocked_fingerprint_id)` |
| FK | source document, output contract, exact lint policy rule |
| UNIQUE | `(fingerprint, source_document_id, output_schema_definition_id, lint_policy_rule_id)` |
| CHECK | fingerprint 32 bytes; count 양수; finite·순서 시각 |
| 초기 인덱스 | `(source_document_id, last_blocked_at DESC)`; `(lint_policy_rule_id, last_blocked_at DESC)`; 업무 UNIQUE 재사용 |
| 주석 목적 | 후보 payload를 저장하지 않고 동일 차단 후보의 반복 검증·승격을 억제 |

---

# 7. 저장 그래프 lint

소유 문서: [`44-lint.md`](44-lint.md)

## 7.1 `lint_rule`

| 항목 | 내용 |
|---|---|
| 컬럼 | `lint_rule_id bigint! ID`, `rule_code text!`, `display_name text!`, `description text!`, `evaluation_scope text!` |
| PK | `(lint_rule_id)` |
| UNIQUE | `(rule_code)` |
| CHECK | text nonblank; scope 닫힌 목록 |
| 초기 인덱스 | PK·code UNIQUE만 사용 |
| 주석 목적 | 안정된 lint 규칙과 평가 범위. 정책별 사용·심각도는 policy rule 소유 |

## 7.2 `lint_policy_version`

| 항목 | 내용 |
|---|---|
| 컬럼 | `lint_policy_version_id bigint! ID`, `version_no integer!`, `validator_version text!`, `is_active boolean! = false`, `created_at timestamptz! = CURRENT_TIMESTAMP`, `activated_at timestamptz?` |
| PK | `(lint_policy_version_id)` |
| UNIQUE | `(version_no)`; 전체 active 정책 최대 한 건 |
| CHECK | version 양수; validator nonblank; active면 activated time 필수; finite time |
| 초기 인덱스 | number·active UNIQUE 재사용 |
| 주석 목적 | 판정 결과에 영향을 주는 validator와 규칙 선택의 불변 정책 버전 |

## 7.3 `lint_policy_rule`

| 항목 | 내용 |
|---|---|
| 컬럼 | `lint_policy_rule_id bigint! ID`, `lint_policy_version_id bigint!`, `lint_rule_id bigint!`, `severity text!` |
| PK | `(lint_policy_rule_id)` |
| FK | policy와 rule FK |
| UNIQUE | `(lint_policy_version_id, lint_rule_id)` |
| CHECK | `severity IN ('BLOCKING','WARNING')` |
| 초기 인덱스 | `(lint_rule_id, lint_policy_version_id)`; selection UNIQUE 재사용 |
| 주석 목적 | 한 policy에서 사용할 stable rule과 해당 심각도의 불변 선택 |

## 7.4 `lint_run`

| 항목 | 내용 |
|---|---|
| 컬럼 | `lint_run_id bigint! ID`, `lint_policy_version_id bigint!`, `scope_kind text! = 'FULL_GRAPH'`, `status text! = 'PENDING'`, `started_at timestamptz?`, `completed_at timestamptz?` |
| PK | `(lint_run_id)` |
| FK | `lint_policy_version_id → lint_policy_version` |
| UNIQUE | policy별 `PENDING/RUNNING` run 최대 한 건 |
| CHECK | full graph 고정; status 닫힌 목록; 상태별 시각 조합; finite·순서 시각 |
| 초기 인덱스 | 진행 status partial; `(lint_policy_version_id, lint_run_id DESC)`; in-progress partial UNIQUE |
| 주석 목적 | 이미 저장된 기준 지식 전체를 한 정책으로 다시 검사한 실행 |

## 7.5 `lint_finding`

| 항목 | 내용 |
|---|---|
| 컬럼 | `lint_finding_id bigint! ID`, `finding_key bytea!`, `knowledge_item_id bigint!`, `lint_policy_rule_id bigint!`, `first_detected_run_id bigint!`, `latest_detected_run_id bigint!`, `first_detected_at timestamptz!`, `last_detected_at timestamptz!`, `detection_count integer! = 1`, `message text!`, `details_json jsonb?`, `resolved_by_run_id bigint?`, `resolved_at timestamptz?`, `resolution_reason text?` |
| PK | `(lint_finding_id)` |
| FK | knowledge item, policy rule, 세 lint run FK |
| UNIQUE | 열린 `finding_key` 최대 한 건 |
| CHECK | key 32 bytes; count 양수; message nonblank; details는 object; 시각 순서; 해결 세 필드 all-or-none |
| 초기 인덱스 | item/open; policy-rule/open; latest run; open key partial UNIQUE |
| 주석 목적 | 사람 거절과 분리된 저장 지식의 결정적 문제 인스턴스와 반복·해결·재발 이력 |

---

# 8. 충돌 스냅샷

소유 문서: [`45-conflict-snapshot.md`](45-conflict-snapshot.md)

## 8.1 `conflict_set`

| 항목 | 내용 |
|---|---|
| 컬럼 | `conflict_set_id bigint! ID`, `relation_id bigint?`, `target_node_id bigint?`, `attribute_revision_id bigint?`, `event_node_id bigint?`, `modality text!`, `current_state text!`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(conflict_set_id)` |
| FK | relation, target node, attribute revision, event temporal extent |
| CHECK | 관계·속성·사건 시간 중 정확히 한 target shape; modality·state 닫힌 목록; finite time |
| 초기 인덱스 | relation target partial; attribute target partial; event target partial |
| 주석 목적 | 어느 Claim이 참인지 판정하지 않는 불변 비교 대상·구성 snapshot의 정체성 |

## 8.2 `conflict_member`

| 항목 | 내용 |
|---|---|
| 컬럼 | `conflict_set_id bigint!`, `claim_id bigint!`, `position_key text!` |
| PK | `(conflict_set_id, claim_id)` |
| FK | conflict set과 claim FK |
| CHECK | position key nonblank |
| 초기 인덱스 | `(claim_id, conflict_set_id)`; `(conflict_set_id, position_key, claim_id)` |
| 주석 목적 | 정확한 Claim 구성과 같은 관점 그룹을 보존하는 불변 snapshot member |

## 8.3 `conflict_state_event` — D

| 항목 | 내용 |
|---|---|
| 컬럼 | `conflict_state_event_id bigint! ID`, `conflict_set_id bigint!`, `from_state text!`, `to_state text!`, `reason text!`, `actor_id <확정 actor PK>!`, `changed_at timestamptz!` |
| PK | `(conflict_state_event_id)` |
| FK | conflict set과 향후 actor FK |
| 상태 | 관리자·인증 actor 계약까지 초기 migration 제외 가능 |
| 주석 목적 | 사람 확인·거절의 append-only 상태 이력이며 member Claim 상태와 별개 |

## 8.4 `conflict_summary`

| 항목 | 내용 |
|---|---|
| 컬럼 | `conflict_summary_id bigint! ID`, `conflict_set_id bigint!`, `model_task_id bigint!`, `common_ground_text text!`, `viewpoint_summary_text text!`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(conflict_summary_id)` |
| FK | conflict set과 model task FK |
| UNIQUE | `(model_task_id)` |
| CHECK | 두 summary text nonblank; finite time |
| 초기 인덱스 | `(conflict_set_id, created_at DESC, conflict_summary_id DESC)`; model task UNIQUE 재사용 |
| 주석 목적 | 불변 conflict member 집합으로 생성한 공통점과 관점 요약이며 자동 승자 판정이 아님 |

---

# 9. 승격과 공개 선택

소유 문서: [`46-promotion-publication.md`](46-promotion-publication.md)

## 9.1 `promotion_batch`

| 항목 | 내용 |
|---|---|
| 컬럼 | `promotion_batch_id bigint! ID`, `lint_policy_version_id bigint!`, `promotion_status text! = 'PENDING'`, `publication_status text! = 'NOT_STARTED'`, `started_at timestamptz! = CURRENT_TIMESTAMP`, `committed_at timestamptz?`, `ready_at timestamptz?`, `promotion_failure_reason text?`, `publication_failure_reason text?` |
| PK | `(promotion_batch_id)` |
| FK | `lint_policy_version_id → lint_policy_version` |
| CHECK | promotion·publication status 닫힌 목록; 여섯 허용 상태 조합; 시각 순서; failure reason nonblank |
| 초기 인덱스 | promotion pending partial; publication work partial; READY `(ready_at DESC, id DESC)` partial |
| 주석 목적 | 기준 지식의 원자 승격 결과와 그 변경의 공개 준비 상태를 분리 관리 |

## 9.2 `publication_affected_node`

| 항목 | 내용 |
|---|---|
| 컬럼 | `promotion_batch_id bigint!`, `node_id bigint!`, `node_search_document_id bigint?`, `node_embedding_id bigint?`, `node_context_id bigint?` |
| PK | `(promotion_batch_id, node_id)` |
| FK | promotion batch, node; search document·embedding·context의 동일 node/document 복합 FK |
| CHECK | embedding/context 선택 시 search document 필수 |
| 초기 인덱스 | `(node_id, promotion_batch_id DESC)` |
| 주석 목적 | 한 batch가 갱신한 node와 최종 선택 artifact이며 지도 구성원 snapshot이 아님 |

---

# 10. 검색 문서와 파생 모델 산출물

소유 문서:

- [`47-search-document.md`](47-search-document.md)
- [`47-model-artifacts.md`](47-model-artifacts.md)
- [`78-embedding-contract.md`](78-embedding-contract.md)

## 10.1 `node_search_document`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_search_document_id bigint! ID`, `node_id bigint!`, `identity_text text!`, `knowledge_text text!`, `input_hash bytea!`, `generator_version text!`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(node_search_document_id)` |
| FK | `node_id → node` |
| UNIQUE | `(node_id, input_hash, generator_version)`; `(node_search_document_id, node_id)` 복합 참조 키 |
| CHECK | 두 text nonempty; hash 32 bytes; generator nonblank; finite time |
| 초기 인덱스 | node/latest; weighted `simple` expression GIN; UNIQUE 재사용 |
| 주석 목적 | 공개 가능한 node를 alias·전문·벡터 검색의 공통 대상으로 만드는 불변 텍스트 버전 |

## 10.2 `search_document_basis`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_search_document_id bigint!`, `knowledge_item_id bigint!` |
| PK | `(node_search_document_id, knowledge_item_id)` |
| FK | search document와 knowledge item FK |
| 초기 인덱스 | `(knowledge_item_id, node_search_document_id)` |
| 주석 목적 | 검색 문서 생성에 기여한 공개 기준 지식 계보이며 벡터 점수의 문장별 인과 설명은 아님 |

## 10.3 `node_embedding`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_embedding_id bigint! ID`, `node_id bigint!`, `node_search_document_id bigint!`, `model_task_id bigint!`, `embedding_vector vector(1024)!`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(node_embedding_id)` |
| FK | `(node_search_document_id, node_id) → node_search_document`; `model_task_id → model_task` |
| UNIQUE | `(model_task_id)`; `(node_embedding_id, node_search_document_id, node_id)` 복합 참조 키 |
| CHECK | finite created time; 1024차원·유한 원소·nonzero norm은 pgvector와 서비스 계약 |
| 초기 인덱스 | `(node_search_document_id, node_id, node_embedding_id)`; ANN 인덱스 없음 |
| 주석 목적 | Qwen document 계약으로 정확한 검색 문서에서 만든 불변 dense 벡터 |

## 10.4 `node_context`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_context_id bigint! ID`, `node_id bigint!`, `node_search_document_id bigint!`, `model_task_id bigint!`, `language text!`, `context_text text!`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(node_context_id)` |
| FK | 동일 node/search document 복합 FK; model task FK |
| UNIQUE | `(model_task_id)`; `(node_context_id, node_search_document_id, node_id)` 복합 참조 키 |
| CHECK | language·context nonblank; finite time |
| 초기 인덱스 | `(node_search_document_id, node_id, node_context_id)` |
| 주석 목적 | 검색 문서에서 사전 생성한 사용자용 한국어 맥락 설명이며 검색 입력으로 되돌리지 않음 |

## 10.5 `followup_question`

| 항목 | 내용 |
|---|---|
| 컬럼 | `followup_question_id bigint! ID`, `node_context_id bigint!`, `model_task_id bigint!`, `slot smallint!`, `question_text text!`, `target_node_id bigint!`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(followup_question_id)` |
| FK | context, model task, target node FK |
| UNIQUE | `(node_context_id, slot)` |
| CHECK | `slot IN (1,2)`; question nonblank; finite time |
| 초기 인덱스 | `(target_node_id, followup_question_id)`; `(model_task_id, slot)`; slot UNIQUE 재사용 |
| 주석 목적 | context에서 다음 지도 중심으로 이동할 질문 두 개와 목적지 node를 저장하며 Relation을 뜻하지 않음 |

---

# 11. 통합 FK 방향 요약

```text
evidence_group
← source_document
← observation

promotion_batch
← knowledge_item
← node / relation / claim

node_type
← node
← relation_endpoint_rule
← attribute_revision

relation_type
← relation_type_revision
← relation

attribute
← attribute_revision
← claim_attribute_value

claim
← claim_relation
← claim_attribute_value
← event_temporal_basis
← claim_observation
← conflict_member

model_task
← agent_attempt
← conflict_summary
← node_embedding
← node_context
← followup_question

lint_policy_version
← lint_policy_rule
← promotion_batch
← lint_run

node_search_document
← search_document_basis
← node_embedding
← node_context
← publication_affected_node
```

모든 공개 의미는 다음 Evidence Trace 가운데 하나로 원문까지 도달해야 한다.

```text
relation ← claim_relation ← claim → claim_observation → observation → source_document

node attribute ← claim_attribute_value ← claim → claim_observation → observation → source_document

event time ← event_temporal_basis ← claim → claim_observation → observation → source_document

node alias ← node_alias_evidence → observation → source_document
```
