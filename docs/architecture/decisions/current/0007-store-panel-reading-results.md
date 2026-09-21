---
id: ADR-0007
title: 질문 답변과 발견별 보고서를 불변 읽기 결과로 저장한다
status: accepted
decision_date: 2026-09-08
recorded_date: 2026-09-08
evidence: [#162](https://github.com/studylida/ontology-map/issues/162), [#163](https://github.com/studylida/ontology-map/issues/163)
supersedes: none
superseded_by: none
affected_docs: [제품](../../../product/design.md), [논리 스키마](../../../data/logical-schema.md), [물리 스키마](../../../data/physical-schema.md)
---

# ADR-0007: 질문 답변과 발견별 보고서를 불변 읽기 결과로 저장한다

## 배경과 결정

이동 대상에 붙은 질문 두 개로는 현재 node를 이해하기 위한 답변·근거를 제공할 수 없다. 사용자는 질문·근거·인사이트 모두 기간을 따르고, 인사이트는 한 보고서 안에 주요 발견별 분석을 담도록 승인했다.

공개 선택된 context에 기간별 질문 묶음과 답변·Claim 참조를 연결한다. 보고서는 기존 node_insight를 사용하고 기간별 선택과 발견별 절·Claim 참조를 추가한다. 성공한 빈 결과를 미준비와 구분하며 기존 Claim과 원문을 복사하지 않는다. 전체 공개 basis와 참조를 읽기 시 재검증하고 클릭 시 모델을 호출하지 않는다.

## 대안과 영향

기존 질문의 target을 답변으로 취급하거나 migration에서 여러 보고서를 합성하는 방식은 실제 생성되지 않은 분석을 만들어 내므로 채택하지 않았다. 기존 이동 질문과 API를 보존하고 추가 구조로 전환한다. 결과가 존재하는 downgrade는 막으며 스키마를 유지한 앱 복귀를 지원한다. 생성 worker·출력 품질 평가는 보류하고 실제 DB의 명시적인 개발 자료로 저장·조회·화면만 검증한다.
