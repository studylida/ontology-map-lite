# #48 통합 무결성·migration·검증 계약

## 문서 상태

- 관련 Issue: #48
- inventory: [`48-integrated-inventory.md`](48-integrated-inventory.md)
- Qwen 계약: [`78-embedding-contract.md`](78-embedding-contract.md)
- 상태: PostgreSQL DDL 구현 전 최종 통합 설계 검토

이 문서는 #40–#47의 영역별 물리 설계를 함께 실행할 때 적용할 정정 사항, 무결성 책임, 결정적 key, migration 순서와 향후 schema test를 확정한다. 실제 `CREATE TABLE`, Alembic revision, API와 worker 코드는 포함하지 않는다.

---

# 1. 통합 결론

- 물리 설계 테이블은 43개다.
- 초기 migration 대상은 41개다.
- `knowledge_state_event`, `conflict_state_event`는 검증 가능한 actor FK가 생길 때까지 비차단 보류한다.
- #78이 Qwen `vector(1024)`와 cosine `<=>`를 확정했으므로 embedding 차원 blocker는 해소됐다.
- #64의 문서 계보 판정 알고리즘과 #81의 ANN 성능 검증은 실제 구현 후속 작업이며 테이블 DDL을 막지 않는다.
- 영역별 문서가 서로 충돌하면 이 문서와 `48-integrated-inventory.md`의 정정 사항을 우선한다.
- 기준 지식, Evidence Trace, 상태·충돌·lint 이력은 cascade 삭제하지 않는다.

다음 구조는 최종 스키마에 없다.

```text
ontology_version
ontology_member
evidence_group_assignment
display_rule_version
candidate_item
candidate_state_event
structured_output
map_revision
graph_revision
```

---

# 2. 통합 과정에서 닫은 충돌

## 2.1 symmetric endpoint CHECK 제거

이전 #42 본문에는 symmetric endpoint를 오름차순으로 저장하며 모든 `relation_endpoint_rule`에 다음 CHECK를 적용하는 표현이 남아 있다.

```text
source_node_type_id <= target_node_type_id
```

이 CHECK는 만들지 않는다. 한 endpoint 행만으로 연결된 revision의 `directionality`를 알 수 없으므로 ID 순서가 반대인 정상 `DIRECTED` 규칙도 거절할 수 있다.

최종 책임:

```text
DB
→ (revision, source type, target type) PK로 동일 triple 중복 방지

서비스
→ revision directionality 조회
→ SYMMETRIC일 때만 두 type ID를 오름차순 정규화
→ 반대 방향 행을 별도로 만들지 않음
```

## 2.2 embedding blocker 해소

이전 문서의 다음 표현은 #78에 의해 대체된다.

```text
vector(n)
#78 OPEN blocker
```

최종값:

```text
vector(1024)
qwen3.7-text-embedding
Alibaba Cloud Model Studio Singapore
text_type=document / text_type=query
query instruction v1
dense output
cosine distance <=>
exact top 50
```

#78 문서가 모델·입력·호환성·검색 SQL을 소유한다.

## 2.3 폐기 이름 정리

공통 명명 예시에 남은 `evidence_group_assignment`는 실제 객체 이름이 아니다. 초기 migration과 ORM 모델에 만들지 않는다. 현재 구조는 다음 하나다.

```text
source_document.evidence_group_id NOT NULL
```

## 2.4 중복 추측성 인덱스 제외

초기 migration에서 다음 두 인덱스를 만들지 않는다.

```text
ix_observation__source_document
(source_document_id, observation_id)

ix_agent_attempt__task
(model_task_id, attempt_no DESC)
```

이유:

- `uq_observation__document_range (source_document_id, start_char, end_char)`가 문서 선두 조회를 지원한다.
- `uq_agent_attempt__number (model_task_id, attempt_no)` B-tree는 역방향 scan도 가능하다.

실제 `EXPLAIN (ANALYZE, BUFFERS)`에서 필요한 정렬·covering 병목이 확인될 때만 별도 인덱스를 추가한다.

## 2.5 forward FK 해소

영역 문서에서 “후속 제약 단계”로 남겼던 참조는 아래 migration 순서를 따르면 일반 FK로 생성할 수 있다.

