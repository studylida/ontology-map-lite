# ontology-map 코드 컨벤션

## 문서 상태

- 상태: 승인된 구현 기준
- 확인일: 2026-09-08
- 관련 Issue: #8, #95–#100, #125, #158
- 구현 스택: [implementation-stack.md](implementation-stack.md)
- Git 규칙: [CONTRIBUTING.md](../../CONTRIBUTING.md)
- PostgreSQL 물리 규칙: [물리 스키마](../data/physical-schema.md)

이 문서는 ontology-map의 TypeScript·React·CSS와 Python·FastAPI·SQLAlchemy·Alembic 코드에 적용할 최소 규칙을 정한다. `web/`에는 실제 FastAPI adapter를 사용하는 React 화면이 있고 `server/`에는 읽기 API, source intake와 프로세스 내 처리 worker, SQLAlchemy query, frozen schema migration과 개발 fixture가 있다. 추출·생성·판정은 기존 명시적 runner를 재사용한다.

## 공통 원칙

- 현재 사용자 흐름, 무결성, 조회 또는 운영 책임으로 설명할 수 있는 코드만 작성한다.
- 같은 문제를 해결할 수 있다면 기존 코드, 표준 라이브러리, 플랫폼 기능, 이미 승인된 의존성 순으로 사용한다.
- 실제 두 번째 구현이나 사용처가 생기기 전에는 공통 interface, factory, registry, wrapper와 공유 package를 만들지 않는다.
- 함수는 한 가지 판단이나 작업 단위를 수행한다. 이름으로 설명하기 어려운 boolean 인수와 여러 책임을 가진 utility 모듈을 만들지 않는다.
- Python 함수의 McCabe 복잡도는 Ruff `C901` 기준 10 이하로 유지한다. TypeScript와 TSX 함수의 인지 복잡도는 Biome `noExcessiveCognitiveComplexity` 기준 10 이하로 유지한다.
- 복잡도가 6 이상인 함수를 변경할 때는 조건 분리, 조기 반환, 데이터 매핑 또는 책임 분리가 더 읽기 쉬운지 검토한다. 10을 넘는 함수는 예외를 추가하지 않고 분리한다.
- 입력 검증, 데이터 손실을 막는 오류 처리, 보안, Evidence Trace와 접근성은 단순화를 이유로 생략하지 않는다.

## 폴더와 의존 방향

```text
ontology-map/
├── web/
└── server/
    ├── migrations/
    └── src/
        └── ontology_map/
            ├── main.py
            ├── api.py
            ├── exploration.py
            ├── search.py
            ├── relations.py
            ├── insights.py
            ├── pagination.py
            └── db/
```

- `web/src/`에는 앱 진입점과 실제 기능 코드를 둔다. 기능별 디렉터리는 그 기능에 코드가 두 개 이상 생길 때 만들고, 컴포넌트·CSS Module·테스트는 사용하는 기능 가까이에 둔다.
- `web/src/shared/`나 공통 component 디렉터리는 서로 다른 기능에서 실제로 재사용하는 코드가 생긴 뒤에만 만든다.
- `api.py`는 HTTP 요청·응답 검증과 오류 변환만 담당한다. top-level 기능 모듈은 application-service 함수로 use case를 조합하고 `db/`는 연결, query와 영속성을 담당한다.
- `db/`는 `api.py`나 application-service 모듈을 import하지 않는다. API와 향후 worker 진입점이 필요한 query와 model 작업을 조합한다.
- 단일 구현을 감싸는 service, repository interface나 범용 base class는 만들지 않는다. 실제 책임이 겹칠 때 가장 가까운 기능에서 분리한다.

## TypeScript와 React

### 이름과 파일

