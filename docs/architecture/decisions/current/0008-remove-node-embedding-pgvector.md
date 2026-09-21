---
id: ADR-0008
title: POC 검색에서 node embedding과 pgvector를 제거한다
status: accepted
decision_date: 2026-09-03
recorded_date: 2026-09-14
evidence: [#121](https://github.com/studylida/ontology-map/issues/121), [PR #195](https://github.com/studylida/ontology-map/pull/195)
supersedes: none
superseded_by: none
affected_docs: [제품](../../../product/design.md), [논리 스키마](../../../data/logical-schema.md), [물리 스키마](../../../data/physical-schema.md), [구현 스택](../../../development/implementation-stack.md), [DB 운영](../../../operations/database.md)
---

# ADR-0008: POC 검색에서 node embedding과 pgvector를 제거한다

## 배경

현재 사용자 검색은 exact alias와 PostgreSQL native FTS만 실행하며 node embedding을 소비하지 않는다. production embedding worker와 query embedding adapter도 없고, 개발 fixture의 합성 vector만 frozen schema와 실행 의존성을 유지하고 있었다.

## 결정

현재 POC에서 `node_embedding`, `EMBEDDING` model task, Python pgvector 의존성, PostgreSQL `vector` extension과 pgvector DB image를 제거한다. publication READY는 검색 문서·context·질문·insight의 승인된 완결성을 유지하되 embedding 산출물을 기다리지 않는다. PostgreSQL의 `tsvector`/`to_tsvector` FTS는 vector embedding과 다른 기능이므로 유지한다.

이번 변경은 빈 환경에서도 pgvector가 필요하지 않게 하기 위해 기존 `0001` frozen baseline을 교체하는 승인된 예외다. 기존 개발 DB의 in-place upgrade·downgrade나 선별 데이터 이관은 제공하지 않고, 필요한 백업을 확인한 뒤 새 baseline으로 재생성한다.

## 검토한 대안

기존 `0001`을 보존하고 forward migration으로 `node_embedding`만 삭제하면 빈 DB의 migration replay에 Python pgvector와 pgvector DB image가 계속 필요하므로 채택하지 않았다. 미래 retrieval 용도를 위해 vector abstraction이나 대체 vector store를 남기는 방안도 실제 POC 소비 경로가 없어 채택하지 않았다.

## 결과

검색 인프라는 PostgreSQL native FTS만 필요로 한다. #117의 exact alias → identity FTS → knowledge FTS bucket 분리와 `match_reasons` 제거는 별도 제품 검색 계약이므로 이 결정에 포함하지 않는다. 과거 embedding 계약 문서와 archive는 역사 기록으로 보존한다.

## 근거

Issue #121의 승인된 breaking baseline 계약과 구현 PR #195를 따른다.
