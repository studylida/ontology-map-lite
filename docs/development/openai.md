# OpenAI 역할별 실행

## 상태와 범위

Issue #240에서 시작해 현재 main에 반영된 역할별 실행 계약이다. 코드/MockTransport 검증, 실제 OpenAI 요청 수락, 모델의 의미 정확성, 실제 DB·publication·화면 성공은 서로 다른 검증이다. Luna 중심·Terra 핵심 단계 배치는 실제 데모에 사용됐지만 자료별 품질을 자동 보증하지 않는다. 이 문서는 추가 유료 실행 승인이 아니다.

`llm_config.py`의 한곳에서 모델·effort·출력 옵션을 선택한다. 기존 Kimi/ModelStudio 이름 일부는 import 호환용이며 외부 호출은 OpenAI 하나뿐이다. 자동 router, Luna→Terra 승격, 다른 provider fallback, SDK 자동 재시도, UI 실시간 모델 호출을 추가하지 않는다.

| 역할 | API 모델 | reasoning_effort |
| --- | --- | --- |
| NODE_CONTEXT | gpt-5.6-luna | low |
| FOLLOWUP_QUESTIONS | gpt-5.6-luna | low |
| KNOWLEDGE_EXTRACTION generation | gpt-5.6-terra | medium |
| 공개 적합성·binding | TypeSafe Jev 1.13.0 | 역할별 고정 임계값 |
| Claim 지지 | gpt-5.6-terra | medium |
| 기존 후보 동일성·신규·특정 entity_resolution | gpt-5.6-terra | medium |
| NODE_INSIGHT | gpt-5.6-terra | medium |

## API와 출력 계약

단일 endpoint는 `POST https://api.openai.com/v1/chat/completions`다. 현재 단발 Chat Completions 실행기와 응답/usage 계약을 유지하는 최소 변경을 선택했다. Responses API, conversation loop와 이중 endpoint 지원은 구현하지 않는다. 직접 HTTPX 전송에 `retries=0`, `follow_redirects=False`, `trust_env=False`를 적용한다. `stream=false`, `store=false`, 역할별 `reasoning_effort`, `max_completion_tokens`, `response_format.type=json_schema`와 `strict=true`를 보낸다. Kimi `thinking`, `max_tokens`, 임의 sampling 옵션은 보내지 않는다.

`openai_schema.wire_schema()`는 원본 Pydantic schema를 복사해 별도 전송 schema를 만든다. 모든 object를 닫고 모든 property를 required로 만들되 nullable은 원래 허용한 필드에만 유지한다. `const`는 같은 값의 singleton enum으로 표현하고 `default` 주석만 제거한다. `pattern`, 지원되는 `format`, 숫자/배열 제약, `$defs/$ref/anyOf`를 유지한다. 다만 실제 OpenAI D1 요청에서 거절된 Pydantic Decimal의 lookahead 정규식만 동등한 정규식으로 바꾼다. 원본 schema와 Decimal 검증은 그대로다. 알 수 없는 의미 키워드는 예약 전에 거절한다. 이는 임의 JSON Schema를 모두 지원하는 변환기가 아니다. 실제 9개 DTO의 Mock 변환 성공을 실제 API 수락으로 취급하지 않는다.

원본 schema는 prompt에도 그대로 첨부하고 원본 DTO strict parser를 유지한다. Mention TOPIC/topic_name 교차 조건은 wire 설명으로 유도하지만 Python validator를 제거하지 않는다. 시간대가 있는 RFC3339 문자열과 정확한 calendar 날짜 문자열은 Python datetime으로 해석한다. DAY의 종료 시각과 MONTH·YEAR의 마지막 날은 같은 정밀도 기간의 시작점으로 정규화하고, 기간을 식별할 수 없는 중간 날짜는 거절한다. modality·stance·관계 방향·원문 ID/version/quote/offset/hash 검증도 유지한다. 문서 단위 `claim_review` 중 공개 적합성·binding은 JEV가 최대 64개 질문으로 묶어 판정한다. Claim 지지 holdout은 86.0%, 기존 후보 동일성 holdout은 18.0%로 94% 활성화 기준을 통과하지 못해 Terra가 계속 담당한다. 이때 Terra의 Claim 지지 요청에는 candidate ID·문장·modality·근거만 보내고 support verdict만 받아, JEV가 맡은 공개 적합성·binding을 중복 생성하지 않는다. DTO를 provider 출력에 맞춰 완화하지 않으며 활성 JEV 역할의 오류를 Terra fallback으로 숨기지 않는다. refusal, 비정상 finish_reason(잘림/content_filter 등), JSON/제품 schema 오류는 정상 empty가 아니다.
실제 D1 생성에서는 binding의 언급 참조에 원문 text가 들어가 모든 의미 연결이 제외됐다. 생성 지시문은 같은 Claim의 `mentions[].mention_id`를 정확히 쓰도록 명시하며, 출력의 잘못된 참조를 자동 보정하지 않는다.

