# ontology-map 구현 스택 기준

## 문서 상태

- 상태: 현재 구현·운영 미활성 범위·후속 책임을 구분한 기술 기준
- 확인일: 2026-09-21
- #215 구현 merge 기준: `542ebe5d4899b06f0fa7022d53549c7c194ec2be`
- #215: completed, PR #220 merged
- 실행 안내: [DB 운영](../operations/database.md)
- initial publication 상세: [Initial publication durable execution](initial-publication.md)
- 코드 규칙: [code-conventions.md](code-conventions.md)

이 문서는 저장소의 현재 runtime·직접 의존성·프로세스·durable 실행 경계를 설명한다. 과거 모델 비교와 품질 시험의 세부 점수·비용·원시 판정은 각 Issue, 특히 #139에 보존하고 여기서 현재 제품 구현과 섞지 않는다. 코드가 main에 병합됐다는 사실과 실제 shared/product DB에서 해당 경로를 enable했다는 사실은 구분한다.

## 기본 원칙

- 하나의 저장소에서 React web과 Python server를 관리한다.
- 브라우저와 PostgreSQL 사이의 제품 경계는 FastAPI HTTP API다.
- FastAPI route, application-service 함수, SQLAlchemy query 함수를 구분한다.
- backend 내부 기능이나 DB 접근을 별도 HTTP microservice로 만들지 않는다.
- API와 durable 실행 코드는 같은 Python package를 사용한다. 현재 durable runner는 명시적으로 호출하는 application/domain 함수이며 Redis·Celery·별도 queue daemon을 두지 않는다.
- 테이블별 범용 CRUD repository, 미래용 interface, generic workflow engine을 만들지 않는다.
- Redis, Celery, LangGraph orchestration, API gateway와 microservice는 현재 스택에 없다.
- 직접 의존성은 고정 버전으로 선언하고 전이 의존성은 lockfile로 관리한다.

## 런타임과 기반 서비스

| 구분 | 선택 | 기준 버전 |
| --- | --- | --- |
| JavaScript 런타임 | Node.js | 24.20.0 |
| JavaScript 패키지 관리자 | npm | 11.19.0 |
| Python 런타임 | Python | 3.14.7 |
| Python 프로젝트·패키지 관리자 | uv | 0.12.7 |
| 데이터베이스 | PostgreSQL | 18.6 |

`compose.yaml`은 고정한 PostgreSQL 18.6 DB와 FastAPI `api` service를 제공한다. web은 Vite 개발 서버로 실행한다. migration은 API 시작 시 자동 적용하지 않고 운영 절차에서 명시적으로 적용한다.

## 프론트엔드

| 역할 | 직접 의존성 | 버전 |
| --- | --- | --- |
| UI | React, React DOM | 19.2.8 |
| 빌드·개발 서버 | Vite | 8.2.2 |
| 언어 | TypeScript | 7.0.2 |
| React plugin | `@vitejs/plugin-react` | 6.1.1 |
| graph | `3d-force-graph` | 1.80.0 |
| 3D rendering | Three.js | 0.185.0 |
| format·lint | Biome | 2.5.11 |
| test | Vitest, React Testing Library | 4.1.11, 16.3.3 |

web은 상대 경로 `/api/v1`을 호출하고 Vite가 `ONTOLOGY_MAP_API_PROXY_TARGET`으로 proxy한다. `web/src/data.ts`는 exploration·검색·Relation·Evidence Trace·peripheral·질문·인사이트 응답을 검증해 화면 모델로 바꾼다. 화면 read/click은 model generation을 trigger하지 않는다.

## 백엔드와 provider 의존성

| 역할 | 직접 의존성 | 버전 |
| --- | --- | --- |
| HTTP framework | FastAPI | 0.141.1 |
| ASGI server | Uvicorn | 0.52.4 |
| SQL·metadata | SQLAlchemy | 2.0.52 |
| migration | Alembic | 1.19.1 |
| PostgreSQL driver | psycopg | 3.3.4 |
| 입력·출력 검증 | Pydantic | 2.13.5 |
| Structured Output | langchain-core, langchain-openai | 1.6.2, 1.6.2 |
| provider HTTP | httpx | 0.28.1 |
| 외부 tracing 비활성화 경계 | langsmith | 0.12.4 |
| 설정 | pydantic-settings | 2.15.0 |
| test | pytest | 9.1.1 |
| format·lint | Ruff | 0.16.5 |
| typecheck | mypy | 2.3.1 |

