# ontology-map 로컬 실행과 DB 운영

이 문서는 PostgreSQL, migration, 개발용 HBF fixture, FastAPI와 web을 한 번에 실행하는 기준 절차다. 브라우저는 Vite proxy를 통해 FastAPI만 호출하며 PostgreSQL에 직접 접근하지 않는다.

## 추출 실행 코드의 로컬 검사

이 검사는 DB·실제 모델·외부 tracing을 사용하지 않는다. `server/`에서 실행하며 DB 설정 파일이나 provider 키를 읽을 필요가 없다.

```bash
uv sync --frozen
uv run --frozen pytest -q tests/test_extraction.py
uv run --frozen ruff check .
uv run --frozen mypy src
```

원문·typed 후보 계약은 `ontology_map.extraction_contracts`, 일반 함수 실행 경로는 `ontology_map.extraction`, Model Studio 경계는 `ontology_map.model_studio`에 있다. API를 시작하지 않고 같은 모듈을 application 코드에서 import할 수 있다. `extract_knowledge(document, ontology, models, limits, include_structure=..., completed=...)`에 검증한 `SourceDocument`, 호출자가 승인 목록으로 만든 `Ontology`, `ModelStudio`, `ExtractionLimits`를 전달한다. `completed`는 선택적인 process-local 정확 재처리 캐시이며 영속 작업 상태가 아니다.

`SourceDocument`는 문서 ID·NFC/LF 정규화 본문·UTF-8 SHA-256과 원문 ID별 `[start,end)`·정확한 quote·quote hash·선택적인 정규화 문단 묶음을 받는다. 비공백 원문을 projection에서 누락하거나 위치·인용을 바꾸면 모델 호출 전에 실패한다. 입력 ontology에는 활성 유형, 허용 Topic, 관계 code·정의 version·방향·endpoint와 속성 code·정의 version·단일 대상 유형·값 종류·허용 단위를 명시한다. `revision_id`는 실제 DB revision을 조회하지 않은 로컬 입력에서는 `null`이며 가짜 영속 ID를 만들지 않는다. DB 연결 시에는 실제 revision·version과 활성 상태를 조회·검증해야 한다. 이 runtime DTO는 제품 DB의 새 schema가 아니다.

TOPIC 언급은 원문에 실제로 있는 `text`와 승인 목록의 `topic_name`을 구분한다. 다른 유형의 `topic_name`은 `null`이다. 코드가 원문 표현·허용 명칭·endpoint를 검사한 뒤 Plus의 별도 의미 연결 판정을 거친다. 원문의 `AI`를 승인 Topic `인공지능`에 제안할 수 있지만 이름 대응만으로 통과시키거나 새 Topic·상위 Topic을 자동 추가하지 않는다.

`ModelStudio`는 `SecretStr` 키, 명시적인 `Budget(max_calls, max_usd)`와 필수 keyword 인자 `base_url`을 받는다. 키 파일을 직접 읽거나 복사하지 않으며 application 실행 프로세스가 기존 설정의 키와 싱가포르 workspace endpoint를 주입해야 한다. 허용되지 않은 주소는 `UNAPPROVED_ENDPOINT`, 주입한 주소와 실제 전송 대상의 불일치는 `ENDPOINT_CONTRACT_ERROR`로 차단한다. 사용 후 `close()`로 HTTP client를 닫는다. 무호출 검사에서는 `httpx.MockTransport`와 가짜 키·가짜 workspace 주소만 사용하고 실제 socket 연결·LangSmith 업로드를 차단한다. 실제 호출은 역할별 호출 수·상한·예산·중단 조건을 승인받은 별도 실행에서만 허용한다. 예산이 0이면 전송하지 않는다.

각 역할에 `CallLimits(max_input_tokens, max_output_tokens, max_request_bytes)`를 제공한다. 실제 직렬화된 요청의 byte 상한은 전송 직전에 검사하고, 출력은 provider의 `max_tokens`로 제한한다. 입력 token 상한은 provider가 반환한 usage로 확인하는 사후 중단 기준이다. Qwen tokenizer가 없는 상태에서 문자 수를 정확한 token 수로 취급하지 않는다.