```text
knowledge_item.promotion_batch_id
→ promotion_batch를 먼저 생성

node/relation/claim shared PK
→ knowledge_item을 먼저 생성

publication_affected_node artifact FK
→ search document·embedding·context를 먼저 생성
```

따라서 초기 migration을 여러 revision으로 나누더라도 의미상 임시 FK 누락 상태를 최종 schema에 남기지 않는다.

---

# 3. 무결성 책임표

같은 규칙을 DB trigger와 서비스에 장기간 중복 구현하지 않는다.

| 규칙 | 최종 책임 | 이유 |
|---|---|---|
| 필수값·nonblank·finite·hash 길이 | row-local CHECK | 한 행만으로 결정 가능 |
| 닫힌 상태·modality·value kind·precision 목록 | row-local CHECK | 저장 표현 자체의 허용 범위 |
| tagged-union 값 형태 | row-local CHECK | 같은 행의 nullable 조합 |
| promotion/publication 상태와 시각 조합 | row-local CHECK | 한 batch 행에서 결정 가능 |
| conflict target 세 형태 XOR | row-local CHECK | 같은 set 행의 nullable 조합 |
| merge 자기 참조 금지와 reverse all-or-none | row-local CHECK | 같은 merge 행에서 결정 가능 |
| PK·FK 참조 존재 | PK/FK | 관계형 참조 무결성 |
| 문서 version·코드·revision 번호 고유성 | UNIQUE | 업무 고유키 |
| 활성 revision·active policy 최대 한 건 | partial UNIQUE | 조건부 최대 한 행 |
| 대표 alias 최대 한 건 | partial UNIQUE | node별 조건부 최대 한 행 |
| 원본 node별 활성 merge 최대 한 건 | partial UNIQUE | source별 현재 redirect 최대 한 행 |
| 열린 lint finding key 최대 한 건 | partial UNIQUE | 해결 행과 새 재발 행 공존 허용 |
| observation 실제 substring·quote hash 일치 | 서비스 | source document 본문을 함께 읽어야 함 |
| source document 새 version 생성 여부 | 서비스 transaction | 최신 version과 전체 불변 메타데이터 비교·동시성 처리 |
| evidence group 선택 | #64 자료 준비 서비스 | hash·원문 계보·후보 비교가 필요하며 제품 DB 밖 책임 |
| relation revision inverse 상호 일치 | 서비스 transaction | 여러 revision 행을 함께 읽어야 함 |
| relation endpoint type 허용 | promotion 서비스 | 실제 node type과 revision endpoint rule 조인 필요 |
| symmetric endpoint 정규화 | 서비스 | revision directionality 조회 필요 |
| code별 active revision 교체 | 서비스 transaction + partial UNIQUE | 기존 비활성화와 신규 활성화를 원자 처리 |
| merge cycle·최종 canonical node | 서비스 transaction | 재귀 그래프와 잠금 순서 필요 |
| event node가 EVENT 유형인지 | 서비스 | node와 node type 조인 필요 |
| precision-aware 시간 순서 | 서비스 | MONTH/YEAR를 의미 범위로 확장해야 함 |
| knowledge item과 정확히 한 subtype | promotion 서비스 transaction | 여러 subtype 테이블의 행 수 확인 필요 |
| Claim의 observation 최소 한 건 | promotion 서비스 transaction | 연결 테이블 행 수 확인 필요 |
| Claim의 의미 대상 최소 한 건 | promotion 서비스 transaction | relation·attribute·event basis를 함께 확인 |
| relation의 observation 있는 SUPPORT Claim 최소 한 건 | promotion 서비스 transaction | stance·Claim·observation 조인 필요 |
| node의 최소 식별 근거와 EVENT 완결성 | promotion 서비스 transaction | alias·Claim·Relation·시간 basis 조인 필요 |
| `attempt_count = attempt 행 수 = MAX(attempt_no)` | model task 서비스 transaction | task와 append-only attempt를 함께 갱신 |
| `blocked_fingerprint`가 BLOCKING pre-promotion rule만 참조 | 승격 전 검증 서비스 | policy rule severity와 lint rule scope 조인 필요 |
| lint finding 반복·해결·재발 | lint 서비스 transaction | 성공 run 전체 결과와 기존 열린 finding 비교 필요 |
| conflict Claim 비교 가능성·최소 두 position | conflict 서비스 transaction | Claim target·modality·time·member 전체 확인 필요 |
| 동일 conflict snapshot 재사용 | conflict 서비스 transaction | 대상 잠금과 정렬 member 전체 비교 필요 |
| conflict set·member 불변성 | repository 권한 + append-only 서비스 | generic trigger보다 좁은 write API 사용 |
| promotion 기준 지식 원자성 | promotion 서비스 transaction | 여러 기준 테이블을 함께 commit/rollback |
| publication READY 완결성 | publication 서비스 transaction | batch 전체 node·artifact·질문·lint·Evidence Trace 확인 |
| node별 최신 READY 선택 | read-time query | `ready_at DESC, promotion_batch_id DESC` |
| 사람 거절·열린 BLOCKING lint 제외 | read-time filter | 과거 READY artifact를 삭제하지 않고 즉시 숨김 |
| 90일·1년 지도 포함 | read-time query | `source_document.published_at` 기준 동적 투영 |
| alias 검색 뒤 canonical node 해석 | read-time/service query | active merge 연쇄를 따라가야 함 |
| 검색 문서 결정적 생성 | search document generator | alias·공개 지식 정렬과 serialization 필요 |
| Qwen vector 호환성·1024차원 | embedding provider service | API contract와 결과 원소 검사 필요 |
| 후속 질문 slot 1·2 정확히 두 건 | READY 서비스 transaction | row UNIQUE는 최대 두 건만 보장하고 최소 수는 못 보장 |

