# Ontology Map Lite Architecture

## 1. 8 Core Tables
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
