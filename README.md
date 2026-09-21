# ontology-map

ontology-map은 공개 자료에서 확인한 근거와 시간축을 지식그래프로 축적하고, 사용자가 선택한 node를 중심으로 동적 부분 graph를 탐색하는 웹 애플리케이션이다. 브라우저와 PostgreSQL 사이의 유일한 제품 경계는 FastAPI HTTP API다.

## 처음 읽을 문서

1. [구현 스택](docs/development/implementation-stack.md)에서 현재 구성과 승인·시험 상태를 확인하고, [HANDOFF.md](HANDOFF.md)에서 인계 기준 commit과 다음 Issue를 확인한다.
2. [문서 안내](docs/README.md)에서 목적에 맞는 정식 문서를 찾는다.
3. [제품 설계](docs/product/design.md)에서 제품과 화면 계약을 확인한다.
4. [논리 스키마](docs/data/logical-schema.md)와 [물리 스키마](docs/data/physical-schema.md)에서 frozen 데이터 의미와 PostgreSQL 계약을 확인한다.
5. 변경을 시작하기 전에 [CONTRIBUTING.md](CONTRIBUTING.md)를 읽는다.

## 저장소 구조

| 경로 | 역할 |
| --- | --- |
| `web/` | React와 Vite 기반 사용자 화면, FastAPI adapter와 component test |
| `server/` | FastAPI HTTP 경계, application-service 함수, SQLAlchemy query, Alembic migration과 개발용 fixture |
| `docs/` | 제품, 아키텍처, 데이터, 운영과 개발의 현재 문서 |
| `scripts/check_docs.py` | 메타데이터 기반 스키마 참고 문서 생성과 문서 계약 검사 |
| `compose.yaml` | 개발용 PostgreSQL과 FastAPI 컨테이너 |

PostgreSQL, migration, 개발용 HBF·100-node fixture, FastAPI, web과 종료·초기화 절차는 [DB 운영 문서](docs/operations/database.md)에 모아 두었다. 제품에는 자료 업로드를 정규화해 `source_document`와 처리 job으로 저장하고 프로세스 내 worker가 추출·승격·초기 공개를 수행하는 경로가 있다. TypeSafe Jev 사전 검사·판정과 역할별 OpenAI 모델, [DB 없는 검사 방법](docs/operations/database.md#추출-실행-코드의-로컬-검사), durable publication 경계는 [구현 스택](docs/development/implementation-stack.md#제품-재사용용-추출-실행-코드)에서 확인한다.

web 실행 후 `/?center=<node ID>&range=90d`에서 지식맵을 연다. `range`는 `90d`, `1y`, `all`을 지원한다. 헤더의 `ontology-map` 브랜드 버튼은 선택 기간을 유지한 채 기본 중심인 비스텔리젼스 node 10으로 돌아간다. 사용자 검토를 거친 디자인을 기본 화면으로 사용하며, 기존 `/design-preview` 주소도 같은 화면으로 열린다. 현재 화면 규칙은 [제품 설계](docs/product/design.md#가독성-디자인-미리보기)에서 확인한다.

## 문서 역할

- [아키텍처](docs/architecture/README.md): 현재 시스템의 경계, 구성 요소, 실행 구조와 중요한 결정
- [제품 설계](docs/product/design.md): 제품 의미, 상호작용과 시각 계약
- [논리 스키마](docs/data/logical-schema.md): frozen 논리 데이터 의미와 수명주기
- [물리 스키마](docs/data/physical-schema.md): frozen PostgreSQL 표현과 무결성 기준
- [스키마 참고 문서](docs/data/schema-reference.md): SQLAlchemy metadata에서 생성한 실제 table, column, constraint와 index 목록
- [구현 스택](docs/development/implementation-stack.md): 현재 런타임·의존성·코드 구조, 승인된 에이전트 역할과 모델 시험 구성
- [코드 규칙](docs/development/code-conventions.md): TypeScript, Python, 테스트와 오류 처리 규칙
- [DB 운영](docs/operations/database.md): 로컬 실행과 DB 운영
- [기여 규칙](CONTRIBUTING.md): Issue, branch, commit, 문서, PR과 병합 규칙

현재 구현 범위나 다음 작업이 바뀌면 같은 사실을 여러 문서에 복사하지 않고 해당 책임 문서와 임시 [HANDOFF.md](HANDOFF.md)를 갱신한다.
