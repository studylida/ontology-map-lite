# ontology-map 문서 안내

이 디렉터리는 ontology-map의 현재 제품, 아키텍처, 데이터, 운영과 개발 계약을 책임별로 나눈다. 구현 전에 목적에 맞는 문서를 읽고, 같은 사실을 여러 문서에 복사하지 않는다.

## 문서 지도

| 책임 | 문서 | 관리 기준 |
| --- | --- | --- |
| 공통 용어 | [용어집](glossary.md) | 의미와 책임 문서 링크만 기록한다. |
| 현재 아키텍처 | [아키텍처](architecture/README.md) | 병합된 코드와 현재 실행 구조만 기록한다. |
| 수용된 결정 | [ADR 색인](architecture/decisions/README.md) | 영향이 크고 되돌리기 어려운 결정만 기록한다. |
| 제품·화면 계약 | [제품 설계](product/design.md) | 사용자 흐름과 시각·상호작용 의미를 기록한다. |
| 도메인 의미 | [논리 스키마](data/logical-schema.md) | ERD, 의미, 관계, 소유권과 수명주기를 사람이 관리한다. |
| 출처·lint 적재 | [출처·lint 적재 정책](data/source-intake-policy.md) | 허용 자료, 재처리, 승격 전·저장 그래프 lint와 READY 경계를 기록한다. |
| 독립 근거 계보 | [근거 계보 판정 정책](data/evidence-lineage-policy.md) | 준비 문서가 기존 또는 새 Evidence Group을 선택하는 규칙을 기록한다. |
| PostgreSQL 표현 | [물리 스키마](data/physical-schema.md) | 공통 자료형, 이름, 불변성과 변경 규칙을 사람이 관리한다. |
| 실제 DB 객체 | [스키마 참고 문서](data/schema-reference.md) | SQLAlchemy metadata에서 생성하며 수동으로 수정하지 않는다. |
| 기술·에이전트 구성 | [구현 스택](development/implementation-stack.md) | 코드·lockfile의 현재 구성, 승인된 미구현 변경, 모델 시험을 구별한다. |
| OpenAI 모델 실행 | [OpenAI 역할별 실행](development/openai.md) | 현재 역할별 provider·identity·예산·연결과 실제 실행 검증 범위를 구분한다. |
| 코드 품질 | [코드 규칙](development/code-conventions.md) | 언어, 계층, 검증과 오류 처리 규칙을 기록한다. |
| 로컬 실행 | [DB 운영](operations/database.md) | 개발 환경의 실행, 검사, 종료와 초기화 절차를 기록한다. |

저장소 진입점은 [README](../README.md), Git 작업과 문서 변경 규칙은 [CONTRIBUTING](../CONTRIBUTING.md), 세션 인계는 임시 [HANDOFF](../HANDOFF.md)가 소유한다. 과거 기록은 [archive](../archive/README.md)에 있으며 현재 계약의 근거로 바로 사용하지 않는다.

## 변경 원칙

- 엔터티, 관계, 카디널리티, 소유권이나 수명주기가 바뀌면 [논리 스키마](data/logical-schema.md)를 같은 PR에서 갱신한다.
- SQLAlchemy metadata가 바뀌면 `uv run --project server --frozen python scripts/check_docs.py --write`로 [스키마 참고 문서](data/schema-reference.md)를 갱신한다.
- 여러 경계에 영향을 주고 되돌리기 비싼 결정만 [ADR](architecture/decisions/README.md)로 남긴다. 논의 중인 선택은 Issue에 둔다.
- 승인된 미구현 결정과 시험 구성은 책임 문서의 별도 절·상태로 설명하고 Issue 근거를 연결한다. 코드·schema·운영 명령의 현재 사실과 섞지 않는다. 상세 후속 계획·미결정 사항·점수·비용·진행 이력은 GitHub Issue에서 관리한다.
- 세션에서 결정이 바뀌면 사용자 승인과 후속 정정을 대조해 관련 Issue에 요약하고 책임 문서에 반영한다. 세션 원문을 복제하지 않으며, 이전 결정은 대체·보류·취소 여부를 남긴다.
- `uv run --project server --frozen python scripts/check_docs.py --check`로 생성 결과, 내부 Markdown 링크와 ADR 규칙을 확인한다.
