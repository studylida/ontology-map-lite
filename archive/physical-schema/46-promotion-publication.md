# #46 승격과 공개 제어의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #46
- 선행 매핑: #43, #44, #69
- 범위: `promotion_batch`, `publication_affected_node`, 후속 FK 계약과 READY 검증

## 1. 설계 원칙

- 기준 그래프 승격과 사용자 공개 준비는 같은 batch에서 서로 다른 상태로 관리한다.
- 승격 실패는 기준 지식 쓰기를 전부 롤백한다.
- 공개 실패는 커밋된 기준 지식을 되돌리지 않고 이전 `READY` 결과를 계속 선택한다.
- 전역 graph version·map version·지도 구성원·좌표를 저장하지 않는다.
- `promotion_batch`는 exact `lint_policy_version_id`를 보존하지만 `ontology_version_id`를 갖지 않는다.
- 성공한 지식은 실제 사용한 node type·relation revision·attribute revision을 직접 참조한다.
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.

## 2. `promotion_batch`

### 2.1 책임

함께 승격할 기준 지식의 원자적 쓰기 결과와 그 변경으로 영향받은 노드의 공개 준비 상태를 관리한다.

### 2.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `promotion_batch_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `lint_policy_version_id` | `bigint` | 불가 | 없음 | 불변 |
| `promotion_status` | `text` | 불가 | `'PENDING'` | 허용 전이만 수정 |
| `publication_status` | `text` | 불가 | `'NOT_STARTED'` | 허용 전이만 수정 |
| `started_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |
| `committed_at` | `timestamptz` | 허용 | 없음 | 승격 성공 시 한 번 채움 |
| `ready_at` | `timestamptz` | 허용 | 없음 | 공개 성공 시 한 번 채움 |
| `promotion_failure_reason` | `text` | 허용 | 없음 | 승격 실패 시 한 번 채움 |
| `publication_failure_reason` | `text` | 허용 | 없음 | 공개 실패·재시도 때 갱신·초기화 |

### 2.3 PK·FK·기본 CHECK

- PK `pk_promotion_batch (promotion_batch_id)`
- FK `fk_promotion_batch__lint_policy_version`
- `promotion_status IN ('PENDING', 'COMMITTED', 'FAILED')`
- `publication_status IN ('NOT_STARTED', 'PREPARING', 'READY', 'FAILED')`
- 시각 값은 모두 finite
- `committed_at`이 있으면 `committed_at >= started_at`
- `ready_at`이 있으면 `committed_at`도 있고 `ready_at >= committed_at`
- 실패 이유가 있으면 nonblank

### 2.4 상태 조합 CHECK

하나의 명명된 CHECK는 다음 조합만 허용한다.

#### 승격 대기

```text
promotion_status = 'PENDING'
publication_status = 'NOT_STARTED'
committed_at IS NULL
ready_at IS NULL
promotion_failure_reason IS NULL
publication_failure_reason IS NULL
```

#### 승격 실패

```text
promotion_status = 'FAILED'
publication_status = 'NOT_STARTED'
committed_at IS NULL
ready_at IS NULL
promotion_failure_reason IS NOT NULL
publication_failure_reason IS NULL
```

#### 승격 성공·공개 미시작

```text
promotion_status = 'COMMITTED'
publication_status = 'NOT_STARTED'
committed_at IS NOT NULL
ready_at IS NULL
promotion_failure_reason IS NULL
publication_failure_reason IS NULL
```

#### 공개 준비 중

```text
promotion_status = 'COMMITTED'
publication_status = 'PREPARING'
committed_at IS NOT NULL
ready_at IS NULL
promotion_failure_reason IS NULL
publication_failure_reason IS NULL
```

#### 공개 준비 실패

```text
promotion_status = 'COMMITTED'
publication_status = 'FAILED'
committed_at IS NOT NULL
ready_at IS NULL
promotion_failure_reason IS NULL
publication_failure_reason IS NOT NULL
```

#### 공개 준비 완료

```text
promotion_status = 'COMMITTED'
publication_status = 'READY'
committed_at IS NOT NULL
ready_at IS NOT NULL
promotion_failure_reason IS NULL
publication_failure_reason IS NULL
```

`READY`는 종료 상태다. 새로운 기준 지식이나 정정은 새 batch의 공개 결과로 처리한다. `FAILED → PREPARING` 재시도에서는 `publication_failure_reason`을 `NULL`로 되돌린다.

### 2.5 승격 트랜잭션

1. 외부 호출·계약 검사·승격 전 lint를 모두 마친 뒤 batch를 `PENDING + NOT_STARTED`로 생성한다.
2. 짧은 그래프 트랜잭션을 시작한다.
3. exact lint policy와 사용할 node type·revision의 활성 상태를 다시 읽는다.
4. 모든 `knowledge_item`, subtype, Claim 의미 연결, observation·alias·사건 시간과 affected-node 범위를 만든다.
5. #43의 최소 근거와 subtype 불변식을 검증한다.
6. 모두 성공하면 같은 트랜잭션에서 `promotion_status = 'COMMITTED'`, `committed_at`을 기록한다.
7. 하나라도 실패하면 그래프 트랜잭션을 전부 롤백한다.
8. 롤백된 트랜잭션 밖에서 batch를 `FAILED + NOT_STARTED`로 닫고 승격 실패 이유를 기록한다.

따라서 `knowledge_item.promotion_batch_id` FK는 이 테이블을 참조하며 모든 새 기준 지식은 생성 batch를 직접 추적한다.

### 2.6 공개 상태 전이

```text
NOT_STARTED → PREPARING → READY
                       └→ FAILED → PREPARING
