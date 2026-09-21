# #47 임베딩·맥락 설명·후속 질문의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #47
- 주 문서: [`47-search-document.md`](47-search-document.md)
- 범위: `node_embedding`, `node_context`, `followup_question`, #46 선택 FK
- 명시적 blocker: #78

## 1. 공통 원칙

- 모든 모델 기반 파생 결과는 불변 행이며 정확한 `node_search_document`와 성공한 `model_task`를 참조한다.
- 결과 행에 input hash·모델·prompt·출력 계약을 중복 저장하지 않고 `model_task_id`로 조회한다.
- `node_id`를 중복 저장해 검색·공개 결과를 node로 바로 변환하되, 검색 문서와의 복합 FK로 일치를 강제한다.
- 새 입력·모델·prompt는 기존 결과를 UPDATE하지 않고 새 작업과 새 행을 만든다.
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.

## 2. `node_embedding`

### 2.1 책임

한 검색 문서의 `identity_text`와 `knowledge_text`를 승인된 고정 순서로 연결한 입력에서 생성한 불변 검색 벡터다.

### 2.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `node_embedding_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `node_id` | `bigint` | 불가 | 없음 | 불변 |
| `node_search_document_id` | `bigint` | 불가 | 없음 | 불변 |
| `model_task_id` | `bigint` | 불가 | 없음 | 불변 |
| `embedding_vector` | `vector(n)` | 불가 | 없음 | 불변 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

`n`은 #78에서 임베딩 모델·거리 연산자와 함께 승인할 실제 모델 차원이다. 기존 자료의 `1536`은 예시 또는 임시값이며 계약이 아니다. placeholder 숫자와 차원 없는 `vector`를 사용하지 않는다.

### 2.3 PK·FK·고유성

- PK `pk_node_embedding (node_embedding_id)`
- 복합 FK:

```text
(node_search_document_id, node_id)
→ node_search_document(node_search_document_id, node_id)
```

- FK `model_task_id → model_task.model_task_id`
- UNIQUE `uq_node_embedding__model_task (model_task_id)`
- #46 선택 FK 대상:

```text
UNIQUE (
    node_embedding_id,
    node_search_document_id,
    node_id
)
```

PK 때문에 ID 자체는 이미 고유하지만 세 컬럼 고유키는 publication 선택 row가 같은 node와 검색 문서인지 복합 FK로 검증하기 위해 둔다.

### 2.4 CHECK·서비스 검증

- `isfinite(created_at)`
- pgvector가 column 차원과 element 표현을 검사한다.
- 서비스는 저장 전 다음을 확인한다.
  - `model_task.task_kind = 'EMBEDDING'`
  - task가 아직 종료되지 않았고 결과 행이 없음
  - task input이 정확한 검색 문서의 고정 입력 텍스트와 일치
  - vector 차원이 #78의 승인값과 일치
- 결과 insert 뒤에만 task를 `SUCCESS`로 전환한다.

### 2.5 인덱스

```sql
CREATE INDEX ix_node_embedding__search_document
ON node_embedding (
    node_search_document_id,
    node_id,
    node_embedding_id
);
```

- model task 방향은 UNIQUE 인덱스가 지원한다.
- 초기 POC에는 HNSW·IVFFlat을 만들지 않는다.
- exact vector search의 거리 연산자와 tie-breaker는 #78에서 확정한다.

### 2.6 호환성

query vector와 document vector는 같은 승인 모델·입력 규칙을 사용해야 한다. 공개 검색은 `node_embedding.model_task_id → model_task.model_version`을 따라 호환 모델만 비교한다.

여러 모델 공간을 한 SQL 거리 순위에 섞지 않는다. 모델 변경은 새 embedding 작업과 새 행을 만들고 READY publication이 새 결과를 선택한 뒤 전환한다.

### 2.7 주석

```sql
COMMENT ON TABLE node_embedding IS
'정확한 node_search_document에서 만든 불변 검색 벡터. 모델·입력 hash·재시도 이력은 model_task가 소유하며 동일 대상 판정이나 관계 생성에 사용하지 않는다.';
```

## 3. `node_context`

### 3.1 책임

