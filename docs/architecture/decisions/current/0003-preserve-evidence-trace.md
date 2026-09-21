---
id: ADR-0003
title: source·Evidence Group·Observation·Claim 추적성을 보존한다
status: accepted
decision_date: 2026-09-01
recorded_date: 2026-09-07
evidence: [#1](https://github.com/studylida/ontology-map/issues/1), [#41](https://github.com/studylida/ontology-map/issues/41), [#43](https://github.com/studylida/ontology-map/issues/43), [PR #74](https://github.com/studylida/ontology-map/pull/74), [#95](https://github.com/studylida/ontology-map/issues/95), [PR #101](https://github.com/studylida/ontology-map/pull/101)
supersedes: none
superseded_by: none
affected_docs: [아키텍처](../../README.md), [논리 스키마](../../../data/logical-schema.md), [물리 스키마](../../../data/physical-schema.md)
---

# ADR-0003: source·Evidence Group·Observation·Claim 추적성을 보존한다

## 배경

공개 자료 여러 건이 같은 원문을 재배포할 수 있고 하나의 문서에서도 여러 주장을 뒷받침하는 구간이 다르다. Claim에 URL이나 인용문만 복사하면 원문 위치와 독립 근거 수를 일관되게 검증하기 어렵다.

## 결정

준비된 `source_document`는 정확히 하나의 Evidence Group에 속하고, Observation은 해당 문서의 정확한 문자 범위를 가리킨다. Claim은 연결 table을 통해 하나 이상의 Observation으로 이어지며, 독립 근거 수는 문서 수가 아니라 Evidence Group 수로 계산한다. 모델 provider의 응답 원문이나 중간 후보는 Evidence Trace로 취급하지 않는다.

## 검토한 대안

- Claim에 URL과 인용문을 직접 복사하는 방식은 원문 계보와 위치 무결성을 잃기 때문에 채택하지 않았다.
- 모든 문서를 독립 근거로 세는 방식은 재배포 문서를 중복 계산하므로 채택하지 않았다.

## 결과

사용자는 Claim에서 정확한 원문 범위와 독립 근거 묶음까지 추적할 수 있다. 대신 source 준비 단계에서 원문을 불변으로 정규화하고 Evidence Group을 판정해야 하며, 그 구체적인 판정 전략은 별도 Issue가 소유한다.

## 근거

[#1](https://github.com/studylida/ontology-map/issues/1), [#41](https://github.com/studylida/ontology-map/issues/41)과 [#43](https://github.com/studylida/ontology-map/issues/43)·[PR #74](https://github.com/studylida/ontology-map/pull/74)가 근거 계보와 물리 경계를 확정했고, [#95](https://github.com/studylida/ontology-map/issues/95)·[PR #101](https://github.com/studylida/ontology-map/pull/101)가 metadata와 migration에 구현했다.
