# #78 HBF 임베딩 검색 fixture

## 문서 상태

- 관련 Issue: #78
- 임베딩 계약: [`78-embedding-contract.md`](78-embedding-contract.md)
- 하이브리드 검색 계약: [`47-search-document.md`](47-search-document.md)
- 상태: 첫 READY 공개 전에 실행할 검증 계약

이 문서는 Qwen 임베딩 모델의 일반 벤치마크 점수를 재현하려는 시험이 아니다. ontology-map의 HBF seed에서 한국어·영어의 짧은 비대칭 질의가 기대한 node를 찾는지 확인하는 최소 회귀 fixture다.

## 1. 전제

- 테스트 대상은 현재 publication이 READY로 선택한 `node_search_document`와 `node_embedding`이다.
- document embedding은 `qwen3.7-text-embedding`, 1024차원, dense, `text_type = document`로 생성한다.
- query embedding은 같은 모델·차원·dense 출력과 `text_type = query`를 사용한다.
- query instruction은 #78 계약의 고정 문장과 정확히 같아야 한다.
- vector branch는 cosine distance `<=>`로 exact top 50을 구한다.
- 전문 검색 branch와 vector branch의 결합은 #47의 RRF `k = 60`을 사용한다.
- alias의 정확한 일치는 별도 우선 bucket이므로 이 fixture의 vector 품질 점수에서 제외한다.
- 동점은 `node_id ASC`로 정렬한다.

## 2. fixture node

최소한 다음 공개 node가 seed에 있어야 한다.

| node | 유형 | 필요한 검색 맥락 |
|---|---|---|
| SK하이닉스 | COMPANY | HBF 규격 공동 제안·발표와 관련된 회사 |
| SanDisk | COMPANY | HBF 규격 공동 제안·발표와 관련된 회사 |
| HBF | TECHNOLOGY | NAND 기반 고대역폭 플래시 메모리 기술·규격 |
| UCIe | TECHNOLOGY | HBF 발표 맥락에서 연결된 인터커넥트 표준 |
| FMS 2026 | EVENT | HBF 규격 또는 기술이 공개된 행사 |

실제 seed의 표기와 내부 ID는 달라도 되지만 기대 node의 의미는 위와 같아야 한다. fixture를 맞추기 위해 근거 없는 alias·Claim·Relation을 추가하지 않는다.

## 3. 의미 검색 질의

정확한 대표명이나 alias만 입력한 질의는 사용하지 않는다.

| ID | 언어 | 질의 | 우선 기대 node |
|---|---|---|---|
| K1 | ko | `NAND 기반으로 대용량 AI 메모리를 구현하는 기술` | HBF |
| K2 | ko | `고대역폭 플래시 규격을 공동 제안한 회사` | SK하이닉스, SanDisk |
| K3 | ko | `그 메모리 규격 발표와 연결된 인터커넥트 표준` | UCIe |
| E1 | en | `flash-based high-bandwidth memory architecture for AI accelerators` | HBF |
| E2 | en | `companies that jointly proposed the high-bandwidth flash specification` | SK하이닉스, SanDisk |
| E3 | en | `conference where the new flash memory specification was unveiled` | FMS 2026 |

복수 기대 node가 있는 K2와 E2는 SK하이닉스 또는 SanDisk 중 하나만 찾는 것으로 끝내지 않는다. 두 회사가 각각 허용 순위 안에 있어야 해당 질의를 통과한 것으로 본다.

## 4. 통과 기준

### 4.1 vector branch 단독

- 여섯 질의 모두 우선 기대 node 전체가 top 10 안에 있어야 한다.
- 여섯 질의 중 다섯 개 이상은 우선 기대 node 전체가 top 5 안에 있어야 한다.
- K2와 E2는 두 회사 중 더 낮은 순위를 해당 질의의 판정 순위로 사용한다.

예:

```text
K2
SK하이닉스 rank 2
SanDisk rank 6
→ K2 판정 rank = 6
→ top 10 통과, top 5 실패
```

### 4.2 하이브리드 검색

