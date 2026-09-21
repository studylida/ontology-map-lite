---
id: ADR-0002
title: 기준 지식그래프와 런타임 동적 부분 graph를 분리한다
status: accepted
decision_date: 2026-09-01
recorded_date: 2026-09-07
evidence: [#65](https://github.com/studylida/ontology-map/issues/65), [PR #66](https://github.com/studylida/ontology-map/pull/66), [#96](https://github.com/studylida/ontology-map/issues/96), [PR #102](https://github.com/studylida/ontology-map/pull/102)
supersedes: none
superseded_by: none
affected_docs: [아키텍처](../../README.md), [논리 스키마](../../../data/logical-schema.md), [제품 설계](../../../product/design.md)
---

# ADR-0002: 기준 지식그래프와 런타임 동적 부분 graph를 분리한다

## 배경

저장된 지식의 정체성과 수명주기는 사용자가 어느 Node를 중심으로 보고 있는지, 화면에 어떤 tier와 좌표로 배치됐는지와 다르다. 화면 상태를 기준 지식에 저장하면 같은 사실이 탐색 방식에 따라 중복되거나 달라질 수 있다.

## 결정

Node, Relation과 Claim을 canonical knowledge graph로 저장하고, 사용자의 중심 Node와 시간 범위에 맞는 runtime partial graph는 조회할 때 조합한다. 중심, 추천, tier, 좌표, 카메라와 화면 표시 단계는 기준 지식이나 전역 graph snapshot으로 저장하지 않는다.

## 검토한 대안

- 화면별 graph snapshot이나 전역 map version을 저장하는 방식은 기준 지식과 표현 상태의 수명주기를 결합하므로 채택하지 않았다.
- Node에 고정 tier와 표시 상태를 저장하는 방식은 중심이 바뀔 때 같은 Node의 역할이 달라지는 제품 동작을 표현하지 못해 채택하지 않았다.

## 결과

같은 기준 지식을 여러 탐색 흐름에서 재사용하고 화면 표현을 독립적으로 개선할 수 있다. 대신 exploration application service가 공개 상태와 시간 범위를 읽어 매 요청의 부분 graph를 구성해야 한다.

## 근거

[#65](https://github.com/studylida/ontology-map/issues/65)와 [PR #66](https://github.com/studylida/ontology-map/pull/66)가 표시 규칙의 영속화를 제거했고, [#96](https://github.com/studylida/ontology-map/issues/96)과 [PR #102](https://github.com/studylida/ontology-map/pull/102)가 동적 exploration aggregate를 구현했다.
