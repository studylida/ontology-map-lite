# ontology-map Architecture Decision Records

ADR은 여러 경계에 영향을 주거나 되돌리기 어려운 결정을 구현 PR에서 수용한 뒤 기록한다. 대안을 논의하는 동안에는 GitHub Issue를 사용하고 `proposed` ADR은 만들지 않는다. 구현 PR의 병합을 결정 수용으로 본다.

## 현재 결정

| ADR | 상태 | 최초 결정일 | 내용 |
| --- | --- | --- | --- |
| [ADR-0001](current/0001-use-fastapi-http-boundary.md) | accepted | 2026-09-01 | 브라우저·FastAPI·PostgreSQL HTTP 경계 |
| [ADR-0002](current/0002-separate-canonical-and-runtime-graphs.md) | accepted | 2026-09-01 | 기준 지식그래프와 런타임 동적 부분 graph 분리 |
| [ADR-0003](current/0003-preserve-evidence-trace.md) | accepted | 2026-09-01 | source·Evidence Group·Observation·Claim 추적성 |
| [ADR-0004](current/0004-use-active-ontology-revisions.md) | accepted | 2026-09-01 | 전역 manifest 없이 ontology revision과 활성 상태 사용 |
| [ADR-0005](current/0005-persist-model-task-contracts-not-provider-payloads.md) | accepted | 2026-08-31 | 모델 작업·Structured Output 계약 저장과 provider 응답 미저장 |
| [ADR-0006](current/0006-separate-promotion-and-publication.md) | accepted | 2026-08-31 | promotion과 publication 수명주기 분리 |
| [ADR-0008](current/0008-remove-node-embedding-pgvector.md) | accepted | 2026-09-03 | POC 검색에서 node embedding·pgvector 제거 |
| [ADR-0009](current/0009-durable-provider-call-slots.md) | accepted | 2026-09-14 | 전송 전 호출 슬롯과 불명확 시도의 예산 보존 |
| [ADR-0010](current/0010-publish-canonical-claims-with-valid-bindings.md) | accepted | 2026-09-20 | 유효한 binding이 남은 canonical Claim 공개 |

## 대체된 결정

현재 대체된 ADR은 없다. 생기면 [superseded 색인](superseded/README.md)에 기록한다.

## 작성과 대체 규칙

모든 ADR은 `id`, `title`, `status`, `decision_date`, `recorded_date`, `evidence`, `supersedes`, `superseded_by`, `affected_docs` 메타데이터와 배경, 결정, 검토한 대안, 결과, 근거를 포함한다. 회고 작성 ADR은 실제 최초 결정일을 `decision_date`, 문서화한 날짜를 `recorded_date`로 구분한다.

새 결정이 기존 결정을 대체하면 새 ADR을 만들고 기존 ADR의 상태를 `superseded`로 바꿔 `current/`에서 `superseded/`로 옮긴다. 새 ADR의 `supersedes`와 기존 ADR의 `superseded_by`를 서로 연결하고, 현재 문서는 새 ADR만 현재 결정으로 참조한다. 이전 경로에는 안내 파일을 남기지 않는다.

새 ADR은 다음 형식을 사용한다.

```markdown
---
id: ADR-0007
title: 결정 제목
status: accepted
decision_date: YYYY-MM-DD
recorded_date: YYYY-MM-DD
evidence: GitHub Issue와 구현 PR 링크
supersedes: none
superseded_by: none
affected_docs: 영향을 받는 현재 문서 링크
---

# ADR-0007: 결정 제목

## 배경

## 결정

## 검토한 대안

## 결과

## 근거
```

- [ADR-0007: 질문 답변과 발견별 보고서 저장](current/0007-store-panel-reading-results.md)