## 3.1 trigger 정책

초기 POC에는 교차 행 의미를 검사하는 custom constraint trigger를 만들지 않는다.

직접 SQL 우회를 DB가 단독 방어해야 하는 운영 요구가 생기면 다음 절차를 사용한다.

1. 서비스 검증 fixture를 먼저 고정한다.
2. 규칙 하나당 좁은 deferred constraint trigger를 만든다.
3. 서비스의 중복 판정 코드는 제거하고 DB 오류 변환만 남긴다.
4. 한 규칙을 두 구현에서 장기간 유지하지 않는다.

---

# 4. 결정적 key 계약

## 4.1 공통 binary framing

- 문자열은 Unicode NFC, LF, UTF-8 bytes를 사용한다.
- 가변 길이 값은 unsigned 64-bit big-endian byte length 뒤에 bytes를 둔다.
- 내부 ID는 signed 64-bit big-endian이다.
- nullable 값은 생략하지 않고 계약이 정한 명시적 tag로 표현한다.
- 문자열 단순 이어붙이기, locale 정렬과 DB의 암묵적 cast에 의존하지 않는다.

## 4.2 key 목록

| key | canonical input | DB 보장 |
|---|---|---|
| `source_document.body_hash` | `normalized_body`의 정확한 UTF-8 bytes | 32-byte CHECK; 복제 후보 인덱스. 문서 행 UNIQUE는 아님 |
| `observation.quote_hash` | `quote_text`의 정확한 UTF-8 bytes | 32-byte CHECK; substring 일치는 서비스 |
| `relation.relation_identity_key` | ASCII `REL1` + canonical source ID + relation revision ID + canonical target ID | 32-byte CHECK + UNIQUE |
| `model_task.input_hash` | task 계약이 실제 provider에 전달한 전체 canonical input bytes | 32-byte CHECK. task별 serializer fixture 필요 |
| `model_task.cache_key` | ASCII `TASK1` + task kind + input hash + output contract ID 또는 0 + model version + prompt version 또는 빈 값 | 32-byte CHECK + UNIQUE |
| `blocked_fingerprint.fingerprint` | ASCII `BLK1` + candidate kind + 해당 blocking rule이 정의한 normalized identity detail | 32-byte CHECK; 문서·계약·policy rule과 복합 UNIQUE |
| `lint_finding.finding_key` | ASCII `FND1` + lint policy rule ID + knowledge item ID + rule별 normalized identity detail | 32-byte CHECK + 열린 key partial UNIQUE |
| `node_search_document.input_hash` | ASCII `NSD1` + node ID + identity text + knowledge text + 오름차순 basis item ID 전체 | 32-byte CHECK; node·generator version과 복합 UNIQUE |
| Qwen document `model_task.input_hash` | `identity_text + "\n\n" + knowledge_text`의 정확한 UTF-8 bytes | #78 document contract와 cache key가 보장 |

