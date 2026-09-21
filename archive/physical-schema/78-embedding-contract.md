# #78 Qwen 임베딩 호환 계약

## 문서 상태

- 관련 Issue: #78
- 선행 설계: #38, #47
- 후속 검증: #48
- 성능 고도화: #81
- 상태: 승인된 물리 설계 계약

이 문서는 `node_embedding.embedding_vector`의 실제 PostgreSQL 타입과 query/document 임베딩 호환 조건을 확정한다. 실제 API 호출 코드, 환경 변수 파일, migration과 검색 API는 후속 구현 Issue에서 작성한다.

## 1. 승인된 조합

| 항목 | 승인값 |
|---|---|
| provider | Alibaba Cloud Model Studio |
| region | Singapore (`ap-southeast-1`) |
| deployment scope | International |
| model ID | `qwen3.7-text-embedding` |
| 출력 종류 | `dense` |
| 벡터 차원 | `1024` |
| PostgreSQL 타입 | `vector(1024)` |
| document 역할 | `text_type = 'document'` |
| query 역할 | `text_type = 'query'` |
| query instruction | 아래 고정 영문 문장 |
| 거리 | cosine distance |
| pgvector 연산자 | `<=>` |
| 검색 방식 | exact nearest-neighbor search |
| branch 후보 수 | 상위 50개 |
| 동점 처리 | `node_id ASC` |
| ANN index | 만들지 않음. #81에서 측정 후 결정 |

공식 Model Studio 문서는 `qwen3.7-text-embedding`이 1024차원을 지원하고 일반 의미 검색에서 1024차원을 성능·비용 균형안으로 권장한다. query와 document 역할 구분, query instruction과 dense 출력은 DashScope SDK 또는 native API에서만 사용할 수 있으므로 OpenAI-compatible endpoint를 이 계약의 실행 경로로 사용하지 않는다.

참고:

- <https://www.alibabacloud.com/help/en/model-studio/embedding>
- <https://www.alibabacloud.com/help/en/model-studio/text-embedding-synchronous-api>
- <https://github.com/pgvector/pgvector>

## 2. API 경계

### 2.1 native DashScope 사용

후속 구현은 Singapore workspace의 native DashScope API를 사용한다.

```text
base URL
https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1
```

실제 workspace ID와 API key는 실행 환경에서 주입한다.

```text
DASHSCOPE_API_KEY
DASHSCOPE_WORKSPACE_ID
```

비밀값과 workspace ID를 데이터베이스 행, migration, 로그 또는 문서 fixture에 하드코딩하지 않는다. SDK와 직접 HTTP 중 어느 Python 호출 방식을 사용할지는 구현 Issue에서 정하되, `text_type`, `instruct`, `dimension`, `output_type`을 모두 명시할 수 있어야 한다.

### 2.2 모델 호환 식별자

문서 임베딩 작업의 `model_task.model_version`에는 다음 안정 문자열을 저장한다.

```text
alibaba-model-studio:ap-southeast-1:qwen3.7-text-embedding:dense:1024:document-v1
```

이 값은 공급자 이름만 나타내는 표시 문자열이 아니라 다음 계약을 함께 식별한다.

- region
- model ID
- dense output
- dimension
- document 역할
- 아래의 document 입력 결합 규칙 버전

query 임베딩은 저장하지 않지만 런타임에서 다음 호환 계약을 사용한다.

```text
alibaba-model-studio:ap-southeast-1:qwen3.7-text-embedding:dense:1024:query-v1
```

query와 document의 역할 문자열은 다르지만 하나의 승인된 비대칭 검색 계약을 이룬다. 다른 provider, region, model ID, dimension, output type, instruction 또는 입력 규칙으로 만든 벡터를 같은 거리 순위에 섞지 않는다.

## 3. document 임베딩 입력

### 3.1 고정 결합 규칙

`node_search_document`의 두 텍스트를 다음 순서와 구분자로 연결한다.