- 여섯 질의 모두 우선 기대 node 전체가 최종 top 5 안에 있어야 한다.
- 정확한 alias bucket을 fixture 성공으로 계산하지 않는다.
- RRF에 들어간 각 branch rank와 최종 rank를 결과 기록에 남긴다.

### 4.3 결정성

같은 DB snapshot과 같은 query contract로 각 질의를 세 번 반복했을 때 node 순서가 같아야 한다.

- cosine distance가 같은 행은 `node_id ASC`
- RRF 점수가 같은 행도 `node_id ASC`
- 실행 시각이나 identity sequence 순서를 의미 점수로 사용하지 않음

### 4.4 실패 강등

다음 장애를 각각 주입한다.

1. query embedding provider timeout
2. vector SQL 실패
3. PostgreSQL 전문 검색 실패

기대 결과:

```text
vector 실패 + FTS 성공
→ FTS 결과 제공 + 의미 검색 실패 안내

FTS 실패 + vector 성공
→ vector 결과 제공 + 키워드 검색 실패 안내

둘 다 실패
→ 검색 오류 + 재시도 제공
```

한 branch의 실패가 성공한 branch 결과를 숨기면 안 된다.

## 5. 호환성 거절 fixture

다음 query vector는 SQL 실행 전에 거절해야 한다.

- 차원이 1024가 아님
- model ID가 다름
- region이 다름
- dense가 아닌 출력
- query instruction version이 다름
- query/document 호환 계약을 알 수 없음

다음 document embedding은 READY 선택 전에 거절해야 한다.

- `model_task.model_version`이 승인된 document 식별자와 다름
- `model_task.status != 'SUCCESS'`
- 1024차원이 아님
- 원소에 NaN 또는 ±Infinity가 있음
- L2 norm이 0임
- 다른 `node_search_document_id` 또는 `node_id`에 속함

## 6. 길이와 입력 규칙 fixture

- document 입력은 `identity_text + "\n\n" + knowledge_text`와 byte 단위로 같아야 한다.
- query는 NFC, LF, 양끝 공백 제거 뒤 embedding한다.
- 내부 공백과 문장부호를 임의로 다시 쓰지 않는다.
- 빈 query는 provider를 호출하지 않는다.
- provider 한도를 넘는 document는 조용히 자르지 않고 non-retryable 입력 오류로 종료한다.
- 같은 검색 문서를 서로 다른 truncation으로 임베딩하지 않는다.

## 7. 결과 기록 형식

실행 결과에는 최소한 다음 값을 남긴다. 현재 물리 스키마에 benchmark 전용 테이블을 추가하지 않고 테스트 출력이나 CI artifact로 보존한다.

```text
fixture version
DB snapshot or seed commit
query ID
normalized query
query contract version
document contract version
vector branch ranks
FTS branch ranks
hybrid ranks
pass/fail
failure injection result
execution timestamp
```

API key, raw query embedding과 전체 document vector는 fixture 보고서에 출력하지 않는다.

## 8. 실패 시 처리

기준을 통과하지 못했다고 차원이나 모델을 조용히 바꾸지 않는다.

1. 실패 질의와 검색 문서 basis를 확인한다.
2. 근거 없이 누락된 alias·Claim을 fixture 통과 목적으로 추가하지 않는다.
3. 검색 문서 생성 규칙 문제이면 새 `generator_version`을 제안한다.
4. query instruction 문제이면 새 query contract version을 제안한다.
5. 모델·차원 교체가 필요하면 #78을 대체하는 새 변경 Issue를 만든다.
6. 변경 뒤 전체 fixture와 실패 강등을 다시 실행한다.

## 9. #81과의 경계

이 fixture는 검색 품질과 호환성을 검사하지만 ANN 전환을 결정하지 않는다.

#81에서만 다음을 측정한다.

- 공개 embedding 수
- exact query 실행 계획
- p95 응답 시간
- exact 대비 ANN recall
- HNSW·IVFFlat build·update 비용

이 fixture를 통과했다는 이유만으로 ANN index를 만들지 않고, exact search가 느리다는 추측만으로 모델이나 차원을 바꾸지 않는다.
