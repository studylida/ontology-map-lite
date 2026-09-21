# #41 준비 문서와 근거의 PostgreSQL 물리 매핑

## 문서 상태

- 관련 Issue: #41
- 공통 규칙: [`physical-data-schema.md`](../../docs/data/physical-schema.md)
- 범위: `evidence_group`, `source_document`, `observation`
- 제외: 자료 발견·크롤링·HTTP 이력, 근거 묶음 판정 알고리즘, Claim과 alias 연결, DDL·migration

## 1. 최종 구조

```text
evidence_group
        ↑
        │ source_document.evidence_group_id NOT NULL
source_document
        │
        └── observation
```

모든 준비 문서는 저장 시점부터 정확히 하나의 독립 근거 묶음에 속한다. 재분류 이력은 저장하지 않으며 승인된 정정 경로가 `source_document.evidence_group_id`를 직접 바꾼다. `evidence_group_assignment`는 만들지 않는다.

## 2. `evidence_group`

### 2.1 책임

같은 원문 계보에서 파생된 여러 문서를 독립 근거 하나로 세기 위한 최소 식별자다. 출처 신뢰도, 객관적 진실, 대표 문서와 근거 강도 점수를 저장하지 않는다.

### 2.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `evidence_group_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

### 2.3 키와 제약

- PK `pk_evidence_group (evidence_group_id)`
- `ck_evidence_group__created_at_finite`: `isfinite(created_at)`
- 모든 FK는 `ON DELETE RESTRICT ON UPDATE RESTRICT`

### 2.4 인덱스

PK 인덱스 외의 인덱스는 만들지 않는다. 묶음별 문서 조회는 자식 `source_document.evidence_group_id` 인덱스가 지원한다.

### 2.5 DB 주석

```sql
COMMENT ON TABLE evidence_group IS
'같은 원문 계보로 판단된 문서를 독립 근거 하나로 세기 위한 최소 묶음. 출처 신뢰도나 사실의 진실성을 뜻하지 않는다.';
```

## 3. `source_document`

### 3.1 책임

제품 밖에서 준비한 정규화 문서의 불변 버전을 저장한다. 같은 논리 자료의 본문이나 Evidence Trace에 영향을 주는 메타데이터가 달라지면 기존 행을 수정하지 않고 다음 `version_no` 행을 만든다.

### 3.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `source_document_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `evidence_group_id` | `bigint` | 불가 | 없음 | 통제된 분류 정정만 허용 |
| `source_key` | `text` | 불가 | 없음 | 불변 |
| `version_no` | `integer` | 불가 | 없음 | 불변 |
| `canonical_url` | `text` | 불가 | 없음 | 불변 |
| `publisher_name` | `text` | 불가 | 없음 | 불변 |
| `title` | `text` | 불가 | 없음 | 불변 |
| `author_text` | `text` | 허용 | 없음 | 불변 |
| `original_language` | `text` | 불가 | 없음 | 불변 |
| `normalized_body` | `text` | 불가 | 없음 | 불변 |
| `body_hash` | `bytea` | 불가 | 없음 | 불변 |
| `published_at` | `timestamptz` | 허용 | 없음 | 불변 |
| `published_precision` | `text` | 불가 | 없음 | 불변 |
| `source_modified_at` | `timestamptz` | 허용 | 없음 | 불변 |
| `modified_precision` | `text` | 불가 | 없음 | 불변 |
| `last_checked_at` | `timestamptz` | 불가 | 없음 | 운영 갱신 가능 |
| `last_check_status` | `text` | 불가 | 없음 | 운영 갱신 가능 |
| `created_at` | `timestamptz` | 불가 | `CURRENT_TIMESTAMP` | 불변 |

### 3.3 PK·FK·고유성

- PK `pk_source_document (source_document_id)`
- FK `fk_source_document__evidence_group`
  - `evidence_group_id → evidence_group.evidence_group_id`
  - `ON DELETE RESTRICT ON UPDATE RESTRICT`
- UNIQUE `uq_source_document__version (source_key, version_no)`

`body_hash`만으로 문서 버전을 고유하게 만들지 않는다. 동일 본문이 여러 URL·발행처에서 재게시될 수 있기 때문이다.

### 3.4 행 내부 CHECK

- `ck_source_document__version_positive`: `version_no >= 1`
- 다음 필드는 `btrim(value) <> ''`
  - `source_key`
  - `canonical_url`
  - `publisher_name`
  - `title`
  - `original_language`
