# ontology-map 요구사항–논리 스키마 추적표

> 상태: Logical Schema v1.2 기준
>
> 확인일: 2026-09-02
>
> 기준 자료: `ontology-map-design-conversation-transcript.md`, `people-intelligence-requirements-transcript.md`, 승인된 GitHub Issue

## 1. 판정 기준

- `일치`: 현재 논리·물리 설계가 최종 결정을 반영함
- `대체`: 초기 결정이 후속 승인으로 교체됨
- `보류`: POC happy path를 막지 않는 후속 기능
- `비대상`: 제품·UX 요구지만 DB 엔터티로 영속화하지 않음

나중의 명시적 승인과 병합된 변경이 초기 대화보다 우선한다.

## 2. 핵심 요구사항 추적

| ID | 영역 | 최종 결정 | 현재 반영 위치 | 판정 |
|---|---|---|---|---|
| `REQ-001` | 제품 범위 | HBF 공개 자료 POC이며 내부 비공개 자료·권한 모델은 후속 범위 | 논리 스키마 1·10절 | 일치 |
| `REQ-002` | 제품 목표 | Prediction보다 근거 기반 Reasoning과 맥락 탐색 우선 | 논리 스키마 1·2절 | 일치 |
| `REQ-003` | 기준 데이터 | 지식그래프는 기준 데이터, 지식맵은 동적 부분 그래프 | 논리 스키마 1·8절 | 일치 |
| `REQ-004` | 지도 노드 | 사람·회사·기술·주제·사건만 기본 지도 노드 | `node_type` | 일치 |
| `REQ-005` | 상세 정보 | Claim·출처·시간·Evidence Trace는 상세 패널에서 탐색 | Claim–observation–document 경로 | 일치 |
| `REQ-006` | 입력 경계 | 발견·GDELT·크롤링·HTML은 DB 밖, 정규화 문서부터 입력 | `source_document`, 제외 범위 | 일치 |
| `REQ-007` | 문서 버전 | 내용·메타데이터 변경은 새 버전, 동일하면 마지막 확인만 갱신 | `source_document` | 일치 |
| `REQ-008` | source key | 준비 레이어가 안정된 논리 자료 키를 제공하며 임베딩을 고유키로 쓰지 않음 | `source_document.source_key` | 일치 |
| `REQ-009` | 독립 근거 | 문서 수가 아니라 동일 원문 계보 묶음 수를 센다 | `evidence_group` | 일치 |
| `REQ-010` | 문서와 묶음 | 문서는 저장 시점부터 `evidence_group_id`를 정확히 하나 가짐 | `source_document.evidence_group_id` | 일치 |
| `REQ-011` | 재분류 | POC에서는 assignment 이력을 저장하지 않고 통제된 정정으로 FK를 직접 수정 | #41, 논리 스키마 5.2 | 일치 |
| `REQ-012` | 원문 위치 | Unicode 문자 `[start,end)`와 인용문·해시를 함께 검증 | `observation` | 일치 |
| `REQ-013` | observation 재사용 | 같은 문서의 같은 범위는 하나만 만들고 여러 Claim·alias가 공유 | observation UNIQUE 계약 | 일치 |
| `REQ-014` | Agent 권한 | Agent는 후보만 제안하며 ID·병합·허용 규칙·상태는 결정하지 않음 | 모델 작업·서비스 검증 | 일치 |
| `REQ-015` | 후보 보존 | Agent JSON과 후보 payload는 저장하지 않음 | Structured Output 경계 | 일치 |
| `REQ-016` | 반복 차단 | 계약 유효 후보의 차단 fingerprint만 payload 없이 집계 | `blocked_fingerprint` | 일치 |
| `REQ-017` | 재시도 | 모델 작업별 최대 다섯 번, 일시 장애만 재시도 | `model_task`, `agent_attempt` | 일치 |
| `REQ-018` | 출력 계약 | JSON Schema 계약은 불변 버전으로 관리 | `output_schema_definition` | 일치 |
| `REQ-019` | 원자 승격 | 외부 호출 뒤 짧은 트랜잭션으로 기준 그래프를 원자 저장 | `promotion_batch` | 일치 |
| `REQ-020` | 공개 준비 | 승격과 공개 수명주기를 분리하고 실패 시 이전 READY 유지 | `promotion_batch`, `publication_affected_node` | 일치 |
| `REQ-021` | 지식 상태 | 근거 확인·사람 확인·보류·거절을 분리하고 거절 이력 보존 | `knowledge_item`, `knowledge_state_event` | 일치 |
| `REQ-022` | 사람 개입 | 사람 확인은 선택 사항이며 초기 공개의 선행 조건이 아님 | 상태 수명주기 | 일치 |
| `REQ-023` | 단일 confidence | 실행·원문 충실성·상태·근거 수를 한 점수로 합치지 않음 | 공통 원칙 | 일치 |
| `REQ-024` | 노드 ID | 이름과 무관한 불변 ID, 병합 뒤에도 기존 ID 보존 | `node`, `node_merge` | 일치 |
| `REQ-025` | alias | 모든 alias로 검색하고 노드당 대표 alias 최대 하나 | `node_alias` | 일치 |
| `REQ-026` | alias 근거 | alias도 정확한 observation 근거를 가질 수 있음 | `node_alias_evidence` | 일치 |
| `REQ-027` | 외부 식별자 | 신뢰된 준비 메타데이터만 저장하고 체계+값을 고유하게 사용 | `external_identifier` | 일치 |
| `REQ-028` | 병합 | 활성 병합은 원본당 하나, 순환 금지, 취소 이력 보존 | `node_merge` | 일치 |
| `REQ-029` | 사건 | 사건은 정식 노드이며 참여자·대상은 관계 endpoint로 표현 | `node`, `relation`, `event_temporal_extent` | 일치 |
| `REQ-030` | 숨은 사건 맥락 | `event_context_node_id` 없이 사건 endpoint 경로 사용 | `relation` | 일치 |
| `REQ-031` | 관계 기간 | POC relation에는 유효 기간 컬럼을 두지 않음 | `relation`, 제외 범위 | 일치 |
| `REQ-032` | 관계 집계 | 같은 endpoint+정확한 relation revision은 하나의 관계로 집계 | `relation_identity_key` | 일치 |
| `REQ-033` | 관계 근거 | 여러 Claim이 관계를 지지·반박하고 Claim이 observation을 가짐 | `claim_relation`, `claim_observation` | 일치 |
| `REQ-034` | 관계 필라멘트 | 지지 Claim의 독립 근거 묶음 수와 같은 개수의 1px 선을 같은 경로 주변에 겹쳐 표시 | 지도 계약 | 일치 |
| `REQ-035` | Claim 원자성 | 독립 판단 가능한 사실은 별도 Claim, 같은 observation 공유 가능 | `claim` | 일치 |
| `REQ-036` | 구조화 값 | STRING·NUMBER·DATE·PERIOD·BOOLEAN tagged union | `claim_attribute_value` | 일치 |
| `REQ-037` | 표현 성격 | 사실·계획·예측·의견을 구분 | `claim.modality` | 일치 |
| `REQ-038` | 시간 정밀도 | 실제 시각·날짜·월·연도·미상을 값과 precision으로 보존 | 공통 시간, Claim·사건·속성값 | 일치 |
| `REQ-039` | 사건 시간 근거 | 채택 사건 시간은 Claim과 다대다 근거 연결 | `event_temporal_basis` | 일치 |
| `REQ-040` | 충돌 | 관계·속성·사건 시간별 불변 Claim snapshot과 호박색 점선 | conflict 계층 | 일치 |
| `REQ-041` | 충돌 판정 | Agent 제안도 코드 검증 후 표시, 사람 확인은 선택 사항 | `conflict_set`, 상태 이벤트 | 일치 |
| `REQ-042` | lint | 승격 전 메모리 검사와 저장 그래프 재검사를 분리 | lint 계층 | 일치 |
| `REQ-043` | 공개 lint | 열린 차단 finding은 사람 거절로 바꾸지 않고 공개에서만 제외 | `lint_finding`, 조회 필터 | 일치 |
| `REQ-044` | 검색 | alias 정확 일치 우선, PostgreSQL 전문 검색과 pgvector를 RRF로 결합 | 검색 계약 | 일치 |
| `REQ-045` | 검색 결과 | 메인 결과는 다섯 노드 유형만 반환 | 검색 계약 | 일치 |
| `REQ-046` | 검색 문서 | identity·knowledge 텍스트를 결정적으로 만들고 context를 되돌려 넣지 않음 | `node_search_document` | 일치 |
| `REQ-047` | 임베딩 | 검색 순위에만 사용하고 동일 대상·관계·상태 판정에는 사용하지 않음 | `node_embedding` | 일치 |
| `REQ-048` | 후속 질문 | context마다 질문 slot 1·2와 필수 대상 노드 | `followup_question` | 일치 |
| `REQ-049` | 고립 노드 | 관계가 없어도 필수 파생 결과와 상세 자료가 완전하면 공개 | READY 검증 | 일치 |
| `REQ-050` | 지도 범위 | 중심 1, 직접 12, 2단계 18, 관계선 60 상한 | 지도 조회 계약 | 일치 |
| `REQ-051` | 시간 보기 | 90일·1년은 지도 프리셋이며 전체 보존 상한이 아님 | 지도 계약·보존 원칙 | 일치 |
| `REQ-052` | 시각 채널 | node 크기와 relation 필라멘트 수만 데이터 채널, 밝기·opacity는 UI 상태 | 지도 계약 | 일치 |
| `REQ-053` | 지도 비영속 | 좌표·카메라·viewport·지도 구성원·전역 지도 버전을 저장하지 않음 | 제외 범위 | 일치 |
| `REQ-054` | 보존 | 기준 지식·Evidence Trace·검토 이력은 자동 삭제하지 않음 | 공통 보존 원칙 | 일치 |