```

- `COMMITTED` batch만 `PREPARING`으로 이동할 수 있다.
- 공개 작업의 세부 provider 재시도는 `model_task`·`agent_attempt`가 소유한다.
- batch에는 publication attempt별 행을 추가하지 않는다.
- 완료된 파생 결과는 재사용하고 실패·누락된 결과만 다시 준비할 수 있다.
- READY 전환 실패는 batch를 `FAILED`로 기록하지만 이미 커밋된 지식을 삭제하지 않는다.

### 2.7 인덱스

```sql
CREATE INDEX ix_promotion_batch__promotion_pending
ON promotion_batch (started_at, promotion_batch_id)
WHERE promotion_status = 'PENDING';

CREATE INDEX ix_promotion_batch__publication_work
ON promotion_batch (publication_status, promotion_batch_id)
WHERE promotion_status = 'COMMITTED'
  AND publication_status IN ('NOT_STARTED', 'PREPARING', 'FAILED');

CREATE INDEX ix_promotion_batch__ready
ON promotion_batch (ready_at DESC, promotion_batch_id DESC)
WHERE promotion_status = 'COMMITTED'
  AND publication_status = 'READY';
```

### 2.8 주석

```sql
COMMENT ON TABLE promotion_batch IS
'기준 지식의 원자 승격 결과와 그 변경의 공개 준비 상태를 분리해 관리한다. 전체 활성 온톨로지 snapshot이나 지도 버전을 저장하지 않는다.';

COMMENT ON COLUMN promotion_batch.publication_status IS
'검색 문서·임베딩·맥락·질문의 공개 준비 상태. 기준 그래프 저장 결과인 promotion_status와 별개다.';
```

## 3. `publication_affected_node`

### 3.1 책임

한 batch 때문에 공개 결과를 다시 준비해야 하는 node와 그 batch가 최종 선택한 정확한 검색 문서·임베딩·맥락 설명을 저장한다. 지도 구성원 목록이 아니다.

### 3.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `promotion_batch_id` | `bigint` | 불가 | 없음 | 불변 |
| `node_id` | `bigint` | 불가 | 없음 | 불변 |
| `node_search_document_id` | `bigint` | 허용 | 없음 | READY 전 선택 가능 |
| `node_embedding_id` | `bigint` | 허용 | 없음 | READY 전 선택 가능 |
| `node_context_id` | `bigint` | 허용 | 없음 | READY 전 선택 가능 |

후속 질문 ID를 중복 저장하지 않는다. 선택된 `node_context_id`에서 slot 1·2를 조회한다.

### 3.3 PK·기본 FK

- PK `pk_publication_affected_node (promotion_batch_id, node_id)`
- FK `promotion_batch_id → promotion_batch.promotion_batch_id`
- FK `node_id → node.node_id`

한 batch의 affected node 집합은 기준 그래프 승격 트랜잭션에서 고정하며 COMMITTED 이후 행을 추가·삭제하지 않는다.

### 3.4 #47을 향한 forward 복합 FK

#47 테이블 생성 뒤 post-creation constraint 단계에서 다음 FK를 추가한다.

```text
(node_search_document_id, node_id)
→ node_search_document(node_search_document_id, node_id)

