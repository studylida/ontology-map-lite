# #48 PostgreSQL 통합 물리 설계 검증

## 문서 상태

- 관련 Issue: #48
- 상태: 통합 물리 설계 완료
- 공통 규칙: [`../physical-data-schema.md`](../../docs/data/physical-schema.md)
- 전체 inventory: [`48-integrated-inventory.md`](48-integrated-inventory.md)
- 무결성·migration·검증: [`48-integrity-migration-tests.md`](48-integrity-migration-tests.md)
- 임베딩 호환 계약: [`78-embedding-contract.md`](78-embedding-contract.md)
- HBF 검색 fixture: [`78-hbf-search-fixtures.md`](78-hbf-search-fixtures.md)

이 문서는 #40의 공통 규칙과 #41–#47의 영역별 물리 매핑을 실제 DDL로 옮기기 전에 읽는 최종 진입점이다.

## 최종 우선순위

서로 다른 문서의 표현이 충돌하면 다음 순서를 적용한다.

1. 이 문서가 연결한 #48 inventory와 무결성·migration 계약
2. #78 Qwen 임베딩 계약
3. 각 영역의 #41–#47 소유 문서
4. #40 공통 규칙
5. 동결 논리 스키마와 요구사항 추적표

#48은 제품 의미를 새로 만들지 않고 영역별 결정의 충돌·누락·순환 참조·중복 인덱스를 해소한다.

## 완료 결과

```text
물리 설계 테이블 43개
├─ 초기 migration 41개
└─ actor 계약까지 보류 2개
```

- `knowledge_state_event`, `conflict_state_event`만 비차단 보류다.
- Qwen `qwen3.7-text-embedding`, dense 1024차원, cosine exact search로 `vector(n)` blocker를 해소했다.
- 모든 기준·근거·이력 FK는 기본 `RESTRICT`다.
- 교차 행 의미 검사는 서비스 transaction 한 곳에 두고 custom trigger로 중복하지 않는다.
- migration 순서를 따르면 shared PK와 publication artifact 복합 FK를 최종 schema에서 모두 선언할 수 있다.
- `observation`과 `agent_attempt`의 중복 가능 인덱스 두 개는 초기 migration에서 제외한다.
- symmetric endpoint의 정렬은 DB의 무조건적인 ID 비교 CHECK가 아니라 revision을 읽는 서비스가 담당한다.
- 지도 좌표·밝기·opacity·부분 graph 구성원·전역 graph/map version은 저장하지 않는다.

## 실제 구현을 시작할 때

1. `48-integrated-inventory.md`의 41개 초기 테이블을 migration phase 순서로 나눈다.
2. 각 migration은 해당 owner 문서의 exact CHECK·COMMENT를 사용한다.
3. schema introspection test로 inventory와 실제 DB를 비교한다.
4. cross-row 규칙은 `48-integrity-migration-tests.md`의 서비스 transaction test로 구현한다.
5. Qwen API key나 실제 model call을 migration에 넣지 않는다.
6. 첫 READY 공개 전에 #78 HBF fixture를 실행한다.
7. exact search가 실제 기준을 충족하지 못할 때만 #81에서 ANN을 검토한다.

## 남은 범위

이 문서가 완료됐다는 것은 다음 작업이 끝났다는 뜻이 아니다.

```text
Alembic migration
SQLAlchemy model
repository와 service transaction
Qwen provider adapter
seed data
API
worker
실제 PostgreSQL 실행
HBF fixture 실행 결과
성능 benchmark
```

이후 구현 Issue는 논리 의미를 다시 결정하지 않고 이 통합 계약을 실제 schema와 코드로 옮긴다. 구현 중 의미 변경이 필요하면 원래 결정을 조용히 바꾸지 않고 별도 변경 Issue를 만든 뒤 #48 회귀 검증을 다시 수행한다.
