---
id: ADR-0005
title: 모델 작업과 Structured Output 계약을 저장하되 provider 응답 원문은 저장하지 않는다
status: accepted
decision_date: 2026-08-31
recorded_date: 2026-09-07
evidence: [#4](https://github.com/studylida/ontology-map/issues/4), [#5](https://github.com/studylida/ontology-map/issues/5), [#44](https://github.com/studylida/ontology-map/issues/44), [PR #75](https://github.com/studylida/ontology-map/pull/75), [#95](https://github.com/studylida/ontology-map/issues/95), [PR #101](https://github.com/studylida/ontology-map/pull/101)
supersedes: none
superseded_by: none
affected_docs: [논리 스키마](../../../data/logical-schema.md), [물리 스키마](../../../data/physical-schema.md), [코드 규칙](../../../development/code-conventions.md)
---

# ADR-0005: 모델 작업과 Structured Output 계약을 저장하되 provider 응답 원문은 저장하지 않는다

## 배경

모델 작업은 입력, task kind, 모델·prompt 계보와 출력 계약을 재현할 수 있어야 한다. 그러나 provider 응답 원문과 검증 전 후보를 기준 DB에 그대로 저장하면 스키마 밖의 임시 표현이 장기 저장 계약이 되고 근거 데이터와 혼동될 수 있다.

## 결정

`model_task`, `agent_attempt`와 task kind별 불변 `output_schema_definition`을 저장해 실행 계보와 Structured Output 계약을 추적한다. provider 응답 payload, 검증 전 후보 JSON과 prompt 전문은 기준 DB에 저장하지 않는다. 일반 애플리케이션 코드가 Structured Output을 검증하고 승인된 저장 계약으로 승격한다.

## 검토한 대안

- provider 응답과 모든 중간 후보를 JSON으로 영속화하는 방식은 임시 DTO를 DB 계약으로 만들기 때문에 채택하지 않았다.
- 출력 계약을 prompt 코드에만 두는 방식은 어떤 계약으로 작업했는지 DB 계보에서 확인할 수 없어 채택하지 않았다.

## 결과

작업 재현에 필요한 계약과 실행 상태를 보존하면서 provider 종속 payload가 기준 데이터에 남지 않는다. 대신 실패 분석에 필요한 안정된 오류 code와 시도 메타데이터를 명시적으로 설계해야 하며, 실제 Agent와 worker는 아직 구현되지 않았다.

## 근거

[#4](https://github.com/studylida/ontology-map/issues/4)와 [#5](https://github.com/studylida/ontology-map/issues/5)가 실행과 출력 계약을 정했고, [#44](https://github.com/studylida/ontology-map/issues/44)·[PR #75](https://github.com/studylida/ontology-map/pull/75)가 물리 계약으로 통합했다. [#95](https://github.com/studylida/ontology-map/issues/95)·[PR #101](https://github.com/studylida/ontology-map/pull/101)이 metadata와 migration에 구현했다.