## 3. Issue #41 — 대체된 근거 묶음 배정 결정

| ID | 이전 결정 | 최종 결정 | 판정 |
|---|---|---|---|
| `SUP-41-01` | `evidence_group_assignment`로 문서의 묶음 소속 이력을 관리 | `source_document.evidence_group_id NOT NULL`로 현재 소속을 직접 관리 | 대체 |
| `SUP-41-02` | `valid_from/valid_to`로 배정 기간 비중복을 관리 | 기간·현재 배정 제약 제거 | 대체 |
| `SUP-41-03` | assignment method·reason·actor를 보존 | POC에서는 저장하지 않음 | 대체 |
| `SUP-41-04` | Claim 공개 전에만 current assignment가 필요 | 문서 저장 시점부터 group 필수 | 대체 |
| `SUP-41-05` | 같은 원문 범위 발견마다 새 observation 가능 | 정확히 같은 범위는 하나만 만들고 재사용 | 대체 |

### 채택 이유

- 자료 준비 레이어가 문서 메타데이터와 원문 계보를 검수한 뒤 제품 DB에 전달한다.
- POC에는 계보 재분류 감사나 운영자별 판단 이력 요구가 없다.
- 직접 FK가 문서당 정확히 하나의 현재 묶음을 구조적으로 표현하고, 기간·현재 assignment 관리와 조인을 제거한다.