Issue #242의 내부 D1–D3 데모에서는 공동 사실을 불필요하게 분해하지 않는 기존 규칙을 유지하면서, 서로 독립적으로 중요한 사실을 하나의 headline Claim으로 압축하지 않고 원자 Claim으로 제안하도록 generation 지시문을 보완한다. Issue #251부터 FOLLOWUP_QUESTIONS는 90일·1년을 한 Structured Output으로 생성하므로 prompt와 identity가 v3로 바뀐다. NODE_INSIGHT의 생성 계약은 유지한다.

응답 model은 실제 요청 model과 정확하게 일치해야 한다. 공식 문서로 확인하지 않은 dated snapshot 접두사를 임의 허용하지 않는다. 실제 API가 다른 snapshot을 반환하면 해당 계약을 별도 확인하기 전에는 fail-closed로 중단한다.

## identity와 과거 task

새 effective input에는 실제 provider, endpoint, 전체 또는 해당 역할 profile, effort, 출력 옵션, wire schema/profile 버전, 원본 출력 지시문 hash, 요청 한도와 기존 prompt/schema/실행 설정이 들어간다. helper 역할 설정도 KE identity에 포함한다. 캐시와 결과 identity 역시 이 설정을 포함하므로 예전 Kimi 결과를 새 모델 결과로 재사용하지 않는다.

Kimi task 14를 GPT로 native retry하지 않는다. 실제 전환은 별도 승인을 받은 새 effective input과 명시적인 새 `execution_generation`의 별도 task로 수행한다. 기존 RUNNING/RESERVED/UNKNOWN/attempt/lease, 미확정 비용 상한과 archive는 초기화·수정하지 않는다. 이 코드 변경만으로 DB migration/activation이나 task 생성이 필요하다는 뜻은 아니다.

## 한도와 예산

앱의 기존 입력 상한 223,232, 출력 상한 32,768, 입력 예약 상한 262,144 token을 자동 확대하지 않는다. 실제 publication 출력 한도는 NODE_CONTEXT 2,048, FOLLOWUP_QUESTIONS 4,096, NODE_INSIGHT 8,192다. FOLLOWUP은 두 기간 출력을 한 응답에 담기 때문에 기존 기간별 2,048의 합과 같은 4,096을 사용한다. `max_completion_tokens`는 reasoning을 포함한다. 응답의 `completion_tokens`에 reasoning을 다시 더하지 않는다.

2026-09-18 공식 일반 처리·짧은 context 기준 USD/백만 token은 Luna 입력 0.20/캐시 입력 0.02/출력 1.20, Terra 입력 2.00/캐시 입력 0.20/출력 12.00이다. cache write는 일반 입력의 1.25배로 안내돼 있다. `token_cost`는 할인 없이 모든 입력을 cache-write 가격으로 계산한 `charged_upper_usd`이며 실제 청구액 추정치로 표시하지 않는다. 선택적 cached/cache_write/reasoning 사용량은 상호 범위를 검사하고 비공개 envelope에 남긴다. 역사적 `kimi-k2.6` 비용 함수는 당시 0.95/4.00을 유지한다.

새 모델의 큰 context가 애플리케이션 입력 상한보다 큰 요청을 수락하지 않도록 원문·prompt·두 schema를 포함한 전체 직렬화 요청에도 219,136 byte 상한을 적용한다. `223232 - 4096`의 byte 기반 admission 정책으로 UTF-8 byte 수가 앱의 token 상한보다 작을 때만 전송을 허용하며, 정확한 tokenizer 계산이나 공급자의 과금 보증은 아니다. 각 caller의 byte 상한이 더 작으면 그 값을 적용한다. 장문 자료는 Jev 선택을 적용해도 원문·Evidence를 변경하지 않으며, 실제 usage가 한도를 넘거나 미확정이면 원장을 확정 성공으로 메우지 않고 중단한다.

generation read timeout 180초, 다른 역할과 connect/write/pool timeout 60초를 유지한다. 기존 10분 lease나 승인 call/cost cap은 늘리지 않는다.

## pacing·lease·transaction

실제 전송에는 `ProcessPacer`의 명시적인 양수 간격과 활성 `PilotBudget`이 모두 필요하다. 공식 모델 Tier 표를 사용자 계정 RPM/TPM으로 가정하지 않는다. 일반 실행은 동시 호출 1개를 유지하고, 승인된 데모 업로드 worker만 최대 4개와 시작 간격 0.13초를 명시한다. 모든 역할은 같은 프로세스 gate를 공유한다. gate는 실제 전송 시작 시각을 기준으로 간격을 지키며 예약 전 대기, 대기 후 lease 확인, 전송 직전 lease 재확인을 수행한다.

대기와 모델 I/O는 DB transaction 밖에 둔다. Entity Resolution은 기존 REPEATABLE READ의 읽기 snapshot을 준비하고 transaction을 닫은 뒤 문서 단위로 한 번 호출한다. 문장 중복을 위한 모델 호출은 없으며 exact statement와 exact semantic target만 결정적으로 재사용한다. canonical write 시점의 기존 재검증은 유지한다. durable generation은 기존 slot 소유자만 예약·기록하며 helper는 durable generation attempt가 되지 않는다. 한 프로세스 제한은 다른 프로세스·앱·동일 계정 전체의 제한기가 아니다.

