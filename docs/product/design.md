---
version: alpha
name: ontology-map
description: Dark-first analytical workspace centered on a dynamic three-dimensional knowledge map.
colors:
  background: "#0B0F14"
  surface: "#121821"
  surface-elevated: "#18212C"
  border: "#2B3645"
  text-primary: "#F3F6FA"
  text-secondary: "#9CA8B7"
  interactive: "#72A7FF"
  focus-ring: "#B9D3FF"
  conflict: "#E6A23C"
  danger: "#F26D78"
  ready: "#4FD1A1"
  node-person: "#F5A24B"
  node-company: "#B792F4"
  node-technology: "#43C6D9"
  node-topic: "#65C98B"
  node-event: "#F17C9E"
  relation: "#7B8797"
typography:
  screen-title:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: 24px
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: -0.01em
  panel-title:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: 18px
    fontWeight: 600
    lineHeight: 1.35
  body:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: 12px
    fontWeight: 550
    lineHeight: 1.4
  technical:
    fontFamily: 'ui-monospace, "SFMono-Regular", Consolas, monospace'
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.4
rounded:
  sm: 4px
  md: 8px
  lg: 12px
  full: 999px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  2xl: 32px
components:
  search-control:
    backgroundColor: "{colors.surface-elevated}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.md}"
    height: 44px
    padding: 12px
  detail-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.lg}"
    width: 420px
    padding: 24px
  primary-action:
    backgroundColor: "{colors.interactive}"
    textColor: "{colors.background}"
    rounded: "{rounded.md}"
    height: 44px
    padding: 12px
  map-tooltip:
    backgroundColor: "{colors.surface-elevated}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.sm}"
    padding: 8px
---

# ontology-map 디자인 계약

## Overview

ontology-map은 공개 자료에서 얻은 근거와 시간축을 탐색하는 분석 작업 공간이다. 사용자는 검색한 노드를 중심으로 동적 지식맵을 살펴보고, 10분 안에 맥락 설명 1개, 근거 있는 관계 3개와 후속 질문 2개를 얻을 수 있어야 한다.

화면의 중심은 3D 지식맵이다. header, 검색, 시간 범위, 범례와 상세 panel은 탐색을 돕는 보조 요소로 유지한다. 장식이나 브랜드 연출이 그래프, Evidence Trace와 읽기 흐름보다 먼저 보이지 않게 한다.

기본 화면은 아래 가독성 디자인 절의 단색 배경·발광 없는 원형 node·가는 관계선·단계별 이름 표시를 사용한다. 다크·라이트 모드를 제공하고 장식보다 관계와 근거의 가독성을 우선한다.

UI 문구는 한국어를 기본으로 한다. node type, relation type, model identifier, ontology code와 API 값처럼 정확한 식별이 필요한 값은 원래 영어 표기를 보존한다.

## Reference Topic 읽기 계약

제품 Reference Topic은 일반 evidence-backed 지식이 아니라 승인 controlled vocabulary다. canonical 표시 이름은 `topic_reference` 정의가 소유하고 일반 검색 문서나 alias Evidence를 만들지 않는다. #203은 lifecycle/schema/read boundary를 제공하고 #201의 명시적 reference-data activation이 승인 Topic 9개를 실제 제품 row로 활성화한다. 앱 startup이 누락 Topic을 자동 생성하거나 보정하지 않는다.

일반 검색에는 Reference Topic을 포함하지 않는다. Topic 진입은 승인 Topic/카테고리 목록 또는 일반 graph에 이미 표시된 Topic 선택을 전제로 한다. `GET /api/v1/topics/{topic_node_id}/exploration?time_window=RECENT_90_DAYS|RECENT_1_YEAR|ALL_TIME`는 공개 가능한 direct `HAS_TOPIC` membership만 반환하고 Topic 중심 2-hop·3-hop 확장은 하지 않는다. 응답은 전체 공개 membership 수와 선택 기간의 최근 member 수·최근 Evidence Group 수를 구분한다.

Topic이 비활성화되어도 기존 유효 membership은 삭제·비공개·재해석하지 않고 새 `HAS_TOPIC` 생성만 차단한다. 연결된 일반 Node를 선택하면 기존 exploration을 그대로 사용한다. 일반 Node exploration의 직접 이웃 상한은 24개이며 direct `HAS_TOPIC` Reference Topic도 같은 후보군에 포함하지만 Topic 전용 quota·별도 graph layer는 두지 않는다.

## 가독성 디자인 미리보기

노드 유형 필터는 범례에 통합한다. 유형의 원이나 글자를 누르면 해당 유형을 숨기고 원·글자를 무채색과 취소선으로 표시하며, 다시 누르면 복원한다. 각 항목은 키보드로 조작하는 토글 버튼이며 눌림 상태는 표시 여부를 뜻한다. 여러 유형을 동시에 선택할 수 있고 접힌 범례에서도 무채색 점과 필터 적용 상태를 유지한다. 검색·기간 조작부에는 별도 유형 필터를 두지 않는다. 첫 진입에는 모든 유형을 표시하며 중심·기간 이동 중에도 선택을 유지한다. 필터는 이미 불러온 graph의 표시만 바꾸고 node 좌표·camera·확대배율·우측 정보와 주변부 조회를 유지한다. 제외한 유형의 node와 그 node에 연결된 간선은 표시·pointer 선택·keyboard 목록에서 함께 제외하며 hover로도 다시 나타나지 않는다. 중심 node에도 같은 조건을 적용한다. 전체 표시와 전체 해제를 제공하며 새로 불러온 node에도 유형 선택을 적용한다. 필터를 바꾸면 표시 대상 node의 외곽선을 기본 밝기에서 약 2.5초 동안 부드럽게 강조한 뒤 원래 밝기로 돌린다. 빠른 재선택은 이전 강조를 교체하고 reduced-motion에서는 깜빡임 없이 잠시 강조한다. 이 강조는 hover의 이름·간선 표시 범위를 넓히지 않는다.

불러온 node가 유형 필터로 모두 숨겨지면 검색·기간 조작부에 “유형 필터로 노드가 숨겨져 있습니다”와 전체 표시 버튼을 제공한다. 버튼은 기존 유형 필터만 해제하며 중심·좌표·배율을 바꾸지 않는다. 데이터 자체가 없는 경우에는 이 안내를 표시하지 않는다.

같은 Relation의 복수 근거 가닥은 가운데에 가까운 한 가닥을 고정해 기본 표시한다. 중심과 1단계 이웃을 직접 잇는 Relation은 모든 가닥을 유지한다. node·간선 hover 또는 keyboard focus에서는 해당 연결의 나머지 가닥을 약 450ms에 걸쳐 표시하고, 초점이나 중심의 직접 관계 범위에서 벗어나면 대표 가닥을 남기고 나머지만 같은 시간 동안 숨긴다. 전환 도중 대상이 바뀌면 현재 밝기에서 이어가며 reduced-motion에서는 즉시 적용한다. 대표 가닥의 좌표·실제 관계·근거 수는 바꾸지 않고 방향 화살표도 대표 가닥을 따라 표시한다. 유형 필터와 단계별 간선 표시 조건이 우선한다.

노드·간선의 최종 표시 상태는 WebGL이 장면을 그리기 전에 합성한다. 추가 page나 크기 갱신에서 기본 밝기를 다시 적용하더라도 그 프레임에 거리·단계·초점·필터를 반영해 잠깐 밝게 나타나는 현상을 방지한다. 기본 2단계 간선의 불투명도는 0.18로 낮춰 중심의 직접 연결(0.9)과 구분하며, hover에서는 기존 강조 밝기를 사용한다.

이동·확대에 따른 간선과 방향 화살표의 불투명도는 양 끝 node의 표시 진행률에 맞춘다. 2단계 node가 완전히 나타날 때까지 간선을 기다리지 않고 함께 서서히 표시하며, 숨겨질 때도 같은 방식으로 낮춘다. 기존 2단계까지의 기본 간선과 3단계 이후 hover 간선 규칙은 유지한다.


#156에서 사용자 검토를 거친 디자인을 기본 웹 화면(`/`)으로 채택했다. 기존 `/design-preview` 주소도 같은 화면을 제공하며 미리보기 배지와 기존 화면 전환 링크는 제거했다. 아래 미리보기라는 명칭은 디자인 검토 당시의 이름이며, 이 절의 시각·전환 규칙이 기존 디자인 설명보다 우선한다. [InfraNodus의 공식 화면](https://infranodus.com/)을 참고해 어두운 단색 배경, 발광 없는 원형 node, node 옆 HTML label과 간결한 검색·기간·상세 panel을 사용한다. 별도의 라이브러리·API·데이터 저장 계약은 추가하지 않는다.

미리보기는 중심 node 기준 z 범위를 ±64로 사용한다. 이전 후보의 실제 ±4.8보다 깊이를 늘려 원근과 pan 시 앞뒤 이동 차이를 드러낸다. x·y의 조밀한 배치와 선택 node의 현재 위치는 유지하며 카메라 조망 거리에는 각 node의 깊이를 반영한다. 추가 page는 기존 z 좌표도 보존한다. 회전은 제공하지 않고 label은 깊이에 따라 작아지지 않는다.

