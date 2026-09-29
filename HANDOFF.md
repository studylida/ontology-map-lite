# 경량화 이전 ontology-map 데모 인계

이 문서는 2026-09-21 데모의 역사 기록이다. 이후 스키마와 서비스가 경량화되었으므로 아래 포트·테이블·처리 경로를 현재 실행 지침으로 사용하지 않는다. 현재 실행 방법은 [README](README.md), 구성은 [아키텍처](docs/ARCHITECTURE.md)를 따른다.

> 확인일: 2026-09-21. 이 인계는 `origin/main` `526f5f7`에 재기반한 PR #246과 5179 web·8015 API 데모 상태를 기준으로 한다. 정확한 병합 commit과 CI 상태는 PR #246을 확인한다.

## 현재 제품 경계

- 브라우저는 FastAPI `/api/v1`만 호출하고 PostgreSQL에 직접 접근하지 않는다.
- `POST /api/v1/source-intake`는 TXT·MD·PDF·DOCX를 정규화해 불변 문서와 처리 job을 저장하고, 프로세스 내 worker가 추출·promotion·initial publication을 수행한다.
- Jev는 원문 span 선택, prompt injection 사전 차단, 공개 적합성과 binding 판정을 맡는다. Luna는 context·FOLLOWUP, Terra는 지식 생성·Claim 지지·Entity Resolution·NODE_INSIGHT를 맡는다.
- 검색·클릭은 저장된 READY 결과만 읽고 모델 호출을 시작하지 않는다.
- 활성 `node_merge`는 읽기에서 대표 Node로 해석하며 물리 Node·Claim·Evidence·관계를 삭제하거나 이동하지 않는다.

## 이번 데모 완료 사항

- 8015가 사용하는 `ontology_map_bistelligence_demo`에 승인된 source Node 39개를 22개 대표 Node로 연결했다. 기존 `181 → 10`, `474 → 486`을 포함한 활성 merge는 41개다.
- ID 존재, 같은 Node 유형, 대표 Node의 비리디렉션, 순환과 기존 충돌 부재를 한 transaction에서 검사했다. Node·Claim·Relation·Observation·Alias 행 수는 변경하지 않았다.
- 검색과 지도·관계 조회는 source Node를 대표 Node로 정규화하고 같은 대표 Node를 중복 반환하지 않는다.
- 헤더의 `ontology-map` 브랜드를 `비스텔리젼스 홈으로 이동` 버튼으로 만들었다. 기간을 유지해 node 10으로 이동하고, 이미 홈이면 중복 요청과 history를 만들지 않는다.
- 5179 브라우저에서 중심 이동, 패널, 기간 유지, 뒤로·앞으로 가기와 키보드 활성화를 확인했다.

## 실행과 복구 기준

- web: 5179, API: 8015, DB: `ontology_map_bistelligence_demo`를 사용한다.
- 5178 코드는 수정하지 않았고 프로세스를 시작하거나 재시작하지 않았다.
- 병합 전 복구점은 `/home/studylida/orca/private/ontology-map-5178-snapshot-20260921/pre-merge.dump`와 복제 DB `ontology_map_bistelligence_demo_5178_20260921`이다.
- 복제 DB와 dump는 복구 전용이며 5179 연결 대상으로 사용하지 않는다.
- 모델 재호출, 재추출, promotion과 publication은 Node 병합 작업에서 수행하지 않았다.

## 운영 시 주의할 점

- migration은 API 시작 시 자동 적용하지 않는다. 현재 실행 안내는 [README](README.md#로컬-실행)를 확인한다.
- worker 함수가 존재하는 것과 운영 queue·다중 프로세스 복구가 준비됐다는 것은 다르다. 현재 worker는 FastAPI 프로세스 안에서 최대 4개로 실행된다.
- source 문서나 provider 응답, credential과 비용 원장은 Git이나 제품 DB에 넣지 않는다.
- 새 Node 병합은 source·대표 유형과 redirect graph 전체를 다시 검증하고 하나의 transaction으로 적용한다.

## 현재 문서

- 현재 실행 구조와 화면 행동: [아키텍처](docs/ARCHITECTURE.md)
- 모델의 DB 표현: [스키마 참고 문서](docs/data/schema-reference.md)
- 로컬 실행과 검증: [README](README.md)