정확한 검색 문서에서 미리 생성한 사용자용 한국어 맥락 설명의 불변 버전이다. 노드 클릭 시 새로 생성하지 않는다.

### 3.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `node_context_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `node_id` | `bigint` | 불가 | 없음 | 불변 |
| `node_search_document_id` | `bigint` | 불가 | 없음 | 불변 |
| `model_task_id` | `bigint` | 불가 | 없음 | 불변 |
| `language` | `text` | 불가 | 없음 | 불변 |
| `context_text` | `text` | 불가 | 없음 | 불변 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

### 3.3 PK·FK·고유성

- PK `pk_node_context (node_context_id)`
- 복합 FK `(node_search_document_id, node_id) → node_search_document`
- FK `model_task_id → model_task`
- UNIQUE `uq_node_context__model_task (model_task_id)`
- #46 선택 FK 대상:

```text
UNIQUE (node_context_id, node_search_document_id, node_id)
```

### 3.4 CHECK와 서비스 검증

- `language`, `context_text` nonblank
- `isfinite(created_at)`
- HBF POC 공개 언어는 서비스에서 `ko`로 제한한다. DB code 목록으로 고정하지 않는다.
- 서비스는 `model_task.task_kind = 'NODE_CONTEXT'`, exact search document 입력, 성공 결과 한 건을 확인한다.
- context insert 뒤에만 task를 `SUCCESS`로 바꾼다.
- 생성된 `context_text`를 `identity_text`나 `knowledge_text`에 복사하지 않는다.

### 3.5 인덱스와 주석

```sql
CREATE INDEX ix_node_context__search_document
ON node_context (
    node_search_document_id,
    node_id,
    node_context_id
);
```

```sql
COMMENT ON TABLE node_context IS
'정확한 검색 문서에서 사전 생성한 사용자용 맥락 설명. 검색 문서 입력으로 되돌려 넣지 않으며 클릭 시 모델을 호출하지 않는다.';
```

## 4. `followup_question`

### 4.1 책임

한 `node_context`에서 다음 지도 탐색으로 이동할 질문 두 개와 필수 대상 node를 저장한다.

### 4.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `followup_question_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `node_context_id` | `bigint` | 불가 | 없음 | 불변 |
| `model_task_id` | `bigint` | 불가 | 없음 | 불변 |
| `slot` | `smallint` | 불가 | 없음 | 불변 |
| `question_text` | `text` | 불가 | 없음 | 불변 |
| `target_node_id` | `bigint` | 불가 | 없음 | 불변 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

### 4.3 PK·FK·UNIQUE·CHECK

- PK `pk_followup_question (followup_question_id)`
- FK `node_context_id → node_context.node_context_id`
- FK `model_task_id → model_task.model_task_id`
- FK `target_node_id → node.node_id`
- UNIQUE `uq_followup_question__slot (node_context_id, slot)`
- `slot IN (1, 2)`
- `question_text` nonblank
- `isfinite(created_at)`

### 4.4 서비스 생성 검증

한 성공한 `FOLLOWUP_QUESTIONS` 작업이 slot 1·2 두 행을 함께 만든다.

1. task kind와 exact context 입력을 확인한다.
2. 모델 입력에는 결정적으로 정렬된 후보 node ID 전체와 검색 입력 버전을 포함한다.
3. 두 결과 slot이 정확히 1과 2이고 같은 `model_task_id`, `node_context_id`인지 확인한다.
4. 각 `target_node_id`가 제공 후보에 포함되고 현재 공개 자격을 갖는지 확인한다.
5. 같은 batch 대상은 출발 node와 원자적으로 READY가 될 수 있어야 한다.
6. 적격한 외부 후보가 하나면 두 질문이 같은 node를 가리킬 수 있다.
7. 적격한 외부 후보가 없을 때만 출발 node 자체를 허용한다.
8. 두 행을 내구성 있게 insert한 뒤 task를 `SUCCESS`로 바꾼다.

후보 목록과 질문의 완전한 답을 DB에 저장하지 않는다. `target_node_id`는 탐색 목적지이며 두 node 사이 Relation을 뜻하지 않는다.

