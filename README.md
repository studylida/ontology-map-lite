# ontology-map

ontology-map은 공개 자료에서 확인한 근거와 시간축을 지식그래프로 축적하고, 사용자가 선택한 node를 중심으로 동적 부분 graph를 탐색하는 웹 애플리케이션이다. 브라우저와 PostgreSQL 사이의 유일한 제품 경계는 FastAPI HTTP API다.

## 처음 읽을 문서

1. [아키텍처](docs/ARCHITECTURE.md)에서 현재 8개 핵심 테이블과 화면 흐름을 확인한다.
2. [스키마 참고 문서](docs/data/schema-reference.md)에서 현재 SQLAlchemy 모델의 컬럼·제약·인덱스를 확인한다.
3. 변경을 시작하기 전에 [CONTRIBUTING.md](CONTRIBUTING.md)를 읽는다.

[HANDOFF.md](HANDOFF.md)는 경량화 이전 데모의 역사 기록이며 현재 실행 계약이 아니다.

## 저장소 구조

| 경로 | 역할 |
| --- | --- |
| `web/` | React와 Vite 기반 사용자 화면, FastAPI adapter와 component test |
| `server/` | FastAPI HTTP 경계, application-service 함수, SQLAlchemy query, Alembic migration과 개발용 fixture |
| `docs/` | 현재 아키텍처와 자동 생성 스키마 참고 문서 |
| `scripts/check_docs.py` | 메타데이터 기반 스키마 참고 문서 생성과 문서 계약 검사 |
| `compose.yaml` | 개발용 PostgreSQL과 FastAPI 컨테이너 |

## 로컬 실행

WSL에서 실행한다. Python 의존성은 [server/pyproject.toml](server/pyproject.toml)과 `server/uv.lock`, 프런트엔드 의존성은 [web/package.json](web/package.json)과 `web/package-lock.json`을 기준으로 설치한다.

백엔드는 실행 디렉터리의 `.env`를 읽는다. `server/.env`에 `ONTOLOGY_MAP_DATABASE_URL`과 `ONTOLOGY_MAP_ENVIRONMENT=development`를 설정하고 준비된 PostgreSQL에 연결한다. 비밀값을 저장소에 커밋하지 않는다. 루트 `.env`의 `POSTGRES_*` 값은 Compose용이며 백엔드 설정을 대신하지 않는다.

```bash
cd server
uv run --frozen uvicorn ontology_map.main:app --app-dir src --host 0.0.0.0 --port 8000 --reload
```

다른 터미널에서 프로젝트 루트를 기준으로 실행한다.

```bash
cd web
npm ci
npm run dev -- --host 0.0.0.0 --port 5173 --strictPort
```

브라우저에서 `http://localhost:5173`을 연다. Vite는 `/api/v1` 요청을 기본적으로 `http://127.0.0.1:8000`에 전달한다. DB 마이그레이션은 서버 시작 시 자동 실행하지 않는다. 연결 대상과 백업을 확인한 뒤 [기존 Alembic 마이그레이션](server/migrations/versions)을 별도로 적용해야 한다.

## 문서 검증

저장소 루트에서 실행하며 DB 연결이나 API 키는 필요하지 않다.

```bash
uv run --project server --frozen python scripts/check_docs.py --check
uv run --project server --frozen pytest -q scripts/test_check_docs.py
```

모델을 변경했으면 `--write`로 스키마 참고 문서를 먼저 갱신한다. 검사는 선언된 스키마와 생성 문서의 일치, 저장소 내부 Markdown 링크, 존재하는 ADR의 메타데이터와 색인 관계를 확인한다. 실제 DB와 모델의 일치 여부를 검증하는 명령은 아니다.

현재 구현이 바뀌면 해당 책임 문서를 갱신하고, 다음 작업과 미완료 사항은 Issue나 PR에 기록한다.