- React 컴포넌트와 해당 파일은 `PascalCase.tsx`, hook은 `useCamelCase.ts`, 일반 모듈과 함수는 `camelCase.ts`와 `camelCase`, 상수는 `UPPER_SNAKE_CASE`를 사용한다.
- 테스트 파일은 대상 가까이에 `*.test.ts` 또는 `*.test.tsx`로 둔다. 앱 진입점을 제외한 모듈은 named export를 기본으로 한다.
- 컴포넌트는 `React.FC` 대신 props 타입을 받은 일반 함수로 작성한다. props는 사용하는 컴포넌트 가까이에 두며 재사용되지 않는 타입을 공통 파일로 옮기지 않는다.
- 순수 데이터와 union은 `type`을 기본으로 한다. 실제 확장 계약이나 declaration merging이 필요할 때만 `interface`를 사용한다.
- API와 데이터베이스의 문자열 상태를 TypeScript `enum`으로 다시 만들지 않는다. API 계약에서 생성하거나 검증한 string literal union을 사용한다.

### 타입과 상태

- 새 프로젝트의 `tsconfig`는 `strict`, `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`, `noImplicitReturns`와 `noFallthroughCasesInSwitch`를 켠다.
- `any`, non-null assertion과 무검증 type assertion을 사용하지 않는다. 외부 입력은 `unknown`으로 받고 경계에서 좁힌다.
- 탐색 가능한 중심 노드, 시간 범위와 검색어처럼 URL로 복원해야 하는 상태는 URL에 둔다. 그 밖의 상태는 컴포넌트 지역 상태를 우선한다.
- Context는 여러 하위 기능이 같은 안정된 값을 읽을 때만 사용한다. 전역 상태·query library는 실제 캐시 동기화 문제가 확인되기 전에는 추가하지 않는다.
- HTTP 호출은 브라우저 `fetch`로 시작한다. 요청 취소, 오류 상태와 응답 검증은 호출 경계에서 처리한다.

### React 동작

- 렌더링 중에는 외부 상태를 변경하지 않는다. effect는 외부 시스템과 동기화할 때만 사용하고 파생 값 계산에는 사용하지 않는다.
- loading, empty, error와 ready 상태를 구분한다. 오류를 빈 결과로 보이게 하거나 실패한 요청을 무한 재시도하지 않는다.
- 목록 key로 배열 index를 사용하지 않는다. 안정된 node, relation, claim 또는 observation 식별자를 사용한다.
- 클릭 가능한 요소는 `button`과 `a` 같은 의미 있는 HTML 요소를 사용한다. 키보드 focus와 accessible name을 유지한다.

## CSS

- 컴포넌트 스타일은 `Component.module.css`를 사용한다. 전역 CSS는 reset, 디자인 토큰과 기본 요소 스타일에만 사용한다.
- 색, 간격, typography와 상태 표현은 [제품 설계](../product/design.md)의 token을 따른다. 임의의 비슷한 색이나 간격을 컴포넌트마다 새로 만들지 않는다.
- layout은 CSS Grid와 Flexbox를 우선하고, DOM 측정이나 JavaScript 배치는 3D graph canvas처럼 CSS로 해결할 수 없는 경우에만 사용한다.
- hover만으로 의미를 전달하지 않는다. focus-visible, reduced-motion, text contrast와 최소 44px 조작 영역을 기본으로 유지한다.
- CSS-in-JS, utility CSS framework와 별도 reset package는 승인된 요구가 생기기 전에는 추가하지 않는다.

## Python

### 이름과 타입

- 모듈, 함수, 변수는 `snake_case`, class와 Pydantic model은 `PascalCase`, 상수는 `UPPER_SNAKE_CASE`를 사용한다. 모듈 내부 전용 이름에는 앞에 `_`를 붙인다.
- `server/src/ontology_map/`의 함수와 method는 매개변수와 반환 타입을 모두 표기한다. 지역 변수는 추론이 분명할 때 별도 표기를 요구하지 않는다.
- mypy는 제품 코드에 `disallow_untyped_defs`, `disallow_incomplete_defs`, `no_implicit_optional`, `warn_return_any`, `warn_unused_ignores`와 `strict_equality`를 적용한다. 테스트 디렉터리는 같은 강도의 완전한 함수 표기를 요구하지 않는다.
- `Any`와 무분별한 `cast()`로 오류를 숨기지 않는다. 타입을 제공하지 않는 외부 library 경계에서는 가장 좁은 adapter에서만 예외를 두고 이유를 주석으로 남긴다.
- `dataclass`, Pydantic model과 일반 class 중 한 가지면 충분한 데이터 표현을 중복해서 만들지 않는다. 외부 입력·출력 검증에는 Pydantic을 사용하고 내부의 단순 값에는 표준 타입을 사용한다.