LangChain/OpenAI-compatible provider integration은 #127에서 main에 병합됐다. LangGraph·memory·자유로운 tool loop·자동 fallback은 사용하지 않는다. provider adapter는 Structured Output과 전송 경계를 담당하고, DB·제품 의미·검증·저장은 일반 Python 코드가 담당한다. 실제 paid/live 호출과 deterministic MockTransport/주입 operation CI를 구분한다.

## Durable provider execution

#125/#127의 current main 경계는 기존 `model_task`, `agent_attempt`, `output_schema_definition`에 `provider_call_slot`을 더해 provider send의 durable budget과 ambiguity를 표현한다.

현재 migration chain의 최신 head는 다음과 같다.

```text
0001 frozen schema
→ 0002 panel reading contracts
→ 0003 attribute units
→ 0004 Topic reference lifecycle
→ 0005 promotion canonical-change provenance (#216)
→ 0006 provider_call_slot (#127)
→ 0007 source_claim + graph projection (#247)
→ 0008 FOLLOWUP 90d+1y output contract activation (#251)
→ 0009 nullable canonical_url for uploaded sources
→ 0010 source processing job
```

공통 lifecycle은 다음 의미를 소유한다.

- `model_task` claim과 opaque lease fence
- `PENDING`, `RUNNING`, `RETRY_WAIT`, `SUCCESS`, `VALIDATION_BLOCKED`, `FINAL_FAILED`
- provider send 전 `provider_call_slot` RESERVED commit
- confirmed terminal provider result의 append-only `agent_attempt`
- timeout/unknown result와 reclaim
- retry/backoff와 slot budget
- exact lease 검증 후 product finalizer 진입

제품별 runner는 이 lifecycle을 재구현하지 않는다.

자료 입력은 `POST /api/v1/source-intake`가 TXT·MD·PDF·DOCX 파일을 본문으로 추출하고 기존 정규화 규칙과 SHA-256을 적용한 뒤 `source_document`와 `source_processing_job`을 한 트랜잭션으로 저장한다. commit 뒤 FastAPI 프로세스의 최대 4개 worker가 기존 `application_execution.run_document(...)` 경계로 추출·promotion·publication을 수행한다. POST 응답과 `GET /api/v1/source-intake/{source_document_id}`는 job 상태·단계·경과 시간·task·batch·오류 코드를 제공한다. 업로드 자료는 외부 URL이 없을 수 있어 `canonical_url`을 `NULL`로 저장한다. migration은 API 시작 시 자동 적용하지 않는다.

추출 준비와 판정에는 TypeSafe Jev `jev-1.13.0`을 사용한다. source span별 불필요한 화면 문구 제거 확률이 `0.95` 이상일 때만 generation 입력에서 제외한다. 같은 사전 검사에서 문서가 AI·system·추출 결과·tool·DB를 조작하려는 지시를 포함할 확률이 `0.95` 이상이면 `PROMPT_INJECTION_DETECTED`로 작업 전체를 중단하고 OpenAI에 보내지 않는다. 공개 적합성·binding은 검증된 역할별 임계값으로 판정한다. Claim 지지와 기존 후보 동일성은 holdout 기준을 통과하지 못해 Terra가 계속 맡는다. `source_document`, 본문 hash, offset과 Evidence 검증 원문은 바꾸지 않는다. 모델·정책 버전·임계값과 역할 활성 상태는 task identity에 포함한다. 활성 JEV 역할의 실패는 명시적으로 재시도하거나 실패시키며 OpenAI fallback으로 숨기지 않는다.

## 에이전트 역할과 모델

### 제품 재사용용 추출 실행 코드

