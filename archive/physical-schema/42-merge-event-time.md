# #42 노드 병합과 사건 시간의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #42
- 주 문서: [`42-ontology-node-identity.md`](42-ontology-node-identity.md)
- 범위: `node_merge`, `event_temporal_extent`
- 보류: 사람 처리자 FK

## 1. 공통 원칙

- 병합과 사건 시간은 기준 node ID를 참조하지만 node의 이름이나 세부 사실을 직접 수정하지 않는다.
- 삭제로 이력을 없애지 않는다.
- 자기 참조·nullable 조합 같은 행 내부 규칙은 DB가 막는다.
- 병합 cycle, 최종 canonical node, EVENT 유형과 서로 다른 precision의 시간 순서는 서비스 트랜잭션이 검사한다.
- 같은 교차 행 규칙을 DB trigger로 중복 구현하지 않는다.
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.

## 2. `node_merge`

### 2.1 책임

동일 대상으로 확인된 기존 node ID를 기준 node ID로 해석하기 위한 리디렉션 이력이다. alias 변경이나 FK 일괄 이동이 아니다.

### 2.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `node_merge_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `source_node_id` | `bigint` | 불가 | 없음 | 불변 |
| `canonical_node_id` | `bigint` | 불가 | 없음 | 불변 |
| `merge_reason` | `text` | 불가 | 없음 | 불변 |
| `merged_at` | `timestamptz` | 불가 | 없음 | 불변 |
| `reversed_reason` | `text` | 허용 | 없음 | `NULL → 값` 한 번만 허용 |
| `reversed_at` | `timestamptz` | 허용 | 없음 | `NULL → 값` 한 번만 허용 |

`merged_by`, `reversed_by` 또는 자유 문자열 actor를 만들지 않는다. 관리자·인증 계약이 정해질 때 정확한 actor FK를 후속 설계한다.

### 2.3 PK·FK·고유성

- PK `pk_node_merge (node_merge_id)`
- FK `fk_node_merge__source (source_node_id → node.node_id)`
- FK `fk_node_merge__canonical (canonical_node_id → node.node_id)`
- 활성 병합은 원본당 최대 하나:

```sql
CREATE UNIQUE INDEX uq_node_merge__active_source
ON node_merge (source_node_id)
WHERE reversed_at IS NULL;
```

### 2.4 행 내부 CHECK

- `source_node_id <> canonical_node_id`
- `btrim(merge_reason) <> ''`
- `isfinite(merged_at)`
- 취소 정보는 함께 존재한다.

```text
reversed_at IS NULL     ↔ reversed_reason IS NULL
reversed_at IS NOT NULL ↔ reversed_reason IS NOT NULL
```

- `reversed_reason`이 있으면 nonblank
- `reversed_at`이 있으면 finite이며 `reversed_at >= merged_at`

DB 제약만으로 이미 채워진 취소 정보를 다시 `NULL`로 바꾸는 행위를 완전히 막지는 않는다. runtime repository는 전용 `reverse_merge` 전이만 노출하고 범용 UPDATE를 제공하지 않는다.

### 2.5 서비스 병합 트랜잭션

1. source와 target node를 ID 오름차순으로 잠근다.
2. source의 현재 활성 merge가 없는지 확인한다.
3. target에서 활성 merge 연쇄를 재귀 조회해 최종 canonical node를 찾는다.
4. 연쇄에 source가 있으면 cycle이므로 거절한다.
5. target 대신 최종 canonical node를 저장한다.
6. 활성 source 부분 고유성 충돌을 다시 확인하고 insert한다.
7. 한 트랜잭션으로 커밋한다.

검색과 조회도 alias에서 찾은 node ID의 활성 merge 연쇄를 따라 최종 node를 얻는다. 원본 node의 alias·Claim·관계·Evidence Trace를 canonical node로 물리 이동하지 않는다.

### 2.6 취소

- 취소는 기존 행의 `reversed_reason`, `reversed_at`을 한 번 채운다.
- 행을 삭제하지 않고 다시 활성화하지 않는다.
- 같은 source를 다시 병합하면 새 `node_merge` 행을 만든다.
- 취소 뒤 조회는 해당 행을 더 이상 활성 리디렉션으로 사용하지 않는다.

### 2.7 인덱스

- `uq_node_merge__active_source`가 source에서 현재 target 조회를 지원한다.
- 현재 canonical node로 병합된 source 목록:

```sql
CREATE INDEX ix_node_merge__active_canonical
ON node_merge (canonical_node_id, source_node_id)
WHERE reversed_at IS NULL;
```

- 전체 병합 이력은 `ix_node_merge__source_history (source_node_id, merged_at DESC)`로 조회한다. 활성 부분 고유 인덱스만으로 이력 시각 정렬을 지원하지 못하므로 별도로 둔다.

### 2.8 주석

```sql
COMMENT ON TABLE node_merge IS
'동일 대상으로 확인된 source node ID를 canonical node ID로 해석하는 리디렉션 이력. alias 변경이나 기존 근거의 물리 이동이 아니다.';

COMMENT ON COLUMN node_merge.reversed_at IS
'값이 없으면 활성 병합, 값이 있으면 취소된 과거 병합이다. 취소 행을 삭제하거나 다시 활성화하지 않는다.';
```

## 3. `event_temporal_extent`

### 3.1 책임

사건 node의 화면·필터용 채택 시간 범위를 최대 한 행으로 저장한다. 출처가 주장한 모든 시간은 Claim과 `event_temporal_basis`에 보존하며, 이 행은 그중 채택된 대표 범위다.

### 3.2 컬럼

