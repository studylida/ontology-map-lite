# #47 검색 문서와 전문 검색의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #47
- 선행 매핑: #43, #46
- 범위: `node_search_document`, `search_document_basis`, alias·전문 검색 경계
- 별도 문서: 임베딩·맥락 설명·후속 질문

## 1. 설계 원칙

- 검색 결과의 기준 대상은 node다. Claim과 source document를 메인 결과로 반환하지 않는다.
- alias 정확 일치, 가중 PostgreSQL 전문 검색, pgvector 검색을 서로 다른 branch로 계산한다.
- alias 정확 일치는 RRF와 무관한 첫 번째 bucket으로 우선 반환한다.
- 검색 문서는 공개 가능한 기준 지식에서 일반 코드가 결정적으로 만드는 불변 버전이다.
- `node_context.context_text`를 검색 문서로 되돌려 넣지 않는다.
- `search_document_basis`는 검색 문서의 공개 지식 계보이지 벡터 점수의 문장 단위 원인 증명이 아니다.
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`다.

## 2. `node_search_document`

### 2.1 책임

한 node를 alias·키워드·벡터 검색의 공통 대상으로 만드는 두 텍스트와 정확한 생성 입력 hash를 보존한다.

### 2.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `node_search_document_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `node_id` | `bigint` | 불가 | 없음 | 불변 |
| `identity_text` | `text` | 불가 | 없음 | 불변 |
| `knowledge_text` | `text` | 불가 | 없음 | 불변 |
| `input_hash` | `bytea` | 불가 | 없음 | 불변 |
| `generator_version` | `text` | 불가 | 없음 | 불변 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

### 2.3 텍스트 구성

#### `identity_text`

- 현재 대표 alias
- 검색 가능한 모든 alias
- node type 표시
- generator가 승인한 안정된 식별 표현

대표 alias를 먼저 두고 나머지 alias는 `language`, 정규화 문자열, `node_alias_id`의 결정적 순서로 배치한다.

#### `knowledge_text`

- 공개 가능한 원자 Claim
- 관계 유형과 주변 node의 대표 alias
- 사건과 구조화 속성값의 읽을 수 있는 표현
- generator가 승인한 고정 구분자

지식 항목은 `item_kind`, 내부 ID 순으로 결정적으로 정렬한다. source 문서 전체와 `node_context.context_text`는 넣지 않는다.

두 필드는 UTF-8, Unicode NFC, LF를 사용하며 DB가 자동 trim하거나 다시 쓰지 않는다. generator는 두 텍스트가 비어 있지 않게 구성한다. 관계가 없는 node도 node type과 근거 Claim 등 공개 가능한 상세 정보로 `knowledge_text`를 만든다.

### 2.4 PK·FK·고유성

- PK `pk_node_search_document (node_search_document_id)`
- FK `fk_node_search_document__node (node_id → node.node_id)`
- UNIQUE `uq_node_search_document__version (node_id, input_hash, generator_version)`
- #46과 하위 artifact의 composite FK 대상:

```text
UNIQUE (node_search_document_id, node_id)
```

PK가 이미 ID를 고유하게 하지만, 이 중복 참조 키는 자식 row의 node가 검색 문서 node와 같은지 복합 FK로 검증하기 위해 의도적으로 둔다.

### 2.5 CHECK

- `char_length(identity_text) > 0`
- `char_length(knowledge_text) > 0`
- `octet_length(input_hash) = 32`
- `btrim(generator_version) <> ''`
- `isfinite(created_at)`

### 2.6 `input_hash` canonical input

SHA-256 입력은 다음 고정 binary framing을 사용한다.

```text
ASCII "NSD1"
+ node_id signed int64 big-endian
+ identity_text UTF-8 byte length uint64 big-endian + bytes
+ knowledge_text UTF-8 byte length uint64 big-endian + bytes
+ basis item count uint64 big-endian
+ knowledge_item_id를 오름차순으로 각각 signed int64 big-endian
```

`generator_version`은 UNIQUE의 별도 구성원이며 hash 입력에 중복하지 않는다. 텍스트 선택·정렬·구분 규칙이 바뀌면 generator version을 올리고 새 행을 만든다.

### 2.7 전문 검색 expression index

POC는 별도 `tsvector` 컬럼을 저장하지 않고 다음 expression과 동일한 GIN index를 사용한다. `simple` config는 최종 한국어 검색 방식이 아니라 POC 기준선이다.

```sql
CREATE INDEX ix_node_search_document__fts
ON node_search_document
USING GIN ((
    setweight(to_tsvector('simple', identity_text), 'A')
    ||
    setweight(to_tsvector('simple', knowledge_text), 'B')
));
```

- query도 정확히 같은 `simple` config와 expression을 사용한다.
- 향후 검색 구현은 FTS expression과 조회 조합을 한 검색 모듈에서 소유하고, index migration과 회귀 테스트도 같은 계약을 기준으로 삼는다. 현재는 구현 코드가 없으므로 문서에 전환 경계만 기록한다.
- `identity_text`를 A, `knowledge_text`를 B 가중치로 둔다.
- rank는 PostgreSQL `ts_rank_cd`를 우선 평가한다.
- 이 순위 함수를 BM25라고 부르지 않는다.
- 한국어 형태소 분석기·`pg_trgm`·BM25 확장은 POC corpus 품질 검증 없이 추가하지 않는다.

### 2.8 일반 인덱스

