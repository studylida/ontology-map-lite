# Reference Topic read UX

이 문서는 Issue #206 CHUNK-01의 공개 읽기 경계를 현재 제품 계약으로 기록한다. Topic 정의·lifecycle·identity 자체는 Issue #203과 `docs/product/design.md`의 ontology 계약을 따른다.

## 진입

- 일반 Node 검색은 publication/search-document 계약을 유지하며 Reference Topic을 검색 결과에 포함하지 않는다.
- Reference Topic은 검색 옆의 별도 `주제` 목록으로 진입한다.
- 목록은 runtime `node_id`, canonical display name, `is_active`를 읽어 구성한다. runtime `node_id`를 frontend에 고정하지 않는다.
- inactive Topic도 목록에 남고 `신규 연결 중단` 상태를 표시한다.
- 목록의 사용자 표시 순서는 canonical display name 가나다순이다.

## 중심 탐색

Reference Topic은 일반 Node와 동일한 center navigation 대상으로 취급한다. 기간, trail, browser history, graph center/camera 전환 의미는 일반 Node 이동과 동일하게 유지한다.

Topic center의 graph/read 범위는 공개 가능한 direct `HAS_TOPIC` membership만이다. Topic 화면에서는 일반 exploration의 peripheral 확장을 요청하지 않는다. 지도 node 유형 filter는 graph 표시만 바꾸며 Topic panel membership 데이터에는 적용하지 않는다.

## Topic 전용 panel

Topic center는 일반 Node의 `탐색 / 근거 / 인사이트` 탭을 사용하지 않는다. panel은 다음 순서로 읽는다.

1. Topic header
2. `최근 근거가 많은 연결`
3. `최근 근거가 있는 연결`
4. `그 외 연결`

`최근 근거가 많은 연결`은 선택 기간 안의 `HAS_TOPIC` evidence-group activity count를 기준으로 상위 최대 3개 member만 보여 준다. card에는 근거 수 숫자를 노출하지 않는다. 같은 기간의 공개 가능한 일반 Node insight report가 있으면 기존 public `insight-report` read를 재사용해 report title을 보여 준다. title을 선택하면 해당 일반 Node를 center로 전환하고 인사이트 탭으로 직접 진입한다.

상위 rich card에 포함된 member는 `최근 근거가 있는 연결`에서 반복하지 않는다. 선택 기간 activity가 0인 기존 direct membership은 `그 외 연결`에 남기며 유형별 그룹 안에서 이름 가나다순으로 표시한다.

## 정상 empty 상태

- 전체 공개 membership이 0이면 `아직 공개된 연결 대상이 없습니다.`를 표시한다.
- 전체 공개 membership은 있지만 선택 기간 activity가 0이면 `최근 90일에 새로 확인된 연결 근거가 없습니다.` 또는 `최근 1년에 새로 확인된 연결 근거가 없습니다.`를 표시하고 기존 membership은 `그 외 연결`에 유지한다.

최초 load 실패/공개 불가 UX와 `HAS_TOPIC` Evidence/Trace 상세 UX는 각각 후속 CHUNK-02/03 범위다.

## HTTP projection

- `GET /api/v1/topics`
  - `items[].node_id`
  - `items[].canonical_display_name`
  - `items[].is_active`
  - `topic_code`는 현재 server reference row의 stable machine identity projection으로 함께 반환되지만 CHUNK-01 UI 표시에 사용하지 않는다.
- `GET /api/v1/topics/{topic_node_id}/exploration?time_window=...`
  - Topic reference metadata
  - 전체 공개 membership count
  - 선택 기간 activity count
  - center + direct member graph

일반 Node report title은 기존 `GET /api/v1/nodes/{node_id}/insight-report?time_window=...&detail=false` 공개 조건을 그대로 재사용한다. Topic용 insight/publication READY 의미는 새로 만들지 않는다.