```text
embedding_document_input =
    identity_text
    + "\n\n"
    + knowledge_text
```

`[IDENTITY]`, `[KNOWLEDGE]` 같은 추가 라벨 토큰은 넣지 않는다. 필드의 의미와 결정적 내부 정렬은 #47의 검색 문서 생성 규칙이 책임진다.

입력 표현은 다음 조건을 그대로 유지한다.

- UTF-8
- Unicode NFC
- LF 줄바꿈
- `identity_text`와 `knowledge_text` 모두 nonblank
- DB나 provider adapter가 자동 trim·요약·재정렬하지 않음

`model_task.input_hash`는 위에서 완성된 문자열의 정확한 UTF-8 바이트에 SHA-256을 적용한다. 구분자나 결합 순서가 바뀌면 `document-v1`을 재사용하지 않고 새 계약 버전을 만든다.

### 3.2 길이 초과

provider 입력 한도를 넘는 검색 문서를 조용히 자르지 않는다.

```text
입력 한도 초과
→ EMBEDDING 작업을 재시도 불가능한 입력 오류로 종료
→ 검색 문서 생성 규칙을 줄인 새 generator_version 작성
→ 새 node_search_document 생성
→ 새 embedding 작업 생성
```

같은 `node_search_document_id`에 구현마다 다른 임의 truncation을 적용하지 않는다.

### 3.3 요청 매개변수

문서 임베딩은 다음 값을 명시한다.

```text
model       = qwen3.7-text-embedding
text_type   = document
dimension   = 1024
output_type = dense
instruct    = 없음
```

기본값이 현재 같은 값이더라도 `dimension`과 `output_type`은 호출에서 생략하지 않는다.

## 4. query 임베딩 입력

### 4.1 정규화

사용자 검색어에는 다음 최소 정규화만 적용한다.

1. Unicode NFC
2. CRLF와 CR을 LF로 통일
3. 문자열 양끝 공백 제거
4. 내부 공백, 문장부호와 대소문자는 유지

정규화 뒤 빈 문자열이면 embedding API를 호출하지 않는다.

### 4.2 고정 instruction

query에는 다음 영문 instruction을 정확히 사용한다.

```text
Given a query about people, companies, technologies, topics, or events, retrieve the most relevant knowledge-map node.
```

instruction의 문구가 바뀌면 `query-v1` 호환 계약을 재사용하지 않는다. 새 query 계약 버전과 회귀 fixture 검증이 필요하다.

### 4.3 요청 매개변수

```text
model       = qwen3.7-text-embedding
text_type   = query
dimension   = 1024
output_type = dense
instruct    = 고정 query instruction
```

사용자 검색어가 provider 한도를 넘거나 query embedding 호출이 실패하면 vector branch만 실패 처리한다. PostgreSQL 전문 검색 branch가 성공했다면 그 결과를 제공하고 의미 검색 실패 안내를 함께 반환한다.

## 5. 벡터 저장 검증

서비스는 `node_embedding` 저장 전에 다음을 검사한다.

- 원소 수가 정확히 1024개다.
- 모든 원소가 유한한 실수다.
- L2 norm이 0보다 크다.
- 작업의 `model_task.task_kind = 'EMBEDDING'`이다.
- 작업의 `model_version`이 승인된 document 호환 식별자와 같다.
- 작업 input hash가 정확한 document 결합 입력과 같다.
- 같은 `model_task_id`의 결과 행이 아직 없다.

provider 응답을 애플리케이션이 다시 L2 정규화하지 않는다. 저장값은 응답의 dense float vector이며 검색 거리는 벡터 크기에 덜 민감한 cosine distance를 사용한다.

## 6. PostgreSQL 타입과 exact query

`node_embedding`의 최종 컬럼 타입은 다음과 같다.

```sql
embedding_vector vector(1024) NOT NULL
```

