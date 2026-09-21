# #42 교차 행 검증 책임 정정

## 목적

`42-ontology-node-identity.md`와 `42-merge-event-time.md`에서 다른 행을 함께 읽어야 하는 규칙의 최종 보장 주체를 명확히 한다. 이 문서의 책임 배정이 #42의 최종 기준이며 #48 통합 문서에 그대로 반영한다.

## 1. DB row-local 제약

다음 규칙만 대상 행의 값으로 판단할 수 있으므로 DB CHECK로 구현한다.

- revision `version_no >= 1`
- `directionality IN ('DIRECTED', 'SYMMETRIC')`
- 같은 revision이 자기 자신을 inverse로 참조하지 않음
- `SYMMETRIC` revision의 inverse FK가 `NULL`
- 속성 값 종류의 닫힌 목록
- NUMBER revision의 `unit_rule` 필수와 그 외 종류의 `NULL`
- node merge 자기 참조 금지
- node merge 취소 시간·이유의 all-or-none 조합
- 사건 시간 값과 `UNKNOWN` precision의 일치

## 2. DB 고유성

- code 고유성
- `(code_id, version_no)` revision 고유성
- code별 활성 revision 최대 한 건
- relation endpoint triple 중복 금지
- node별 대표 alias 최대 한 건
- 외부 식별자의 `(identifier_system, identifier_value)` 고유성
- source node별 활성 merge 최대 한 건

## 3. 서비스 트랜잭션 검증

다음은 관련 revision·node·규칙 행을 함께 읽어야 하므로 서비스만 구현한다.

### relation revision 활성화

- inverse 대상도 `DIRECTED`인지 확인
- inverse 관계가 양방향으로 서로를 가리키는지 확인
- endpoint rule이 한 건 이상 있는지 확인
- 같은 relation code의 기존 revision 비활성화와 새 revision 활성화

### endpoint rule

- `DIRECTED`는 원래 방향으로 저장
- `SYMMETRIC`은 node type ID 두 개를 오름차순으로 정규화
- `SYMMETRIC`의 반대 방향 endpoint row를 별도로 만들지 않음

`relation_endpoint_rule` 행만으로는 연결된 revision의 `directionality`를 알 수 없으므로, `source_node_type_id <= target_node_type_id`를 모든 행에 적용하는 DB CHECK는 만들지 않는다. 그렇게 하면 ID 순서가 반대인 정상 `DIRECTED` 규칙도 거절하게 된다.

### 새 지식 승격

- node type과 exact relation·attribute revision이 활성인지 재확인
- 실제 relation endpoint node type이 endpoint rule과 일치하는지 확인
- 구조화 값 대상 node type과 `attribute_revision.target_node_type_id` 일치

### node merge

- 활성 merge 연쇄의 cycle 금지
- 최종 canonical node 결정
- 잠금 순서를 통한 동시 cycle 방지

### 사건 시간

- 대상 node가 실제 `EVENT` 유형인지 확인
- 서로 다른 precision을 의미 범위로 확장해 시간 순서 검사
- 채택 범위를 뒷받침하는 Claim·observation 존재 확인

## 4. 선택 이유와 변경 방법

### 현재 선택

교차 행 의미 검증을 서비스 한 곳에 둔다. DB custom trigger와 같은 알고리즘을 이중 유지하지 않는다.

### 대안

직접 SQL 쓰기를 DB가 단독으로 방어해야 하는 운영 요구가 생기면 규칙별 deferred constraint trigger를 추가할 수 있다.

### 전환 절차

1. 서비스 검증 테스트를 trigger의 입력·기대 결과 fixture로 고정한다.
2. 규칙 하나씩 좁은 constraint trigger를 추가한다.
3. 서비스에서는 중복 판정 로직을 제거하고 DB 오류를 도메인 오류로 변환한다.
4. 두 구현을 장기간 동시에 유지하지 않는다.