### 4.5 인덱스

- context의 slot 조회는 UNIQUE 인덱스가 지원한다.
- `ix_followup_question__target (target_node_id, followup_question_id)`
- `ix_followup_question__model_task (model_task_id, slot)`

### 4.6 질문 버전 경계

현재 한 context는 slot 1·2 한 세트를 소유한다. 질문 prompt·모델 규칙을 바꿔 새 질문 세트를 공개하려면 새 context publication bundle을 생성한다. 질문만 독립적으로 여러 세트를 선택하는 `followup_question_set`은 만들지 않는다.

```sql
COMMENT ON COLUMN followup_question.target_node_id IS
'질문 클릭 뒤 새 중심으로 탐색할 node. 질문의 완전한 답이나 두 node 사이의 근거 있는 Relation을 뜻하지 않는다.';
```

## 5. #46 복합 FK 제공

이 Issue는 `publication_affected_node`가 참조할 다음 고유키를 제공한다.

```text
node_search_document(node_search_document_id, node_id)

node_embedding(
  node_embedding_id,
  node_search_document_id,
  node_id
)

node_context(
  node_context_id,
  node_search_document_id,
  node_id
)
```

따라서 READY가 선택한 검색 문서·임베딩·context가 다른 node 또는 다른 검색 문서에 속하면 FK가 거절한다.

## 6. `vector(n)` blocker

| 항목 | 상태 |
|---|---|
| 타입 family `vector(n)` | 확정 |
| 한 POC 모델·한 벡터 공간 | 확정 |
| 차원 `n` | #78 OPEN blocker |
| 거리 연산자 | #78 OPEN blocker |
| query 입력 규칙 | #78 OPEN blocker |
| 초기 exact search | 확정 |
| HNSW·IVFFlat | #81의 측정 기준을 충족하기 전 제외 |

#78이 닫히기 전에는 `node_embedding` migration을 최종 확정하지 않는다. 다른 네 파생 테이블과 FK·수명주기 설계는 차단하지 않는다.

## 7. 비영속 지도·검색 상태

다음 항목은 테이블이나 컬럼으로 만들지 않는다.

- `display_rule_version`
- node·relation 밝기와 opacity
- 시간 감쇠 값
- 중심·직접·2단계·주변부 역할 저장값
- 중심별 지도 구성원과 응답 snapshot
- x·y·z 좌표, 카메라, viewport, force layout
- RRF 결과와 검색 session
- 후속 질문 후보 목록

90일·1년, node·relation 상한과 정렬은 애플리케이션 조회 계약이다.

## 8. 수명주기와 되돌리기

| 대상 | 분류 | 수정 가능 값 |
|---|---|---|
| node embedding | 불변 모델 산출물 | 없음 |
| node context | 불변 모델 산출물 | 없음 |
| followup question | 불변 질문 bundle 구성원 | 없음 |

### 독립 질문 set 도입

질문만 context와 독립적으로 자주 변경해야 하면 다음 순서로 전환한다.

1. `followup_question_set`과 `node_context_id`, 생성 task를 추가한다.
2. 기존 context별 질문 두 행을 set으로 backfill한다.
3. publication 선택에 `followup_question_set_id`를 추가한다.
4. 기존 `(node_context_id, slot)` 고유성을 `(question_set_id, slot)`으로 전환한다.
5. READY 검증과 query를 새 set 기준으로 바꾼 뒤 기존 직접 연결 의미를 제거한다.

### 여러 embedding 공간

여러 모델을 동시에 운영해야 하면 모델별 물리 컬럼·partition 또는 별도 table을 검토한다. 차원 없는 vector와 row별 dimension을 임시로 사용하지 않는다.

### 근사 검색

#81에서 공개 임베딩 수, 실제 query 실행 계획, p95 응답 시간과 검색 품질을 측정한다. exact search가 기준을 충족하지 못할 때만 HNSW와 IVFFlat의 recall, 인덱스 생성·갱신 비용을 비교한다. 모델이 바뀌면 서로 다른 임베딩 공간을 섞지 않고 재임베딩과 인덱스 재생성 경계를 함께 검증한다.