미리보기의 첫 진입은 현재 불러온 graph가 한 점처럼 작게 보이는 먼 거리에서 시작한다. 전체 DB를 추가 조회하는 연출은 아니다. 먼 조망을 240ms 유지한 다음 1200ms 동안 현재 불러온 graph가 한눈에 들어오는 거리로 접근하며 감속한다. 이 거리에서 400ms 멈춘 뒤 다시 가속해 1500ms 동안 중심으로 확대한다. 두 확대 구간은 각각 거리의 로그값과 ease-in-out을 사용해 정지 지점에서 속도가 이어지도록 한다. 초기 연출의 두 번째 확대에서는 2단계 이후 node와 간선의 불투명도를 첫 1200ms 동안 서서히 낮춘다. 이때만 거리 기반 숨김 대신 연출 진행률을 사용하며, 일반 탐색은 기존 표시 전환 규칙을 유지한다. 최종 거리는 중심과 1단계 이웃의 깊이를 반영한 화면 맞춤 거리를 기준으로 정한다. 가까운 중심 조망에서는 2단계 이후 node와 해당 간선을 숨긴다. 축소하거나 중심에서 pan하면 이미 불러온 주변 node의 불투명도를 이동량에 따라 서서히 높여 같은 좌표로 표시한다. pan의 표시 전환 거리는 현재 확대배율을 반영하므로 많이 확대해도 작은 이동부터 주변 node가 드러난다. 원래 중심 조망으로 돌아오면 다시 서서히 숨기며, 거의 투명한 node는 클릭을 가로채지 않는다. 일반 2단계 간선은 주변 node와 함께 서서히 표시하고, hover·focus한 node의 연결 강조는 전환 중에도 유지한다. 이때 간선은 2단계까지 표시하고 3단계 이후 node는 간선 없이 표시한다. 추가 클릭이나 데이터 재조회는 필요하지 않다. 고정 크기 label은 첫 접근 중에는 숨기고 첫 정지 구간의 400ms 동안 페이드인한다. 중심·1단계 이름은 표시 대상으로 삼되 이 초기 연출과 아래의 겹침 처리 규칙을 따른다. 2단계 이후·주변부 이름은 초기 정지 구간과 모든 배율에서 평소 숨기며 node hover·키보드 초점 또는 간선 hover·초점에서는 대상 node와 양 끝 node의 이름을 표시한다. node 자체의 표시 규칙과 데이터는 유지한다. 동작 줄이기 설정에서는 먼 조망과 확대를 생략한다. 로딩이 끝난 상태에서 graph만 다시 생성되어도 조작 잠금이 풀리며, 이미 끝낸 초기 연출은 반복하지 않는다.

미리보기의 중심 label은 16px, 직접·2단계는 14px다. 긴 이름은 말줄임으로 표시하고 선택·키보드 초점에서 펼친다. 위의 단계·배율 규칙으로 이름 표시 대상을 정한 뒤 겹침을 처리한다. node 좌표와 글자 크기는 유지하고 이름을 node 좌우와 위아래 24px 범위의 가까운 후보에 놓는다. 현재 hover·키보드 초점, 중심, 직접 이웃, 2단계 순으로 우선하며 같은 우선순위에서는 node ID 순서를 사용한다. 가까운 위치에 겹침 없이 놓을 수 없는 일반 이름은 숨긴다. 이름을 숨겨도 node와 키보드 탐색은 유지하고 hover·초점에서 다시 표시한다. 간선 hover·초점은 양 끝 이름을 모두 표시한다. 중심·초점 이름끼리 겹치면 숨기지 않고 초점 이름을 앞에 둔다. 이름 숨김은 초기 연출·단계별 불투명도와 별도로 처리하며 이동·배율·초점 변화로 공간이 생기면 다시 표시한다. 정지 화면은 기존 배치 결과를 재사용한다. node 유형별 색, 활동량별 크기, 2~3단계와 더 흐린 주변부의 데이터, 실제 관계와 복수 근거 선은 유지한다. 근접 조망 밖에서는 단계별 흐릿함을 다시 표시한다.

다크 모드에서 중심에 직접 연결된 일반 간선과 현재 조작 대상의 연결은 파란색 `#72A7FF`, 나머지는 회청색 `#829bb5`이다. 일반 2단계 간선의 기본 불투명도는 0.32로, 직접 연결의 0.90보다 낮게 유지하되 연결 형태를 따라갈 수 있게 한다. 라이트 모드의 회청색은 `#576d83`을 사용한다. 직접 이웃은 본체 불투명도 0.98과 일정한 얇은 테두리로 2단계 이웃보다 선명하게 표시하고 반복 발광하지 않는다. 2단계 node는 유형 색을 유지하되 기본 불투명도를 0.55로 낮춘다. 3단계와 주변부는 유형 색 없이 중립색 실루엣으로 표시하며 기본 불투명도는 각각 0.28과 0.18이다. node·간선 hover 또는 키보드 초점에서는 기존 400ms 전환으로 원래 유형 색과 선명함을 복구하고, 벗어나면 실루엣으로 돌아간다. 거리·진입 페이드와 유형 필터는 그대로 적용한다. 테두리는 hover·필터 강조로 자연스럽게 이어진다. 평소 숨긴 3단계 이후 간선도 해당 node를 hover하거나 키보드로 focus하면 그 node에 연결된 간선만 일시적으로 표시하고 파란색으로 강조한다. 충돌은 강조 여부와 관계없이 빨간색 `#F26D78`과 점선으로 표시하며 범례·접근 가능한 관계 이름·상세 내용에서도 문자로 구분한다. 미리보기에서는 충돌 token도 같은 빨간색을 사용하지만 실패 상태와는 문구·형태로 구분한다. node와 겹치는 간선은 뒤에 가리고 클릭하면 기존 Relation 근거를 연다. 축소나 주변부 조회로 간선이 다시 나타난 직후에도 hover와 선택이 동작해야 하며 지도 갱신이 멈추지 않아야 한다.

미리보기의 node·Relation hover와 키보드 초점은 이름 테두리, node 외곽선과 연결선으로 표시한다. 첫 렌더 프레임의 현재 밝기에서 400ms 동안 강조 상태로 전환한 뒤 일정 밝기를 유지한다. 먼 node도 hover·초점에서는 본체와 이름의 거리·단계 흐릿함을 서서히 해제하고, 벗어나면 현재 조망에 맞는 흐릿함으로 복귀한다. 강조 전환은 현재 대상 node에만 적용하며 다른 node의 외곽선 크기나 밝기를 바꾸지 않는다. 라이트 모드의 외곽선은 유형 색을 가리지 않는 낮은 불투명도로 제한한다. 유형 필터와 데이터 이탈 상태는 초점보다 우선한다. 반복 맥동과 외곽선 크기 진동은 사용하지 않고 전환이 끝나면 강조용 animation frame을 종료한다. 빠르게 대상을 바꾸면 현재 표시 상태에서 이어지며 벗어나면 기본 밝기로 복귀한다. 테마 전환도 외곽선의 현재 밝기를 보존한다. 인식 영역 자체의 크기는 바꾸지 않으며 충돌의 빨간색·점선은 유지한다. 동작 줄이기에서는 즉시 정적으로 강조한다. 중심 전환은 현재 camera 거리를 유지하고 전환 도중 사용한 wheel 배율도 보존한다.

node hover·키보드 초점에서는 연결된 node의 근접 조망 숨김을 400ms 동안 해제해 관계선의 반대편을 표시한다. 연결 node는 단계별 본체 밝기와 이름 표시 규칙을 유지하며 hover 발광은 추가하지 않는다. 초점이 빠지면 현재 밝기에서 조망의 숨김 상태로 복귀한다. 유형 필터를 적용한 node와 관계는 복원하지 않으며 좌표·배율을 바꾸지 않는다. 실제 화면 밖에 있는 좌표를 화면 안으로 이동시키는 동작은 포함하지 않는다.

hover·키보드 초점으로 강조한 일반 연결선은 기본 중심 연결과 구분하도록 다크 모드에서 연한 하늘색 `#bad8ff`, 라이트 모드에서 밝은 파란색 `#548ce0`으로 400ms 동안 전환한다. 초점이 빠지면 현재 색에서 기본 색으로 복귀한다. 사용자 검토에서 색 차이만으로 구분이 충분하다고 확인해 간선 발광 장식은 사용하지 않는다. 충돌 간선은 기존 빨간색과 점선을 유지하며 간선의 페이드와 유형 필터를 따른다.

헤더의 라이트 모드 전환은 지도·이름·간선·panel·근거 dialog에 함께 적용된다. 지도 객체를 다시 만들거나 탐색 상태·배율을 초기화하지 않는다. 기본은 다크 모드이며 선택은 현재 화면에서 유지한다. 밝은 배경에서도 중심 연결은 파란색, 충돌은 빨간 점선으로 구분한다.

