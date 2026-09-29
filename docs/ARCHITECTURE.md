# Ontology Map Lite Architecture

## 1. 8 Core Tables

현재 스키마의 단일 정의는 [schema.py](../server/src/ontology_map/db/schema.py)의 `Base.metadata`다. Alembic과 문서 생성기는 이 정의를 사용한다. 컬럼·제약·인덱스는 [자동 생성 스키마 참고 문서](data/schema-reference.md)에서 확인한다. 문서 검사는 운영 DB에 연결하지 않으며 테이블을 생성하거나 변경하지 않는다.

- **classifications**: 온톨로지 분류 체계 (COMPANY, TECH, PERSON 등)
- **relations**: 엔티티 간 관계 정의 (INVESTS_IN, DEVELOPS, LEADS 등)
- **documents**: 원천 텍스트 및 지식 출처 문서
- **claims**: 문서 내 문장/근거(Quote) 단위 추출물
- **nodes**: 정제된 지식그래프 핵심 노드 (엔티티)
- **edges**: 노드 간의 유향 관계 연결선 (source_node_id -> target_node_id)
- **node_insights**: LLM이 도출한 노드별 종합 요약, 최근 히스토리, 이슈 태그
- **node_qa_pairs**: 노드 중심 핵심 질의응답 쌍

## 2. Services & API
- `POST /api/v1/nodes`: 엔티티 해소(Resolution) 및 노드 생성
- `GET /api/v1/nodes/{id}/graph`: 1-hop 서브그래프 조회 (nodes, edges)
- `GET /api/v1/nodes/{id}/insights`: 노드 AI 종합 인사이트 & Q&A 조회
- `GET /api/v1/classifications`: 온톨로지 분류 목록

## 3. Frontend (web/)
- `App.tsx`: 100줄 미만의 단일 데이터 흐름 컨테이너
- `GraphCanvas.tsx`: 3d-force-graph 기반 3차원 물리 시뮬레이션 및 카메라 인터랙션
- `SidePanel.tsx`: 노드 인사이트 및 Q&A 아코디언 통합 플로팅 패널
- `types.ts`, `api.ts`: 8개 테이블 및 백엔드 API와 1:1 동기화된 가벼운 클라이언트

노드와 이름표는 같은 중심 이동 동작을 실행한다. 원형 노드는 누른 위치에서 Three.js로 직접 판정하고 5px 이내의 포인터 흔들림을 허용하며, 실제 드래그와 취소는 선택에서 제외한다. 이름표는 키보드로도 활성화할 수 있는 버튼이다.

## 4. 초기 화면 전환

- 초기 로딩은 그래프 데이터와 시작 카메라가 준비된 뒤 종료한다. 정상 모션 설정에서는 최소 1.4초 표시 후 완료 표시와 페이드아웃을 거친다.
- 그래프를 계속 표시한 채 1초 확대, 0.3초 정지와 라벨 등장, 1.1초 확대를 진행한다. 사용자 조작은 시작 연출을 중단하며, 모션 감소 설정에서는 최종 위치를 바로 표시한다.
- 최초 조회 실패는 같은 로딩 화면에서 안내하고 다시 시도 버튼으로 페이지를 새로고침한다. 실패 시 다른 노드를 자동 검색하지 않는다. 초기 로딩 이후의 조회 실패는 기존 지도를 유지하며 안내한다.