- `author_text`가 있으면 `btrim(author_text) <> ''`
- `ck_source_document__body_nonempty`: `char_length(normalized_body) > 0`
  - DB가 본문을 trim하거나 정규화해서 다시 쓰지 않는다.
- `ck_source_document__body_hash_length`: `octet_length(body_hash) = 32`
- `published_precision`, `modified_precision`은 `INSTANT`, `DAY`, `MONTH`, `YEAR`, `UNKNOWN` 중 하나
- 게시 시점 조합:

```text
published_at IS NULL     ↔ published_precision = 'UNKNOWN'
published_at IS NOT NULL ↔ published_precision <> 'UNKNOWN'
```

- 수정 시점도 같은 조합을 적용한다.
- 값이 있는 `published_at`, `source_modified_at`, `last_checked_at`, `created_at`은 finite timestamp다.
- `last_check_status IN ('SUCCESS', 'FAILED')`

서로 다른 precision의 게시·수정 시점 순서는 raw anchor만 비교하지 않는다. 필요한 의미 검사는 준비 레이어가 precision-aware 범위로 수행한다.

### 3.5 버전 생성과 동시성

자료 준비 서비스는 다음 순서를 한 짧은 트랜잭션에서 수행한다.

1. `source_key`의 최신 행을 잠그거나 조회한다.
2. 새 입력과 최신 행의 불변 필드를 canonical form으로 비교한다.
3. 모든 불변 필드가 같으면 `last_checked_at`, `last_check_status`만 갱신한다.
4. 하나라도 다르면 `version_no + 1` 행을 삽입한다.
5. `(source_key, version_no)` 충돌 시 최신 행을 다시 읽고 동일성 판정을 반복한다.

별도 `version_fingerprint`는 만들지 않는다. 본문은 `body_hash`, 나머지 불변 메타데이터는 관계형 컬럼으로 직접 비교한다.

### 3.6 인덱스

- `uq_source_document__version`이 같은 자료의 버전 조회를 지원한다.
- `ix_source_document__body_hash (body_hash)`
  - 정확한 본문 복제 후보 조회
- `ix_source_document__evidence_group (evidence_group_id, source_document_id)`
  - 묶음별 문서 조회
- URL·발행처·언어·상태 단독 인덱스는 현재 승인된 조회 경로가 없어 만들지 않는다.

### 3.7 DB 주석

```sql
COMMENT ON TABLE source_document IS
'제품 밖에서 준비한 정규화 문서의 불변 버전. 발견·GDELT·크롤링·HTML·HTTP 시도는 저장하지 않는다.';

COMMENT ON COLUMN source_document.body_hash IS
'normalized_body의 UTF-8 바이트 SHA-256. 문서 행을 합치는 키가 아니라 정확한 본문 복제 후보와 근거 묶음 판정에 사용한다.';

COMMENT ON COLUMN source_document.last_checked_at IS
'준비 레이어가 같은 자료를 마지막으로 확인한 시점. 출처 게시 시점이나 노드 활동량 계산 시점이 아니다.';

COMMENT ON COLUMN source_document.evidence_group_id IS
'현재 독립 근거 계보 묶음. 재배정 이력은 보존하지 않으며 승인된 정정 경로만 수정할 수 있다.';
```

## 4. `observation`

### 4.1 책임

특정 불변 문서 버전의 정확한 Unicode 문자 범위에서 Claim 또는 alias의 근거를 발견했다는 기록이다. 이 행은 출처가 해당 내용을 말했다는 점을 입증하지만 객관적 진실을 입증하지 않는다.

### 4.2 컬럼

| 컬럼 | PostgreSQL 타입 | NULL | 기본값 | 변경 규칙 |
|---|---|---:|---|---|
| `observation_id` | `bigint` | 불가 | `GENERATED ALWAYS AS IDENTITY` | 불변 |
| `source_document_id` | `bigint` | 불가 | 없음 | 불변 |
| `start_char` | `integer` | 불가 | 없음 | 불변 |
| `end_char` | `integer` | 불가 | 없음 | 불변 |
| `quote_text` | `text` | 불가 | 없음 | 불변 |
| `quote_hash` | `bytea` | 불가 | 없음 | 불변 |
| `paragraph_number` | `integer` | 허용 | 없음 | 불변 |
| `observed_at` | `timestamptz` | 불가 | 없음 | 불변 |

### 4.3 PK·FK·고유성

