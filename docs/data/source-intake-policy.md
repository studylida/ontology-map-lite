# 출처·lint 적재 정책

> 상태: 승인된 데이터 정책 · 제품 lint 정의·저장 그래프 검사 구현, 데모 업로드 입력 API 연결
>
> 확정일: 2026-09-15 · 서류 요건 제거: 2026-09-18 사용자 지시
>
> 근거: [Issue #110](https://github.com/studylida/ontology-map/issues/110), 선행 [Issue #64](https://github.com/studylida/ontology-map/issues/64), 연계 [Issue #124](https://github.com/studylida/ontology-map/issues/124)·[#126](https://github.com/studylida/ontology-map/issues/126)

## 목적과 경계

이 프로젝트에서 lint는 출처 자격을 통과한 계약 유효 후보와 저장된 기준 지식그래프가 Evidence Trace·온톨로지·무결성 계약을 지키는지 일반 코드가 결정적으로 검사하는 절차다. lint는 데이터 원천·corpus·모델 평가나 사실의 진실성 판정이 아니다.

제품 DB는 정규화한 불변 문서부터 관리한다. 발견·다운로드·운영 수집 플랫폼은 제품 책임이 아니며, 데모 업로드는 브라우저가 전달한 파일을 백엔드가 정규화해 이 경계로 넘긴다. 정확한 Relation·attribute·Topic 목록은 #126이 정하고, 이 정책은 목록을 만들거나 모델 작업에 배정하지 않는다.

출처 자격, 독립 원문 계보와 의미 대상은 서로 다른 판단이다.

- 출처 자격은 문서를 제품 입력으로 받을 수 있는지 정한다.
- [Evidence Group](evidence-lineage-policy.md)은 같은 원문의 복제·재게시·전재·번역인지 정한다. 같은 사건을 다룬 독립 기사 묶음이 아니다.
- Event·Relation·Claim은 원문의 의미를 승격한 지식이다. 주제·사건·게시 시점이 비슷하다는 이유만으로 문서를 같은 Evidence Group이나 Event로 합치지 않는다.

출처 자격에는 발행처의 서면 허가서, 기사별·corpus별 권리 승인 서류 또는 이용약관 대조를 포함하지 않는다. 어떤 입력 프로필에서도 이런 서류를 적재 선행조건으로 요구하지 않으며, `qualification_ref`는 아래의 원문·메타데이터·개인정보 검사 결과를 가리킨다.

## 출처 자격

### 공통 기준

다음 조건을 모두 만족한 문서만 계보 판정으로 넘긴다.

- 로그인·접근 통제 우회 없이 공개 URL에서 확인한 공식 문서 또는 발행 주체와 편집 책임을 식별할 수 있는 보도·편집 자료다.
- manifest 적재는 canonical URL, 발행처, 제목, 언어, 비어 있지 않은 정규화 본문과 SHA-256 본문 해시를 제공한다. 사용자 파일 업로드는 canonical URL이 없을 수 있으며 나머지 불변 필드를 백엔드가 채운다. 작성자와 게시일은 실제로 알 수 없으면 생략할 수 있다.
- 출처가 말한 내용과 원문 위치를 그대로 보존할 수 있다. 요약·번역·모델 생성문을 원문으로 가장하지 않는다.
- 주민·계정 고유 식별자, 계정 정보, 직접 연락처, 정밀 위치, 민감정보 또는 미성년자 정보처럼 노출 위험이 큰 개인정보가 포함되지 않는다. 하나라도 포함되면 문서 전체를 저장 전 거절한다.

다음 자료는 받지 않는다.

- 로그인, paywall, robots 정책 또는 다른 접근 제한을 우회해야 하는 자료
- 유출 자료와 출처를 확인할 수 없는 익명·개인 게시물
- 본문을 확보하지 못했거나 필수 메타데이터·해시가 계약을 만족하지 않는 자료

출처 자격을 통과했어도 독립 원문 계보의 결정적 신호가 충돌하거나 유사 후보만 있어 판정을 끝내지 못한 자료는 자격 거절과 구분해 검토 보류한다. 기존 manifest 적재 경로에서는 어느 경우에도 `source_document`를 만들지 않는다. 제품의 사용자 파일 업로드 경로는 현재 데모 자동 통과 경계로, 본문 추출·정규화·해시와 DB 무결성만 확인한 뒤 새 Evidence Group으로 저장한다. 브라우저 업로드의 공개 적합성 source kind는 `OTHER`이며, 영어 자료는 처리 job을 `EXCLUDED_LANGUAGE`로 끝내고 번역하지 않는다.

출처 자격은 신뢰도 점수나 순위를 만들지 않는다. 독립 근거 수, 충돌, 자료 부족, 직접·배경 관련성과 출처가 말한 사실의 객관적 진실성은 별도 축이며 자격 통과만으로 확정하지 않는다.

### 시연 프로필과 향후 입력

[#131](../../review/131-gdelt-collection/README.md)은 `2026-06-11T00:00:00Z` 이상 `2026-09-09T00:00:00Z` 미만의 공개 한국어 자료 가운데 시연 주제와 직접 관련된 본문 1,000건을 고른 일회성 프로필이다. 공개 URL의 원문을 접근 제한된 환경에 보관해도 된다는 판단은 #131과 그중 작은 입력을 넘기는 #111 시연에만 적용한다. 원문은 공개 배포하지 않고, 제품 화면에는 Evidence Trace에 필요한 인용문과 원문 링크만 제공한다. 자료 준비 담당자는 접근 제한 우회 여부, 본문·해시와 고위험 개인정보, 접수된 삭제·이용 중단 요청, 독립 원문 계보를 확인한다. 이는 운영 수집이나 원문 재배포의 일반 규칙이 아니다.

#131에서 배경 관련 자료를 제외한 것은 고정 시연 corpus의 선택 규칙이다. 일반 입력에서 배경 근거나 게시일 불명 자료를 거절하는 규칙이 아니며, 저장되면 그 의미와 날짜 정밀도를 유지한다. 공개 manifest는 URL·메타데이터·본문 해시와 수집 상태를 보여줄 뿐 개인정보 기준이나 추출·모델 산출물의 품질을 증명하지 않으며, 이 작업에서는 원문을 다시 감사하지 않는다.

향후 외부 제공 자료 경로에도 같은 원문 무결성, 접근 제한, 개인정보, 삭제 요청 및 독립 원문 계보 검사를 적용한다. 이를 위해 제품에 수집기나 모집 플랫폼을 추가하지 않는다.

## 저장과 재처리

고정 주기 수집이나 자동 재검사는 두지 않는다. 새 자료가 전달되거나 기존 자료를 명시적으로 다시 확인할 때 다음 흐름을 실행한다.

```text
출처 자격
→ #64 독립 원문 계보 판정
→ source_document와 source_processing_job 저장
→ Agent 후보의 출력 계약 검증
→ 승격 전 lint
→ 통과한 후보의 원자적 승격
→ 현재 publication 계약으로 파생 결과 준비
→ READY
```

업로드 commit 뒤 활성화된 데모 worker가 위 처리를 자동 실행한다. 같은 문서의 `QUEUED`·`RUNNING`·`READY` job은 재사용하고, 실패·중단 뒤에는 새 attempt job을 만들 수 있다. 프로세스가 재시작되면 완료되지 않은 job은 `INTERRUPTED`로 명시하며 자동으로 성공 처리하지 않는다.

- 출처 자격을 통과하지 못하거나 계보 판정이 보류되면 `source_document`를 만들지 않는다.
- 같은 `source_key`와 불변 내용이면 새 버전을 만들지 않고 `last_checked_at`, `last_check_status`만 갱신한다.
- 같은 `source_key`의 본문 또는 Evidence Trace에 영향을 주는 불변 메타데이터가 바뀌면 기존 행을 고치지 않고 다음 버전을 만든다.
- 이미 저장한 URL을 다시 확인할 수 없으면 `last_check_status = FAILED`로 갱신하되 불변 문서와 연결된 지식을 지우거나 자동으로 비공개 처리하지 않는다.
- 같은 `source_document`의 같은 원문 범위만 기존 Observation을 재사용한다. 서로 다른 문서는 본문 해시가 같아도 각 문서에 속한 Observation을 만들고 같은 Evidence Group으로 묶는다. Relation은 정확한 `relation_identity_key`가 같을 때만 재사용한다. Claim의 동일 의미 판정과 재사용은 #125·#127의 범위이며 이 정책에서 정하지 않는다.
- 계약 유효 후보 일부가 `BLOCKING`이면 그 후보와 의존 후보만 제외하고 나머지를 한 번의 짧은 트랜잭션으로 승격한다. 트랜잭션 실패 시 현재 문서에서 선택한 승격 전체를 롤백하며 이전 문서의 지식은 유지한다.
- 관련 후보가 모두 `BLOCKING`이면 작업은 `VALIDATION_BLOCKED`, 계약은 유효하지만 관련 후보가 없으면 `SUCCESS`와 0개 결과다. 일부를 정상 승격하면 작업은 `SUCCESS`다.

문서와 Evidence Trace는 자동 만료 없이 보존한다. 현재 POC에는 삭제·takedown·controlled purge가 없으며, 구체적인 삭제 권한·영향 분석·공개 결과 무효화·복구 절차는 향후 외부 입력 경로 승인과 함께 별도로 정한다.

## lint 정책

Structured Output 계약을 만족한 후보만 승격 전 lint에 들어간다. 현재 정책의 분류는 다음과 같다.

#203의 제품 Reference Topic 자체는 외부 사실 주장이 아니라 승인 controlled vocabulary이므로 `PRODUCT_REFERENCE` lifecycle로 식별하고 아래 Evidence Trace lint 대상에서 제외한다. 이는 `node_type=TOPIC` 전체를 건너뛰는 예외가 아니다. 기존 evidence-backed TOPIC, `HAS_TOPIC` Relation과 지지 Claim은 기존 Evidence Trace·ontology·lint 계약을 그대로 따른다. Reference Topic을 위해 가짜 `source_document`, Observation, Claim, 상태, promotion 또는 publication READY를 만들지 않는다.

| 적용 범위 | 분류 | 검사 | 처리 |
| --- | --- | --- | --- |
| `BOTH` | `BLOCKING` | Node(Event 포함)·Relation·Claim이 필요한 Observation·Claim 연결을 거쳐 `source_document`까지 도달하지 못함 | 후보와 의존 후보를 승격하지 않거나 저장 지식을 공개에서 제외 |
| `BOTH` | `BLOCKING` | Observation 범위, 인용문 또는 해시가 불변 본문과 다름 | 후보와 의존 후보를 승격하지 않거나 저장 지식을 공개에서 제외 |
| `BOTH` | `BLOCKING` | Claim에 Relation·attribute 값·사건 시간 중 의미 대상이 하나도 없음 | 후보를 승격하지 않거나 저장 Claim을 공개에서 제외 |
| `BOTH` | `BLOCKING` | Relation에 Observation이 있는 지지 Claim이 없음 | Relation을 승격하지 않거나 저장 Relation을 공개에서 제외 |
| `PRE_PROMOTION` | `BLOCKING` | 새 지식이 비활성 node 유형·revision을 쓰거나 현재 Relation endpoint·attribute 값 종류·단위 규칙을 위반 | 후보와 의존 후보를 승격하지 않음 |
| `PERSISTED_GRAPH` | `BLOCKING` | 저장 지식이 생성 당시 기록한 revision의 endpoint·값 종류·단위 규칙과 맞지 않음 | 저장 지식을 공개에서 제외 |
| `BOTH` | `WARNING` | 분리하면 의미가 훼손되는 한 Claim이 여러 의미 대상을 함께 가리킴 | 경고를 남기고 승격·공개 허용 |

유형이나 revision을 나중에 비활성화해도 생성 당시 규칙에 맞는 기존 지식의 의미·상태·공개 여부는 바꾸지 않는다. 활성 여부는 새 지식 승격에만 적용한다.

다음 항목은 lint가 아니다.

- 모델·provider·transport 실패와 Structured Output 계약 위반
- 선택적인 작성자·게시일이 실제로 없음
- 독립 근거 수가 적음, 서로 충돌함 또는 배경 관련성만 있음
- 출처 등급, 신뢰도 점수와 모델 품질 평가
- 출처 자격 거절과 계보 검토 보류

규칙의 코드와 평가 범위는 `lint_rule`, 함께 적용하는 규칙과 심각도는 불변 `lint_policy_version`·`lint_policy_rule`이 소유한다. 선택 규칙·심각도 또는 판정 결과가 달라질 때만 새 정책 버전을 활성화한다. 승격 전 `BLOCKING`은 후보 payload 없이 `blocked_fingerprint`로 반복 범위만 기록한다.

저장된 그래프 재검사는 평가 범위가 `PERSISTED_GRAPH | BOTH`인 결정적 규칙만 `FULL_GRAPH` `lint_run`으로 수행하고 `lint_finding`을 남긴다. 성공한 전체 실행만 사라진 finding을 해결할 수 있고, 실패하거나 끝나지 않은 실행은 기존 finding을 해결하지 않는다. 열린 `BLOCKING` finding은 사람의 지식 상태를 바꾸지 않고 일반 공개 조회에서 해당 지식과 이를 basis로 삼은 파생 결과를 즉시 숨긴다.

제품 코드의 `source-intake-110-v1` validator는 위 표의 여섯 `BLOCKING` 검사를 `EVIDENCE_TRACE_COMPLETE`, `OBSERVATION_SOURCE_INTEGRITY`, `CLAIM_SEMANTIC_TARGET`, `RELATION_SUPPORTED`, `ACTIVE_ONTOLOGY`, `STORED_REVISION_VALID`로 고정한다. 승격 전 검사는 기존 추출 후보 검증, 원문 Observation 검증, 승격 트랜잭션의 활성 온톨로지·관계 지지 검사를 재사용한다. 저장 그래프 검사는 `ontology_map.db.product_lint.run_full_graph`가 호출된 때에만 실행되며, 사람의 지식 상태를 변경하지 않는다. 여러 의미 대상을 분리할 수 없는지 판단하는 `WARNING`은 결정적 규칙으로 정의되지 않았으므로 이 validator 버전에 포함하지 않는다.

## publication과 READY

승격과 publication은 별도 단계다. 새 승격 결과는 현재 [논리 스키마](logical-schema.md)와 [제품 읽기 계약](../product/design.md)이 요구하는 affected node의 publication 완결성과 전체 공개 basis·Evidence Trace 검증이 완료된 뒤에만 `READY`가 된다. 질문이나 보고서가 0개인 정상 결과는 `SUCCESS`와 빈 결과로 보존하며 미준비·실패 상태로 바꾸지 않는다.

새 publication이 실패하면 이미 승격한 기준 지식은 유지하고 이전의 유효한 `READY` 결과를 계속 제공한다. 공개 basis 하나가 비공개 상태이거나 열린 `BLOCKING` finding을 가지면 그 basis를 직접 반환하지 않는 파생 결과도 노출하지 않는다. 실패 원인에 따라 자동으로 재생성·복구하는 실행 시스템은 현재 없으며 [#180](https://github.com/studylida/ontology-map/issues/180)의 별도 범위다.

## 대표 검증 사례

| 사례 | 근거 | 기대 결과 |
| --- | --- | --- |
| 직접·배경 관련성 | #131 manifest의 `SELECTED/DIRECT` 1,000건과 `EXCLUDED_RELEVANCE/BACKGROUND` 1,046건 | #131 선택은 재현하되 배경 제외를 일반 출처·lint 규칙으로 확장하지 않음 |
| 같은 URL·본문 | #131 manifest의 `DUPLICATE_URL` 142건과 `DUPLICATE_BODY` 39건 | 새 독립 근거로 중복 계산하지 않고 #64 계보·버전 규칙 적용 |
| 같은 사건의 독립 기사 | 서로 다른 발행처·본문이며 전재 관계가 없다고 확인한 합성 전제 | 서로 다른 Evidence Group을 유지하고 Event 동일성은 지식 후보 단계에서 별도 판단 |
| 계보가 모호함 | 결정적 신호가 충돌하거나 유사 후보만 있는 합성 입력 | 검토가 끝날 때까지 `source_document`를 저장하지 않음 |
| 게시일 불명 | 필수 본문·출처 정보는 있고 게시일만 없는 합성 입력 | `UNKNOWN` 정밀도로 저장하고 날짜 불명 의미를 유지 |
| 충돌하는 주장 | 같은 대상을 서로 다르게 말하며 각자 원문 근거가 있는 합성 입력 | 둘 다 추적 가능하게 저장하고 충돌로 표현하며 lint로 거절하지 않음 |
| 부분 차단 | 독립 후보 중 하나만 Observation 해시가 틀린 합성 입력 | 해당 후보와 의존 후보를 빼고 나머지를 원자 승격해 `SUCCESS` |
| 전체 차단 | 모든 관련 후보가 `BLOCKING`인 합성 입력 | 지식 승격 없이 `VALIDATION_BLOCKED` |
| 관련 후보 없음 | 계약 유효 응답에 승격할 후보가 없는 합성 입력 | `SUCCESS`와 0개 결과 |
| URL 소실 | 이미 저장한 문서의 재확인만 실패한 합성 입력 | `last_check_status = FAILED`, 기존 불변 근거와 공개 지식 유지 |
| 고위험 개인정보 | 직접 연락처 등 금지 항목이 하나라도 있는 합성 입력 | lint 전에 문서 전체를 거절하고 원문을 제품 DB에 저장하지 않음 |

#131 manifest 사례는 시연 corpus의 입력·중복 상태만 검증한다. 위 합성 사례는 정책 분기 검증용이며 실제 Agent·DB·publication 실행이나 추출 품질의 증거가 아니다. 개발 fixture와 실제 제공 자료는 같은 READY·공개 basis 계약을 따르되 저장 위치와 생성 주체를 분리한다. fixture는 실제 API 읽기와 UX 계약만 검증하고 실제 원문·모델 품질의 증거로 사용하지 않는다.

## 제외 범위

- 수집기, scheduler, worker, lint 실행기, 모델 호출·prompt·의존성 구현
- 실제 자료의 Agent 입력·DB 적재·publication 실행과 품질 평가
- schema, migration, API, fixture와 온톨로지 목록 변경
- 삭제·takedown·controlled purge와 #180 자동 복구 구현
- #121·#117 검색 변경, #68·#129 생성 작업과 #139 모델 시험
