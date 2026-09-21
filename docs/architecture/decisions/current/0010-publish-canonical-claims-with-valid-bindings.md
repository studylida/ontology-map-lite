---
id: ADR-0010
title: 유효한 binding이 남은 canonical Claim만 공개한다
status: accepted
decision_date: 2026-09-20
recorded_date: 2026-09-20
evidence: [Issue #247](https://github.com/studylida/ontology-map/issues/247)
supersedes: none
superseded_by: none
affected_docs: [논리 스키마](../../../data/logical-schema.md), [물리 스키마](../../../data/physical-schema.md), [구현 스택](../../../development/implementation-stack.md), [DB 운영](../../../operations/database.md)
---

# ADR-0010: 유효한 binding이 남은 canonical Claim만 공개한다

## 배경

기존 추출은 Claim의 binding 하나가 Meaning 또는 Entity Resolution에서 실패하면 원문이 뒷받침하는 Claim 전체를 버렸다. D3에서는 원문 근거가 충분한 Claim까지 이 조건 때문에 모두 사라졌다. 반대로 graph에 연결되지 않은 별도 Source Claim을 제품 결과로 저장하면 사용자가 보는 publication 기준과 내부 성공 기준이 다시 갈라진다.

## 결정

한 문서의 생성 결과를 문서 단위 `claim_review` 한 번으로 검토한다. 이 검토는 Claim의 원문 지지, publication 적합성, 각 binding의 의미 일치를 함께 반환한다. 원문 위치·quote·hash와 출력 계약 검사는 일반 코드가 계속 확인한다.

원문 지지와 publication 적합성이 `TRUE`이고 유효한 binding이 하나 이상 남은 Claim만 Entity Resolution으로 보낸다. Entity Resolution은 문서의 mention을 한 번에 판정하며, unresolved mention이 필요한 binding만 제거한다. 다른 유효한 binding이 남으면 같은 Claim을 canonical `claim`과 Observation에 저장하고 promotion을 `COMMITTED`로 만든다. binding이 하나도 남지 않으면 Claim을 공개하지 않고 task를 `VALIDATION_BLOCKED`로 끝낸다.

문장 중복을 판정하는 별도 모델 호출은 사용하지 않는다. statement와 의미 대상이 정확히 같은 기존 Claim만 결정적으로 재사용하고, 표현이 다른 근거 기반 Claim은 새 canonical Claim으로 저장한다. `source_claim*` table의 기존 행은 삭제하지 않지만 현재 runtime은 새 행을 쓰지 않는다. 사용자 노출은 canonical Claim의 publication `READY`를 계속 요구한다.

## 검토한 대안

근거가 확인된 모든 문장을 Source Claim으로 저장하는 방안은 검토 결과를 위한 별도 제품 수명주기를 만들고 공개 기준과 task 성공 기준을 분리하므로 채택하지 않았다. Claim 전체에 Meaning과 Entity Resolution을 적용하는 기존 방식은 유효한 binding도 함께 버리므로 유지하지 않았다. 유사 문장마다 중복 모델을 호출하는 방식은 비용과 시간을 늘리면서 publication 가능성을 낮추므로 제거했다.

## 결과

추출은 문서당 generation, claim review, 필요한 경우 Entity Resolution의 최대 세 모델 단계로 줄어든다. 일부 graph 표현만 가능한 Claim도 유효한 부분을 canonical Claim으로 보존하며, 원문 근거·publication 적합성·최소 한 binding이라는 공개 조건은 유지한다. 기존 Source Claim 실험 행은 호환 자료로 남지만 새 실행의 성공 증거로 사용하지 않는다.

## 근거

Issue #247의 사용자 결정과 D1–D3 실자료 검토를 따른다. D3-3처럼 원문에 없는 전략·의도를 덧붙인 해석은 publication 적합성 검토에서 제외한다.