Retry-After와 허용한 rate-limit/remaining/reset 헤더만 안전하게 보관한다. 알려진 quota/billing 429는 임시 rate-limit과 구별하며 자동 retry하지 않는다. 429·timeout에서 사용량이 미확정이면 기존 pilot 중단과 미확정 예약을 유지한다. 중단된 pilot을 자동으로 풀거나 예약을 0 token SUCCESS로 바꾸지 않는다.

## 키와 공통 연결

`ontology_map.openai_clients.openai_clients`는 명시적 `SecretStr` 또는 로컬 `OPENAI_API_KEY`만 읽는다. `MOONSHOT_API_KEY`, Qwen key, Codex/Orca/ChatGPT 구독 토큰은 사용하지 않는다. key 존재 자체는 실행 승인이 아니다. `server/.env.openai.example`은 항목 설명용이며 자동으로 읽거나 유료 실행하지 않는다.

호출자는 승인된 값으로 `ProcessPacer(min_interval_seconds)`를 만들고 `openai_clients(helper_budget, pacer=pacer)` 안에서 기존 `application_execution.run_document(..., pilot=pilot)` 경계를 사용한다. client 생성만으로 전송/DB 접근/원장 생성은 하지 않는다. 데모 업로드 자동 처리는 `ONTOLOGY_MAP_SOURCE_PROCESSING_ENABLED=true`, `ONTOLOGY_MAP_DEMO_UNBOUNDED_PROVIDER=true`, 소유자 전용 0700 원장 디렉터리와 두 provider key가 모두 있을 때만 켠다. 무제한 모드는 call/USD 상한을 추정하지 않지만 모든 호출과 시간을 문서별 비공개 원장에 기록한다.

## 응답 보관과 안전한 진단

보관 모듈의 기존 이름 `kimi_response_archive.py`는 최소 변경을 위해 유지한다. OpenAI 응답 기본 경로는 pilot 원장 옆 `openai-responses`, pilot 없는 Mock 호출은 `~/.local/state/ontology-map/openai-responses`다. JEV는 별도 `jev-responses` 또는 `ONTOLOGY_MAP_JEV_RESPONSE_DIR`를 사용한다. 두 경로 모두 Git 밖 절대 경로로 지정할 수 있다. 이전 Kimi 경로와 환경값은 새 호출에 재사용하지 않으며 기존 파일은 건드리지 않는다.

성공/HTTP 오류의 전체 `response.content` bytes를 HTTP 상태/JSON/제품 parser보다 먼저 비공개 파일에 저장한다. 이는 HTTPX 압축 해제 후 파서가 받는 전체 본문이며 압축 전 네트워크 packet은 아니다. 디렉터리 0700/파일 0600, no-follow/exclusive 생성, 원본 덮어쓰기 금지를 유지한다. request·response hash, 로컬 response_id, provider/model, 역할, task/slot/pilot sequence, 안전한 envelope 식별자/usage와 실패 단계/field path를 연결한다. key·인증 헤더·source·raw response는 콘솔/PR/공유 artifact/제품 DB에 저장하지 않는다.

보관 I/O 실패는 제품 판정을 바꾸지 않는다. 무응답 timeout은 NO_RESPONSE이며 공급자 미수신/미과금이라는 뜻이 아니다. 오류 원문이나 임의 header/model 문자열을 진단으로 내보내지 않는다.

## 검증과 실제 D1 승인 관문

MockTransport/가짜 시계는 요청·파싱·진단·보관·단발 전송·예산·pacing/lease 경계만 검사한다. 실제 형식 수락과 의미 품질은 별도다. 실제 D1 한 건은 유효 Claim/Evidence 1개 이상, 정확한 원문 식별/인용 연결, 공식 canonical 저장·promotion COMMITTED·publication READY, 실제 API와 화면의 동일 근거까지 모두 확인해야 한다. HTTP 200/attempt SUCCESS/정상 empty만으로 대신하지 않는다.

실행 전 운영자는 두 모델의 API 권한, 실제 계정/프로젝트 RPM·TPM과 다른 호출 영향, 승인 call/USD cap, 별도 private 원장/응답 경로, 새 execution_generation, 대상 문서와 원문 공개 범위를 확인한다. Orca의 로컬 작업 설정은 xhigh이며 제품 API reasoning_effort와 별개다. 승인된 작은 표본의 사람 근거 기반 Luna/Terra 비교는 후속 평가이며 이 문서로 추가 유료 호출을 허가하지 않는다.

## 공식 확인 출처

- [Luna 모델](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [Terra 모델](https://developers.openai.com/api/docs/models/gpt-5.6-terra), [가격](https://developers.openai.com/api/docs/pricing)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Reasoning](https://developers.openai.com/api/docs/guides/reasoning), [Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)