`KNOWLEDGE_EXTRACTION`의 role-specific prompt·Structured Output과 OpenAI adapter는 #127에서 main에 병합되어 있으며, 공통 durable claim/lease/call-slot lifecycle과 canonical promotion 경로에 연결된다. #247은 문서 단위 generation·claim review·Entity Resolution로 호출을 줄이고, unresolved mention에 의존한 binding만 제거한 뒤 유효한 binding이 하나 이상 남은 Claim을 canonical graph에 저장한다. `mention.text`는 원문에 실제로 나타나며 대상을 구별하는 가장 짧은 식별 구절이고, 부정·수량·기간·조건을 포함한 전체 서술은 Claim statement가 소유한다. 모델 생성 품질·실제 자료 적합성은 별도 평가이며 durable 실행 성공과 동일시하지 않는다.

파일럿 유료 실행은 호출자가 승인받은 `pilot_id`, 최대 호출 수, 최대 USD, 저장소·제품 DB 밖의 절대 파일 경로로 `PilotBudget`을 새로 만든 뒤 같은 객체를 각 `run_document(..., pilot=pilot)` 또는 `resume_publication(..., pilot=pilot)` 호출에 전달한다. 원장은 `0600`으로 배타 생성하고 호출 전 HTTP 메서드·주소·본문의 SHA-256 식별자와 보수적 최대 비용을 `fsync`한 뒤, 확인된 provider token 사용량과 그에 따른 비용 상한만 별도 기록한다. 예약·전송 전에 확정된 `REQUEST_SIZE_LIMIT`·`INVALID_REQUEST` preflight 오류는 해당 task 실패를 기록한 뒤 같은 파일럿의 다음 문서를 허용하며, 기존 파일의 자동 재개와 UNKNOWN·기록 실패·애매한 호출 뒤 추가 전송은 막는다. 이 계산은 현재 고정된 Model Studio 요금표와 입력 1,000,000 token·요청 출력 상한이 실제 과금 상한이라는 운영 전제에 의존하며, 새 파일럿 승인과 요금 확인 없이 유료 실행을 열지 않는다. `--dry-run`은 파일을 만들지 않는다.

### 현재 제품 구현 상태

| 역할·작업 | 현재 구현 상태 | 소유 경계 |
| --- | --- | --- |
| 본문 추출 / `KNOWLEDGE_EXTRACTION` | 전체 정규화 본문을 보존하고, task identity에 고정된 Jev 선택이 있으면 해당 span만 generation에 전달한 뒤 Claim을 문서 단위로 검토함. 원문 지지·publication 적합성과 유효한 binding을 통과한 canonical Claim만 저장함 | #254, #247, #127, #125, #128, #216 |
| `ENTITY_RESOLUTION_PROPOSAL` | 저장 후보 조회·동일 대상 판정·canonical write 연계 경계가 구현됨. 이름/alias 일치만으로 확정하지 않음 | #128 |
| `FOLLOWUP_QUESTIONS` | #129의 90일+1년 atomic input identity, provider adapter, durable runner, validation/finalizer가 존재 | #129, #251 |
| `NODE_INSIGHT` | #68의 90일+1년 atomic input identity, provider adapter, durable runner, validation/finalizer가 main에 존재 | #68 |
| `NODE_CONTEXT` | #215의 product identity·finalizer·provider adapter·durable runner가 main에 존재하고 initial coordinator에 연결됨 | #215 |
| `CONFLICT_SUMMARY` | 기존 conflict 계약을 따르며 별도 승인 없는 범용 생성/요약 runner는 추가하지 않음 | #130 |
| `EVIDENCE_LINEAGE_PROPOSAL` | schema task kind와 lineage 계약을 구분하며 별도 제품 실행 범위는 소유 Issue를 따름 | #64, #124 |

FOLLOWUP과 NODE_INSIGHT의 품질 의미·Claim role·normal empty·`VALIDATION_BLOCKED`는 각 제품 Issue가 소유한다. #215는 runner API를 consumer로 사용할 뿐 이 의미를 복제하지 않는다.

## Initial publication

#215 / merged PR #220은 main의 #216/#127/#129/#68 경계를 다음 순서로 조합한다.