(node_embedding_id, node_search_document_id, node_id)
→ node_embedding(node_embedding_id, node_search_document_id, node_id)

(node_context_id, node_search_document_id, node_id)
→ node_context(node_context_id, node_search_document_id, node_id)
```

기본 `MATCH SIMPLE`을 사용한다. PREPARING 중에는 일부 참조가 `NULL`일 수 있기 때문이다.

행 내부 CHECK는 다음 의존성만 막는다.

```text
node_embedding_id IS NOT NULL → node_search_document_id IS NOT NULL
node_context_id IS NOT NULL   → node_search_document_id IS NOT NULL
```

READY의 세 참조 필수 여부는 batch 전체와 질문·lint를 함께 읽어야 하므로 서비스가 검사한다.

### 3.5 준비 중과 READY 이후

- `PREPARING`·`FAILED`: 성공한 결과 포인터를 유지하고 누락·실패 결과를 재시도할 수 있다.
- `READY`: 세 artifact 포인터는 모두 값이 있어야 하며 이후 수정하지 않는다.
- 더 새로운 batch가 PREPARING·FAILED여도 이전 READY 행은 그대로 유지한다.

### 3.6 인덱스

```sql
CREATE INDEX ix_publication_affected_node__node
ON publication_affected_node (node_id, promotion_batch_id DESC);
```

batch의 모든 node 조회는 PK가 지원한다. artifact ID 단독 역조회는 현재 제품 조회 경로가 없어 인덱스를 추가하지 않는다.

### 3.7 주석

```sql
COMMENT ON TABLE publication_affected_node IS
'한 batch의 공개 준비 영향 범위와 최종 선택 artifact를 저장한다. 지도 구성원·좌표·전체 공개 그래프 snapshot이 아니다.';
```

## 4. READY 전환 서비스 검증

서비스는 batch와 모든 affected-node 행을 잠근 뒤 다음을 한 트랜잭션에서 확인한다.

1. `promotion_status = 'COMMITTED'`, `publication_status = 'PREPARING'`이다.
2. affected node 집합이 비어 있지 않거나, 지식 변화가 사용자 공개 결과에 영향을 주지 않는다는 명시적 no-op 판정이 있다.
3. 각 node에 대표 alias가 정확히 하나 있다.
4. 각 affected row에 검색 문서·임베딩·맥락 설명 세 FK가 모두 있다.
5. 세 artifact가 같은 `node_id`와 같은 `node_search_document_id`를 가리킨다.
6. 임베딩·맥락 작업이 각각 올바른 `task_kind`와 `SUCCESS` 상태다.
7. 선택된 context에는 slot 1·2 질문이 정확히 한 행씩 있다.
8. 질문 작업이 성공했고 각 `target_node_id`가 제공 후보에 포함되며 공개 자격을 갖춘다.
9. 같은 batch 대상은 출발 node와 함께 원자적으로 READY가 될 수 있다.
10. 검색 문서의 모든 `search_document_basis` 지식이 공개 가능한 현재 상태다.
11. 공개 지식에 열린 `BLOCKING` lint finding이 없다.
12. 상세 패널에 필요한 relation·Claim·observation·source document를 조회할 수 있다.
13. 표시할 relation이 있으면 양 endpoint node도 공개 가능하다.
14. 관계 없는 node도 나머지 필수 결과와 상세 자료가 완전하면 허용한다.

모두 통과하면 `publication_status = 'READY'`, `ready_at`을 함께 기록한다. 실패하면 `FAILED`와 정형화된 nonblank 사유를 기록한다.

## 5. node별 최신 READY 선택

한 node의 사용자 결과는 다음 조건으로 선택한다.

```sql
SELECT pan.*
FROM publication_affected_node AS pan
JOIN promotion_batch AS pb
  ON pb.promotion_batch_id = pan.promotion_batch_id