### 4.2.1 rule별 identity detail

`blocked_fingerprint`와 `lint_finding`은 자유 message나 raw JSON key 순서를 hash 입력으로 사용하지 않는다.

각 `lint_rule`의 validator fixture는 다음을 명시해야 한다.

- identity에 포함할 typed field 목록과 순서
- set 성격 배열의 정렬 기준
- `NULL/UNKNOWN` tag
- 숫자·시간 canonical 표현
- validator version

규칙 알고리즘 또는 identity detail이 달라지면 같은 validator·policy version을 재사용하지 않는다.

## 4.3 hash 회귀 테스트

각 key에는 최소 다음 golden fixture를 둔다.

- 같은 입력 → 같은 bytes·같은 SHA-256
- 필드 순서가 달라도 set 정렬 뒤 같은 결과
- 실제 의미 필드 하나 변경 → 다른 hash
- `NULL`과 빈 문자열 → 다른 hash
- NFC 전후 동등 문자열 → 정규화 뒤 같은 hash
- LF와 CRLF → 승인된 정규화 뒤 같은 hash

---

# 5. migration 의존 순서

다음 순서로 생성하면 최종 FK graph는 acyclic하다.

## Phase 0 — extension과 namespace

1. `public` schema 사용 확인
2. `CREATE EXTENSION vector`

## Phase 1 — 독립 코드·계약

1. `node_type`
2. `relation_type`
3. `attribute`
4. `output_schema_definition`
5. `lint_rule`
6. `lint_policy_version`

## Phase 2 — revision과 정책 선택

1. `relation_type_revision`
2. `relation_endpoint_rule`
3. `attribute_revision`
4. `lint_policy_rule`

self FK `relation_type_revision.inverse_relation_type_revision_id`는 같은 테이블 선언에서 만들 수 있다. 상호 inverse 두 행은 비활성 상태로 생성한 뒤 한 짧은 transaction에서 서로 연결하고 전체 검증 후 활성화한다.

## Phase 3 — 준비 문서와 원문 근거

1. `evidence_group`
2. `source_document`
3. `observation`

## Phase 4 — 모델 작업·승격 기반

1. `model_task`
2. `agent_attempt`
3. `blocked_fingerprint`
4. `promotion_batch`

`promotion_batch`를 기준 지식보다 먼저 만들어 `knowledge_item.promotion_batch_id`를 즉시 FK로 선언한다.

## Phase 5 — 공통 기준 지식

1. `knowledge_item`
2. `node`
3. `relation`
4. `claim`

shared PK FK를 모두 같은 phase에서 선언한다. subtype에는 identity를 두지 않는다.

## Phase 6 — 노드·Claim 세부 연결

1. `node_alias`
2. `node_alias_evidence`
3. `external_identifier`
4. `node_merge`
5. `event_temporal_extent`
6. `claim_relation`
7. `claim_attribute_value`
8. `claim_observation`
9. `event_temporal_basis`

## Phase 7 — 저장 그래프 lint

1. `lint_run`
2. `lint_finding`

## Phase 8 — 충돌 snapshot

1. `conflict_set`
2. `conflict_member`
3. `conflict_summary`

`conflict_state_event`는 actor 계약 전까지 만들지 않는다.

## Phase 9 — 검색 문서와 계보

1. `node_search_document`
2. `search_document_basis`

## Phase 10 — 모델 파생 결과

1. `node_embedding` — `vector(1024)`
2. `node_context`
3. `followup_question`

## Phase 11 — 공개 선택

1. `publication_affected_node`

artifact 테이블을 먼저 생성했으므로 search document·embedding·context 복합 FK를 즉시 선언할 수 있다.

## Phase 12 — 지원 객체