```text
#127 promotion transaction
→ canonical mutation + #216 provenance
→ promotion COMMITTED
→ commit

post-commit application handoff
→ affected Node projection
→ publication_affected_node frozen membership
→ PREPARING
→ deterministic node_search_document
→ NODE_CONTEXT durable execution
→ FOLLOWUP 90d+1y atomic durable runner
→ NODE_INSIGHT 90d+1y atomic durable runner 또는 구조적 normal-empty 확정
→ existing completeness/stale validation
→ READY
```

`publication_affected_node`는 최초 `NOT_STARTED → PREPARING` 때 #216 provenance에서 확정하고 그 뒤 같은 generation의 authoritative membership으로 사용한다. deterministic search-document exact row reuse는 허용하지만 과거 generation의 NODE_CONTEXT/FOLLOWUP/Insight를 새 generation completeness에 섞지 않는다.

search document와 NODE_CONTEXT alias grounding은 publication-visible alias만 사용한다. historical alias, current batch alias, READY batch alias는 허용하고 다른 NOT_STARTED/PREPARING/FAILED batch가 만든 alias는 제외한다. Reviewer 확인 시점에는 기존 `node_alias` row를 in-place 수정하는 production path가 없으므로, 향후 그런 mutation 경로를 도입하면 alias provenance/visibility 판정을 재검토한다.

NODE_CONTEXT effective input identity에는 publication generation, selected search-document identity/basis, 실제 agent input과 result-affecting execution settings가 포함된다.

#215는 queue, scheduler, publication job/attempt table, 새 generation table을 추가하지 않는다. process restart나 명시적 재실행에서는 durable task의 현재 상태를 이어가며 terminal SUCCESS를 재전송하지 않는다. 상세는 [Initial publication durable execution](initial-publication.md)을 따른다.

READY 이후 basis invalidation/reconciliation/recovery는 #180 소유이며 initial publication과 구분한다.

## 데이터·저장 경계

- generated raw response, reasoning, 검증 전 candidate와 별도 Source Claim을 제품 결과로 저장하지 않는다. 공개 가능한 결과는 Observation에 연결되고 유효한 binding이 하나 이상인 canonical Claim이다.
- Agent는 입력으로 제공받은 stable ID를 참조하고 일반 코드가 존재성·공개성·generation·basis를 검증한다.
- canonical mutation과 #216 provenance는 promotion transaction 안에서 기록하고 `promotion_status=COMMITTED`까지 소유한다.
- initial publication은 promotion transaction commit 이후 별도 application handoff와 transaction에서 시작한다.
- 이전 READY publication/artifact와 canonical knowledge는 새 generation 실패 때문에 삭제·rollback하지 않는다.
- 사용자 read/click은 generation이나 provider call을 시작하지 않는다.

## 주요 디렉터리와 의존 방향

```text
ontology-map/
├── compose.yaml
├── server/
│   ├── migrations/versions/        # 0001 ... 0010
│   ├── src/ontology_map/
│   │   ├── main.py                 # FastAPI application
│   │   ├── api.py, panel_api.py    # HTTP boundary
│   │   ├── exploration.py, search.py, relations.py, insights.py
│   │   ├── extraction*.py          # #127 product/provider execution
│   │   ├── durable_provider.py     # shared provider call composition
│   │   ├── followup_*.py           # #129 provider/execution/runner
│   │   ├── insight_*.py            # #68 provider/execution/runner
│   │   ├── node_context_*.py       # #215 context product/provider/runner
│   │   ├── initial_publication_coordinator.py
│   │   ├── initial_publication_handoff.py
│   │   └── db/
│   │       ├── schema.py, session.py
│   │       ├── model_tasks.py       # shared claim/lease/call ledger
│   │       ├── promotion_provenance.py
│   │       ├── followup_tasks.py, insight_tasks.py
│   │       └── initial_publication_*.py
│   └── tests/
└── web/
```

의존 방향은 HTTP/read path와 background durable execution을 모두 같은 제품 DB 계약에 모은다.

```text
browser → FastAPI/application-service → DB query → PostgreSQL

explicit coordinator/runner call
→ durable model_task claim/lease
→ provider_call_slot reservation
→ provider operation
→ agent_attempt terminal
→ product finalizer
→ PostgreSQL artifact/publication state
```