| 컬럼 | 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `event_node_id` | `bigint` | 불가 | 없음 | 공유 PK, 대상 불변 |
| `start_at` | `timestamptz` | 허용 | 없음 | 채택 근거 변경 시 통제된 수정 |
| `end_at` | `timestamptz` | 허용 | 없음 | 채택 근거 변경 시 통제된 수정 |
| `start_precision` | `text` | 불가 | 없음 | 값과 함께 통제된 수정 |
| `end_precision` | `text` | 불가 | 없음 | 값과 함께 통제된 수정 |

별도 `start_unknown`, `end_unknown` Boolean을 두지 않는다.

### 3.3 PK·FK

- PK `pk_event_temporal_extent (event_node_id)`
- FK `fk_event_temporal_extent__node (event_node_id → node.node_id)`
- node 한 개에 사건 시간 행은 최대 하나다.

### 3.4 행 내부 CHECK

- 각 precision은 `INSTANT`, `DAY`, `MONTH`, `YEAR`, `UNKNOWN` 중 하나다.
- 시작 경계:

```text
start_at IS NULL     ↔ start_precision = 'UNKNOWN'
start_at IS NOT NULL ↔ start_precision <> 'UNKNOWN'
```

- 종료 경계도 같은 조합을 사용한다.
- 값이 있는 timestamp는 finite다.

DB는 정규화 anchor만 보고 `start_at <= end_at`을 강제하지 않는다. 다음처럼 서로 다른 precision에서 anchor의 단순 비교가 의미 범위와 다를 수 있기 때문이다.

```text
start: 2026년 전체 / YEAR
end:   2026-03-10 / DAY
```

### 3.5 정규화와 표시

| 원문에서 아는 값 | 저장값 예 | precision | 사용자 의미 |
|---|---|---|---|
| 정확한 시각 | 실제 UTC instant | `INSTANT` | 정확한 순간 |
| 2026-08-20 | 해당 날짜 시작 anchor | `DAY` | 그 날짜 |
| 2026년 8월 | `2026-08-01 00:00:00+00` | `MONTH` | 8월 전체 |
| 2026년 | `2026-01-01 00:00:00+00` | `YEAR` | 2026년 전체 |
| 미상 | `NULL` | `UNKNOWN` | 알려진 경계 없음 |

월·연도 anchor를 실제 1일 발생으로 표시하거나 노드 활동량에 사용하지 않는다.

### 3.6 서비스 검증

사건 시간 채택·수정 트랜잭션은 다음을 확인한다.

1. `event_node_id`의 `node_type_code = 'EVENT'`다.
2. start와 end를 precision-aware 가능한 범위로 확장한다.
3. 가능한 모든 시작이 가능한 모든 종료보다 뒤라면 거절한다.
4. 채택 범위를 직접 뒷받침하는 `event_temporal_basis` Claim이 하나 이상 존재한다.
5. Claim에는 observation이 최소 하나 있고 공개 가능한 상태다.
6. 기존과 다른 시간이 주장되면 기존 Claim을 덮어쓰지 않고 conflict 후보로 보존한다.

### 3.7 인덱스

- PK가 사건 node별 상세 조회를 지원한다.
- 시간 범위 후보 조회용:

```sql
CREATE INDEX ix_event_temporal_extent__start
ON event_temporal_extent (start_at)
WHERE start_at IS NOT NULL;
```

복잡한 overlap 검색이나 GiST range 인덱스는 실제 쿼리와 규모가 확인되기 전에는 추가하지 않는다. 기본 지도 활동량은 사건 시간이 아니라 `source_document.published_at`을 사용한다.

### 3.8 주석

```sql
COMMENT ON TABLE event_temporal_extent IS
'사건 node의 채택 시간 범위. 출처별 모든 주장 시간을 저장하는 곳이 아니며 근거 Claim은 event_temporal_basis로 연결한다.';

COMMENT ON COLUMN event_temporal_extent.start_at IS
'precision이 MONTH 또는 YEAR이면 범위 계산을 위한 시작 anchor다. 실제 월 1일 또는 1월 1일 발생을 뜻하지 않는다.';
```

## 4. 수명주기와 권한

| 대상 | 분류 | 허용 변경 |
|---|---|---|
| `node_merge` 핵심 컬럼 | 불변 이력 | 없음 |
| `node_merge` 취소 컬럼 | 한 방향 종료 | `NULL → 값` 한 번 |
| `event_temporal_extent` | 채택된 현재 투영 | 근거가 바뀐 승인 트랜잭션에서만 값·precision 함께 수정 |

- runtime에는 범용 DELETE를 허용하지 않는다.
- merge core를 수정하거나 취소를 되돌리는 repository 함수를 만들지 않는다.
- 사건 시간 변경은 Claim·basis·conflict 검증과 같은 트랜잭션 경계에서 수행한다.

## 5. 선택과 되돌리기

### 병합 cycle을 DB trigger로 옮기는 대안

직접 SQL 우회를 DB가 차단해야 할 운영 요구가 생기면 recursive CTE를 사용하는 deferred constraint trigger를 별도 Issue로 설계할 수 있다. 현재는 서비스와 trigger에 같은 cycle 규칙을 중복 구현하지 않는다.

### 처리자 FK 추가

관리자·인증 모델이 확정되면 `merged_by_actor_id`, `reversed_by_actor_id`를 실제 actor PK에 연결한다. 임시 문자열을 backfill 기준으로 사용하지 않는다.

### 사건 시간 이력

채택 시간 자체의 변경 이력이 제품 요구가 되면 현재 행을 덮어쓰는 대신 `event_temporal_extent_revision`과 current 선택 구조를 별도 Issue에서 검토한다. 현재 Claim·basis·conflict가 원문 주장 이력을 보존하므로 POC에는 추가하지 않는다.