POC에는 HNSW·IVFFlat 인덱스를 만들지 않는다. pgvector는 별도 ANN 인덱스가 없으면 exact nearest-neighbor search를 수행한다.

현재 공개 가능한 node별 최신 READY embedding을 먼저 선택한 뒤 다음 순서로 상위 50개를 구한다.

```sql
WITH current_ready_embedding AS (
    SELECT DISTINCT ON (pan.node_id)
        pan.node_id,
        pan.node_embedding_id
    FROM publication_affected_node AS pan
    JOIN promotion_batch AS pb
      ON pb.promotion_batch_id = pan.promotion_batch_id
    WHERE pb.promotion_status = 'COMMITTED'
      AND pb.publication_status = 'READY'
      AND pan.node_embedding_id IS NOT NULL
    ORDER BY
        pan.node_id,
        pb.ready_at DESC,
        pb.promotion_batch_id DESC
)
SELECT
    cre.node_id,
    ne.embedding_vector <=> CAST(:query_vector AS vector(1024)) AS cosine_distance
FROM current_ready_embedding AS cre
JOIN node_embedding AS ne
  ON ne.node_embedding_id = cre.node_embedding_id
JOIN model_task AS mt
  ON mt.model_task_id = ne.model_task_id
WHERE mt.status = 'SUCCESS'
  AND mt.model_version = :document_model_version
ORDER BY
    cosine_distance ASC,
    cre.node_id ASC
LIMIT 50;
```

실제 검색 서비스는 이 결과에 현재 지식 상태와 열린 `BLOCKING` lint의 read-time filter를 함께 적용한다. query vector와 document vector의 차원 또는 호환 계약이 다르면 SQL을 실행하지 않고 vector branch 실패로 처리한다.

사용자에게 유사도를 보여줄 필요가 있을 때만 다음 값을 계산한다.

```text
cosine_similarity = 1 - cosine_distance
```

이 점수를 문장 단위 인과관계나 동일 대상 판정 근거로 설명하지 않는다.

## 7. 모델 변경 절차

모델·차원·region·입력 계약을 바꿀 때 기존 `node_embedding`을 UPDATE하지 않는다.

1. 새 호환 계약과 model version을 별도 Issue에서 승인한다.
2. 차원이 달라지면 새 물리 컬럼·테이블·migration 경계를 먼저 설계한다.
3. 모든 대상 검색 문서에 새 `model_task`를 만든다.
4. 새 `node_embedding` 행을 생성한다.
5. HBF 한국어·영문 fixture와 검색 실패 강등을 검증한다.
6. 새 `promotion_batch`의 publication을 `PREPARING`으로 둔다.
7. READY 검증이 통과한 뒤 새 embedding을 선택한다.
8. 이전 READY 결과와 이전 embedding 행은 보존한다.

서로 다른 embedding 공간을 한 SQL 거리 순위에 섞지 않는다.

## 8. 제외 범위

- sparse vector 저장과 검색
- 여러 embedding provider의 동시 혼합
- 별도 vector database
- HNSW·IVFFlat 조기 생성
- cross-encoder reranking
- query embedding 영속화
- 모델 registry 테이블
- 실제 API key·workspace 설정

exact search의 확장 기준과 ANN 비교는 #81이 소유한다.

## 9. 완료 기준

- `node_embedding.embedding_vector`를 `vector(1024)`로 구현할 수 있다.
- query와 document 역할·입력·instruction·차원·모델·region이 하나의 호환 계약으로 고정되어 있다.
- exact cosine query와 결정적 tie-breaker가 정의되어 있다.
- 조용한 truncation과 서로 다른 공간의 혼합이 금지되어 있다.
- 모델 변경 시 기존 벡터를 덮어쓰지 않는 절차가 있다.
- HBF 최소 품질 fixture는 별도 #78 fixture 문서에 정의하고 첫 READY 공개 전에 실행한다.