- PK `pk_observation (observation_id)`
- FK `fk_observation__source_document`
  - `source_document_id → source_document.source_document_id`
  - `ON DELETE RESTRICT ON UPDATE RESTRICT`
- UNIQUE `uq_observation__document_range (source_document_id, start_char, end_char)`

정확히 같은 문서 범위는 하나의 observation을 재사용한다. 부분적으로 겹치는 다른 범위는 허용한다.

### 4.4 행 내부 CHECK

- `start_char >= 0`
- `end_char > start_char`
- `char_length(quote_text) = end_char - start_char`
- `octet_length(quote_hash) = 32`
- `paragraph_number IS NULL OR paragraph_number >= 1`
- `isfinite(observed_at)`

`quote_text`는 자동 trim하지 않는다.

### 4.5 서비스 검증

다른 테이블의 본문을 읽어야 하는 다음 규칙은 observation 생성 트랜잭션 전 서비스가 검사한다.

```text
end_char <= char_length(source_document.normalized_body)
substring(normalized_body FROM start_char + 1 FOR end_char - start_char) = quote_text
SHA-256(UTF-8 quote_text) = quote_hash
```

- 오프셋은 UTF-8 byte나 UTF-16 code unit가 아닌 Unicode 문자 인덱스다.
- 본문과 quote는 같은 NFC·LF 정규화 규칙을 사용한다.
- `paragraph_number`가 있으면 승인된 문단 분할 규칙과 일치해야 한다.
- 기존 observation을 새 문서 버전으로 자동 이동하지 않는다.

### 4.6 인덱스

- `uq_observation__document_range`가 문서의 정확한 범위 조회를 지원한다.
- `ix_observation__source_document (source_document_id, observation_id)`는 문서의 모든 observation 조회를 지원한다. 고유 인덱스의 선두 컬럼으로 충분한 실행 계획이 확인되면 이 인덱스는 생략할 수 있으며 #48에서 중복 여부를 최종 확인한다.

### 4.7 DB 주석

```sql
COMMENT ON TABLE observation IS
'불변 source_document의 정확한 Unicode 문자 범위에서 근거를 식별한 기록. 출처의 발화를 증명하지만 객관적 진실을 증명하지 않는다.';

COMMENT ON COLUMN observation.observed_at IS
'시스템이 원문 범위를 근거로 식별한 시점. 출처 게시 시점이나 노드 활동량 계산 시점이 아니다.';
```

## 5. 수명주기와 권한

| 테이블 | 분류 | 허용 변경 |
|---|---|---|
| `evidence_group` | 불변 식별자 | 없음 |
| `source_document` | 불변 버전 + 운영 확인 정보 | `evidence_group_id`의 통제된 정정, `last_checked_at`, `last_check_status` |
| `observation` | 불변 근거 산출물 | 없음 |

- API·worker runtime에는 이 세 테이블의 범용 `DELETE`를 허용하지 않는다.
- `source_document` 불변 메타데이터와 `observation`을 UPDATE하는 일반 repository 함수는 만들지 않는다.
- 의도적 전체 삭제는 FK cascade가 아니라 향후 controlled purge 작업에서 영향 범위를 계산한 뒤 처리한다.

## 6. 지원해야 할 조회

1. `source_key`의 모든 버전과 최신 버전 조회
2. 정확한 `body_hash`를 가진 문서 후보 조회
3. 한 `evidence_group`의 문서 목록 조회
4. 문서의 정확한 문자 범위 observation 재사용
5. observation에서 원문 URL·제목·게시 시점·본문으로 이동
6. 이후 #43의 Claim과 #42의 alias에서 observation을 역방향으로 조회

## 7. 선택과 되돌리기

### 채택한 단순안

`source_document.evidence_group_id`가 current group을 직접 가진다.

### 수용한 손실

과거에 다른 group으로 판정했던 이력, 판정자와 재분류 이유를 DB에서 재현하지 않는다.

### 되돌리기

재분류 감사가 필요하면 별도 Issue에서 다음을 한 번에 전환한다.

1. `evidence_group_assignment` 생성
2. 기존 문서별 current assignment backfill
3. assignment 기간 비중복과 current 최대 한 건 제약
4. `source_document.evidence_group_id` 제거 또는 assignment 기반 view로 대체
5. 모든 집계·조회·FK를 assignment 기준으로 변경

두 표현을 동시에 source of truth로 유지하지 않는다.