### 수용한 손실

문서가 과거 어느 묶음에 속했는지, 누가 왜 재분류했는지를 DB에서 재현하지 않는다.

### 되돌리기 경로

새 Issue에서 `evidence_group_assignment`, 기존 문서의 current assignment backfill, 기간 비중복·현재 최대 한 건 제약을 추가한 뒤 `source_document.evidence_group_id`를 제거하거나 assignment 기반 view로 대체한다. 두 구조를 동시에 source of truth로 사용하지 않는다.

## 4. Issue #69 — 대체된 온톨로지 결정

| ID | 이전 결정 | 최종 결정 | 판정 |
|---|---|---|---|
| `SUP-69-01` | `ontology_version`이 활성 규칙 전체 snapshot을 가짐 | 실제 유형·revision 행의 `is_active` 사용 | 대체 |
| `SUP-69-02` | `ontology_member`가 node type·relation revision·attribute revision 중 하나를 선택 | 엔터티 제거, 결과의 실제 FK로 의미 보존 | 대체 |
| `SUP-69-03` | `promotion_batch.ontology_version_id`로 전체 규칙 집합 추적 | 컬럼 제거, `lint_policy_version_id`만 유지 | 대체 |
| `SUP-69-04` | relation/attribute code를 닫힌 CHECK로 관리 가능 | `relation_type`, `attribute` 코드 테이블 유지 | 일치 |
| `SUP-69-05` | 유형·revision 비활성화 시 기존 지식 처리 미정 | 새 지식 생성만 막고 기존 지식은 그대로 유지 | 일치 |

## 5. 보류와 비대상

| ID | 항목 | 상태 | 이유 |
|---|---|---|---|
| `DEFER-001` | 상태 이벤트와 node merge의 사람 처리자 FK | 보류 | user·actor·인증 계약이 초기 POC에 없음 |
| `BLOCK-001` | `node_embedding.vector(n)`의 구체적인 차원 | 물리 단계 blocker | 승인된 임베딩 모델이 아직 없음 |
| `OUT-001` | 지도 좌표·카메라·중심별 구성원 저장 | 비대상 | 브라우저가 동적 배치 |
| `OUT-002` | GDELT·크롤링·HTTP 이력 저장 | 비대상 | 자료 준비 레이어 책임 |
| `OUT-003` | 원본 HTML·Agent 응답 JSON·후보 payload | 비대상 | 저장하지 않기로 확정 |
| `OUT-004` | ACL·내부 자료 권한 모델 | 비대상 | 공개 자료 POC 이후 범위 |

## 6. 물리 스키마 인계

Issue #40의 공통 규칙과 `physical-schema/41-prepared-evidence.md`를 기준으로 #42~#47을 이어 작성하고 #48에서 다음을 통합 검증한다.

- 모든 논리 엔터티와 필드의 유일한 물리 위치
- 제거된 `evidence_group_assignment`, `ontology_version`, `ontology_member`, `display_rule_version`의 재등장 여부
- key 타입과 복합 FK 일치
- DB row-local 제약과 서비스 교차 행 검증의 중복 여부
- migration 생성 순서
- HBF 흐름의 Evidence Trace와 READY 완결성
- 명시적 blocker와 비차단 deferred 항목