#159에서 채택한 화면은 56px 헤더 안에 브랜드·최근 탐색 경로·테마 버튼을 한 줄로 배치하고 현재 중심 경로의 공간을 우선한다. 검색·기간은 지도 왼쪽 위에 유지하고 검색 결과는 입력 바로 아래에 맞춘다. 중복되는 `현재 지도` 제목은 제거하되 중심 이름은 유지하며 node 개수는 표시하지 않는다. 범례는 왼쪽 아래에서 접고 펼친다. 상세 panel은 360px 너비를 유지하고 이름을 22px로 강조하며 유형·맥락을 보조 정보로 둔다. 추천·관계·인사이트 목록은 반복 카드 배경과 테두리 대신 여백·얇은 구분선을 사용하고 hover·키보드 초점 배경으로 선택 가능성을 표시한다. 기존 응답의 연결 이유는 한 줄 말줄임 없이 줄바꿈하며 관계 방향·독립 근거 수·충돌 상태를 유지한다. 패널의 현재 읽기 역할은 아래 #162 계약을 따른다.

미리보기의 데이터 대기 동작은 선택 node와 camera를 고정한 채 주변 node가 바깥쪽으로 최대 2 좌표 단위 이동하는 320ms 준비 동작이다. 긴 대기에서는 멈춰 있고 반복 부유·밝기 맥동은 사용하지 않는다. 응답이 오면 현재 표시 위치와 속도를 이어받아 1200ms 재배치를 시작하고 목표에서 정지한다. 실패·취소 시 고정된 기존 배치로 320ms 동안 복귀한다. 연속 선택은 현재 표시 위치에서 시작하고 동작 줄이기 설정에서는 준비 이동을 생략한다.

미리보기는 기존 개발 DB/API와 100-node 검토 자료로 확인한다. 이 자료는 생성 품질이나 실제 사실의 증거가 아니다. 초기 로딩·조망·확대, 기간 변경, 주변부 cursor 조회, 근거 팝업·인사이트 읽기와 키보드 조작의 기존 계약은 유지한다. 생성 기능·간이 인사이트·검색 개편·모바일·cache·거리 기반 제거는 이 디자인 후보의 범위가 아니다. 기존 기본 화면의 아래 계약과 미리보기의 변경안을 혼동하지 않는다.

## 현재 구현과 제품 경계

브라우저와 PostgreSQL 사이의 유일한 제품 경계는 FastAPI HTTP API다. web은 DB table이나 SQLAlchemy model을 알지 않으며 API 응답을 `web/src/data.ts`에서 화면 모델로 검증·변환한다.

현재 web은 exploration aggregate, node search, Relation 목록과 Evidence Trace를 사용한다. peripheral API도 web에 연결되어 초기 주변부와 추가 page를 조회한다. 저장 인사이트 목록·상세도 API와 web에 연결되어 있고 생성·품질 후속 작업은 #68이 소유한다.

중심 전환에서는 선택 node의 현재 위치로 camera target을 이동하고, 새 응답의 이웃을 그 node 기준의 조밀한 목표 좌표로 한 번 재배치한다. 규칙적인 격자 느낌을 줄이기 위해 node ID로 결정되는 작은 x·y 편차를 주며, 렌더링이나 page 추가 때마다 무작위 값을 다시 뽑지 않는다. 전환 종료 후 좌표 고정을 풀거나 force simulation을 다시 시작하지 않는다. 추가 page에서는 기존 좌표와 node 객체를 유지하고 새 node만 배치하며 새 응답에 없는 node·Relation은 전환 후 장면에서 제거한다. HTML label도 같은 node ID로 재사용하고 이탈·unmount 시 DOM에서 제거해 이전 label이 남거나 중복되지 않게 한다. #114의 이전 데모 비교와 사용자 시각 승인은 별도로 추적한다. node·label 가독성은 #106에서 사용자가 반복 검토한다. 현재 검토 후보는 기존 활동량별 반지름 비율을 유지하되, 너무 크다는 사용자 피드백에 따라 직전 후보보다 core 반지름을 30% 줄인다. label은 HTML/CSS로 표시하며 중심16px·직접/2단계14px를 기준으로 한다. 지도 배치 영역은 desktop panel 바깥의 공간으로 제한하고 graph 간격과 camera 거리를 함께 조정한다. 사용자 승인 전까지 최종 시각 값으로 확정하지 않는다. 빈 map의 primary drag는 pan에 연결하고 회전과 node drag는 비활성화한다. 실제 화면 회귀 검증은 #107에서 추적한다. 첫 진입의 0~99% loading은 API 응답과 graph 준비를 기다린 뒤 intro로 이어지고, 일반 Relation 색·panel 제목과 control의 최소 조작 영역은 기존 디자인 token에 맞춘다. 실제 화면 검증은 #135에서 추적한다. 여러 peripheral page가 누적된 뒤 장면 정리와 세션 위치 cache가 실제로 필요한지는 #136에서 관찰 후 결정한다. 아래 시각·상호작용 절은 구현 완료 보고가 아니라 유지해야 할 제품 계약이며 현재 차이는 해당 Issue로 추적한다.

## 현재 HTTP 읽기 계약

| 화면 흐름 | HTTP endpoint | 주요 응답 | application service | DB query 경로 | web 상태 |
| --- | --- | --- | --- | --- | --- |
| 초기 탐색·중심 이동·시간 범위 변경 | `GET /api/v1/exploration/{center_node_id}?time_window=...` | 중심 맥락, 활성 graph, 구조화 추천, 후속 질문 2개 | `get_exploration` | 최신 node별 READY → 검색 문서·basis·context·질문 → 공개 relation·Claim·Evidence Trace 집계 | 연동 완료 |
| node 검색 | `GET /api/v1/nodes/search?q=...&limit=5` | node 이름·유형과 `EXACT_ALIAS | FULL_TEXT` 이유 | `search_nodes` | 활성 merge 해소 → 최신 READY 검색 문서 → alias 또는 `simple` expression GIN | 연동 완료 |
| node의 공개 Relation | `GET /api/v1/nodes/{node_id}/relations?cursor=...&limit=20` | 상대 node, relation 유형, 지지 근거 묶음 수, 충돌 여부 | `list_node_relations` | 최신 READY 검색 문서·basis → relation → 지지 Claim → Observation → Source Document | web 연동 구현 |
| Relation 근거 | `GET /api/v1/relations/{relation_id}/evidence?cursor=...&limit=10` | Claim stance, source metadata, quote와 locator | `list_relation_evidence` | Claim Relation → Claim Observation → Observation → Source Document | web 연동 구현 |
| 주변부 추가 조회 | `GET /api/v1/exploration/{center_node_id}/peripheral?time_window=...&cursor=...&limit=20` | `AMBIENT` node, 활성 graph·현재/이전 page 사이의 실제 Relation, 다음 cursor | `list_peripheral_nodes` | exploration 활성 graph → 최신 READY 공개 node의 다음 page → 활성 graph와의 relation | web 연동 구현 |
| 저장 인사이트 목록 | `GET /api/v1/nodes/{node_id}/insights?time_window=...` | slot·제목·독립 근거 수 | `list_node_insights` | 최신 READY의 NODE_INSIGHT SUCCESS → 전체 basis 공개 재검증 → window별 결과 | web 연동 구현 |
| 저장 인사이트 상세 | `GET /api/v1/insights/{insight_id}` | summary·synthesis·caveat·역할별 Claim과 Trace | `get_insight` | 현재 목록과 같은 공개 검사 → Claim Observation → Observation → Source Document | web 연동 구현 |

응답 DTO는 DB table 모양을 그대로 노출하지 않는다.

| 응답 | 필드 |
| --- | --- |
| exploration | `center_node_id`, `context_text`, `graph.nodes[]`, `graph.relations[]`, `recommendations[]`, `followup_questions[]` |
| graph node | `node_id`, `name`, `node_type { code, display_name }`, `tier`, `activity_evidence_group_count` |
| graph Relation | `relation_id`, `source_node_id`, `target_node_id`, `relation_type_display_name`, `directionality: DIRECTED | SYMMETRIC`, `supporting_evidence_group_count`, `has_conflict` |
| recommendation | `target_node`, `reason_code`, nullable `via_node_id`, 직접 근거가 있을 때만 `supporting_evidence_group_count`, 실제 연결의 `path[]` |
| follow-up question | `slot`, `question_text`, `target_node_id` |
| search | `items[] { node_id, name, node_type }` |
| node Relations | `items[] { relation_id, other_node, source_node_id, target_node_id, directionality, relation_type_display_name, supporting_evidence_group_count, has_conflict }`, `next_cursor` |
| Relation Evidence | `items[] { claim_text, stance, source, quote_text, locator }`, 전체 공개 trace의 `trace_count`, `next_cursor` |
| Evidence source·locator | `source { title, publisher_name, published_at, published_precision, canonical_url }`, `locator { paragraph_number, start_char, end_char }` |
| peripheral | `graph.nodes[]`, `graph.relations[]`, `next_cursor`; 모든 node의 `tier`는 `AMBIENT` |

인사이트 목록은 `items[] { insight_id, slot, title, evidence_group_count }`, 상세는 같은 필드와 `summary`, `synthesis`, `caveat`, `claims[] { claim_id, claim_text, role, traces[] { source, quote_text, locator } }`를 반환한다. role은 `KEY_CLAIM | SUPPORTING_CLAIM | CONTRASTING_CLAIM`이며 source와 locator는 기존 Evidence Trace 계약을 재사용한다. 목록은 slot 순서로 최대3개이고 근거 수는 저장 결과의 `as_of_at`을 기준으로 선택한 90일·365일 범위 `[as_of_at - 기간, as_of_at)`에 게시된 연결 Claim 근거의 `COUNT(DISTINCT evidence_group_id)`다. 원문 Trace는 연결 Claim의 전체 출처를 보여주므로 기간 밖 출처가 포함되면 표시 근거 수와 Trace 수는 다를 수 있다. 상세의 Claim ID는 화면 항목 식별에만 사용하고 사용자 문구로 표시하지 않는다.

