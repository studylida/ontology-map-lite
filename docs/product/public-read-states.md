# Public read 실패·empty UX 계약

이 문서는 Issue #208에서 승인된 공개 읽기 실패·정상 empty·재진입 경계를 제품 계약으로 기록한다. backend 공개 가능성의 판정 의미는 #120, invalid READY와 recovery 의미는 #180이 소유한다. Reference Topic 진입·전용 panel·일반 Node 전환은 #206과 `reference-topic-read.md`를 그대로 따른다.

## 초기 진입과 재진입

URL center와 배포 기본 center가 모두 없어도 오류가 아니다. 사용자는 `탐색할 Node를 검색하거나 주제를 선택해 주세요.` 안내와 함께 일반 Node 검색과 `주제` 진입을 계속 사용할 수 있다.

최초 center read가 실패해도 같은 두 재진입 동선을 유지한다.

- `404 NODE_NOT_FOUND`, `retryable=false`: `요청한 Node를 찾을 수 없습니다.`를 표시하고 retry를 제공하지 않는다.
- `422 INVALID_REQUEST`, `retryable=false`: `요청을 확인할 수 없습니다. 다른 Node를 검색하거나 주제를 선택해 주세요.`를 표시하고 network/recovery/generation 실패로 해석하지 않는다.
- `503 PUBLICATION_NOT_READY`, `retryable=true`: `현재 이 Node의 공개 탐색 자료를 불러올 수 없습니다.`와 `다시 조회`를 제공한다.
- network failure: `탐색 데이터를 불러오지 못했습니다. 네트워크 연결을 확인한 뒤 다시 시도해 주세요.`와 같은 read의 retry를 제공한다.

배포 기본 center가 존재하고 실패한 center와 다를 때만 `기본 탐색으로 이동`을 제공한다. runtime Node ID나 개발 환경변수·fixture 정보를 사용자 제품 의미로 노출하지 않는다.

503을 `준비 중`, `복구 중`, `생성 중`, `곧 제공`, `최초 공개 전`, `최종 실패`처럼 현재 API가 보장하지 않는 상태로 설명하지 않는다. HTTP status, 내부 error code, environment variable과 runtime ID도 일반 사용자 copy에 노출하지 않는다.

## 성공 화면 보존과 commit 경계

새 read가 실패했다고 이미 성공적으로 읽은 화면을 삭제하거나 강제 redirect·자동 refresh하지 않는다. 실패한 새 read 상태는 화면에 보이게 하되, 보존된 이전 화면을 새 read에서도 유효성이 재확인된 최신 결과처럼 표현하지 않는다.

- A를 읽은 뒤 B 이동 read가 실패하면 A를 유지하고 B를 성공 current/trail/navigation으로 commit하지 않는다.
- 실패한 B가 retryable이면 `다시 조회`는 B의 동일 read를 다시 실행한다.
- A의 최근 90일 화면에서 최근 1년 read가 실패하면 최근 90일 화면과 선택 상태를 유지한다. retry는 A/최근 1년 read이며 성공 뒤에만 최근 1년을 commit한다.
- browser Back/Forward가 가리키는 target read가 실패해도 마지막 성공 화면/current/trail을 성공 target으로 바꾸지 않는다. 오류 처리 때문에 새 history entry를 만들지 않고, retry 성공 뒤에만 target을 반영한다.
- retry가 성공하면 `최신 공개 상태로 다시 불러왔습니다.`를 비모달 상태 안내로 제공한다.

retry는 실패했던 동일 공개 read request의 재실행일 뿐이다. recovery/model/admin 작업을 시작하지 않는다.

## 추가 read와 panel/dialog

pagination·peripheral graph·panel의 추가 read가 실패하면 이미 읽은 item/graph를 유지한다. 실패 위치에는 `추가 자료를 불러올 수 없습니다. 이미 불러온 내용은 계속 볼 수 있습니다.`를 inline으로 표시한다.

panel 첫 read는 다음 의미를 구분한다.

- `503 PANEL_NOT_READY`: `현재 이 영역의 공개 자료를 불러올 수 없습니다.`
- `404 PANEL_NOT_FOUND`: `요청한 자료를 찾을 수 없습니다.`
- Relation Trace의 404: `이 연결의 공개 근거를 현재 불러올 수 없습니다.`

Evidence/report dialog 첫 read가 실패해도 dialog를 자동으로 닫지 않는다. 오류 안내, retry 가능한 경우 `다시 조회`, 사용자 close를 제공하며 기존 Tab / Shift+Tab / Escape / opener focus return 접근성 계약을 유지한다. Trace 첫 page가 성공한 뒤 추가 page가 실패해도 기존 Trace를 계속 읽을 수 있어야 한다.

## 정상 empty와 비대상

정상 성공 응답의 empty는 오류가 아니다.

- FOLLOWUP_QUESTIONS 성공 0개: `이 기간에는 공개된 후속 질문이 없습니다.`
- NODE_INSIGHT 정상 empty: `이 기간에는 공개된 인사이트가 없습니다.`

두 상태에는 오류 색상·경고·retry UI를 붙이지 않는다.

Reference Topic은 일반 FOLLOWUP_QUESTIONS와 NODE_INSIGHT의 대상이 아니다. 질문/인사이트 부재를 empty·미준비·실패로 재해석하지 않고 #206의 Topic 전용 panel과 Topic membership 정상 empty 계약을 유지한다.

#206 병합 후 Topic read integration도 같은 경계를 따른다.

- `주제` picker의 Topic 목록 read가 실패하면 popover 안에서 실패를 표시하고 `retryable=true`인 경우 같은 목록 read의 `다시 조회`를 제공한다. 목록 read 실패를 정상 empty처럼 조용히 삼키지 않는다.
- Topic rich card가 일반 Node의 Insight title을 보조로 읽을 때 `SUCCESS + 0 reports`이면 기존처럼 title 영역을 생략한다. read 자체가 실패하면 해당 rich card 범위에만 오류를 표시하고 retryability를 반영하며, Topic center나 다른 card를 blocking error로 확대하지 않는다.

## 구현 경계

현재 frontend는 서버가 제공하는 `code + retryable`만으로 이 계약을 표현한다. 새로운 recovery reason/status API, polling/push/streaming, 사용자 클릭 기반 model/recovery 실행, cache/eviction 정책을 이 계약에 추가하지 않는다.

`HAS_TOPIC` Evidence/Trace의 정상 진입 UX는 후속 CHUNK-03 범위이며 이 문서가 소유하지 않는다.