- `ix_node_search_document__node (node_id, created_at DESC, node_search_document_id DESC)`
- UNIQUE version 인덱스가 같은 입력 재사용을 지원한다.
- expression GIN이 전문 검색 후보를 지원한다.

### 2.9 주석

```sql
COMMENT ON TABLE node_search_document IS
'공개 가능한 한 node를 키워드·벡터 검색의 공통 대상으로 만드는 불변 텍스트 버전. 생성된 node_context를 입력으로 되돌려 넣지 않는다.';

COMMENT ON COLUMN node_search_document.knowledge_text IS
'결정적으로 정렬한 공개 기준 지식의 검색 표현. source 문서 전체나 생성된 맥락 설명을 복사한 필드가 아니다.';
```

## 3. `search_document_basis`

### 3.1 책임

검색 문서가 입력으로 사용한 공개 `knowledge_item`을 추적한다. alias 자체는 `node_alias`에서 직접 설명하며 basis를 벡터 점수의 정확한 문장별 기여라고 해석하지 않는다.

### 3.2 컬럼·키·FK

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 |
|---|---|---:|---|
| `node_search_document_id` | `bigint` | 불가 | 없음 |
| `knowledge_item_id` | `bigint` | 불가 | 없음 |

- PK `pk_search_document_basis (node_search_document_id, knowledge_item_id)`
- FK `node_search_document_id → node_search_document`
- FK `knowledge_item_id → knowledge_item`
- 별도 contribution kind를 저장하지 않는다. `knowledge_item.item_kind`에서 읽는다.

### 3.3 인덱스

```sql
CREATE INDEX ix_search_document_basis__knowledge_item
ON search_document_basis (knowledge_item_id, node_search_document_id);
```

- 검색 문서의 전체 basis는 PK가 지원한다.
- 지식 거절·lint 차단 뒤 어떤 검색 문서를 무효화할지 역방향 인덱스가 지원한다.

### 3.4 Evidence Trace

basis가 Claim이면 직접 Claim→observation→source document로 이동한다. node나 relation이면 그 node를 대상으로 하거나 relation을 지지하는 공개 Claim을 거쳐 원문으로 이동한다.

basis 행 자체는 원문 근거가 아니며 사용자에게 그대로 노출하지 않는다.

## 4. 검색 branch 계약

### 4.1 alias 정확 일치

```text
node_alias.alias_text 정확 일치
→ 활성 node_merge 연쇄의 최종 canonical node
→ canonical node의 대표 alias 표시
```

정확 일치 결과를 첫 bucket에 두고 전문·벡터 점수와 섞지 않는다.

### 4.2 전문 검색

- 현재 READY publication이 선택한 검색 문서만 대상으로 한다.
- 상위 50개 node 후보를 반환한다.
- node state, 열린 BLOCKING lint, selected-basis 유효성을 read-time filter로 다시 확인한다.

### 4.3 벡터 검색

- 현재 READY publication이 선택한 호환 embedding만 대상으로 한다.
- 상위 50개 후보를 반환한다.
- 모델·차원·거리 연산자는 #78에서 결정한다.

### 4.4 RRF

두 branch가 성공하면 애플리케이션이 다음 순위만 결합한다.

```text
RRF score = Σ 1 / (60 + branch_rank)
```

- `k = 60`
- 동점은 불변 `node_id` 오름차순
- 한 branch만 성공하면 그 결과를 제공하고 실패 사실을 함께 반환
- 두 branch가 모두 실패하면 검색 오류
- 최대 30개 후속 질문 후보를 만들 때도 같은 branch 계약을 재사용할 수 있다.

RRF 결과나 검색 session을 DB 테이블로 저장하지 않는다.

## 5. 수명주기와 권한

| 대상 | 분류 | 수정 가능 값 |
|---|---|---|
| node search document | 불변 파생 버전 | 없음 |
| search document basis | 불변 계보 | 없음 |

- 새 공개 입력이나 generator version은 새 검색 문서와 전체 basis를 만든다.
- runtime에는 검색 문서·basis UPDATE·DELETE 경로를 제공하지 않는다.
- POC 동안 이전 검색 문서와 basis를 자동 정리하지 않는다.

## 6. 선택과 되돌리기

### 저장 `tsvector`

query의 expression 복제가 늘거나 expression을 자주 변경해야 할 때, 실행 계획에 문제가 생길 때, 또는 운영 중 검색 vector를 직접 관찰해야 할 때만 다음 순서로 전환한다.

1. 동일 expression의 generated 또는 저장 `search_vector` 컬럼을 추가한다.
2. 기존 행을 backfill하고 GIN index를 새 컬럼으로 교체한다.
3. query가 같은 config·가중치를 쓰는지 회귀 테스트한다.
4. expression index를 제거한다.

### 한국어 검색 확장

#80에서 정확한 이름과 별칭, 오타, 부분 일치, 조사·어미 변화, 복합어, 자연어 의미 검색을 현재 POC 기준선으로 먼저 측정한다. 품질이 부족한 항목에 한해 tokenizer·`pg_trgm`·BM25 후보를 비교하며, extension이나 외부 검색 엔진을 먼저 추가하지 않는다.

### basis 세분화

검색 이유 UX에 기여 종류가 실제로 필요하면 자유 text 컬럼을 붙이지 않고 닫힌 `basis_role`을 추가한 새 generator version으로 backfill한다.