`time_window`는 필수이며 `RECENT_90_DAYS | RECENT_1_YEAR | ALL_TIME`을 허용한다. exploration과 관계·Claim·Evidence 조회는 출처 게시일이 선택 기간에 드는 공개 근거만 사용하며 `ALL_TIME`은 기준 시각 이전의 게시일 미상 자료도 포함한다. 선택 기간에 중심 node의 공개 근거가 없으면 중심만 activity 0으로 반환하고 화면은 빈 지도 안내와 `전체 기간 보기`를 제공한다. 기간을 자동 변경하지 않는다. exploration은 중심 1개, 직접 이웃 최대 12개, 중요한 2단계 이웃 최대 18개, 실제 3단계 이웃 최대 20개와 활성 graph Relation 최대 60개를 반환한다. 후보는 지지 독립 근거 묶음 수 내림차순, 선택 기간 활동량 내림차순, 내부 ID 오름차순으로 정렬한다.

추천은 backend가 `DIRECT | TWO_HOP | AMBIENT`, 대상 node, 선택적 경유 node와 적용 가능한 근거 수를 반환한다. 사용자에게 보이는 한국어 추천 문장은 frontend가 작성한다. 현재 후속질문은 #162에 따라 기간별 질문과 근거가 연결된 답변을 4개씩 읽으며 총개수는 고정하지 않는다. 질문을 선택하면 답변을 펼치고 지도 중심을 이동하지 않는다. 기존 slot 1·2와 `target_node_id`를 가진 이동형 데이터와 API는 전환 중 보존하지만 현재 패널 계약으로 사용하지 않는다. 질문 선택이나 node 클릭으로 모델을 호출하지 않는다. 생성 작업은 #129, 실제 자료로 진행할 후속 품질 검토는 #181에서 관리한다.

PostgreSQL의 `bigint` ID는 JavaScript 정밀도 손실을 막기 위해 모든 HTTP JSON에서 문자열로 보낸다. DB 내부 ID, 검색 순위 점수, Claim·Observation·Source Document·evidence group ID와 의미가 섞인 confidence는 화면에 노출하지 않는다.

`cursor`는 정렬 위치와 endpoint scope를 담아 server가 발급하는 불투명한 문자열이다. client는 내용을 해석하거나 만들지 않고 같은 resource와 query에 그대로 재전송한다. server는 cursor 종류, version, scope와 값을 검증하며 다른 node·Relation·시간 범위에 사용된 cursor는 `422 INVALID_REQUEST`로 거절한다.

| 목록 | 기본·최대 limit | 안정 정렬 |
| --- | --- | --- |
| peripheral node | 20·50 | node 내부 ID 오름차순 |
| node Relation | 20·50 | 지지 독립 근거 묶음 수 내림차순, Relation 내부 ID 오름차순 |
| Relation Evidence Trace | 10·50 | 게시 시점 내림차순(`NULLS LAST`), Source Document ID 내림차순, Observation ID와 Claim ID 오름차순 |

공통 오류 본문은 `{"error":{"code":"...","retryable":false}}`다. 잘못된 ID, query, limit, time window와 cursor는 `422 INVALID_REQUEST`다. 공개 node가 없으면 exploration·peripheral·Relation 목록은 `404 NODE_NOT_FOUND`, 공개 Relation 근거가 없으면 Evidence Trace는 `404 RELATION_NOT_FOUND`를 반환한다. node는 공개 가능하지만 필요한 READY 결과가 없으면 exploration·peripheral·Relation 목록은 `503 PUBLICATION_NOT_READY`와 `retryable: true`를 반환한다.

일반 사용자 조회에는 현재 공개 가능한 최신 READY 결과만 포함한다. `promotion_status = COMMITTED`, `publication_status = READY`, 지식 상태 `EVIDENCE_VERIFIED | HUMAN_VERIFIED`와 열린 `BLOCKING` lint 부재를 다시 확인한다. selected 검색 문서의 모든 `search_document_basis`가 계속 공개 가능한지 재검증하는 것은 제품 불변성이다. search와 저장 인사이트 경로뿐 아니라 exploration, peripheral, node Relation과 Relation Evidence Trace가 사용하는 공통 공개 경로도 이 basis 재검증을 수행한다. 새 publication이 실패해도 이전 READY 결과가 있으면 계속 제공한다.

현재 search는 exact alias → `identity_text` 단어 FTS → `knowledge_text` 단어 FTS의 세 bucket을 순서대로 반환한다. exact alias bucket은 `node_id ASC`, 각 FTS bucket은 `ts_rank_cd DESC, node_id ASC`로 정렬하고 활성 merge를 해소한 같은 canonical Node는 전체 결과에서 한 번만 반환한다. HTTP 응답과 web 후보에는 `node_id`, 이름과 유형만 포함하며 검색 이유를 노출하지 않는다. READY, selected `search_document_basis`와 열린 `BLOCKING` lint 공개 필터를 유지하고 `node_context.context_text`는 검색 입력으로 사용하지 않는다. 실제 한국어 단어 FTS 누락 사례가 확인될 때만 #80에서 tokenizer, `pg_trgm` 또는 BM25 같은 확장을 다시 검토한다.

## 승인된 지식 선정·의미 보존 원칙

