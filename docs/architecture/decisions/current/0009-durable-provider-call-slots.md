---
id: ADR-0009
title: durable provider 호출 예산을 전송 전 슬롯으로 보존한다
status: accepted
decision_date: 2026-09-14
recorded_date: 2026-09-14
evidence: [#125 승인](https://github.com/studylida/ontology-map/issues/125#issuecomment-5658185041), [#124 감사](https://github.com/studylida/ontology-map/issues/124#issuecomment-5658186263), [PR #192](https://github.com/studylida/ontology-map/pull/192)
supersedes: none
superseded_by: none
affected_docs: [논리 스키마](../../../data/logical-schema.md), [물리 스키마](../../../data/physical-schema.md), [DB 운영](../../../operations/database.md)
---

# ADR-0009: durable provider 호출 예산을 전송 전 슬롯으로 보존한다

## 배경

provider 전송 후 terminal 이력을 commit하기 전에 process가 종료되면 결과 행만으로 실제 호출 예산을 보존할 수 없다. 최신 사용자 승인은 이 crash window를 위한 최소 실행 제어 구조를 허용했다.

## 결정

local preflight 뒤 전송 전에 provider_call_slot을 RESERVED로 commit한다. 최대 3개이며 결과가 불명확한 slot은 reclaim 시 UNKNOWN으로 닫고 반환하지 않는다. 확정 결과만 agent_attempt에 append하고 attempt_no는 slot_no를 따른다. attempt_count는 durable terminal 행 수다. 결과 기록과 count·COMPLETED는 원자적으로 처리한다. task row lock과 매번 새로 발급하는 lease token으로 오래된 worker의 기록을 거부한다.

## 검토한 대안

임시 성공·오류 outcome으로 결과를 꾸미거나 lease 필드를 다른 목적으로 재사용하지 않는다. raw payload와 result/staging 구조, task→promotion FK는 이 문제의 해법으로 도입하지 않는다.

## 결과

전송 여부를 모르는 시도도 예산을 보수적으로 소비하므로 실제 전송이 없었어도 slot이 소진될 수 있다. UNKNOWN은 provider 오류가 아니며 기존 terminal 이력에 가짜 행을 추가하지 않는다. runtime helper는 이 구조를 사용하지 않는다. 이전 ADR-0005의 제품 계약·payload 비영속 원칙은 유지한다.

## 근거

위 #125/#124 승인과 구현 PR #192를 따른다. 이번 결정은 publication 또는 resolver 계약을 변경하지 않는다.
