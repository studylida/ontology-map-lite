---
id: ADR-0001
title: 브라우저와 PostgreSQL 사이에 FastAPI HTTP 경계를 둔다
status: accepted
decision_date: 2026-09-01
recorded_date: 2026-09-07
evidence: [#38](https://github.com/studylida/ontology-map/issues/38), [PR #49](https://github.com/studylida/ontology-map/pull/49), [#95](https://github.com/studylida/ontology-map/issues/95), [PR #101](https://github.com/studylida/ontology-map/pull/101), [#100](https://github.com/studylida/ontology-map/issues/100), [PR #104](https://github.com/studylida/ontology-map/pull/104)
supersedes: none
superseded_by: none
affected_docs: [아키텍처](../../README.md), [구현 스택](../../../development/implementation-stack.md), [코드 규칙](../../../development/code-conventions.md)
---

# ADR-0001: 브라우저와 PostgreSQL 사이에 FastAPI HTTP 경계를 둔다

## 배경

ontology-map은 React 화면과 PostgreSQL 저장 계층을 함께 사용한다. 브라우저가 저장 구조를 직접 알면 데이터 계약과 보안 경계가 화면 코드에 새고, 내부 변경이 곧바로 사용자 경계 변경이 된다.

## 결정

브라우저와 PostgreSQL 사이의 유일한 제품 경계는 FastAPI HTTP API로 둔다. React는 `/api/v1`을 호출하고, FastAPI route는 Pydantic DTO와 오류 변환을 담당하며, 같은 Python 프로세스의 application service와 명시적 SQLAlchemy query가 PostgreSQL에 접근한다.

## 검토한 대안

- 브라우저가 PostgreSQL이나 별도 DB API에 직접 접근하는 방식은 저장 계약을 공개 경계로 만들기 때문에 채택하지 않았다.
- 내부 기능별 microservice를 먼저 분리하는 방식은 현재 POC의 독립 배포나 확장 요구가 없어 채택하지 않았다.

## 결과

HTTP DTO와 DB schema를 독립적으로 바꿀 수 있고 입력·오류 처리를 한 경계에서 통제한다. 대신 route에서 내부 결과를 응답 DTO로 명시적으로 변환해야 한다. Vite proxy는 로컬 실행 방식일 뿐 제품 경계를 바꾸지 않는다.

## 근거

[#38](https://github.com/studylida/ontology-map/issues/38)과 [PR #49](https://github.com/studylida/ontology-map/pull/49)가 구현 스택과 경계를 정했고, [#95](https://github.com/studylida/ontology-map/issues/95)·[PR #101](https://github.com/studylida/ontology-map/pull/101) 및 [#100](https://github.com/studylida/ontology-map/issues/100)·[PR #104](https://github.com/studylida/ontology-map/pull/104)가 server와 web 연결로 이를 구현했다.