1. 명명된 CHECK·UNIQUE·partial UNIQUE 확인
2. inventory의 일반·partial·GIN 인덱스 생성
3. 한국어 `COMMENT ON TABLE/COLUMN/CONSTRAINT`
4. seed code·revision·policy는 별도 data migration으로 삽입
5. schema introspection snapshot과 migration downgrade 검증

## Phase 13 — 후속 actor 기능

인증·관리자 actor 모델이 승인된 뒤에만 다음 테이블을 새 migration으로 만든다.

```text
knowledge_state_event
conflict_state_event
```

임시 actor text나 존재하지 않는 FK를 초기 migration에 넣지 않는다.

---

# 6. rollback과 불변성

## 6.1 schema migration rollback

- 아직 데이터가 없는 초기 migration은 Alembic downgrade로 역순 제거할 수 있다.
- 운영 데이터가 생긴 뒤 기준·근거·이력 테이블을 제거하는 downgrade는 자동 실행하지 않는다.
- destructive migration은 backup·영향 분석·명시적 승인과 forward fix를 우선한다.
- PK·FK 타입 변경은 expand → backfill → dual-read/write → switch → contract 순서를 사용한다.

## 6.2 데이터 rollback

```text
promotion 실패
→ 같은 기준 지식 transaction 전체 rollback
→ 원문·model task·attempt는 별도 수명주기로 유지

publication 실패
→ 커밋된 기준 지식 유지
→ 이전 node별 최신 READY 계속 제공
→ 같은 batch의 완성 artifact 재사용 후 재시도

사람 거절 또는 BLOCKING lint
→ 과거 READY artifact 삭제 안 함
→ read-time에서 즉시 제외
→ 영향을 받은 node의 새 publication 준비
```

## 6.3 물리 삭제

- runtime role에는 기준·근거·이력의 범용 DELETE·TRUNCATE를 주지 않는다.
- 모든 FK의 기본은 `RESTRICT`다.
- 의도적 전체 삭제는 일반 CASCADE가 아니라 향후 controlled purge가 범위·공유 observation·파생 결과를 계산한 뒤 처리한다.
- POC 동안 과거 파생 결과도 자동 정리하지 않는다.

---

# 7. HBF end-to-end walk-through

## 7.1 준비 문서

1. 자료 준비 레이어가 SK하이닉스 HBF 문서의 메타데이터와 `normalized_body`를 완성한다.
2. #64 규칙으로 기존 `evidence_group` 선택 또는 새 그룹 생성을 결정한다.
3. `source_document`를 `evidence_group_id NOT NULL`로 저장한다.
4. 본문과 메타데이터가 같으면 확인 시각·상태만 갱신하고, 바뀌면 다음 `version_no` 행을 만든다.
5. 원문 범위마다 `observation`을 만들거나 동일 범위를 재사용한다.

## 7.2 모델 추출과 승격 전 검증

1. `output_schema_definition`의 active 지식 추출 계약을 선택한다.
2. `model_task`를 deterministic cache key로 생성·재사용한다.
3. provider 호출마다 `agent_attempt`을 추가하고 count를 같은 transaction에서 갱신한다.
4. 실제 structured output JSON과 후보 payload는 DB에 저장하지 않는다.
5. memory에서 observation 범위, node/relation/attribute/time 규칙과 lint를 검사한다.
6. 동일한 BLOCKING 후보는 `blocked_fingerprint`로 반복 검증을 억제한다.

## 7.3 기준 지식 승격

하나의 `promotion_batch` transaction에서 다음을 함께 만든다.

```text
SK하이닉스 node와 alias
SanDisk node와 alias
HBF technology node와 alias
FMS 2026 event node와 시간
필요한 relation
원자 Claim
claim_relation SUPPORT/DISPUTE
claim_attribute_value
event_temporal_basis
claim_observation
node_alias_evidence
publication_affected_node의 고정 affected node 집합
```

커밋 전에 다음을 확인한다.

- 각 item에 정확히 한 subtype
- 모든 Claim에 observation과 의미 대상
- 모든 relation에 observation이 있는 SUPPORT Claim
- alias·event time의 근거
- 실제 endpoint type 허용
- active exact revision
- merge cycle 없음