## 프로세스와 실행

| 프로세스·경계 | 현재 상태 | 진입점 예 |
| --- | --- | --- |
| PostgreSQL | 구현 | `docker compose up -d db` |
| FastAPI | 구현 | `ontology_map.main:app` / Compose `api` |
| web | 구현 | `web/`의 `npm run dev` |
| KNOWLEDGE_EXTRACTION durable execution | main 구현 | #127 runner/application functions |
| FOLLOWUP durable execution | main 구현 | `followup_runner.run_followup()` |
| NODE_INSIGHT durable execution | main 구현 | `insight_runner.run_insight()` |
| #215 initial publication durable integration | main 구현 | `initial_publication_handoff.finalize_extraction_with_initial_publication()` / `initial_publication_coordinator.run_initial_publication()` |
| generic scheduler/queue daemon | 없음 | 의도적으로 미도입 |

runner 함수의 존재를 product/shared DB 운영 enable과 동일시하지 않는다. 실제 cutover·enable 검증은 #227에서 별도로 수행한다.

## 설정과 비밀 관리

server는 PostgreSQL DSN과 environment 설정을 읽는다. provider credential·workspace endpoint는 provider 실행 시 명시적으로 주입하며 저장소·로그·브라우저 `VITE_*`에 넣지 않는다. 공개 endpoint로 자동 대체하지 않는다.

MockTransport/injected operation을 사용한 CI와 실제 외부 paid provider 호출을 구분한다. credential이 없는 deterministic CI를 실제 live provider 품질 검증으로 표현하지 않는다.

## 검사와 CI

주요 backend 검사는 다음을 사용한다.

| 대상 | 명령/경계 |
| --- | --- |
| server | `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src`, `pytest` |
| migrations | `alembic upgrade head`, `alembic current`, `alembic check` |
| 문서 | `uv run --project server --frozen python scripts/check_docs.py --check` |
| web | `npm run check` |

Durable provider와 publication 변경은 격리 PostgreSQL 18.6에서 migration head와 실제 transaction/lease/call-slot/finalizer 순서를 검증한다. Issue별 전용 DB environment가 필요한 회귀는 해당 workflow에서 실행하고 다른 workflow의 skip을 그 Issue의 PASS 증거로 세지 않는다.

#215 final candidate는 exact merge-ref에서 Phase A/B/C/D, #129/#68 durable runner, 전체 backend, Ruff, mypy, Alembic, docs/diff 검사를 통과한 뒤 main에 병합됐다. 이 검증은 실제 paid/live provider 품질이나 shared/product DB cutover 완료를 뜻하지 않는다.

## 현재 구현과 후속 경계

| 영역 | 현재 구현 | 후속 경계 |
| --- | --- | --- |
| 공개 읽기 | exploration·검색·Relation·Evidence·peripheral·질문 답변·종합보고서와 read-time basis 재검증 | READY 이후 invalidation/recovery는 #180 |
| source intake | 불변 문서와 처리 job 저장, 프로세스 내 worker의 추출·promotion·publication | 운영 queue·다중 프로세스 복구는 별도 범위 |
| extraction/promotion | #127 durable provider execution과 canonical promotion/provenance 연계 | 실제 자료 품질·운영 사용은 별도 평가 |
| FOLLOWUP | #129 durable runner와 product finalizer main 반영 | 실제 모델 품질 평가는 #129 기록에 따름 |
| NODE_INSIGHT | #68 durable atomic runner와 product finalizer main 반영 | 실제 모델 품질 평가는 #68 기록에 따름 |
| initial publication | #215 Phase A/B/C/D durable integration과 post-commit handoff가 main에 병합됨 | READY 이후 recovery는 #180 |
| shared/product DB enable | 코드·격리 DB 검증과 분리 | #227: current migration head 적용 + #216 legacy cutover guard + 실제 coordinator enable/smoke |

인증·관리자·배포 인프라와 #180 recovery는 승인된 별도 작업 없이 #215에 추가하지 않는다. schema의 존재, 합성 fixture, MockTransport integration을 실제 paid/live 모델 품질 보증으로 해석하지 않는다.