### FastAPI와 SQLAlchemy

- route 함수는 HTTP parsing, Pydantic 검증, 작업 호출과 응답 변환만 담당한다. 지식 승격, publication과 재시도 판단은 route에 직접 넣지 않는다.
- 동기 SQLAlchemy session과 psycopg 호출을 기본으로 한다. 측정된 병목과 동시성 요구가 확인되기 전에는 async session을 추가하지 않는다.
- transaction의 commit과 rollback은 ingest, 승격, publication 같은 작업 단위의 진입점이 소유한다. 하위 조회·저장 helper는 숨겨서 commit하지 않는다.
- ORM row를 API 응답으로 직접 반환하지 않는다. HTTP 경계의 Pydantic 응답 model로 변환한다.
- N+1 query를 감추기 위한 범용 repository를 만들지 않는다. 실제 query에서 필요한 eager loading이나 명시적 select를 사용한다.

## Agent와 모델 호출

현재 제품에서 재사용할 추출·생성·판정 함수와 최소 provider 의존성이 있으며, 자료 저장 API와 명시적 실행 경계가 연결되어 있다. 아래 규칙은 #125의 승인된 역할 경계를 #127·#128·#129·#68 등의 제품 구현에 적용할 때 따른다. 모델 배정·검증 상태는 [구현 스택](implementation-stack.md#제품-재사용용-추출-실행-코드)을 참조하며 로컬 실행 코드의 검증을 자동 provider 실행 완료로 취급하지 않는다.

- provider 선택과 `provider:model` 해석은 `agent` 경계 안에 둔다. provider 전용 class를 API나 데이터베이스 모듈에 노출하지 않는다.
- 모델 출력은 작업별 Pydantic structured output으로 검증한다. 실제 응답 JSON과 후보 payload는 기준 데이터베이스에 저장하지 않는다.
- provider library의 내부 재시도는 끄고 애플리케이션의 최대 5회 재시도 정책만 사용한다. 모델 호출 실패와 계약·원문 위치·온톨로지·lint 검증 실패를 다른 상태로 처리한다.
- prompt, structured output schema와 모델 식별자는 작업 종류 가까이에 둔다. 하나의 거대한 prompt registry나 모든 작업을 감싸는 범용 agent class는 만들지 않는다.
- LangChain의 필요한 최소 기능을 사용하고 일반 코드가 후보 조회·원문 참조 복원·검증·저장을 맡는다. Agent에 직접 DB 접근을 주거나 자동 수정 모델 호출·fallback을 추가하지 않는다. #139의 자동 재시도 없는 시험 설정과 위 제품의 일시 장애 재시도 계약은 서로 대체하지 않는다.
- 테스트에서는 실제 provider와 네트워크를 호출하지 않는다. 모델 경계가 반환할 최소 structured output을 작은 fake로 제공한다.

## 오류 처리와 로그

