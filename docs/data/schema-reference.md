<!-- scripts/check_docs.py가 생성합니다. 직접 수정하지 마세요. -->

# ontology-map PostgreSQL 스키마 참고 문서

이 문서는 [SQLAlchemy 모델](../../server/src/ontology_map/db/schema.py)의 실제 table, column, constraint와 index를 이름순으로 보여 주는 생성 결과다. 현재 구성과 테이블 역할은 [아키텍처](../ARCHITECTURE.md)가 설명한다. 운영 DB를 조회한 결과가 아니라 Base.metadata에 선언된 스키마다.

- table 수: 8
- 생성 명령: `uv run --project server --frozen python scripts/check_docs.py --write`
- 검사 명령: `uv run --project server --frozen python scripts/check_docs.py --check`

## Table 목차

- [`claims`](#claims)
- [`classifications`](#classifications)
- [`documents`](#documents)
- [`edges`](#edges)
- [`node_insights`](#node_insights)
- [`node_qa_pairs`](#node_qa_pairs)
- [`nodes`](#nodes)
- [`relations`](#relations)

## `claims`

—

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 아니요 | — | — | — |
| `document_id` | `BIGINT` | 아니요 | — | — | — |
| `quote_text` | `TEXT` | 아니요 | — | — | — |
| `statement` | `TEXT` | 아니요 | — | — | — |
| `start_offset` | `INTEGER` | 예 | — | — | — |
| `end_offset` | `INTEGER` | 예 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `now()` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | — | `FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE` |
| PRIMARY KEY | — | `PRIMARY KEY (id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_claims_document_id` | 아니요 | `document_id` | — |

## `classifications`

—

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 아니요 | — | — | — |
| `code` | `VARCHAR(100)` | 아니요 | — | — | — |
| `display_name` | `VARCHAR(200)` | 아니요 | — | — | — |
| `description` | `TEXT` | 예 | — | — | — |
| `is_active` | `BOOLEAN` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| PRIMARY KEY | — | `PRIMARY KEY (id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_classifications_code` | 예 | `code` | — |

## `documents`

—

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 아니요 | — | — | — |
| `title` | `VARCHAR(500)` | 아니요 | — | — | — |
| `normalized_content` | `TEXT` | 아니요 | — | — | — |
| `source_uri` | `TEXT` | 예 | — | — | — |
| `metadata_json` | `JSONB` | 아니요 | — | — | — |
| `status` | `VARCHAR(50)` | 아니요 | — | — | — |
| `error_code` | `VARCHAR(100)` | 예 | — | — | — |
| `error_detail` | `TEXT` | 예 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `now()` | — | — |
| `updated_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `now()` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| PRIMARY KEY | — | `PRIMARY KEY (id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_documents_status` | 아니요 | `status` | — |

## `edges`

—

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 아니요 | — | — | — |
| `source_node_id` | `BIGINT` | 아니요 | — | — | — |
| `target_node_id` | `BIGINT` | 아니요 | — | — | — |
| `relation_id` | `BIGINT` | 아니요 | — | — | — |
| `claim_id` | `BIGINT` | 예 | — | — | — |
| `properties` | `JSONB` | 아니요 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `now()` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | — | `FOREIGN KEY (claim_id) REFERENCES claims (id) ON DELETE SET NULL` |
| FOREIGN KEY | — | `FOREIGN KEY (relation_id) REFERENCES relations (id)` |
| FOREIGN KEY | — | `FOREIGN KEY (source_node_id) REFERENCES nodes (id) ON DELETE CASCADE` |
| FOREIGN KEY | — | `FOREIGN KEY (target_node_id) REFERENCES nodes (id) ON DELETE CASCADE` |
| PRIMARY KEY | — | `PRIMARY KEY (id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_edges_claim_id` | 아니요 | `claim_id` | — |
| `ix_edges_relation_id` | 아니요 | `relation_id` | — |
| `ix_edges_source_node_id` | 아니요 | `source_node_id` | — |
| `ix_edges_target_node_id` | 아니요 | `target_node_id` | — |

## `node_insights`

—

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 아니요 | — | — | — |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `recent_history_summary` | `TEXT` | 아니요 | — | — | — |
| `overall_insight` | `TEXT` | 아니요 | — | — | — |
| `issues` | `JSONB` | 아니요 | — | — | — |
| `input_fingerprint` | `VARCHAR(64)` | 예 | — | — | — |
| `generated_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `now()` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | — | `FOREIGN KEY (node_id) REFERENCES nodes (id) ON DELETE CASCADE` |
| PRIMARY KEY | — | `PRIMARY KEY (id)` |
| UNIQUE | — | `UNIQUE (node_id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_node_insights_input_fingerprint` | 아니요 | `input_fingerprint` | — |

## `node_qa_pairs`

—

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 아니요 | — | — | — |
| `node_id` | `BIGINT` | 아니요 | — | — | — |
| `question` | `TEXT` | 아니요 | — | — | — |
| `answer` | `TEXT` | 아니요 | — | — | — |
| `sequence` | `INTEGER` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | — | `FOREIGN KEY (node_id) REFERENCES nodes (id) ON DELETE CASCADE` |
| PRIMARY KEY | — | `PRIMARY KEY (id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_node_qa_pairs_node_id` | 아니요 | `node_id` | — |

## `nodes`

—

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 아니요 | — | — | — |
| `name` | `VARCHAR(255)` | 아니요 | — | — | — |
| `classification_id` | `BIGINT` | 아니요 | — | — | — |
| `description` | `TEXT` | 예 | — | — | — |
| `properties` | `JSONB` | 아니요 | — | — | — |
| `claim_ids` | `JSONB` | 아니요 | — | — | — |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `now()` | — | — |
| `updated_at` | `TIMESTAMP WITH TIME ZONE` | 아니요 | `now()` | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| FOREIGN KEY | — | `FOREIGN KEY (classification_id) REFERENCES classifications (id)` |
| PRIMARY KEY | — | `PRIMARY KEY (id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_nodes_classification_id` | 아니요 | `classification_id` | — |
| `ix_nodes_name` | 예 | `name` | — |

## `relations`

—

### Columns

| 이름 | PostgreSQL type | nullable | default | identity | 설명 |
| --- | --- | --- | --- | --- | --- |
| `id` | `BIGINT` | 아니요 | — | — | — |
| `code` | `VARCHAR(100)` | 아니요 | — | — | — |
| `display_name` | `VARCHAR(200)` | 아니요 | — | — | — |
| `description` | `TEXT` | 예 | — | — | — |
| `is_directed` | `BOOLEAN` | 아니요 | — | — | — |
| `is_active` | `BOOLEAN` | 아니요 | — | — | — |

### Constraints

| 종류 | 이름 | 정의 |
| --- | --- | --- |
| PRIMARY KEY | — | `PRIMARY KEY (id)` |

### Indexes

| 이름 | unique | column 또는 expression | 조건 |
| --- | --- | --- | --- |
| `ix_relations_code` | 예 | `code` | — |
