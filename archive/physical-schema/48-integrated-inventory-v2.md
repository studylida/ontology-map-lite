# #48 통합 PostgreSQL 테이블 inventory v2

## 문서 상태

- 관련 Issue: #48
- 기준선: [`48-integrated-inventory.md`](48-integrated-inventory.md), PR #89
- 추가 설계: #91, PR #92
- 논리 기준: [`../logical-data-schema.md`](../../docs/data/logical-schema.md) + [`../logical-schema/91-node-insight.md`](../logical-schema/91-node-insight.md)
- 상태: 인사이트 확장을 반영한 최종 구현 inventory

기존 inventory의 43개 테이블 정의는 그대로 유지한다. 이 문서는 #91 이후 달라진 전체 수량, task-kind 계약, `publication_affected_node`와 새 인사이트 테이블을 최종 기준으로 갱신한다. 아래에서 명시적으로 바뀐 항목은 기존 문서의 같은 항목을 대체한다.

## 1. 전체 수량

| 구분 | v1 | v2 |
|---|---:|---:|
| 물리 설계에 정의된 테이블 | 43 | 45 |
| 초기 migration 대상 | 41 | 43 |
| actor 계약까지 비차단 보류 | 2 | 2 |

추가 테이블:

```text
node_insight
node_insight_claim
```

초기 migration에서 보류 가능한 테이블은 이전과 동일하다.

```text
knowledge_state_event
conflict_state_event
```

## 2. 전체 45개 테이블

### 준비 문서와 원문 근거

1. `evidence_group`
2. `source_document`
3. `observation`

### 온톨로지와 노드 정체성

4. `node_type`
5. `relation_type`
6. `relation_type_revision`
7. `relation_endpoint_rule`
8. `attribute`
9. `attribute_revision`
10. `node`
11. `node_alias`
12. `node_alias_evidence`
13. `external_identifier`
14. `node_merge`
15. `event_temporal_extent`

### 기준 지식과 Evidence Trace

16. `promotion_batch`
17. `knowledge_item`
18. `knowledge_state_event` — actor 계약까지 비차단 보류
19. `relation`
20. `claim`
21. `claim_relation`
22. `claim_attribute_value`
23. `event_temporal_basis`
24. `claim_observation`

### 모델 작업과 lint

25. `output_schema_definition`
26. `model_task`
27. `agent_attempt`
28. `blocked_fingerprint`
29. `lint_rule`
30. `lint_policy_version`
31. `lint_policy_rule`
32. `lint_run`
33. `lint_finding`

### 충돌

34. `conflict_set`
35. `conflict_member`
36. `conflict_state_event` — actor 계약까지 비차단 보류
37. `conflict_summary`

### 공개 선택과 파생 결과

38. `publication_affected_node`
39. `node_search_document`
40. `search_document_basis`
41. `node_embedding`
42. `node_context`
43. `followup_question`
44. `node_insight`
45. `node_insight_claim`

## 3. task-kind 변경

### 3.1 `output_schema_definition.task_kind`

기존 닫힌 목록에 다음 값을 추가한다.

```text
NODE_INSIGHT
```

`NODE_INSIGHT`는 JSON Structured Output 계약을 가지므로 `output_schema_definition` 행이 필요하다.

### 3.2 `model_task.task_kind`

기존 닫힌 목록에 다음 값을 추가한다.

```text
NODE_INSIGHT
```

`NODE_INSIGHT`에서는 다음 조합을 사용한다.

```text
output_schema_definition_id NOT NULL
prompt_version NOT NULL
```

`EMBEDDING`의 `NULL` 예외를 적용하지 않는다.

## 4. 변경된 `publication_affected_node`

소유 문서:

- [`46-promotion-publication.md`](46-promotion-publication.md)
- [`91-node-insight.md`](91-node-insight.md)

| 항목 | v2 최종 내용 |
|---|---|
| 컬럼 | `promotion_batch_id bigint!`, `node_id bigint!`, `node_search_document_id bigint?`, `node_embedding_id bigint?`, `node_context_id bigint?`, `node_insight_model_task_id bigint?` |
| PK | `(promotion_batch_id, node_id)` |
| FK | promotion batch, node, search document·embedding·context 복합 FK, `node_insight_model_task_id → model_task` |
| CHECK | embedding/context 선택 시 search document 필수. insight task의 kind·status·node·검색 문서 일치는 서비스 검증 |
| 초기 인덱스 | `(node_id, promotion_batch_id DESC)` |
| 주석 목적 | 한 batch가 갱신한 node와 최종 선택 artifact·인사이트 작업이며 지도 구성원 snapshot이 아님 |

### READY v2

READY 전환 시 각 affected node는 다음을 모두 만족한다.

```text
검색 문서 있음
임베딩 있음
맥락 설명 있음
후속 질문 slot 1·2 있음
node_insight_model_task_id 있음
선택 task가 NODE_INSIGHT + SUCCESS
선택 task가 같은 node와 같은 node_search_document를 사용
90일·1년 범위를 모두 처리
```

인사이트 결과가 0개인 시간 범위도 성공한 작업이 해당 범위를 명시적으로 처리했다면 READY를 막지 않는다.