비용은 요청마다 문서화된 입력 최대 1,000,000 tokens와 해당 출력 상한, [Model Studio 싱가포르 최대 구간 단가](https://www.alibabacloud.com/help/en/model-studio/model-pricing)를 먼저 예약한다. 알려진 정상 usage를 받으면 같은 최대 단가로 계산한 보수적 비용으로 정산해 나머지 예약을 반환한다. 이는 실제 청구액이 아니라 상한 추정치다. 다음 호출의 전체 예약까지 예산 안에 들어와야 전송할 수 있다. 실패·잘림 등으로 usage가 불명확하면 예약을 유지하고 중단한다. token 기준 초과·인증·transport·응답 모델 불일치도 중단하며, 확정된 usage가 있는 출력 계약 오류에는 repair·fallback·자동 재호출을 하지 않는다. 한 실행의 client·budget은 순차적으로만 사용한다.

키·본문·인용·prompt·모델 응답·reasoning은 로그에 남기지 않는다. 호출 기록은 역할·모델·요청 hash·성공/오류 code·알려진 token 수·예약/상한 비용·시간만 메모리에 보유한다. 새 context와 `tracing_context(enabled=False, parent=False)`로 상위 LangChain callback·trace를 배제한다. LangChain debug/verbose 또는 provider의 DEBUG 로깅이 켜져 있으면 호출 전에 실패한다. 시스템 전체에서 원문을 기록하는 임의 logger나 외부 callback을 이 경로에 추가하지 않는다.

평가에는 `extraction_metrics.summarize(result, required_fact_ids, reviews, final_reviews=...)`를 사용한다. 모든 생성 후보와 최종 반환 후보를 각각 유한 독립 검토하며 이 검토를 모델에 보내지 않는다. 연결이 일부 제외된 최종 후보에 생성 단계의 검토를 자동 재사용하지 않는다. `evidence_supported`는 statement·modality·귀속의 자기 근거 충분성, `correct`는 해당 단계 후보의 의미 보존을 평가한다. 생성 단계에서 ontology 미지원 자체는 사실 오류로 세지 않지만 최종 보존율에서는 누락으로 센다. Claim 근거 판정의 오승인·오거절은 `support_*`로, 최종 오류·산출량은 `final_*`로 구별한다. 오류가 발견된 최종 Claim을 분모에서 빼지 않고, 비중복 Claim 하나에 중대 오류가 여러 개여도 한 번 센다. 빈 결과의 오류율은 0%가 아니라 평가 불가이며 데모 관문 통과로 해석하지 않는다. 함수는 데모 승인 신호를 반환하지 않는다.

현재 검증은 반환 후보까지다. 실제 모델의 품질, 독립 자료의 보존율 70% 이상·중대 오류율 1% 미만, 반복 중대 오류 사실군 차단, Node 동일 대상·Claim 의미 중복 판정과 DB 정합화·저장·publication·사이트 연결은 별도 검증이 필요하다. 실제 시험 자료·후보 수·비용·검토 기준은 정식 문서에 복제하지 않고 #127·#139에서 동결한다. 고정 모델·의존성 버전과 구현 경계는 [구현 스택](../development/implementation-stack.md#제품-재사용용-추출-실행-코드)을 따른다.

`server/run_role_harness_trial.py`는 #139에서 승인한 개발 비교를 위 함수로 실행하는 한정된 실행기다. `server/`에서 `PYTHONPATH=src uv run --frozen python run_role_harness_trial.py --sources <기존 sources-frozen.json> --gold <기존 gold.json> --output-dir <접근 제한 임시 디렉터리>`를 실행하면 모델 전송 없이 입력과 기존 credential 파일의 권한·endpoint를 검사하고 manifest를 동결한다. 설정은 시험 프로세스 안에서만 읽으며 manifest에는 개별 endpoint 값 대신 SHA-256만 남긴다. 승인한 유료 실행에만 같은 명령에 `--execute`를 붙인다. 실행기는 동결된 입력·코드·endpoint hash가 바뀌었거나 이미 실행한 디렉터리이면 호출하지 않는다. 키는 실제 실행 프로세스의 환경 변수로만 주입한 뒤 제거한다. provider 원시 응답·reasoning은 저장하지 않는다. 유한 평가에 필요한 parsed 후보와 판정은 해당 임시 디렉터리에만 제한 보관하며 Git·제품 DB·로그·외부 tracing으로 보내지 않는다. 이 실행기는 제품의 artifact 보존 기능이나 일반 시험 플랫폼이 아니다.

실행기는 #139에서 확인한 이전 유료 실행의 hash·역할별 호출 수·보수적 비용을 manifest에 포함하고, 전체 승인 한도에서 기존 사용량을 뺀 `Budget`으로 시작한다. 실행 결과에는 이번 실행과 누적 호출·비용을 구분한다. 역할별 허용량보다 전체 호출·비용 한도가 우선하며 이전 실패를 이유로 예산을 초기화하지 않는다. 고정된 네 문서·두 조건·후보 상한이 역할별 호출 범위를 제한한다. 이 실행기는 승인된 새 동결 실행 한 번만을 위한 것이며 다른 임시 디렉터리에서 반복해도 된다는 뜻이 아니다. 추가 실행에는 갱신한 누적 사용량과 승인이 필요하며 자동 재개 기능은 없다.

본문 선택의 중복 ID는 `BODY_SELECTION_DUPLICATE_ID`, 입력에 없는 ID는 `BODY_SELECTION_UNKNOWN_ID`, 둘 다 있으면 `BODY_SELECTION_DUPLICATE_AND_UNKNOWN_ID`로 반환한다. 일반 실행 함수의 `error_code`와 시험 실행기의 중단 기록에 이 code를 유지하며 후속 생성·판정을 호출하지 않는다. Claim의 기존 원문 참조 검증·오류 계약은 바꾸지 않는다.

공통 비공백 문자열은 `^[\s\S]*\S[\s\S]*$`로 정의해 로컬의 부분 일치와 provider의 전체 문자열 제약 양쪽에서 여러 글자·공백·줄바꿈을 같은 의미로 허용한다. 빈 문자열과 공백만 있는 값은 계속 거절하며 입력 ID의 실제 존재·중복은 별도로 검사한다. 반환된 ID를 보정하거나 원문을 재작성하지 않는다. 이는 전송 schema의 해석 차이를 줄이는 변경이며 실제 provider 내부 원인 확인이나 생성 품질 개선의 증거가 아니다. 이전 동결 실행의 schema·기록은 변경하지 않고 새 유료 실행에는 별도 승인·동결을 적용한다.

시험 실행기는 이 실패에 한해 접근 제한 임시 디렉터리의 `body-selection-error.json`에 문서 ID와 선택된 ID·중복 ID·입력에 없는 ID의 개수 및 제한된 목록을 남긴다. 목록별 최대 32개와 생략 개수를 기록하고, 64자 이하의 ASCII 영문·숫자·`_ . : -` 식별자만 표시한다. 그 외 값은 `id=null`과 SHA-256으로 남겨 원문이나 장문이 ID 자리에 반환돼도 복사하지 않는다. 이 제한은 진단 표시 범위이며 모델 입력·출력 schema나 ID 유효성 규칙을 바꾸지 않는다. 파일은 0600·배타 생성으로 만들고 기존 파일을 덮어쓰지 않는다. 예외 문자열·표준 출력·일반 실행 결과에는 구체 ID를 넣지 않는다. 이전 실패의 선택 ID를 복구하거나 원인을 소급 확정하는 기능은 아니다.

## 요구 버전

| 항목 | 버전 |
| --- | --- |
| Python | 3.14.7 |
| uv | 0.12.7 |
| Node.js | 24.20.0 |
| npm | 11.19.0 |
| PostgreSQL image | 18.6 |

Docker Desktop을 쓰는 Windows 환경에서는 현재 WSL distribution의 Docker integration을 먼저 켜고 WSL에서 `docker version`과 `docker compose version`이 모두 성공하는지 확인한다.

[#121](https://github.com/studylida/ontology-map/issues/121)에 따라 개발 DB는 공식 PostgreSQL 18.6 image만 사용하며 pgvector extension과 Python pgvector 패키지를 요구하지 않는다. #121 이전 `0001`로 만든 개발 volume은 in-place upgrade 대상이 아니며, 필요한 백업을 확인한 뒤 새 frozen baseline으로 재생성한다. 공유 DB나 다른 세션의 volume은 자동으로 초기화하지 않는다.

## 1. 환경 변수 준비

저장소 루트에서 개발 환경 파일을 만든다.

```bash
cp .env.example .env
```

`.env`의 빈 값은 다음 형식으로 채운다. 이 값은 로컬 개발 예시이므로 공유 환경의 credential로 재사용하지 않는다.

```dotenv
POSTGRES_DB=ontology_map
POSTGRES_USER=ontology_map
POSTGRES_PASSWORD=ontology_map-local
POSTGRES_PORT=127.0.0.1:5432
ONTOLOGY_MAP_ENVIRONMENT=development
ONTOLOGY_MAP_DATABASE_URL=postgresql+psycopg://ontology_map:ontology_map-local@127.0.0.1:5432/ontology_map
```

`ONTOLOGY_MAP_DATABASE_URL`의 사용자, 비밀번호, 포트와 데이터베이스 이름은 같은 파일의 `POSTGRES_*` 값과 일치해야 한다. 실제 secret은 `.env.example`이나 Git에 넣지 않는다.

## 2. PostgreSQL과 migration

저장소 루트에서 PostgreSQL을 시작한다.

```bash
docker compose up -d db
docker compose ps
```

기본 `compose.yaml`은 작업 디렉터리의 Compose project별 DB volume을 사용한다. 위 명령과 CI의 일반 `docker compose` 명령은 다른 작업 디렉터리나 기존 `ontology-map-postgres` volume에 연결하지 않는다. `COMPOSE_PROJECT_NAME`을 공통 값으로 지정하면 이 격리가 깨지므로 작업 디렉터리마다 기본 project 이름을 사용한다.

`POSTGRES_PORT`에는 `[바인드 호스트:]포트`를 지정한다. 위 예시처럼 `127.0.0.1:5432`를 쓰면 DB는 로컬 호스트에만 공개된다. 호스트를 생략하고 `5432`만 쓰면 Docker가 모든 호스트 인터페이스에 바인딩하므로, 다른 장치에서 접속해야 하는 운영 경로에만 해당 환경의 네트워크·인증 정책을 확인한 뒤 사용한다.

D1은 이 로컬 Docker 환경에 새 `ontology-map-postgres` volume을 한 번 만들어 사용한다. D1 단독 작성자는 저장소 루트에서 아래 명령으로 Docker 대상과 정확한 이름의 volume 존재 여부를 먼저 확인한다. `docker volume inspect`가 해당 volume이 없다고 실패해야 새로 만들 수 있다. 이미 있으면 출처·사용 중인 세션·자료와 필요한 백업을 확인할 때까지 생성하거나 연결하지 않는다.

```bash
docker context show
docker info --format '{{.Name}}'
docker volume inspect ontology-map-postgres --format '{{.Name}}'
```

대상이 의도한 로컬 Docker 환경이고 volume이 없음을 확인한 D1 단독 작성자만 다음 명령을 한 번 실행한다. 이때 새 volume에는 백업할 기존 DB 자료가 없다. 기존 volume이 발견되면 이를 빈 것으로 간주하거나 삭제하지 않고 자료 보존·백업 판단을 먼저 한다.

```bash
docker volume create ontology-map-postgres
docker volume inspect ontology-map-postgres --format '{{.Name}}'
```

이 작업 트리에는 `.env`가 없으므로 D1은 저장소 밖의 접근 제한된 환경 파일을 사용한다. 아래 `/absolute/path/to/restricted/d1.env`를 실제 절대 경로로 바꾸고 파일 권한을 `0600`으로 제한한다. 파일에는 `POSTGRES_*`, `ONTOLOGY_MAP_ENVIRONMENT`, `ONTOLOGY_MAP_DATABASE_URL`을 일치시켜 넣되 값이나 자격 증명을 저장소·로그에 복사하지 않는다. `compose.authoritative.yaml`은 위에서 만든 정확한 이름의 외부 volume만 연결하며 volume을 자동 생성하지 않는다. D1의 Compose project도 일반 개발 project와 분리한다.

```bash
docker compose --env-file /absolute/path/to/restricted/d1.env -p ontology-map-d1 -f compose.yaml -f compose.authoritative.yaml up -d db
```

D1에서 시작한 컨테이너를 조회하거나 멈출 때도 같은 환경 파일·project·두 설정 파일을 지정한다. D1의 `uv` 명령에도 같은 제한된 환경 파일을 `--env-file`로 전달한다.

```bash
docker compose --env-file /absolute/path/to/restricted/d1.env -p ontology-map-d1 -f compose.yaml -f compose.authoritative.yaml ps
docker compose --env-file /absolute/path/to/restricted/d1.env -p ontology-map-d1 -f compose.yaml -f compose.authoritative.yaml down
```

`server/`에서 고정된 의존성을 설치하고 Alembic migration을 적용한다.

```bash
cd server
uv sync --frozen
uv run --env-file ../.env alembic upgrade head
uv run --env-file ../.env alembic current
```

현재 기준 revision은 `0001_create_frozen_schema.py` → `0002_add_panel_reading_contracts.py` → `0003_support_multiple_number_attribute_units.py` → `0004_support_topic_reference_lifecycle.py` → `0005_add_promotion_canonical_change.py` → `0006_add_provider_call_slot.py` → `0007_add_source_claim_projection.py` → `0008_bundle_followup_questions.py` → `0009_allow_uploads_without_url.py` → `0010_add_source_processing_job.py`다. PostgreSQL 객체는 `public` schema에 만들며 migration과 SQLAlchemy metadata는 같은 schema를 표현한다. #121 이후 `0001`에는 `vector` extension, `node_embedding` table과 `EMBEDDING` task 허용 계약이 없다.

`0009`를 적용하면 외부 URL이 없는 사용자 업로드를 `source_document`에 저장할 수 있고, `0010`은 업로드 처리 job을 추가한다. migration은 API 시작 시 자동 적용하지 않으며, 실제 대상 DB와 백업·복구 계획을 확인한 뒤 운영 절차로 적용한다.

`0003`은 기존 NUMBER `attribute_revision.unit_rule`을 `attribute_revision_allowed_unit`의 허용 단위 행으로 옮긴 뒤 `claim_attribute_value(attribute_revision_id, unit_code)`를 그 허용 집합에 FK로 연결한다. 기존 단일 단위 revision은 한 행으로 그대로 이관한다. 기존 Claim의 unit이 legacy `unit_rule`과 다르면 값을 환산하거나 수정하지 않고 migration을 실패시킨다. 복수 허용 단위가 생성된 뒤 `0002`로 downgrade하면 의미를 한 문자열로 되돌릴 수 없으므로 downgrade도 중단한다.

`0004`는 `knowledge_item.lifecycle_kind`와 `topic_reference`를 추가한다. 기존 행은 모두 `EVIDENCE_BACKED`로 해석되어 state/promotion 값을 그대로 유지하며 기존 evidence-backed TOPIC도 자동 변환하지 않는다. 새 `PRODUCT_REFERENCE` Topic만 일반 state/promotion 없이 저장할 수 있고 DB 무결성이 TOPIC node + reference definition 조합으로 제한한다. Reference Topic row가 존재하면 `0003` downgrade는 의미 손실을 막기 위해 실패한다.

`0004` 자체는 제품 Topic row를 seed하지 않고 startup도 누락 Topic을 자동 생성하지 않는다. 승인 Topic 9개의 실제 활성화는 migration과 분리된 #201 명시적 reference activation transaction이 담당한다. 개발 fixture의 evidence-backed TOPIC과 제품 Reference Topic을 같은 데이터로 간주하지 않는다.

`0005`는 #216의 immutable `promotion_canonical_change` provenance만 추가하며 historical association을 추정 backfill하지 않는다. initial publication coordinator를 enable하기 전 `COMMITTED + NOT_STARTED` legacy batch가 있으면 `assert_initial_publication_cutover_safe()`가 차단하며 timestamp attribution, 자동 skip, provenance fabrication이나 publication 상태 변경을 수행하지 않는다. 실제 one-time remediation은 별도 운영 판단 없이 이 migration이 자동 수행하지 않는다.

`0007`이 추가한 `source_claim`, `source_claim_observation`, `source_claim_projection`은 중단된 실험의 기존 행을 보존하기 위해 남아 있다. 현재 extraction runtime은 이 table에 새 행을 쓰지 않고 canonical Claim만 promotion한다. 기존 Source Claim을 자동 backfill·변환·삭제하지 않으며, 행이 하나라도 있으면 downgrade는 데이터 손실을 막기 위해 중단한다.

### 제품 ontology reference data 활성화

migration 적용 뒤 제품에서 사용할 승인 ontology reference data는 `server/`에서 다음 명령으로 명시적으로 활성화한다.

```bash
PYTHONPATH=src uv run --env-file ../.env python -m ontology_map.db.ontology_reference_data
```

이 명령은 [승인 ontology reference data](../data/ontology-reference-data.md)의 Relation 13개 + `HAS_TOPIC`, attribute 7개, Reference Topic 9개만 활성화한다. 앱 startup에서는 실행하지 않으며 재실행해도 stable code·revision·Topic row를 중복 생성하지 않는다. 이미 같은 code에 계약과 다른 active revision이 있으면 기존 의미를 덮어쓰거나 비활성화하지 않고 실패한다. 기존 HBF fixture의 `PUBLICLY_ASSOCIATED_WITH`, 저장 Relation·Claim·Evidence·promotion은 수정하지 않는다.

Reference Topic은 #203의 `PRODUCT_REFERENCE` activation 경계를 사용하므로 `source_document`, Observation, Claim, 일반 state, promotion batch 또는 READY publication을 만들지 않는다. `MAX_MEMORY_BANDWIDTH`는 하나의 NUMBER active revision 아래 `GB_PER_S`, `TB_PER_S` 두 허용 원문 단위를 등록하며 환산·정규화·비교를 수행하지 않는다.

## 3. 개발용 HBF fixture

`server/`에서 fixture 명령을 실행한다.

```bash
PYTHONPATH=src uv run --env-file ../.env python -m ontology_map.db.fixture
```

fixture는 `ONTOLOGY_MAP_ENVIRONMENT=development`에서만 동작하고 같은 DB에 다시 실행해도 중복 행을 만들지 않는다. 출력에는 `sk_hynix`, `hbf` 등 node 이름별 bigint ID가 표시된다. 이 값 중 하나를 web의 기본 중심으로 사용한다.

이 fixture는 개발용 고정 데이터다. 외부 source에서 수집한 운영 데이터나 모델의 실제 출력으로 해석하지 않는다.

### 많은 node와 복수 근거를 검토할 개발 자료

기본 HBF 자료를 보존하면서 별도 100-node 검토 자료를 추가하려면 `server/`에서 다음 명령을 실행한다.

```bash
PYTHONPATH=src uv run --env-file ../.env python -m ontology_map.db.review_fixture
```

명령은 development 환경에서만 실행되며 재실행해도 중복 적재하지 않는다. 출력의 `center`를 URL의 `center` 값으로 사용한다. 이름의 `[검토]` 표시와 `example.invalid` 출처는 실제 주장이나 수집 결과가 아니라는 뜻이다. `daa0bf2`의 20-node·24-Relation 구조와 1·3·6개의 독립 근거, 충돌을 재현하고 100 nodes로 늘려 단계별 표시·방향·여러 페이지를 검토한다. 운영 데이터 의미나 모델 품질을 검증하는 자료가 아니며 기존 HBF fixture와 DB volume을 삭제할 필요가 없다.

## 4. FastAPI 실행

호스트에서 실행하려면 `server/`에서 다음 명령을 사용한다.

```bash
PYTHONPATH=src uv run --env-file ../.env uvicorn ontology_map.main:app --reload
```

Docker에서 실행하려면 migration과 fixture를 먼저 적용한 뒤 저장소 루트에서 다음 명령을 사용한다.

```bash
docker compose up --build api
```

API는 기본적으로 `http://127.0.0.1:8000`에서 열리고 OpenAPI UI는 `http://127.0.0.1:8000/docs`에서 확인할 수 있다. 별도 health endpoint는 없다. 애플리케이션은 시작할 때 `SELECT 1`로 DB 연결을 확인하며 실패하면 시작하지 않는다.

## 5. web 실행

새 terminal에서 `web/`로 이동해 환경 파일을 만든다.

```bash
cd web
cp .env.example .env.local
npm ci
```

`.env.local`의 `VITE_DEFAULT_CENTER_NODE_ID`에는 HBF fixture가 출력한 node ID 하나를 문자열 그대로 넣는다. API를 기본 주소가 아닌 곳에서 실행할 때만 proxy target을 바꾼다.

```dotenv
VITE_DEFAULT_CENTER_NODE_ID=1
ONTOLOGY_MAP_API_PROXY_TARGET=http://127.0.0.1:8000
```

다음 명령으로 Vite 개발 서버를 시작한다.

```bash
npm run dev
```

WSL에서 `/mnt/c` 파일을 편집했는데 새로고침 후에도 이전 모듈이 제공되면 개발 서버를 종료하고 `CHOKIDAR_USEPOLLING=true CHOKIDAR_INTERVAL=500 npm run dev`로 다시 실행한다. 이 환경에서 실제 변경 감지 누락을 확인했으며 polling은 해당 개발 실행에만 적용한다.

브라우저에서 Vite가 출력한 주소를 열면 web은 `/api/v1` 요청을 FastAPI로 전달한다. `VITE_DEFAULT_CENTER_NODE_ID`가 없거나 공개할 수 없는 ID이면 초기 화면에 설정 또는 API 오류가 표시된다.

## 6. 최소 smoke check

fixture가 출력한 실제 ID를 전용 shell 변수에 넣는다. 다음 `1`은 형식 예시이므로 현재 DB의 출력값으로 바꾼다.

```bash
export ONTOLOGY_MAP_CENTER_ID='1'
curl --fail "http://127.0.0.1:8000/api/v1/exploration/${ONTOLOGY_MAP_CENTER_ID}?time_window=RECENT_90_DAYS"
curl --fail --get 'http://127.0.0.1:8000/api/v1/nodes/search' --data-urlencode 'q=SK하이닉스' --data 'limit=5'
```

브라우저에서는 기본 중심 graph가 열리고, node 선택과 `RECENT_90_DAYS`·`RECENT_1_YEAR` 변경 및 검색 결과 선택이 새 exploration 요청으로 이어지는지 확인한다. Relation 목록과 Evidence Trace는 상세 panel과 graph에서 실제 API로 조회한다. peripheral 첫 page는 자동으로 표시되며 사용자 축소와 바깥 경계 pan으로 다음 page를 조회한다. 인사이트 tab은 저장 목록·상세를 읽고 연결 근거를 펼친다. source intake의 프로세스 내 worker는 별도 승인 설정을 켠 데모 실행에서만 자료 저장 뒤 추출·promotion·publication을 진행한다.

## 7. 검사

제품 lint 정의는 `ontology_map.db.product_lint.ensure_product_policy(session)`을 호출자 소유 트랜잭션에서 명시적으로 한 번 등록한다. 재실행은 같은 정의를 확인하고, 활성 fixture 정책이나 정의·버전 불일치는 덮어쓰지 않고 중단한다. `require_product_policy(session)`은 읽기 전용 확인이며, 저장된 기준 그래프 재검사는 `REPEATABLE READ` 이상의 별도 트랜잭션에서 `run_full_graph(session)`을 명시적으로 호출한다. 검사는 성공한 전체 실행에서만 이전 finding을 해결하고 열린 `BLOCKING` finding은 기존 공개 조회 필터가 숨긴다. 실제 제품 DB에서 이 호출을 수행하는 권한과 순서는 D1 단독 작성자의 전환 절차를 따른다.

PostgreSQL이 실행 중이고 migration이 적용된 상태에서 `server/` 검사를 실행한다.

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run --env-file ../.env pytest -q
uv run --env-file ../.env alembic check
```

`web/` 검사는 다음 명령 하나로 format, lint, typecheck, component test와 production build를 순서대로 실행한다.

```bash
npm run check
```

## 8. agent와 worker

현재 `server/`에는 agent/worker 진입점과 실행 명령이 없다. 작은 자료의 입력 준비는 #111, 추출 계약은 #127, 동일 대상 판정은 #128, 질문 생성은 #129, 인사이트 생성·품질은 #68에서 다룬다. 저장 인사이트 읽기는 이미 구현되어 있다. #111을 운영 수집·publication 전체의 구현 Issue로 해석하지 않는다.

API와 worker는 구현된 뒤에도 같은 Python 코드와 image를 사용하고 실행 명령만 구분한다. Redis, Celery, LangGraph와 별도 microservice는 실제 필요가 승인되기 전에는 추가하지 않는다.

역할과 모델 snapshot은 [구현 스택](../development/implementation-stack.md#에이전트-역할과-모델)이 소유한다. #139의 임시 시험 실행기와 저장소 밖 WSL secret 주입은 제품 실행 절차가 아니다. 이 문서의 DB·API·web 명령은 모델을 호출하지 않으며, 시험 재현과 비용 승인은 #139의 해당 실행 기록을 따른다. 시험용 credential이나 개별 Workspace endpoint를 문서·저장소·브라우저 설정에 복사하지 않는다.

## 9. 종료와 초기화

컨테이너를 멈추되 DB volume을 보존하려면 저장소 루트에서 실행한다.

```bash
docker compose down
```

개발 DB를 완전히 다시 만들 때만 다음 명령을 사용한다. #121 이전 frozen baseline의 개발 volume을 새 baseline으로 전환할 때도 이 재생성 경로를 사용한다. 먼저 필요한 `pg_dump` 백업과 복구 가능성을 확인하고 다른 세션이 해당 volume을 쓰지 않는지 확인한다. 이 명령은 현재 Compose project의 개발 volume과 안의 로컬 데이터를 삭제하므로 되돌릴 수 없다. 위 D1 overlay의 `ontology-map-postgres`는 외부 volume이므로 이 명령의 대상이 아니다.

```bash
docker compose down --volumes
```

초기화한 뒤에는 PostgreSQL 시작, migration과 fixture 단계를 다시 수행한다.

## 패널 질문·보고서 검토 자료와 전환

`server/`에서 `PYTHONPATH=src uv run --env-file ../.env python -m ontology_map.db.panel_fixture`로 별도 개발 자료를 추가한다. 출력의 gaon은 충분·충돌 사례, empty는 유효한 질문 0개·보고서 0개 사례다. URL `/?center=<gaon ID>&range=90d`에서 검토한다. 기존 자료를 보존하고 development에서만 동작하며 재실행 시 중복 적재하지 않는다. `[패널 검토]` 이름과 개발 출처를 유지한다. 손으로 작성한 가상 예시는 읽기 UX 검증용이며 실제 기업 정보나 모델 출력 품질의 증거가 아니다.

공유 개발 DB에 migration을 적용하기 전 `docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'`의 출력을 저장소 밖의 접근 제한된 백업 파일에 보관한다. `pg_restore -l`로 archive를 확인하고 백업을 외부 게시하지 않는다. 복구가 필요하면 별도 빈 복구 DB에 `pg_restore --exit-on-error`로 복원해 확인한 뒤 사용자가 승인한 전환 절차를 따른다. 실행 중인 DB를 drop하거나 기존 자료를 덮어쓰지 않는다.

0002는 추가 구조만 생성하므로 앱을 이전 버전으로 되돌려도 기존 API는 유지된다. 새 결과가 들어 있으면 downgrade는 중단하며 추가 구조와 결과를 보존한다. 운영 배포·모델 실행은 이 검토 명령에 포함되지 않는다.

## Durable 호출 ledger 검증

`0006_add_provider_call_slot.py`는 현재 0002 head 위에서 호출 슬롯과 attempt_count comment 의미만 보강한다. 과거 persistence patch의 purpose_code·task→promotion FK와 관계없다. 기존 개발 fixture와 ontology reference data를 등록하거나 변경하지 않는다. 소비된 slot이 있으면 downgrade를 거부하며, 기존 attempt를 가짜 slot로 backfill하지 않는다.

`ONTOLOGY_MAP_KE_TEST_DATABASE_URL`은 migration이 적용된 loopback의 별도 `_ke127_test` DB만 허용한다. `server/tests/test_provider_call_slots_postgres.py`는 합성 reference data로 실제 process 종료, reservation/result rollback, UNKNOWN gap, 동시 예약, lease reclaim과 stale worker 거부를 검증한다. test provider는 외부 모델을 호출하지 않는다. `.github/workflows/knowledge-extraction.yml`은 고정 환경과 run별 PostgreSQL을 사용하고 종료 시 해당 run의 volume만 제거한다.

현재 `db/model_tasks.py`와 `durable_provider.py`는 호출 ledger·단일 전송 경계다. 이 경계의 통과를 전체 extraction·ontology·Claim promotion 또는 publication 구현 완료로 해석하지 않는다. 호출자는 실제 active output contract와 prepared request를 검사하고 product 성공을 같은 promotion transaction에 연결해야 한다.