WHERE pan.node_id = :node_id
  AND pb.promotion_status = 'COMMITTED'
  AND pb.publication_status = 'READY'
ORDER BY pb.ready_at DESC, pb.promotion_batch_id DESC
LIMIT 1;
```

- 업무 순서는 `ready_at`이 결정한다.
- `promotion_batch_id`는 동일 시각 tie-breaker일 뿐 시간 의미를 갖지 않는다.
- 더 새로운 PREPARING·FAILED batch는 조회 대상이 아니므로 이전 READY를 계속 제공한다.
- 전역 latest batch 하나를 선택하지 않으므로 영향받지 않은 node까지 새 공개를 기다리지 않는다.

## 6. 거절·lint 차단의 즉시 효과

READY artifact는 불변 이력이지만 read-time eligibility는 현재 지식 상태와 열린 lint를 다시 확인한다.

### node 거절·차단

- node 자체가 `REJECTED`이거나 열린 BLOCKING finding이 있으면 alias를 포함한 모든 검색·지도·상세 결과에서 제외한다.

### Claim·relation 거절·차단

- 해당 지식을 즉시 지도·상세 계산에서 제외한다.
- 최신 선택 검색 문서의 basis에 해당 지식이 포함되어 있으면 그 문서와 임베딩을 일반 하이브리드 검색에서 임시 제외한다.
- node의 `node_alias` 정확 검색은 유지한다.
- 영향을 받은 node에 새 파생 결과를 준비하고 다음 READY 선택이 생기면 하이브리드 검색을 복구한다.

이 필터는 `promotion_batch`나 immutable artifact를 UPDATE·DELETE하지 않는다. 새 공개 준비를 시작하는 애플리케이션 trigger·worker 구현은 이 Issue의 범위가 아니다.

## 7. 수명주기와 권한

| 대상 | 분류 | 수정 가능 값 |
|---|---|---|
| promotion batch identity·policy·started | 불변 | 없음 |
| promotion status | 운영 상태 | 단방향 PENDING→COMMITTED/FAILED |
| publication status | 운영 상태 | 승인된 전이 |
| failure reason | 상태 종속 | 실패·재시도에서 명시적 변경 |
| affected node membership | COMMITTED 이후 불변 | 없음 |
| artifact pointers | 준비 중 선택 | READY 전만 수정, READY 이후 불변 |

runtime repository는 `mark_promotion_committed`, `mark_promotion_failed`, `start_publication`, `record_publication_failure`, `mark_publication_ready` 같은 전용 전이만 노출한다. 범용 상태 UPDATE와 DELETE를 제공하지 않는다.

## 8. 선택과 되돌리기

### 전역 publication revision

전체 화면의 과거 구성을 정확히 재현해야 하면 immutable publication revision과 node·relation 구성원 snapshot을 추가할 수 있다. 이 경우 node별 latest READY 조회를 revision 선택으로 전환해야 하며 두 공개 기준을 동시에 사용하지 않는다.

### publication attempt 이력

공개 재시도별 운영 감사가 필요하면 `publication_attempt`를 추가하고 기존 batch의 마지막 실패 사유·시각을 최초 이력으로 backfill한다. 상태 행과 attempt 이력의 갱신을 한 트랜잭션으로 처리한다.

### 외부 staging

artifact 포인터를 READY 전에 DB에 부분 저장하지 않으려면 외부 staging에 결과를 모은 뒤 affected-node 행을 한 번에 insert할 수 있다. 이 전환에서는 재시도 중 성공 결과 재사용 경로를 별도로 마련해야 한다.