하나라도 실패하면 batch의 기준 지식 쓰기를 전부 rollback한다.

## 7.4 파생 결과 준비

영향 node마다 다음을 생성한다.

1. `node_search_document`와 전체 `search_document_basis`
2. Qwen document embedding `vector(1024)`
3. `node_context`
4. context의 `followup_question` slot 1·2
5. `publication_affected_node`의 세 선택 pointer

embedding 입력은 다음과 같다.

```text
identity_text + "\n\n" + knowledge_text
```

## 7.5 READY 전환

publication 서비스는 batch와 affected row를 잠그고 다음을 검사한다.

- promotion이 COMMITTED
- 모든 affected node에 대표 alias 정확히 한 건
- search document·embedding·context 모두 선택됨
- 세 artifact가 같은 node·search document에 속함
- embedding이 Qwen 1024 document 계약과 호환됨
- context마다 질문 slot 1·2 정확히 두 건
- basis item이 모두 공개 가능
- 열린 BLOCKING finding 없음
- relation·Claim·observation·source document Evidence Trace 완결

통과하면 `publication_status = READY`, `ready_at`을 함께 기록한다.

## 7.6 검색과 지도

1. alias 정확 일치는 첫 bucket으로 반환한다.
2. 현재 node별 최신 READY 검색 문서로 PostgreSQL FTS top 50을 구한다.
3. 같은 READY의 Qwen embedding으로 exact cosine top 50을 구한다.
4. 한 branch가 실패하면 다른 결과와 장애 안내를 제공한다.
5. 둘 다 성공하면 RRF `k = 60`, tie `node_id ASC`로 결합한다.
6. 선택된 중심 node의 최근 90일 또는 최근 1년 published evidence를 기준으로 직접·중요 2단계 이웃을 계산한다.
7. 좌표·밝기·opacity·부분 graph 구성원은 저장하지 않고 브라우저가 그린다.

## 7.7 Evidence Trace

사용자가 관계·속성·사건 시간을 열면 다음 경로로 정확한 인용문과 URL까지 이동한다.

```text
relation
← claim_relation
← claim
→ claim_observation
→ observation
→ source_document

claim attribute
← claim
→ claim_observation
→ observation
→ source_document

event time
← event_temporal_basis
← claim
→ claim_observation
→ observation
→ source_document
```

---

# 8. 향후 schema·transaction test 목록

## 8.1 migration smoke

- 빈 PostgreSQL 18.6에 upgrade head 성공
- 모든 41개 초기 테이블, PK, FK, UNIQUE, CHECK, 인덱스와 COMMENT 존재
- downgrade가 빈 DB에서 역순 성공
- upgrade → downgrade → upgrade 반복 성공
- pgvector 0.8.6과 `vector(1024)` introspection 일치

## 8.2 row-local 거절

- 빈 필수 text
- 32바이트가 아닌 hash
- 값과 `UNKNOWN` precision 불일치
- 잘못된 tagged-union 조합
- promotion/publication 불가능 상태 조합
- conflict target 0개 또는 2개 이상
- merge 자기 참조
- reverse reason만 있거나 time만 있는 행
- question slot 0·3
- 1024차원이 아닌 vector와 비유한 원소

## 8.3 고유성과 FK

- 같은 code·version 중복 거절
- 활성 revision 두 개 거절
- preferred alias 두 개 거절
- 동일 외부 식별자가 다른 node에 연결되는 행 거절
- source의 활성 merge 두 개 거절
- 같은 document range observation 중복 재사용
- 같은 model task attempt 번호 중복 거절
- 같은 context slot 중복 거절
- publication artifact가 다른 node·search document를 가리키면 복합 FK 거절

## 8.4 서비스 transaction

- source version 동시 생성 충돌 후 최신 재판정
- observation substring·quote hash 불일치 차단
- inverse 불일치와 endpoint rule 없음 활성화 차단
- symmetric endpoint 정렬과 directed 역순 정상 허용
- merge cycle A→B→C→A 차단
- precision-aware event·Claim period 역전 차단
- knowledge item subtype 누락·복수 subtype rollback
- observation 없는 Claim rollback
- SUPPORT Claim 없는 relation rollback
- `attempt_count`, attempt 행 수와 최대 번호 원자 일치
- conflict set 최소 두 position·같은 modality 검증
- 같은 conflict snapshot 동시 생성 직렬화