## 5. 새 `node_insight`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_insight_id bigint! ID`, `node_id bigint!`, `node_search_document_id bigint!`, `model_task_id bigint!`, `time_window text!`, `as_of_at timestamptz!`, `slot smallint!`, `title text!`, `summary_text text!`, `synthesis_text text!`, `caveat_text text!`, `created_at timestamptz! = CURRENT_TIMESTAMP` |
| PK | `(node_insight_id)` |
| FK | `(node_search_document_id, node_id) → node_search_document`; `model_task_id → model_task` |
| UNIQUE | `(model_task_id, time_window, slot)` |
| CHECK | `time_window IN ('RECENT_90_DAYS','RECENT_1_YEAR')`; `slot IN (1,2,3)`; 네 text nonblank; 시각 finite |
| 초기 인덱스 | `(node_id, node_search_document_id, time_window, model_task_id, slot)` |
| 주석 목적 | 한 node의 공개 검색 문서와 근거 Claim을 모델이 미리 종합한 불변 분석 리포트 |

### 시간 의미

```text
RECENT_90_DAYS
→ [as_of_at - 90일, as_of_at)

RECENT_1_YEAR
→ [as_of_at - 1년, as_of_at)
```

범위는 `source_document.published_at`에 적용한다. 게시 시점이 `UNKNOWN`인 근거는 범위별 인사이트와 count에서 제외한다.

## 6. 새 `node_insight_claim`

| 항목 | 내용 |
|---|---|
| 컬럼 | `node_insight_id bigint!`, `claim_id bigint!`, `role text!`, `display_order smallint!` |
| PK | `(node_insight_id, claim_id)` |
| FK | `node_insight_id → node_insight`; `claim_id → claim` |
| UNIQUE | `(node_insight_id, display_order)` |
| CHECK | `role IN ('KEY_CLAIM','SUPPORTING_CLAIM','CONTRASTING_CLAIM')`; `display_order >= 1` |
| 초기 인덱스 | `(claim_id, node_insight_id)`; PK와 display-order UNIQUE 재사용 |
| 주석 목적 | 인사이트가 사용한 기존 Claim과 화면 역할을 연결하며 사실·원문·Relation 사본을 만들지 않음 |

## 7. 근거 수와 Evidence Trace

목록의 근거 개수는 저장하지 않는다.

```text
node_insight
→ node_insight_claim
→ claim_observation
→ observation
→ source_document
→ COUNT(DISTINCT evidence_group_id)
```

계산 시 `node_insight.time_window + as_of_at`의 게시 범위 안에 있는 문서만 센다.

원문 추적:

```text
node_insight
→ node_insight_claim
→ claim
→ claim_observation
→ observation
→ source_document
```

Relation 추적:

```text
node_insight_claim
→ claim_relation
→ relation
```

다음 테이블·컬럼은 추가하지 않는다.

```text
node_insight_relation
node_insight_observation
node_insight_source_document
node_insight.evidence_count
node_insight.verified_fact_text
```

## 8. 통합 FK 방향 v2

기존 방향에 다음을 추가한다.

```text
node_search_document
← node_insight

model_task
← node_insight
← publication_affected_node.node_insight_model_task_id

claim
← node_insight_claim

node_insight
← node_insight_claim
```

공개 인사이트의 원문 경로는 모든 단계에서 필수 FK로 이어진다. 단, 한 인사이트의 최소 Claim 수와 `KEY_CLAIM` 최소 한 건은 교차 행 조건이므로 READY 전 서비스가 검사한다.

## 9. 수명주기

| 대상 | 분류 | 수정 가능 값 |
|---|---|---|
| `node_insight` | 불변 모델 산출물 | 없음 |
| `node_insight_claim` | 불변 근거 연결 | 없음 |
| `publication_affected_node.node_insight_model_task_id` | 공개 선택 | READY 전 선택 가능, READY 뒤 불변 |

입력 Claim, 검색 문서, 모델, prompt, 출력 계약, `as_of_at` 중 하나가 바뀌면 새 model task와 새 인사이트 전체를 만든다.

## 10. legacy READY 행

#91 이전에 만들어진 READY publication row에는 `node_insight_model_task_id`가 없다. 물리 컬럼은 nullable로 유지하고 다음처럼 구분한다.

```text
legacy READY
→ 인사이트 기능 도입 이전 공개 결과

v2 READY
→ 인사이트 작업을 선택한 공개 결과
```

실제 인사이트 API를 공개하기 전에 대상 node의 새 promotion/publication bundle을 준비한다. 기존 READY 행에 임의 task ID를 backfill하지 않는다.

## 11. 폐기 구조 확인

v2에서도 다음 구조는 만들지 않는다.

```text
ontology_version
ontology_member
evidence_group_assignment
display_rule_version
candidate_item
candidate_state_event
structured_output
node_insight_set
node_insight_relation
인사이트 원문·Relation 사본
저장된 evidence count
```

시간 범위별 작업을 독립 교체하거나 한 publication에서 여러 insight task를 조합해야 하는 실제 요구가 생길 때만 `node_insight_set`을 후속 설계한다.