- 신뢰 경계의 입력은 사용하기 전에 검증한다. 예외를 잡은 뒤 계속 진행해 부분 저장이나 잘못된 공개 상태를 만들지 않는다.
- 호출자가 복구 방법을 달리 선택할 때만 구체적인 application exception을 만든다. 그렇지 않으면 표준 예외를 유지하고 API 또는 worker 경계에서 한 번 변환한다.
- `except Exception`으로 오류를 숨기지 않는다. 경계에서 transaction rollback이나 최종 실패 기록을 위해 잡을 때는 원래 예외를 보존하고 다시 발생시키거나 명시적인 실패 결과로 바꾼다.
- 같은 오류를 여러 계층에서 반복해서 기록하지 않는다. 복구나 최종 상태를 결정하는 경계에서 한 번 기록한다.
- Python 표준 `logging`의 모듈 logger를 사용하고 안정된 event 이름과 `task_id`, `attempt`, `status`, `duration_ms` 같은 필드를 함께 기록한다. 별도 logging wrapper나 dependency는 추가하지 않는다.
- API key, credential, 정규화 본문, 인용문, prompt, 모델 전체 응답과 사용자에게 공개할 수 없는 내부 자료는 로그에 남기지 않는다.
- 사용자 응답에는 안정된 오류 code와 해결 가능한 설명만 제공하고 stack trace와 내부 식별자를 노출하지 않는다.

## Alembic migration

- migration은 `server/migrations/versions/`에 두고 기본 `<revision>_<slug>.py` 이름을 사용한다. slug는 해당 revision의 한 가지 목적을 짧은 `snake_case`로 표현한다.
- 한 revision에는 한 Issue에서 승인한 응집된 물리 변경만 넣는다. 여러 후속 migration을 예상해 빈 revision이나 미래용 table을 만들지 않는다.
- autogenerate 결과는 초안으로 취급하고 모든 column, constraint, index, server default와 삭제 동작을 승인된 물리 설계와 대조한다.
- migration은 Alembic과 SQLAlchemy metadata만 사용하고 API, Agent, application service나 외부 모델을 import하지 않는다.
- `upgrade()`와 `downgrade()`를 명시한다. 안전하게 되돌릴 수 없는 변경은 가짜 역연산을 쓰지 않고 해당 물리 설계 Issue와 PR에 위험과 복구 방법을 기록한다.
- `main`에 병합된 revision을 수정하거나 순서를 다시 쓰지 않는다. 변경이 필요하면 새 revision을 만든다.
- PostgreSQL 자료형, ID, schema namespace, object·constraint 이름, `NULL`, default, hash, comment와 삭제 정책은 [물리 스키마](../data/physical-schema.md)를 따른다.

## 테스트 기준

- 분기, loop, parser, 상태 전이, retry, transaction, 무결성 제약과 과거 오류의 회귀처럼 실패 위험이 있는 동작에만 가장 작은 테스트를 둔다.
- 정적 상수, type만 있는 파일, 단순 import·wiring, framework 자체 동작과 삭제된 기능을 확인하는 테스트는 만들지 않는다.
- 하나의 동작을 여러 계층에서 중복해서 검증하지 않는다. 순수 판단은 unit test, PostgreSQL 제약과 transaction은 실제 PostgreSQL integration test, 사용자 상호작용은 React Testing Library component test로 확인한다.
- React 테스트는 role, accessible name과 사용자 동작으로 관찰 가능한 결과를 검증한다. DOM 구조와 class 이름 snapshot은 기본적으로 사용하지 않는다.
- 테스트는 실시간 웹, provider API와 외부 시스템에 의존하지 않는다. 고정 입력과 가장 작은 fake를 사용한다.
- 코드 coverage 비율, 테스트 개수와 브라우저 E2E를 병합 조건으로 두지 않는다. 새 위험이 생겼을 때 그 위험을 잡는 테스트를 추가한다.

## 프로젝트 명령 계약

아래 명령은 현재 각 애플리케이션의 manifest와 설정에 구현되어 있다. DB가 필요한 정확한 실행 순서와 환경 변수는 [DB 운영](../operations/database.md)을 따른다.

### web

`web/package.json`은 다음 script를 제공한다.

| 목적 | 명령 | 실제 도구 명령 |
| --- | --- | --- |
| 개발 서버 | `npm run dev` | `vite` |
| 포맷 적용 | `npm run format` | `biome format --write .` |
| 포맷 검사 | `npm run format:check` | `biome format .` |
| lint | `npm run lint` | `biome lint .` |
| 타입 검사 | `npm run typecheck` | `tsc --noEmit` |
| 테스트 | `npm test` | `vitest run` |
| 빌드 | `npm run build` | `vite build` |
| 전체 검사 | `npm run check` | `biome ci . && tsc --noEmit && vitest run && vite build` |

