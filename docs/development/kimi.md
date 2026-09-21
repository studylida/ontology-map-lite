# Kimi 국제판 LLM 실행

> 과거 Kimi 실행 계약의 기록이다. 현재 실행은 [OpenAI 역할별 실행](openai.md)을 따른다. 이전 task·응답·원장에는 당시 provider/model을 그대로 보존하며 이 문서를 이유로 재실행하거나 재표시하지 않는다.

## 적용 범위와 상태

[Issue #233](https://github.com/studylida/ontology-map/issues/233)의 사용자 승인에 따라 모든 LLM 호출을 Kimi 국제판으로 전환한다. 임시 데모 모드나 provider fallback이 아니다. 이 문서는 해당 구현 브랜치의 실행 계약을 설명하며, 실제 계정 호출과 D1의 DB → READY → 화면 성공은 별도로 검증해야 한다.

| 역할 | 실행 경계 |
| --- | --- |
| 본문 선택·지식 생성 평가·Claim/Meaning 판정 | `KimiModels.call` |
| Entity Resolution·Claim 중복 판정 | 기존 공식 proposer → `KimiModels.call` |
| durable KNOWLEDGE_EXTRACTION | `KimiGenerationAdapter.prepare` |
| NODE_CONTEXT·FOLLOWUP_QUESTIONS·NODE_INSIGHT | 기존 제품 adapter → `KimiStructuredTransport` |

기존 `ModelStudio*`, `FLASH`, `PLUS` import는 외부 호출부 호환을 위해 남지만 모두 같은 Kimi 구현으로 연결된다. Alibaba endpoint와 다른 모델은 preflight에서 거절한다. 옛 #139 유료 시험 CLI는 폐기했으며, 과거 Qwen 시험의 예산·승인·credentials를 새 Kimi 실행으로 이어받지 않는다.

## 요청과 검증

공식 국제판 endpoint는 `https://api.moonshot.ai/v1`이고 기본 모델은 `kimi-k2.6`이다. `thinking={"type":"disabled"}`, `stream=false`, `response_format={"type":"json_object"}`를 사용한다. Kimi의 고정 sampling 제약 때문에 temperature와 top_p 등을 보내지 않는다. 설정은 `llm_config.py` 한 곳이 소유한다.

제품 JSON Schema는 변경 없이 system message 뒤에 결정적으로 첨부한다. `pattern`, `format`, `$ref`는 provider grammar로 제출하지 않으며, 응답의 필수 필드·타입·Mention·시간 및 각 제품 의미 검증은 기존 코드로 수행한다. JSON mode는 유효한 제품 출력을 보장하지 않는다. 원문·source ID·인용 범위·hash, Claim/Meaning 판정, Entity Resolution, canonical promotion 및 publication READY 조건은 완화하지 않는다. 자동 보정이나 출력 재요청 루프는 없다.

`EVENT_TIME`의 값이 시간대가 있는 RFC3339 문자열이면 `TemporalPoint`는 이를 datetime으로 해석하고, `DAY`·`MONTH`·`YEAR`의 정확한 ISO 날짜 문자열도 같은 정밀도 기간으로 해석한다. 기간 시작과 종료 표현은 내부 시작점으로 정규화하며, `INSTANT`의 날짜 전용 값, 잘못된 날짜와 기간을 식별할 수 없는 월·연도 중간 날짜는 계속 거절한다. 이 변환은 저장된 원문이나 Claim 의미를 바꾸지 않는다.

모든 helper와 durable adapter는 공통 단발 HTTP transport를 사용한다. 요청 byte 상한을 전송 전에 검사하고, redirect·자동 retry·환경 proxy를 사용하지 않는다. 실제 모델과 usage가 확인되지 않은 호출은 보수적인 예약액을 유지하고 pilot을 중단한다. usage가 확인된 잘린 출력은 사용량을 계산하되 제품 출력 실패로 남긴다. 원시 응답·reasoning·키·원문은 ledger에 쓰지 않는다. 수신한 전체 응답은 아래 정책에 따라 별도 비공개 로컬 파일로 보관한다.

## 한도와 실행 식별

K2.6의 공식 context는 256K다. 애플리케이션은 보수적으로 입력 223,232 + 출력 32,768 = 256,000 token을 허용 상한으로 사용한다. 현재 publication 요청의 출력 한도는 NODE_CONTEXT 2,048, 두 기간을 묶은 FOLLOWUP_QUESTIONS 4,096, NODE_INSIGHT 8,192 token이며 각 task identity에 포함된다. 기존 1,000,000 입력 상한을 지정한 로컬 caller는 새 상한 이하로 명시적으로 변경하고 runtime identity를 다시 구성해야 한다. model window와 실제 tokenizer의 경계 판단은 provider가 담당하며 로컬에서 정확한 token 수를 추측하지 않는다.

비스트리밍 지식 생성 요청은 HTTP 응답을 기다리는 read timeout만 180초로 둔다. 연결·쓰기·pool timeout과 다른 Kimi 역할의 read timeout은 60초다. read timeout은 전체 실행 시간 상한이 아니라 응답 데이터 사이의 대기 시간이며, durable task의 10분 lease와 기존 slot·retry 계약은 그대로 적용한다.

유료 전송 상한은 caller의 명시적 `PilotBudget`이 소유한다. helper의 `Budget`만으로는 실제 전송이 허용되지 않는다. 단가는 2026-09-18 공식 국제판의 uncached 입력 $0.95 / 출력 $4.00 (백만 token 기준)를 사용한다. cache 할인·충전액·프로모션은 가정하지 않는다. 불확실한 입력 사용량은 262,144 token으로 보수적으로 예약한다. 가격이나 모델 변경 시 설정·한도·테스트를 함께 갱신한다.

`llm_config.request_identity_settings()`의 provider·endpoint·model·wire option·출력 지시문 hash·profile 버전은 실제 요청 구성과 공유된다. KE effective input에는 이 설정을 직접 포함하고, NODE_CONTEXT/FOLLOWUP/INSIGHT는 기존 `*_execution.py`의 structured request identity를 통해 포함한다. 제품 모델 식별자도 실제 Kimi 모델로 바꾼다. 제품 output schema는 그대로이므로 이 변경만을 위한 schema migration이나 활성 schema 덮어쓰기는 필요하지 않다.

기존 FINAL_FAILED task의 상태·attempt·slot·lease를 초기화하지 않는다. 수동 재처리는 새 `execution_generation`을 명시하고, 기존 `runtime.identity_settings()`와 실제 입력을 다시 일치시킨다. 과거 Qwen 기록과 결과는 수정하지 않으며, Kimi 품질 검증의 증거로 재사용하지 않는다.

## 기존 애플리케이션 경로 연결

실제 키는 채팅·Issue·PR·소스에 넣지 않는다. 별도 개인 환경에 `MOONSHOT_API_KEY`를 설정한다. `server/.env.kimi.example`은 변수 이름만 제공한다. 이 파일을 import할 때 자동으로 읽거나 다른 provider 키를 대체 사용하지 않는다.

아래는 기존 caller에서 이미 검증된 `engine`, `document_id`, `runtime`, `execution`, 각 예산·판정 한도를 전달하는 연결 예시다. 새 CLI나 새 실행기를 만들지 않는다. `execution_generation`은 예를 들어 `d1-kimi-v1-01`처럼 재처리를 구분하는 값으로 지정한다.

```python
from ontology_map.application_execution import run_document
from ontology_map.kimi_clients import kimi_clients

# helper_budget, pilot: 이 실행에 명시적으로 허용한 상한.
# runtime / execution: 기존 source, ontology, validator와 실제 한도를 반영.
with kimi_clients(helper_budget) as clients:
    result = run_document(
        engine=engine,
        document_id=document_id,
        worker_name=worker_name,
        execution=execution,
        runtime=runtime,
        helpers=clients.helpers,
        prepare_generation=clients.generation.prepare,
        propose_resolution=clients.resolution_proposer(resolution_limits),
        propose_claim_duplicate=clients.claim_duplicate_proposer(duplicate_limits),
        prepare_node_context_provider=clients.node_context.prepare,
        prepare_followup_provider=clients.followup.prepare,
        prepare_insight_provider=clients.insight.prepare,
        pilot=pilot,
    )
```

`run_document`는 공식 enqueue → extraction → finalizer → post-commit publication 순서를 유지한다. COMMITTED batch의 publication만 미완료이면 기존 `resume_publication`을 사용한다. 초기화나 새 extraction으로 후속 실패를 숨기지 않는다. terminal 후속 task는 단순 재호출만으로 초기화되지 않는다.

## 완료 확인

HTTP 200 또는 JSON 파싱 성공만으로 완료라고 하지 않는다. 실제 D1에서는 유효한 후보가 하나 이상 남고, Claim·Evidence가 D1의 정확한 source·quote·offset·hash에 연결되어 저장되며, 공식 promotion COMMITTED와 publication READY를 거친 뒤 실제 API 및 화면에서 같은 근거가 표시되는지 확인한다. mocked HTTP 회귀, 실제 유료 provider 실행, 제품 DB와 화면 성공, formal 모델 품질 관문은 서로 다른 증거다.

기존 제품 검증이 빈 질문이나 빈 Insight를 정상 결과로 인정하는 경우 그 의미는 유지한다. 모델 교체를 이유로 가짜 질문·보고서를 채우거나 READY 조건을 생략하지 않는다. 읽기 API와 화면 조작은 LLM 호출을 시작하지 않는다.

## 비공개 응답 보관과 출력 오류 진단

[Issue #237](https://github.com/studylida/ontology-map/issues/237)의 사용자 요청에 따라 공통 `KimiStructuredTransport`는 HTTP 응답이 반환되면 상태 검사와 파싱 전에 전체 `response.content` bytes를 비공개 로컬 파일에 보관한다. 성공 응답과 4xx/5xx 응답도 포함한다. JSON 재직렬화, 문자열 디코딩, 잘라내기, 요약, task ID별 분기는 없다. HTTPX가 content-encoding을 해제한 뒤 파서에 넘기는 bytes이며 TLS/압축 전 네트워크 패킷의 보관은 아니다. 요청 본문·키·인증 헤더는 보관기로 전달하지 않는다.

기본 위치는 `PilotBudget.path.parent / "kimi-responses"`이고, 파일럿이 없는 독립 호출은 `~/.local/state/ontology-map/kimi-responses`를 사용한다. `ONTOLOGY_MAP_KIMI_RESPONSE_DIR`로 절대 경로를 지정할 수 있다. 기존 디렉터리가 있으면 현재 사용자 소유의 0700이어야 한다. Git 작업 트리 내부, symlink 경로, 상대 경로는 보관을 거절한다. 디렉터리는 0700, 파일은 0600이며 원장과 같은 exclusive/no-follow 패턴으로 생성한다. 보관 파일은 Git·콘솔·공유 artifact·제품 DB·자동 백업 공유 대상에 넣지 않는다. 보관 기간과 디스크 사용량은 운영자가 관리하고, 코드는 원본을 자동 축약하거나 삭제하지 않는다.

각 호출은 고유 `response_id` 디렉터리를 만든다. `response.body`는 전체 원본, `metadata.json`은 role·schema_name·요청/응답 hash·byte 수·수신 시각·model_task_id·provider_slot_no·pilot_sequence·원장 파일명을 담는다. 같은 요청 hash의 재시도도 별도 디렉터리에 보관한다. helper에는 durable slot을 발명하지 않고 null로 남긴다. 실제 task ID는 extraction runner와 application finalizer 경계가, 파생 생성과 generation의 slot은 기존 durable send 경계가 제공한다. 독립 adapter 호출처럼 task가 없는 호출도 null로 남긴다. 기존 로컬 caller가 공식 run_document/run_extraction을 쓰면 보관용 task ID를 따로 주입할 필요가 없다.

각 adapter는 `PreparedJsonCall.parse(existing_parser)`로 기존 파서를 그대로 실행한다. 실패하면 같은 디렉터리의 `failure.json`과 `failure_diagnostic(error)`에 response_id를 연결한다. envelope JSON은 `RESPONSE_JSON`, model은 `RESPONSE_MODEL`, usage는 `USAGE`, choices/finish_reason/message/content 형태는 `OUTPUT`의 구체적 reason, 모델이 생성한 content의 JSON 문법은 `OUTPUT_JSON`, KnowledgeProposals 등 제품 검증은 `OUTPUT_SCHEMA`로 구분한다. Pydantic 필드 경로는 제품 스키마에 있는 이름과 배열 index만 노출한다. 알 수 없는 키 이름은 마스킹하고 값·msg·ctx·입력은 내보내지 않는다. 진단 경로는 최대 64개와 전체 오류 수를 남기며 원본 응답 자체는 제한 없이 보관한다. metadata만 존재한다고 제품 검증 성공이나 READY를 의미하지 않는다.

보관은 best effort다. 파일 쓰기·권한·디스크 실패는 `KIMI_RESPONSE_ARCHIVE_FAILED`라는 안전한 경고와 `response_archive=FAILED`로 식별하며, 기존 오류·usage 처리·파일럿 중단·재시도·slot·lease·제품 성공 판정을 바꾸지 않는다. 보관기가 받지 못한 완전한 응답에는 `NO_RESPONSE`를 남기고 가짜 빈 원본 파일을 만들지 않는다. 이는 provider가 요청을 수신하지 않았거나 과금하지 않았다는 뜻이 아니다. 성공·실패 원본을 다시 파싱하는 로컬 분석은 새 모델 호출과 별개이며 원래 task 이력이나 정식 데이터는 수정하지 않는다.

실행 전에는 대상 경로가 Git 밖의 0700 디렉터리인지, 디스크 여유가 있는지, 합성 MockTransport 호출로 response.body와 metadata/failure의 ID가 연결되는지만 확인한다. 실제 task 11을 보관 기능 시험용으로 재실행하거나 slot을 초기화하지 않는다. 과거 원본이 없으면 현재의 OUTPUT_CONTRACT_ERROR 원인은 UNKNOWN으로 유지한다.

## 공식 문서

- [모델별 파라미터](https://platform.kimi.ai/docs/api/models-overview)
- [Kimi K2.6 안내](https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart)
- [JSON mode](https://platform.kimi.ai/docs/guide/use-json-mode-feature-of-kimi-api)
- [Chat Completions](https://platform.kimi.ai/docs/api/chat)
- [국제판 모델 가격](https://platform.kimi.ai/)