이 절은 [#127에 보완한 사용자 결정](https://github.com/studylida/ontology-map/issues/127#issuecomment-5580300060)을 정리한다. 제품 worker·자동 저장·운영 품질이 구현됐다는 뜻은 아니다. 데이터 저장 형태는 [논리 스키마](../data/logical-schema.md), 모델과 시험 구성은 [구현 스택](../development/implementation-stack.md#에이전트-역할과-모델)이 소유한다.

- 특정 기업·인물·기술·제품·사건의 구체적인 활동·관계·속성을 선정한다. 명시적인 방문·관람도 포함할 수 있지만 이를 협력·지지로 추론하지 않는다. 추가 해석은 인사이트와 구별한다.
- 기술 적용·성능·호환성·출시 정보는 대상과 조건을 보존한다. 표의 색상·무게·포트 수를 모두 복제하지 않고 기술·사업 맥락에서 비교·변화·적용 조건의 핵심인 경우 선정한다. 근거 없는 홍보 표현·일반 정의·단순 전달 방식은 생략할 수 있다.
- 계획·추진·예정도 선정할 지식이다. 아직 실행되지 않았다는 이유로 제외하거나 계획을 현재 시행 사실로 바꾸지 않는다. 시점·수량·부정·제한·목적처럼 의미를 바꾸는 조건은 해당 사실에 연결한다.
- 실제 발언자의 이름·당시 직책과 발언 속 행동·계획의 주체를 함께 보존한다. 직책과 업무 맥락이 있는 발언은 조직 입장을 표현할 수 있지만 발언자를 지우지 않는다. 사실 주장·계획·예측·평가와 객관적 진실 여부를 혼동하지 않는다.
- 독립 적용되는 제품별 사실은 분리하고 공동 행위는 공동 의미를 유지한다. 필요한 지식의 선정 여부와 현재 ontology로 저장할 수 있는지는 별도로 검토한다.
- 의미 보존·필수 정보·독립 사실 분리·근거 충분성·인용 범위·내부 출력 계약을 구별해 검증한다. 특정 정보의 생략 허용을 Claim 전체의 통과로 확대하지 않는다. 기존 점수의 재평가와 새 출력의 모델 개선도 구별한다.

이 최소 원칙은 #110의 전체 출처·적재·보존 정책과 #126의 Relation·attribute·Topic 목록 승인을 대신하지 않는다. #139의 진단 역할·판정 상태와 정답표는 시험용이며 사용자 화면이나 DB에 새 객체를 추가하는 계약이 아니다.

## 반영된 화면 개선안

2026-09-08에 [#159](https://github.com/studylida/ontology-map/issues/159)의 디자인 후보 구현·검토가 승인되어 기본 화면에 반영됐다. 상세 패널·헤더·목록의 정보 위계와 연결 이유를 정리하고, 이름을 node 가까이에 배치하되 겹치는 일반 이름은 숨기고 hover·초점에서 다시 보여준다. 반복 맥동은 부드럽게 밝아진 뒤 일정 밝기를 유지하는 강조로 바꿨다. 상세 수치와 사용자 시각 검토 기록은 #159에서 관리한다.

기존 색상, 초기 접근·정지·확대와 이름 페이드인, 관계·근거·인사이트 읽기는 유지한다. 헤더의 `ontology-map` 브랜드는 `비스텔리젼스 홈으로 이동`이라는 접근 가능한 버튼이며, 현재 기간을 유지하고 node 10으로 이동한다. 이미 node 10이 중심이면 새 API 요청이나 history 항목을 만들지 않는다. 후속 질문의 간이 인사이트 전환·cache·거리 기반 제거·모바일은 이번 개선 범위가 아니며 공개 API·DB schema·의존성 변경도 없다.

## Colors

배경은 `background` 하나를 기준으로 하고 panel은 `surface`, overlay와 입력 요소는 `surface-elevated`를 사용한다. 계층은 밝은 배경을 새로 만드는 대신 표면 색과 `border`로 표현한다.

`text-primary`는 제목과 본문, `text-secondary`는 metadata와 보조 설명에 사용한다. 본문 색을 투명하게 만들어 중요도를 조절하지 않는다. 지식맵의 밝기와 불투명도는 중심·활성 graph·주변부 같은 탐색 상태만 구분한다. 정보의 게시 시점은 상세 panel과 Evidence Trace에서 명시한다.

`interactive`는 링크, 선택 가능한 control과 주요 action에 사용한다. `focus-ring`은 키보드 focus 전용이며 `conflict`는 충돌 관계와 충돌 설명에만 사용한다. `danger`는 실행 실패와 복구할 수 없는 오류, `ready`는 공개 준비 완료 상태에 사용한다.

공개 node type은 다음 색을 사용한다.

| 유형 | 색 | 보조 표기 |
| --- | --- | --- |
| 사람 | `node-person` | `사람` label 또는 사람 icon |
| 회사 | `node-company` | `회사` label 또는 건물 icon |
| 기술 | `node-technology` | `기술` label 또는 도구 icon |
| 주제 | `node-topic` | `주제` label 또는 tag icon |
| 사건 | `node-event` | `사건` label 또는 calendar icon |

node type은 색만으로 구분하지 않는다. tooltip, 상세 panel과 keyboard accessible name에 유형을 포함하고 범례에는 색과 문자 label을 함께 표시한다.

## Typography

별도 웹 폰트를 내려받지 않고 운영체제의 system sans-serif를 사용한다. 숫자, hash, code와 정밀한 시점처럼 기술 값에만 `technical` typography를 제한적으로 사용한다.

화면 제목은 `screen-title`, node 상세 제목은 `panel-title`, 설명과 Evidence Trace는 `body`, node label과 metadata는 `label`을 사용한다. 본문을 14px보다 작게 만들지 않고 긴 인용문은 충분한 줄 간격을 유지한다.

굵기만으로 의미를 구분하지 않는다. 제목 수준, 간격, label과 상태 문구를 함께 사용한다. 전체 대문자는 짧은 ontology code 외에는 사용하지 않는다.

## Layout

화면은 viewport를 채운다. 56px header 아래에서 지식맵이 남은 영역 전체를 사용하며 검색과 시간 범위는 맵의 왼쪽 위에 하나의 surface block으로 겹쳐 놓는다. 이 block은 1px border와 `rounded.lg`를 사용하며 검색 결과도 같은 block 안에서 펼친다. desktop 전체 너비는 padding을 포함해 360px로 고정한다.

너비가 1024px 이상이면 상세 panel은 오른쪽에 420px 너비로 겹쳐 표시한다. panel을 열어도 중심 node와 주요 직접 이웃을 가리지 않도록 카메라 중심을 남은 map 영역에 맞춘다.

너비가 768px 이상 1024px 미만이면 상세 panel은 화면 너비의 46%를 사용하되 420px을 넘지 않는다. 너비가 768px 미만이면 검색은 좌우 16px 여백을 두고, 상세 정보는 전체 너비의 bottom sheet로 표시하며 높이는 viewport의 72%를 넘지 않는다.

현재 실행 가능한 web은 너비 1024px 이상의 desktop 핵심 화면을 우선 검증한다. 1024px 미만의 반응형 동작은 이 계약을 유지하되 별도 범위에서 구현한다.

간격은 front matter의 4·8·12·16·24·32px 단계만 사용한다. map 위 overlay 사이에는 최소 12px, panel section 사이에는 24px, 관련된 label과 값 사이에는 8px을 둔다.

지도의 활성 graph에는 중심 node와 직접·중요한 2단계·실제 3단계 이웃을 처음부터 표시한다. 3단계는 한층 낮은 밝기로 구분하고, 그 밖의 공개 node는 더 어두운 형체로 표시한다. 주변부는 독립 node도 포함하며 실제 경로가 없는 node에 hop 수나 관계선을 붙이지 않는다. 초기 주변부 20개를 자동 조회한다. 이후 사용자가 바깥쪽으로 pan할 때 현재 화면에 반 화면만큼의 여유를 더한 범위가 graph 경계에 닿으면 이동 종료를 기다리지 않고 다음 page를 미리 요청한다. 사용자 축소도 즉시 다음 page를 요청하며 한 조작당 최대 한 page로 제한한다. 확대·프로그램 camera 이동·page 완료만으로 추가 요청하지 않는다. cursor는 한 번에 하나씩 요청하고 실패는 명시적으로 재시도한다. 중심·기간 변경 시 이전 요청과 cursor를 버린다. 초기 연출 중 도착한 page는 연출 종료까지 표시를 미루며, 같은 중심의 추가 page는 기존 좌표와 camera를 유지한다. 초기 조망은 현재 확보한 graph의 조망이며 전체 DB의 고정 지도나 전체 지도 version을 뜻하지 않는다.

지식맵은 x·y 평면의 군집 배치를 주된 구조로 사용하는 얕은 2.5D 장면이다. 초기 깊이 범위는 중심 기준 약 ±32로 제한하고 z축은 node 겹침과 앞뒤 구분에만 사용한다. 회전은 제공하지 않으며 빈 map 영역을 drag하면 평행 이동하고 wheel이나 trackpad로 확대·축소한다.

기본 카메라 배율에서는 중심 node, 직접 이웃과 중요한 2단계 이웃의 대부분을 한 화면에서 함께 탐색할 수 있어야 한다. node 간격은 label과 발광 core가 겹치지 않는 범위에서 조밀하게 유지하며, 다른 node를 찾기 위해 매번 축소한 뒤 다시 확대하도록 만들지 않는다. node 사이 거리는 탐색을 위한 layout 값일 뿐 관계 강도나 다른 의미를 나타내지 않는다.

### Motion

미리보기 node·간선의 hover 강조는 실제 첫 표시 frame의 현재 밝기에서 400ms 동안 가속·감속하며 강조 밝기로 전환한 뒤 느린 맥동을 시작한다. node 외곽선·간선 색과 불투명도·이름 테두리를 함께 전환한다. 강조가 처음부터 최대 밝기로 나타나지 않으며, 동작 줄이기에서는 즉시 정적으로 강조한다.

control의 hover·focus 강조와 panel의 짧은 상태 전환은 160ms, 시간 범위처럼 관측 활동량이 바뀔 때의 node 크기 전환은 320ms, 새 중심 node로 이동하는 전체 전환은 1200ms를 기본으로 한다. 기간 변경의 크기 전환은 camera와 기존 node 좌표를 유지하며 현재 표시 크기에서 시작한다. 도중에 기간을 다시 바꾸면 새 목표로 전환하고, 주변부 page가 도착해도 같은 목표의 종료 시점을 늘리지 않는다. 1200ms 전환은 천천히 출발해 중간 구간에서 가속하고 도착 전에 다시 감속하는 ease-in-out 하나를 사용한다.

node를 선택하면 카메라는 선택한 node의 현재 위치를 새 중심으로 바라보며 이동한다. 같은 1200ms 동안 선택한 node를 그 위치에 고정하고 새 이웃을 한 번 계산한 목표 좌표로 보간한다. 종료 후 force simulation을 다시 시작하지 않는다. 카메라 위치, node 위치, core 밝기, halo와 외곽선, node와 label의 불투명도, 관계선 색과 불투명도는 하나의 진행률로 보간한다. 기존 graph와 새 graph에 함께 속한 node와 관계선은 같은 객체와 위치를 이어서 사용하고, 진입 요소는 불투명도 0에서 시작하며 이탈 요소는 정확히 0까지 낮춘다. 전체 canvas나 panel을 다시 그리는 fade는 적용하지 않는다.

node 선택 직후 API 응답을 기다리는 동안에는 주변 node가 작게 떠 움직이며 준비 중임을 보여준다. 클릭한 node와 camera는 고정하고, 관계선 끝점은 화면에 보이는 node 위치를 따른다. 이 움직임은 표시 위치에만 적용하며 force나 저장 좌표를 변경하지 않는다. 응답이 도착하면 그때 보이는 위치에서 1200ms 중심 전환으로 이어지고, 빠른 재선택·오류·unmount 때 이전 준비 동작을 정리한다. `prefers-reduced-motion`에서는 준비 동작을 생략한다.

전환 중에는 기존 중심을 확정 상태로 유지하고 새 중심은 pending 상태로만 다룬다. header, URL, 탐색 경로와 상세 panel의 동적 내용은 전환이 끝난 뒤 새 중심으로 함께 갱신한다. `재배치 중` 같은 별도 문구로 내용을 바꾸지 않으며 graph 영역은 전환 시작부터 종료까지 busy 상태를 알린다.

부분 graph의 목표 좌표는 전환 시작 시 한 번 계산한다. 전환 종료 시 force를 다시 시작하지 않으며 이탈 node와 관계선, HTML label은 불투명도가 0이 된 뒤 제거한다. 남은 node의 위치는 그대로 유지하고 장식 목적으로 계속 흔들지 않는다. `prefers-reduced-motion`에서는 같은 상태를 한 번에 적용하고 반복 animation을 끈다.

전환 시작 시 현재 node 위치에서 관계선의 source와 target을 새 graph에 연결하고 같은 진행률로 node와 관계선 끝점을 이동한다. 각 frame에서 위치와 속도를 고정하며 force simulation을 시작하지 않는다. 관계선 좌표가 바뀌면 클릭 판정 범위도 갱신해 이동 후에도 Relation 정보를 열 수 있게 한다.

## Elevation & Depth

UI panel의 깊이는 `surface`, `surface-elevated`와 1px `border`로 구분한다. 넓고 흐린 shadow, glass blur와 panel 배경 gradient는 사용하지 않는다. 지식맵 canvas에는 node 군집의 공간 깊이를 읽을 수 있도록 낮은 강도의 거리 안개, vignette와 bloom 후처리를 사용할 수 있다.

3D 깊이는 관계 묶음을 읽기 위한 보조 표현이다. 제한된 z축 차이는 겹침을 줄이기 위한 시각 배치일 뿐이며 node 사이 거리와 높이는 관계 강도, 조직 서열, 시간이나 인과를 뜻하지 않는다.

모든 node는 유형 색을 유지하는 작은 발광 core와 낮은 강도의 halo를 가질 수 있다. 중심 node는 현재 탐색의 기준임을 바로 알아볼 수 있도록 활동량이나 hover 상태와 무관하게 장면에서 가장 밝게 유지한다. 직접 이웃은 중요한 2단계 이웃보다 본체와 정적인 테두리를 선명하게 표시하고, 활성 graph 밖의 주변부만 한 단계 낮게 표시한다. 선택하거나 hover한 node와 그 직접 경로는 중심 node보다 밝아지지 않는 범위에서 bloom과 외곽선을 일시적으로 강화한다. bloom 반경이나 halo 크기에 confidence, 근거 수, 게시 시점과 같은 별도 의미를 부여하지 않으며 node label과 관계선의 대비를 낮추지 않는다.

현재 중심 node에 직접 닿은 Relation만 가장 선명하게 표시하며 직접 이웃끼리의 선은 이 강조에 포함하지 않는다. 나머지 선은 2단계·3단계·주변부 순으로 대비를 낮추되 hover·focus 강조와 충돌 표시는 유지한다. 관계선과 화살표는 모든 node 뒤에 그려 node와 겹치는 부분이 비치지 않게 한다. 발광 halo와 장식 외곽선은 클릭을 가로채지 않으며 실제 node 표면과 관계선을 각각 선택할 수 있다.

키보드로 지도 node·Relation 목록에 진입하면 목록을 지도 내부 여백에 맞춰 표시한다. 가용 너비와 높이를 넘지 않도록 제한하고 긴 이름은 줄바꿈하며, 항목의 최소 44px 높이와 목록 내부 스크롤을 유지한다. 팝업을 닫으면 선택한 항목으로 focus가 복귀한다.

## Shapes

일반 control과 입력은 `rounded.md`, tooltip과 작은 label은 `rounded.sm`, 큰 panel과 sheet는 `rounded.lg`를 사용한다. `rounded.full`은 status badge처럼 짧고 독립된 상태 표시에만 사용한다.

모든 클릭·터치 target은 최소 44px을 확보한다. 작은 icon 자체가 44px일 필요는 없지만 icon을 포함한 button의 hit area는 이 기준을 충족해야 한다. 기간 선택·retry·검색 입력·탐색 경로도 최소 44×44px 조작 영역을 적용한다. 일반 Relation과 범례는 `relation` token을, 상세 panel 제목은 `panel-title` typography를 사용한다.

관계선은 독립 근거 묶음마다 1px core 필라멘트 하나를 같은 경로 주변에 겹쳐 하나의 관계 묶음으로 표현한다. 기본 관계의 필라멘트는 실선이고 충돌 관계의 모든 필라멘트만 `conflict` 색의 점선을 사용한다. 모든 필라멘트는 source node의 정확한 중심에서 시작해 target node의 정확한 중심으로 들어가며 곡선의 중간 control point만 벌린다. z축 위치와 node의 불투명도에 관계없이 관계선 묶음은 node 원형 안에서 완전히 가려져야 한다. node 표면 아래에 배경색의 불투명 가림 glyph를 먼저 그리고, 가림 glyph와 node 표면 및 label을 관계선과 같은 투명 렌더 단계의 더 높은 순서로 그린다. 외곽 halo는 필라멘트 수를 알아보기 어렵게 만들지 않으며 선택 경로에서도 node와 label을 가리지 않는 범위에서만 밝아진다. 단순한 시각적 다양성을 위해 선 모양이나 node geometry를 늘리지 않는다.

## Components

### Header와 검색

header는 56px 높이의 단색 분석 도구 bar로 유지한다. 작은 별자리형 product mark, `ontology-map`과 현재 세션의 최근 탐색 경로만 표시한다. product mark와 `ontology-map`은 `비스텔리젼스 홈으로 이동` 버튼이며 활성화하면 선택 기간을 유지한 채 기본 중심 Node 10으로 기존 지도 전환과 URL history를 사용해 이동하고, 이미 기본 중심이면 다시 조회하지 않는다. 화면 이름, 부분 graph 범위와 공개 상태처럼 본문에서 이미 알 수 있는 정보는 반복하지 않는다. 탐색 경로는 현재 node를 포함해 최근 4개까지만 표시하고 이전 node를 선택하면 그 위치로 돌아가며 이후 경로를 제거한다. 현재 node는 link가 아닌 `aria-current` 상태로 표시하고 새로고침하면 URL의 현재 중심부터 경로를 다시 시작한다. 검색, 시간 범위와 필요한 map control은 지도 위 overlay에 둔다. navigation이 실제로 생기기 전에는 빈 menu와 미래 기능 entry를 만들지 않는다.

검색 panel에는 `노드 검색`, 현재 중심 주변의 표시 node 수와 시간 범위만 둔다. 검색 후보에는 node 이름과 유형만 표시하고 순위 이유나 내부 점수는 노출하지 않는다. 검색 input과 결과는 keyboard로 이동하고 선택할 수 있어야 하며, 후보를 선택하면 node 클릭과 같은 중심 이동을 실행한다. 직접 이웃과 2단계 이웃의 개별 수는 기본 화면에 반복하지 않는다.

지식맵 범례는 node 유형 색을 요약한 한 줄 상태로 시작하고 `범례` button으로 전체 설명을 펼친다. 펼친 범례에는 node 유형, 독립 근거 수에 따른 필라멘트와 충돌 관계를 표시하며 그래프의 해석 규칙을 별도 점수로 바꾸지 않는다.

### Knowledge map

모든 공개 node는 pointer와 keyboard로 선택할 수 있고 선택하면 새 중심이 된다. 선택한 node의 현재 world position을 유지한 채 카메라 중심과 부분 graph가 함께 전환되며, 새 중심 기준의 직접 이웃과 중요한 2단계 이웃을 다시 계산한다. accessible name에는 node 이름과 유형을 포함한다.

Relation 방향 표현과 Evidence Trace 상호작용은 #118의 승인된 후속 계약을 따른다. `DIRECTED` Relation만 target 방향 화살표를 표시하고 `SYMMETRIC` Relation에는 화살표를 표시하지 않는다. Relation 선을 hover하거나 focus하면 선과 양쪽 node를 함께 강조하고 관계명과 근거 수를 보여준다. graph Relation과 상세 panel의 Relation 행은 같은 Evidence Trace dialog를 열며 중심 node는 바꾸지 않는다. keyboard 사용자는 접근 가능한 Relation button 목록에서 같은 정보와 dialog에 접근한다. exploration과 peripheral의 graph 응답은 frozen Relation type revision의 `directionality`를 전달한다. Node Relation 목록도 `source_node_id`, `target_node_id`, `directionality`를 전달하며 panel을 열 때 조회한다. Evidence Trace는 Relation 선택 때 조회하고 두 목록은 server cursor가 있을 때 더 보기를 제공한다. 오류와 재시도, 실제 연동 검증은 #118에서 추적한다.

주변부 공개 node는 중심과 1·2단계 이웃보다 작고 어둡게 보이되 선택 가능성을 잃지 않는다. 주변부 node와 활성 graph 사이에 실제 Relation이 있으면 낮은 불투명도의 관계선을 이어서 2단계 이웃 바깥의 탐색 경로를 보여준다. Relation이 없는 node나 검색으로만 정한 대상에는 관계선을 만들지 않는다. 주변부 node를 선택하면 현재 위치에서 같은 중심 이동을 시작하고, 해당 node 기준의 활성 graph와 주변부를 다시 계산한다.

제품은 임의의 공개 node 1,000개를 먼저 불러와 전체 graph처럼 보이게 만들지 않는다. server가 발급한 opaque cursor를 사용한 주변부 증분 조회는 backend에 구현되어 있고 #115에서 cursor 없는 첫 page와 후속 `next_cursor`를 web에 연결했다. 주변부 선조회 시점과 범위는 위 지도의 활성 graph 계약을 따른다. 프로그램 camera 이동·page 완료 자체는 추가 조회를 시작하지 않는다. 요청은 한 번에 하나만 진행하고 ID로 병합하며 실제 응답의 Relation만 표시한다. 오류 뒤에는 같은 cursor를 명시적으로 재시도하고, 중심이나 기간을 바꾸면 이전 요청과 누적 주변부를 초기화한다. 실제 API·DB·화면 검증은 아직 대기 중이다. 여러 page가 누적됐을 때 멀어진 주변부를 장면과 force 계산에서 제외하거나 세션 위치를 cache해야 하는지는 현재 필수 구현 계약으로 확정하지 않는다. #115 구현 뒤 실제 여러-page 탐색에서 문제가 재현될 때 #136이 제거·보호·유예·cache 초기화의 최소 경계를 정하며, 문제가 없으면 해당 정리와 cache를 구현하지 않는다. 이 증분 로드는 서버에 저장된 지도 좌표나 기준 DB를 전제하지 않는다.

홈페이지 첫 진입의 제품 계약은 graph를 준비하는 동안 viewport 전체에 단색 dark loading 화면을 표시하고 중앙에 `Loading`, `-- 42% --` 형식의 진행률과 2px progress bar만 두는 것이다. 진행률은 진입마다 강도를 정한 두 속도 파동을 가속·감속 곡선에 더해 1.4초 동안 불규칙한 속도로 0%에서 89%까지 이동하고 graph 준비가 끝날 때까지 89%를 유지한다. 준비가 끝나면 90%, 95%, 99%를 짧게 표시한 뒤 200ms 동안 loading 화면을 숨기며 100%는 표시하지 않는다. API 응답이 빨라도 1.4초 ramp를 마치며 graph 준비가 늦으면 89%에서 기다린다. 초기 오류에서는 loading을 닫고 오류·retry를 제공한다. reduced motion에서는 ramp와 fade를 생략하고 준비 후 즉시 intro로 이어진다. 재시도와 이후 탐색에서 전체 loading을 다시 시작하지 않는다. 숫자와 막대는 같은 진행률을 사용하고 막대에 별도의 지연 transition을 두지 않는다. 디자인 미리보기에서는 하단에 실제 제공하는 조작법 한 줄을 진입마다 무작위로 골라 유지한다. 문구를 읽기 위해 로딩을 연장하지 않는다.

loading 화면이 사라지면 첫 exploration 응답의 부분 graph를 화면에 맞춰 한 번 조망하고 720ms 동안 유지한 다음 기본 중심 node로 1200ms 동안 확대한다. 중심 node는 전체 조망부터 화면 중앙에 고정하고 확대 중에는 camera target과 node 위치를 바꾸지 않은 채 camera 거리만 줄인다. node 배치와 그래픽 준비는 loading 중에 끝내고 초기 force warmup이나 간선 곡선 재생성을 확대 중에 반복하지 않는다. BISTelligence node가 fixture에 없는 검증 예시에서는 SK하이닉스를 기본 중심으로 사용한다. 이 조망은 저장 좌표나 기준 DB를 뜻하지 않는 일시적인 intro 상태이며, intro가 끝나면 같은 동적 부분 graph 탐색을 유지한다. #134는 loading 종료 뒤 현재 GraphCanvas의 이 intro가 정확히 한 번 시작되는지도 함께 검증한다.

주변부 선조회는 camera의 시야각·화면 비율·확대배율로 화면 바깥 여유 범위를 계산하되, 많이 확대해도 여유가 배치 두 칸(x 96·y 56 장면 단위)보다 작아지지 않게 하고 x·y축의 바깥 이동을 각각 판정한다. 한쪽 가장자리에 머문 채 다른 방향으로 이동해도 조회할 수 있다. 조작 종료 후 감속이나 새 page의 도착만으로 다음 page를 연속 조회하지 않는다. 느린 응답이나 빠른 장거리 이동에서는 선조회 범위를 넘어갈 수 있으며, 전체 DB 선조회나 거리별 cache는 추가하지 않는다.

node는 클릭하거나 keyboard로 선택해 중심을 바꾸지만 직접 끌어 배치할 수 없다. 빈 map 영역의 drag는 항상 카메라 평행 이동에만 사용하며, node drag로 force simulation을 다시 시작하거나 navigation control을 점유하지 않는다.

node 크기는 선택 기간에 게시된 출처가 뒷받침하는 `근거 확인됨` 또는 `사람 확인됨` Claim의 독립 근거 묶음 수를 나타낸다. 같은 Claim과 같은 독립 근거 묶음이 여러 경로로 같은 node에 도달해도 한 번만 센다. 크기 범위는 가장 작은 node와 가장 큰 node가 지름 기준 1:2.2를 넘지 않게 제한하고, 근거 활동량이 낮아도 label과 선택 가능성을 유지한다.

node core, halo와 bloom은 상호작용 상태를 나타내는 하나의 밝기 체계로 다룬다. 중심 node는 별도의 영구 강조 단계로 두고 현재 장면에서 가장 밝게 유지한다. 직접 이웃은 중요한 2단계 이웃보다 선명하게 표시하고 주변부는 더 낮춘다. 선택·hover 강조는 사용자가 초점을 옮기는 동안에만 추가하되 중심 node의 밝기를 넘지 않는다. 선택한 node가 새 중심으로 전환되면 영구 강조도 그 node로 이동하고 이전 중심은 활성 graph에서 맡은 역할에 맞는 밝기로 돌아간다. bloom이 node 크기 비율을 흐리거나 인접 node가 하나의 덩어리처럼 합쳐질 정도로 번지지 않게 한다.

관계는 하나의 굵은 선을 늘리는 대신 독립 근거 묶음 수와 같은 개수의 1px 필라멘트를 겹쳐 표시한다. 필라멘트가 1개에서 5개까지 늘어날 때는 중간 control point 간격을 1.2px로 유지하고, 5개를 넘으면 전체 폭을 4.8px로 고정한 채 간격만 줄여 더 촘촘하게 보이게 한다. hover·focus tooltip과 상세 panel에는 정확한 독립 근거 묶음 수를 표시한다. 관계 종류나 확신 점수를 필라멘트 수와 전체 폭으로 표현하지 않는다.

정보의 오래됨은 node나 관계선의 밝기와 불투명도로 표현하지 않는다. 사용자는 상세 panel의 마지막 근거 게시일과 Evidence Trace에서 날짜를 확인한다. 충돌 관계는 `conflict` 색의 점선으로 표시한다.

Issue #67의 POC에서 직접 이웃의 관계선 core는 전환 감쇠 전 불투명도 0.90을 기준으로 표시한다. 중요한 2단계 이웃의 관계선은 0.56, 3단계 이후 주변부의 실제 관계선은 0.30을 기준으로 사용해 각 단계의 연결을 계속 따라갈 수 있게 한다. 주변부 node label은 hover·focus나 keyboard 탐색 중에만 나타낸다. hover나 focus 중에는 직접·2단계 경로를 최대 0.90, 주변부 경로를 최대 0.66까지 높이되 중심 node나 label보다 먼저 보이지 않게 한다. 관계 단계에 따른 대비는 색과 불투명도로만 구분하며 근거 강도의 의미는 필라멘트 수로 유지한다.

관계선은 군집의 깊이와 겹침을 읽기 위해 완만한 곡선을 사용할 수 있다. 곡률은 관계 종류나 강도를 뜻하지 않는다. 기본 필라멘트는 `relation` 색의 core와 약한 halo를 사용하고, hover·focus 경로는 같은 색 체계 안에서 불투명도와 halo만 높인다. node 유형 색을 관계선 양 끝에 섞어 관계 종류처럼 보이게 만들지 않는다. 뒤쪽 관계선은 거리 안개로 자연스럽게 낮아질 수 있지만 충돌 관계의 호박색 점선과 선택 경로는 식별할 수 있어야 한다.

### Detail panel

상세 panel의 공통 header는 node 이름과 유형을 표시하고 `개요`, `기록`, `인사이트` tab을 제공한다. tab bar는 scroll 중에도 상단에 남고 ArrowLeft·ArrowRight로 이동한다. 중심 또는 기간 변경 시 개요 tab으로 돌아가며 펼친 답변과 보고서를 닫고 이전 요청을 취소한다. 패널의 읽기 요청은 지도 exploration과 분리하여 카메라 이동을 기다리게 하지 않는다.

`개요`는 최근 이력 요약 → 선택 기간의 최근 Claim → 더 알아보기 → 이어서 살펴보기 순서다. 최근 이력 요약은 대상의 일반 소개가 아니라 등록된 자료에서 확인한 활동·관계·발언의 흐름이다. 기간 Claim은 최근 게시 시점 내림차순으로 최대 3개를 보여준다. 더 알아보기는 현재 공개 근거로 답할 수 있는 질문과 사전 저장한 짧은 답변이며 node 이동 action이 아니다. 처음 4개와 추가 4개를 server cursor로 표시한다. 답변은 panel 안에서 펼쳐 읽고 구체적인 한계, 기준 시점과 대표 원문 링크 한 개를 확인한다. 답변에 Claim이나 원문이 여러 개면 `+`로 기록 미리보기를 열어 사용한 Claim과 원문을 확인하고 `기록 전체 보기`로 기록 tab을 연다. 연결된 인사이트 절이 현재 유효할 때만 `관련 분석 읽기`를 제공하며 답변 자체는 보고서 없이도 읽을 수 있다. 생성 worker는 source intake 경로에 연결됐으며 prompt와 실제 모델 품질은 실행별로 별도 평가한다.

이어서 탐색은 기존 추천의 실제 경로, 관계 방향, 독립 근거 수와 상태를 유지한다. 기본 추천은 직접 관계 2개, 2단계 1개와 주변부 1개이며 부족한 범주는 공개 후보로 보완한다. 실제 Relation이 없는 주변부에 관계가 있는 것처럼 표시하지 않는다. 선택한 추천만 중심 node 이동을 수행한다.

`기록`은 선택 node의 속성 Claim, 사건 시간 근거, 관계의 지지·반박 Claim과 해당 대상의 공개 충돌 구성원을 Claim 단위로 제공한다. 같은 Claim이 여러 경로로 연결돼도 한 번 표시하고 연결 대상들을 함께 표시한다. 목록은 server cursor 순서를 그대로 사용하며 날짜순이라고 표현하지 않는다. 행을 선택하면 연결된 원문 목록의 첫 기사 URL을 조회해 현재 tab에서 바로 연다. 원문이 없거나 조회가 실패하면 목록 안에서 오류를 알리고 다시 선택할 수 있다. 문단 번호와 문자 위치는 검증용 API 데이터로 유지하되 제품 화면에는 표시하지 않는다. `지도에서 강조`는 현재 지도에 있는 node나 관계를 약 2.5초 강조할 뿐 중심·카메라·URL·조회 상태를 바꾸지 않는다. 서로 다른 근거 수는 기간 내 원문 계보 묶음 수이며 확신 점수가 아니다.

`인사이트`는 선택 기간의 한 종합보고서에 대한 핵심 해석과 발견별 목차를 표시한다. 제목이나 목차에서 큰 modal dialog를 열며 선택한 절로 이동한다. 보고서는 핵심 해석을 먼저 제시하고, 각 발견에서도 해석과 판단을 근거 Claim보다 먼저 읽도록 구성한다. 사용자는 보고서의 관점을 이해한 뒤 근거와 해석의 한계를 확인한다. Claim과 원문을 여러 건 함께 펼쳐 비교할 수 있고 두 건 이상 펼치면 모두 접기를 제공한다. Claim 문장과 원문 인용이 같으면 같은 문장을 반복하지 않는다. 보고서와 관계 근거 팝업은 배경 조작을 막고, 표시된 활성 조작 요소의 양 끝에서 Tab·Shift+Tab을 누르면 팝업 안에서 순환한다. 닫기·Escape·바깥 클릭으로 닫히며 원래 opener로 focus를 돌려준다. 팝업을 여는 것은 지도 중심이나 배율을 바꾸지 않는다.

보고서는 지도와의 연결감을 남기되 읽기에 집중할 수 있도록 다크 모드 본문 배경의 불투명도를 0.95로, 바깥 가림막을 0.32로 유지한다. 라이트 모드는 기존 흰색 본문 배경을 사용한다. 테두리와 약한 그림자로 경계를 구분하며, 닫기 버튼은 고정하지 않고 기존 바깥 클릭·Escape 동작을 유지한다.

화면 높이가 700px 이하이면 우측 패널 제목 영역의 위아래 여백을 20px에서 12px로, 본문 영역은 24px에서 12px로 줄인다. 맥락 설명 아래 여백은 24px에서 16px로 줄인다. 글자 크기·행간·가로 여백·클릭 영역과 보고서 팝업의 간격은 유지한다.

질문·근거·인사이트는 모두 90일·1년 선택을 따른다. 전체 기간에서는 관계·Claim·Evidence를 조회하되 전체 기간용 질문과 인사이트를 생성하거나 표시하지 않고 기간 선택 안내를 제공한다. 생성물의 기간 계산은 저장한 `as_of_at` 기준이고 목록에 기준일을 표시한다. Claim 목록의 첫 조회는 현재 시각을 기준으로 기간을 고정하고 cursor에 같은 시각을 이어간다. 출처 게시일이 선택 기간에 드는 Claim을 목록에 포함하며 Trace에서는 기간 밖 배경 자료와 게시 시점 미상을 구분해 보존한다. 게시일을 사건 발생일로 해석하지 않는다.

새 패널 계약은 #162와 #163에서 기존 이동형 질문·개별 인사이트 목록 계약을 대체한다. 기존 `followup_question` 데이터와 exploration의 호환 필드, 기존 인사이트 API는 보존하지만 새 화면에서 이동형 질문을 사용하지 않는다. 기존 답변이나 통합 보고서가 없는 자료를 자동 재해석하지 않는다. 새 기간별 결과가 없으면 `503 PANEL_NOT_READY`, 유효한 빈 묶음 또는 0개 보고서는 정상 빈 목록이다. 선택된 최신 READY의 전체 basis와 사용 Claim을 목록·상세에서 재검증하고 비공개·열린 BLOCKING 근거가 있으면 생성물을 제공하지 않는다. 새 publication 실패는 여전히 유효한 이전 READY를 유지한다. 생성 worker 구현 완료나 실제 생성 품질을 개발 fixture로 증명하지 않는다.

패널 읽기 API는 `GET /api/v1/nodes/{node_id}/questions`, `GET /api/v1/questions/{question_id}`, `GET /api/v1/nodes/{node_id}/claims`, `GET /api/v1/nodes/{node_id}/claims/{claim_id}/evidence`, `GET /api/v1/nodes/{node_id}/insight-report`다. 자료 제출은 `POST /api/v1/source-intake`가 multipart 파일을 정규화해 `source_document_id`와 처리 job을 반환하고, `GET /api/v1/source-intake/{source_document_id}`가 처리 단계와 READY·실패 상태를 읽는다. 활성화된 데모 worker는 저장 뒤 추출·promotion·publication을 자동 진행한다. node 목록과 report는 필수 `time_window`를 받는다. questions는 page당 4개, claims는 기본 10·최대 50개, Claim Trace는 20개를 제공하고 server cursor로 이어간다. Trace는 답변·Claim이 제공한 timezone 포함 `as_of_at`을 받는다. report는 `detail=false`에서 요약·목차, `detail=true`에서 절별 본문과 Claim 참조를 반환한다. 잘못된 입력·cursor는 `422 INVALID_REQUEST`, 공개 대상 부재는 `404 PANEL_NOT_FOUND`, 결과 미준비·무효화는 `503 PANEL_NOT_READY`다.


### 상태

일반적인 부분 loading은 어느 영역을 준비하는지 문구로 알리고 map 전체를 불필요하게 가리지 않는다. 첫 진입 graph 준비는 사용자가 조작할 수 있는 화면이 아직 없으므로 앞에서 정의한 viewport 전체 loading을 예외로 사용한다. 첫 진입 loading과 graph-ready 연결은 #134에서 검증한다. empty 상태는 시간 범위 변경이나 새 검색처럼 가능한 다음 행동을 하나 제시한다.

error 상태는 실패한 영역과 다시 시도할 수 있는지 설명한다. publication 준비 실패 중에는 이전 READY 결과를 계속 보여주고 최신 기준 지식이 사라진 것처럼 빈 화면으로 바꾸지 않는다.

## Do's and Don'ts

### Do

- 지식맵이 viewport와 시각적 관심의 대부분을 차지하게 한다.
- 선택한 중심, 시간 범위, node 유형과 관계 근거를 항상 확인할 수 있게 한다.
- 색과 함께 label, 선 모양, accessible name과 상태 문구를 사용한다.
- Evidence Trace와 충돌 근거를 읽기 쉬운 순서로 제공한다.
- 새 UI를 만들기 전에 기존 token과 component 역할로 표현할 수 있는지 확인한다.
- 작은 node, 가는 관계선과 선택적인 label로 군집이 별자리처럼 읽히게 한다.

### Don't

- node 크기, 밝기, 관계선 필라멘트 수와 거리에 승인되지 않은 의미를 추가하지 않는다.
- 의미가 섞인 단일 confidence 점수를 화면에 만들지 않는다.
- 전체 지도 version, 저장된 x/y/z 좌표나 확정된 조직 구조처럼 보이는 layout을 표현하지 않는다.
- 여러 색의 neon rainbow, 화면을 하얗게 덮는 flare, 계속 맥동하거나 깜박이는 glow, 장식용으로 이동하는 particle과 계속 움직이는 배경을 사용하지 않는다.
- bloom과 거리 안개로 node 유형 색, 관계선 필라멘트 묶음, 충돌 점선, label과 focus 표시를 알아보기 어렵게 만들지 않는다.
- 존재하지 않는 관리자, 인증, 후보 검토와 모델 실행 화면을 미리 설계하지 않는다.
- 다른 회사의 브랜드 색, typography, logo나 고유한 component 외형을 복제하지 않는다.

이 문서의 구조는 Apache-2.0으로 공개된 [Google DESIGN.md format](https://github.com/google-labs-code/design.md/blob/9bf8eae67128b6cc55ad9bf86665767deb4c11cd/docs/spec.md)을 참고했으며, 시각 규칙과 제품 의미는 ontology-map 요구사항에 맞게 작성했다.
