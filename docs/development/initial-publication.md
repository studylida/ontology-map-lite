# Initial publication durable execution (#215)

## 상태

- 확인일: 2026-09-20
- #215 구현 merge 기준: `542ebe5d4899b06f0fa7022d53549c7c194ec2be`
- migration head: `0010_add_source_processing_job.py`
- 대상: [Issue #215](https://github.com/studylida/ontology-map/issues/215) (completed), merged PR #220
- 실제 product/shared DB cutover 후속: #227

이 문서는 #215 initial publication의 현재 구현 책임과 durable execution 연결을 설명한다. #180의 READY 이후 recovery를 포함하지 않으며, 실제 shared/product DB enable 상태를 뜻하지 않는다.

## 현재 main dependency

현재 main에는 다음 선행 경계가 존재한다.

- #216 `promotion_canonical_change`와 migration `0005_add_promotion_canonical_change.py`
- #127 공통 durable `model_task` lease/retry, `provider_call_slot`, `agent_attempt`, migration `0006_add_provider_call_slot.py`
- #129 `FOLLOWUP_QUESTIONS` durable task identity, provider adapter, runner
- #68 `NODE_INSIGHT` 90일+1년 atomic durable task identity, provider adapter, runner

#215는 위 lifecycle과 #129/#68 제품 의미를 복제하지 않는다.

## Initial publication 흐름

정상 production 경로는 promotion transaction과 publication 실행을 분리한다.

```text
#127 finalize_extraction()
→ canonical mutation + #216 provenance
→ promotion_status = COMMITTED
→ promotion transaction commit

post-commit application handoff
→ initial_publication_coordinator.run_initial_publication()
→ COMMITTED + NOT_STARTED
→ publication_affected_node frozen membership
→ PREPARING
→ deterministic node_search_document
→ NODE_CONTEXT durable task/runner
→ FOLLOWUP 90d+1y atomic durable runner
→ NODE_INSIGHT 90d+1y atomic durable runner 또는 구조적 normal-empty 확정
→ publication_readiness()
→ mark_publication_ready()
→ READY
```

`initial_publication_handoff.finalize_extraction_with_initial_publication()`은 #127 product finalizer가 commit된 뒤 반환한 `promotion_batch_id`가 있을 때만 별도 publication coordinator를 호출한다. no-op promotion의 `promotion_batch_id=None`에서는 coordinator를 호출하지 않는다.

`initial_publication_coordinator.run_initial_publication()`은 dependency order만 조합한다. 범용 queue, scheduler, workflow table, 별도 attempt 원장을 추가하지 않는다.

## publication-visible alias

search document와 NODE_CONTEXT grounding은 해당 publication generation에서 볼 수 있는 alias만 사용한다.

- `NODE_ALIAS_CHANGED` provenance가 없는 historical alias는 허용한다.
- current promotion batch의 alias는 허용한다.
- publication `READY`가 된 이전 batch의 alias는 허용한다.
- 다른 `NOT_STARTED`, `PREPARING`, `FAILED` batch가 만든 alias는 제외한다.

이 규칙은 아직 공개되지 않은 다른 publication의 canonical alias가 search/context 입력으로 새어 들어가는 것을 막는다.

현재 구현의 전제도 함께 보존한다. #215 Reviewer 확인 시점의 production alias write path에는 기존 `node_alias` row를 in-place `UPDATE`하는 경로가 없고, 새 alias mutation은 provenance로 추적되는 insert 경계를 사용한다. 향후 기존 alias row의 text/preferred 상태를 in-place 수정하는 production 경로를 도입하면 이 publication-visible alias/provenance 판정을 함께 재검토해야 한다.

## Reference Topic endpoint grounding

#203의 `PRODUCT_REFERENCE` Topic은 Node identity를 갖지만 ordinary evidence-backed publication 대상은 아니다. 따라서 #216 provenance projection이 `HAS_TOPIC` Relation의 양 endpoint를 복원하더라도 `publication_affected_node`에는 `EVIDENCE_BACKED` Node만 들어간다. Reference Topic 자체에는 PAN, search document, NODE_CONTEXT, FOLLOWUP, NODE_INSIGHT를 만들지 않는다.

member Node의 publication grounding에서는 다음 경계를 함께 지킨다.

- `HAS_TOPIC` Relation과 이를 지지하는 Claim/Observation은 다른 evidence-backed knowledge와 같은 current-state, promotion/publication, lint, Evidence Trace 검증을 거친다.
- member의 검색 문서는 공개 가능한 `SUPPORT` Claim에 Observation 근거가 있는 `HAS_TOPIC` Relation만 선택한다. 이 조건을 잃으면 기존 검색 문서의 근거가 stale로 판정된다.
- Relation의 Topic endpoint는 `PRODUCT_REFERENCE` lifecycle, `TOPIC` node type, 승인된 stable `topic_code`와 `canonical_display_name`, null `current_state`/`promotion_batch_id`를 확인한 뒤 identity dependency로만 허용한다.
- Topic 표시 이름은 `topic_reference.canonical_display_name`을 사용하며 `node_alias`를 요구하거나 생성하지 않는다.
- member의 `search_document_basis`에는 검증된 `HAS_TOPIC` Relation과 support Claim을 유지하지만 Reference Topic의 `knowledge_item_id` 자체는 넣지 않는다. 따라서 Topic을 일반 evidence-backed public basis로 가장하지 않는다.
- FOLLOWUP과 NODE_INSIGHT의 direct-connection grounding도 같은 검증된 Reference Topic identity를 허용하되, Topic을 basis item으로 추가하지 않는다.
- `topic_reference.is_active`는 새 membership 생성 경계다. 이미 생성된 evidence-backed `HAS_TOPIC` membership의 읽기/grounding에서는 inactive 전환만으로 과거 의미를 숨기지 않는다.

이 경계는 `node_type == 'TOPIC'` 전체를 skip하는 특례가 아니다. legacy evidence-backed Topic과 승인된 PRODUCT_REFERENCE Topic은 lifecycle로 구분하고, Reference Topic identity가 #203 계약과 다르면 publication preparation을 fail-closed 한다.

## Durable ownership

`NODE_CONTEXT`는 #215 제품이므로 #215가 task-specific provider adapter와 runner를 소유한다. 다만 claim/lease, retry/backoff, provider call slot, `agent_attempt`, provider outcome 기록은 모두 #127 공통 `db.model_tasks`와 `durable_provider`를 사용한다. 기존 `prepare_node_context()`, `ensure_node_context_task()`, `apply_node_context()`가 generation identity, stale 검증, immutable artifact와 product terminal 의미의 source of truth다.

NODE_CONTEXT effective input identity에는 publication generation, selected search-document identity/basis, 실제 `NodeContextAgentInput`, provider 결과에 영향을 주는 execution settings를 포함한다. execution limits와 structured-request identity는 provider adapter와 task identity가 같은 source를 사용한다.

FOLLOWUP은 `db.followup_tasks.enqueue_followup()`과 `followup_runner.run_followup()`을 사용한다. 한 durable task가 90일과 1년 입력·출력을 함께 소유하고 두 `node_question_set`을 한 transaction에서 저장한다. #215는 input hash/cache key를 계산하지 않는다. migration `0008` 이전 READY 데이터의 기간별 task 두 개 형태도 읽을 수 있다.

NODE_INSIGHT는 `db.insight_tasks.enqueue_insight()`과 `insight_runner.run_insight()`을 사용한다. 한 durable task가 90일+1년 전체 bundle을 소유하며 #215는 기간별 Insight task를 만들지 않는다. 두 기간 모두 Claim 2개와 `IN_WINDOW` Claim 1개라는 보고서 최소 구조를 충족할 수 없으면 provider slot과 attempt를 만들지 않고 두 기간의 `report=null`을 성공 결과로 저장한다. 한 기간이라도 가능하면 기존 Terra bundle 호출을 유지한다.

## Restart와 READY

같은 batch 재실행은 frozen membership과 기존 deterministic search document를 재사용하고, 각 task-owned durable identity로 기존 task를 찾는다. `SUCCESS`, `RUNNING`, `RETRY_WAIT`, `VALIDATION_BLOCKED`, `FINAL_FAILED`를 #215가 초기화하거나 다른 의미로 바꾸지 않는다. terminal `SUCCESS` task에는 provider send를 반복하지 않는다.

READY는 runner 성공만으로 전환하지 않는다. 기존 `publication_readiness()`가 같은 publication generation의 search document, NODE_CONTEXT, FOLLOWUP 90일·1년, NODE_INSIGHT atomic bundle과 stale 상태를 모두 검증한 뒤에만 기존 `mark_publication_ready()`를 호출한다.

PREPARING 이후 affected membership의 authoritative source는 `publication_affected_node`다. #216 provenance는 최초 `NOT_STARTED → PREPARING` 진입 때만 membership 산정에 사용하고 READY/failure/retry에서 재projection하지 않는다.

동일 deterministic search-document row가 다른 publication generation에서도 재사용될 수 있지만 과거 generation의 NODE_CONTEXT, FOLLOWUP, NODE_INSIGHT 결과를 새 generation completeness에 섞지 않는다.

## 최종 검증

#215 최종 Reviewer가 검토한 candidate는 다음과 같다.

```text
base main: 06cc901c64c4f72fa0e2a0577f23235600280cc0
PR #220 head: 07e3161be0942a9aa55ce068ae2c7f3cfdd975b8
successful exact merge-ref: 77947626ecae3dfafafb9dcc5067162e3258563e
merged main: 542ebe5d4899b06f0fa7022d53549c7c194ec2be
```

Fresh PostgreSQL 18.6에서 migration `0001 → ... → 0006`을 적용한 exact merge-ref 검증 결과:

- Phase A: 6 passed
- Phase B: 10 passed
- Phase C: 5 passed
- Phase D: 16 passed
- current #129/#68 durable runner: 31 passed
- full backend: 329 passed, 113 skipped
- Ruff format/check PASS
- mypy PASS
- Alembic check PASS
- docs/diff check PASS

관련 `Initial Publication`, `Knowledge Extraction`, `Promotion Provenance`, `Entity Resolution`, `Documentation`, `Ontology Reference Data`, `Topic Reference` workflow도 final head에서 green이었다.

CI는 deterministic injected provider operation / MockTransport를 사용했다. live paid Model Studio E2E는 #215 완료 조건으로 실행하지 않았다.

## 검증과 운영 경계

격리 PostgreSQL 회귀는 실제 durable claim → slot reservation commit → provider operation → `agent_attempt` terminal → exact lease fence → product finalizer → READY 순서를 검증한다. CI provider operation은 deterministic injected operation 또는 MockTransport를 사용할 수 있으며 유료 Model Studio 호출과 구분한다.

실제 shared/product DB coordinator enable은 #227에서 별도로 관리한다. enable 전에는 current migration head 적용과 `assert_initial_publication_cutover_safe()` PASS가 필요하다. legacy `COMMITTED + NOT_STARTED`가 발견되면 자동 추정·skip하지 않고 remediation 대상으로 분리한다.

READY 이후 effective public basis invalidation과 recovery는 #180 소유다. #215는 #216 provenance를 recovery event log나 reconciliation identity로 사용하지 않는다.
