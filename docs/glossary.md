# ontology-map 용어집

이 문서는 여러 책임 문서에서 반복되는 핵심 용어의 뜻과 소유 문서만 연결한다. table이나 column 목록은 복사하지 않는다.

| 용어 | 뜻 | 책임 문서 |
| --- | --- | --- |
| Node | 사람, 회사, 기술, Topic이나 사건처럼 지식그래프에서 정체성을 가진 대상이다. | [논리 스키마의 노드 정체성](data/logical-schema.md#57-노드-정체성) |
| Observation | 준비된 원문에서 정확한 문자 범위를 가리키는 불변 근거 조각이다. | [논리 스키마의 observation](data/logical-schema.md#53-observation) |
| Claim | 하나 이상의 Observation으로 추적할 수 있는 근거 기반 주장이다. | [논리 스키마의 기준 지식](data/logical-schema.md#58-기준-지식) |
| Relation | exact ontology revision과 두 Node를 연결하는 기준 지식이다. 화면의 임시 선이나 추천 이동을 뜻하지 않는다. | [논리 스키마의 relation](data/logical-schema.md#relation) |
| Evidence Group | 같은 원문 계보의 문서를 독립 근거 하나로 세기 위한 묶음이다. 신뢰도나 사실의 진실성을 뜻하지 않는다. | [논리 스키마의 evidence_group](data/logical-schema.md#51-evidence_group) |
| Evidence Trace | Claim에서 Observation, 원문과 Evidence Group까지 이어지는 근거 추적 경로다. | [논리 스키마의 기준 지식과 Evidence Trace](data/logical-schema.md#34-기준-지식과-evidence-trace) |
| promotion | 검증을 통과한 후보를 기준 지식으로 원자적으로 저장하는 수명주기다. | [논리 스키마의 승격과 공개](data/logical-schema.md#63-승격과-공개) |
| publication | 저장된 기준 지식으로 검색 문서, context, 질문과 인사이트 같은 공개용 파생 결과를 준비하는 별도 수명주기다. | [ADR-0006](architecture/decisions/current/0006-separate-promotion-and-publication.md) |
| READY | 한 publication batch가 정한 공개 완결성 검사를 통과해 일반 조회에서 선택될 수 있는 상태다. 지식 자체의 진실성 등급이 아니다. | [물리 스키마의 Publication과 공개 조회](data/physical-schema.md#publication과-공개-조회) |
| canonical knowledge graph | Node, Relation과 Claim 등 검증된 기준 지식으로 이루어진 저장 그래프다. | [ADR-0002](architecture/decisions/current/0002-separate-canonical-and-runtime-graphs.md) |
| runtime partial graph | 사용자의 중심 Node와 시간 범위에 맞춰 읽기 시점에 조합하는 화면용 부분 그래프다. 별도 영속 graph snapshot이 아니다. | [ADR-0002](architecture/decisions/current/0002-separate-canonical-and-runtime-graphs.md) |
| ontology revision | Relation이나 attribute 의미와 허용 규칙을 불변 버전으로 보존하는 계약이다. | [ADR-0004](architecture/decisions/current/0004-use-active-ontology-revisions.md) |
| Structured Output contract | task kind별 모델 출력 형태를 검증하는 버전된 JSON Schema 계약이다. provider 응답 원문을 뜻하지 않는다. | [ADR-0005](architecture/decisions/current/0005-persist-model-task-contracts-not-provider-payloads.md) |