## 8.5 lint

- 실패 run이 열린 finding을 해결하지 않음
- 성공 full run에서 미발견 key만 해결
- 해결된 key 재발 시 새 행 생성
- BLOCKING은 공개 제외, WARNING은 공개 유지
- 사람의 `current_state`를 lint가 REJECTED로 바꾸지 않음

## 8.6 promotion과 publication

- promotion 중간 실패 시 모든 새 기준 지식 rollback
- publication 실패 시 기준 지식과 이전 READY 유지
- PREPARING 중 성공 artifact pointer 재사용
- READY 전 질문 한 건·artifact 누락 거절
- READY 뒤 artifact pointer 수정 금지
- 같은 `ready_at`에서 큰 `promotion_batch_id`를 tie-breaker로 선택
- 더 최신 FAILED/PREPARING batch가 이전 READY를 가리지 않음
- 거절·BLOCKING lint는 과거 READY가 있어도 즉시 숨김

## 8.7 검색

- alias 정확 일치가 FTS·vector보다 앞섬
- FTS expression이 GIN index expression과 byte-for-byte 동일
- Qwen query/document contract 호환성 검사
- cosine distance ASC, tie node ID ASC
- #78 한국어·영문 HBF fixture 통과
- vector 실패 시 FTS 결과, FTS 실패 시 vector 결과
- 두 branch 실패 시 명시적 검색 오류
- context text가 search document 입력으로 순환하지 않음

## 8.8 삭제·이력

- 부모 삭제가 `RESTRICT`로 차단됨
- Claim 삭제 시 공유 observation이 cascade되지 않음
- merge 취소·lint 해결·상태 거절 이력이 물리 삭제되지 않음
- runtime role의 DELETE·TRUNCATE·DDL 거절

---

# 9. blocker와 후속 작업

| ID | 구분 | 상태 | 영향 | 소유 Issue |
|---|---|---|---|---|
| `PHY-BLOCK-001` | embedding 모델·차원·거리 | RESOLVED | `node_embedding = vector(1024)`, cosine exact query 가능 | #78 |
| `PHY-DEFER-001` | 사람 상태 변경 actor FK | NON_BLOCKING | 상태 이벤트 두 테이블의 초기 migration만 지연 | 후속 인증·관리자 Issue |
| `PHY-FOLLOWUP-001` | 문서 evidence group 판정 알고리즘 | NON_BLOCKING FOR SCHEMA | ingest 서비스 구현·fixture 필요 | #64 |
| `PHY-FOLLOWUP-002` | ANN 전환 기준 | NON_BLOCKING | exact search로 초기 구현 가능 | #81 |
| `PHY-GATE-001` | Qwen HBF fixture 실제 실행 | IMPLEMENTATION GATE | 첫 READY 공개 전에 실행 필요 | #78 fixture·검색 구현 Issue |

#48 완료 뒤 실제 DDL·Alembic 구현 Issue를 만들 수 있다. `PHY-DEFER-001`을 제외한 미결정 타입·placeholder·조용한 `TBD`는 없다.

---

# 10. 완료 판정

- 43개 테이블의 컬럼·키·제약·초기 인덱스·주석 책임이 inventory에 있다.
- 초기 migration 41개와 보류 2개가 구분되어 있다.
- 모든 논리 엔터티는 한 물리 위치 또는 승인된 보류에 대응한다.
- shared PK와 composite FK 컬럼 타입이 모두 `bigint`로 일치한다.
- migration 순서는 acyclic하며 publication forward FK가 해결된다.
- 각 불변식은 DB, 서비스 transaction, read-time filter 또는 보류 중 하나의 책임을 가진다.
- embedding placeholder는 Qwen `vector(1024)` 계약으로 해소됐다.
- HBF 흐름은 nullable 모순·고아 Evidence Trace·부분 promotion·조기 publication 없이 표현된다.
- 이 완료는 물리 설계의 완료이며 실제 DDL 실행과 런타임 검증 완료를 뜻하지 않는다.