의존성 설치 검증은 `web/`에서 `npm ci`로 수행한다. CI의 Biome 검사는 파일을 수정하지 않는 `biome ci .`를 사용한다.

### server

다음 명령은 `server/`에서 실행한다.

| 목적 | 명령 |
| --- | --- |
| 잠금 버전 설치 | `uv sync --frozen` |
| 포맷 적용 | `uv run ruff format .` |
| 포맷 검사 | `uv run ruff format --check .` |
| lint | `uv run ruff check .` |
| 타입 검사 | `uv run mypy src` |
| 테스트 | `uv run pytest -q` |
| migration 적용 | `uv run alembic upgrade head` |
| metadata와 migration 비교 | `uv run alembic check` |

PostgreSQL이 필요한 pytest와 Alembic 명령에는 `uv run --env-file ../.env`를 사용한다. 별도 Makefile, task runner나 shell wrapper는 같은 명령을 반복할 필요가 생기기 전에는 만들지 않는다.

## 주석, 설정과 생성 파일

- 주석은 코드가 무엇을 하는지 번역하지 않고 제약, 불변 조건, 선택 이유와 안전한 변경 조건을 설명한다.
- 후속 작업은 `TODO(#123): 이유와 제거 조건`처럼 추적 가능한 Issue와 함께 적는다. Issue가 없는 막연한 TODO와 FIXME는 남기지 않는다.
- 환경 변수는 Pydantic settings와 한 곳의 프론트엔드 설정 경계에서 읽는다. 제품 코드 곳곳에서 `os.environ`이나 `import.meta.env`를 직접 읽지 않는다.
- 필수 설정이 없거나 잘못되면 시작 단계에서 실패한다. 개발 편의를 위한 가짜 credential이나 production fallback을 만들지 않는다.
- `.env.example`에는 이름과 안전한 예시 형식만 두며 실제 secret은 넣지 않는다. 브라우저에 공개할 수 없는 값은 `VITE_*`로 만들지 않는다.
- `package-lock.json`과 `uv.lock`은 manifest와 함께 커밋한다. build output, cache, coverage와 로컬 환경 파일은 커밋하지 않는다.
- 생성 파일에는 생성 원본과 명령이 있어야 한다. 생성된 API client나 schema 복사본은 실제 소비자가 생기고 갱신 검사가 마련되기 전에는 추가하지 않는다.
- `docs/data/schema-reference.md`는 SQLAlchemy metadata에서 생성한다. `uv run --project server --frozen python scripts/check_docs.py --write`로 갱신하고 수동으로 수정하지 않는다.

## 리뷰 확인표

- 변경이 현재 Issue의 사용자 흐름이나 무결성 요구로 설명되는가?
- 같은 기능을 기존 코드, 표준 라이브러리나 승인된 의존성으로 해결할 수 있는가?
- 새 abstraction과 공유 코드에 실제 두 번째 사용처가 있는가?
- 입력, 오류, transaction, 로그와 secret 경계가 분명한가?
- 변경한 함수가 복잡도 10 이하이며 비자명한 동작에 가장 작은 검증이 있는가?
- 관련 format, lint, typecheck, test와 build 검사가 통과했는가?
- PostgreSQL 물리 결정은 [물리 스키마](../data/physical-schema.md)를 따르고 Git 작업은 [기여 규칙](../../CONTRIBUTING.md)을 따르는가?

## 참고 자료

- [TypeScript strict 옵션](https://www.typescriptlang.org/tsconfig/strict)
- [Biome CLI](https://biomejs.dev/reference/cli/)
- [Biome noExcessiveCognitiveComplexity](https://biomejs.dev/linter/rules/no-excessive-cognitive-complexity/)
- [Ruff C901](https://docs.astral.sh/ruff/rules/complex-structure/)
- [uv project 명령 실행](https://docs.astral.sh/uv/concepts/projects/run/)
- [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
