---
id: ADR-0004
title: 전역 manifest 없이 ontology revision과 활성 상태를 사용한다
status: accepted
decision_date: 2026-09-01
recorded_date: 2026-09-07
evidence: [#42](https://github.com/studylida/ontology-map/issues/42), [#69](https://github.com/studylida/ontology-map/issues/69), [PR #71](https://github.com/studylida/ontology-map/pull/71), [#95](https://github.com/studylida/ontology-map/issues/95), [PR #101](https://github.com/studylida/ontology-map/pull/101)
supersedes: none
superseded_by: none
affected_docs: [논리 스키마](../../../data/logical-schema.md), [물리 스키마](../../../data/physical-schema.md)
---

# ADR-0004: 전역 manifest 없이 ontology revision과 활성 상태를 사용한다

## 배경

Relation과 attribute의 의미와 허용 규칙은 시간이 지나며 바뀔 수 있지만, 과거 지식을 새 의미로 암묵적으로 다시 해석하면 안 된다. 모든 ontology 객체를 하나의 전역 manifest version으로 묶으면 서로 무관한 변경도 같은 배포 단위가 된다.

## 결정

안정된 Relation·attribute code와 불변 revision을 분리하고, 새 지식을 만들 때 사용할 exact revision을 `is_active`로 선택한다. 기존 지식은 생성 당시의 exact revision을 계속 참조한다. Node type도 새 Node 생성 허용 여부를 자체 활성 상태로 표현하며 전역 ontology manifest나 전역 현재 version pointer를 두지 않는다.

## 검토한 대안

- 모든 ontology 정의를 전역 manifest와 version으로 묶는 방식은 독립적인 변경을 불필요하게 결합하므로 채택하지 않았다.
- 기존 row를 새 의미로 갱신하는 방식은 과거 지식의 해석을 바꾸므로 채택하지 않았다.

## 결과

관계와 속성 규칙을 독립적으로 진화시키면서 과거 의미를 보존할 수 있다. 대신 새 지식 생성과 검증 서비스가 활성 exact revision을 명시적으로 선택해야 한다.

## 근거

[#42](https://github.com/studylida/ontology-map/issues/42)가 물리 revision 구조를 정했고, [#69](https://github.com/studylida/ontology-map/issues/69)와 [PR #71](https://github.com/studylida/ontology-map/pull/71)이 전역 manifest를 제거했다. [#95](https://github.com/studylida/ontology-map/issues/95)와 [PR #101](https://github.com/studylida/ontology-map/pull/101)이 최종 구조를 구현했다.
