# 네이버 부동산 매물 모니터

관심 아파트 단지의 매매·전세 매물 물량과 호가 추이를 추적하는 개인용 모니터링 도구.

- 설계·계획: [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md) (§3·§6·§8은 2026-08-22 실측으로 갱신됨 — `CLAUDE.md` 참조)
- Claude Code용 컨텍스트: [`CLAUDE.md`](./CLAUDE.md)

## 빠른 시작

```bash
# 조사 1회를 끝까지 (평소에는 이것만)
python3 run_all.py
```

## 단계별 실행

```bash
# 1) DB 초기화 (최초 1회, 이미 data/monitor.db가 있으면 생략)
python3 -c "import sqlite3; sqlite3.connect('data/monitor.db').executescript(open('scripts/schema.sql').read())"

# 2) 매물 수집 (사용자 PC에서 실행 — 네이버는 클라우드/확장 접근을 차단함)
python3 collect_naver.py
#    → naver_data/<수집시각>/ 에 JSON 생성

# 3) DB 적재
python3 scripts/ingest.py naver_data/<수집시각>

# 4) 중복 판정 (규칙) — 애매 쌍은 data/ambiguous_<snapshot_id>.json 으로 나온다
python3 scripts/dedup.py <snapshot_id>

# 5) (선택) AI 판정 결과 반영
python3 scripts/dedup.py apply <snapshot_id> data/verdicts_<snapshot_id>.json

# 6) 정합성 진단
python3 scripts/integrity.py <snapshot_id> naver_data/<수집시각>/counts.json

# 7) 조사일 간 물건 연결 (신규/소멸/호가변동 기록)
python3 scripts/link_snapshots.py

# 8) 대시보드 생성 (dashboard/index.html — 브라우저로 열면 끝)
python3 scripts/build_dashboard.py
```

## 시간축은 조사일이다

등록일 기준으로 물량을 세면 팔리거나 내린 매물이 응답에서 빠지는 탓에 언제나 조사일에
가까운 쪽이 많아 보인다. 그래서 조사할 때마다 전량을 스냅샷으로 통째 저장하고, 조사일
축에서 비교한다. 소멸은 이 축에서만 관측되므로 실제 순증감과 호가 변동도 여기서만 나온다.
**`run_all.py`를 주기적으로 돌리는 것 자체가 데이터를 만드는 일이다.**

## 대시보드

`dashboard/template.html`이 앱이고, `build_dashboard.py`가 DB에서 뽑은 JSON을 끼워
`dashboard/index.html` 한 장으로 굽는다. 외부 CDN에 의존하지 않으므로 파일만 열면 동작한다.

- 좌측 **단지 ▸ 동 ▸ 평형 ▸ 층** 체크박스 트리와 매매/전세 토글이 아래 모든 차트·표를 함께 좁힌다. 선택은 localStorage에 남는다.
- **호가와 등록 추이** — 등록일 축. 점 1개 = 물건 1개(y=그룹 최저 호가), 아래 패널은 같은 날 등록된 매물 수. 생존 편향이 있는 축이므로 카드에 그 한계를 적어 둔다.
- **매물 분포** — 호가 구간별 히스토그램(원시 매물 / 물건 두 겹).
- **물량 추이** — 조사일 축. 하한~상한 밴드. 조사일이 1개면 선 대신 숫자로 보여준다.
- **조사일별 호가** — 조사일마다 그때 살아 있던 물건만으로 낸 중앙값과 25~75% 구간.
- **조사일별 증감** — 신규(위)·소멸(아래). 단지를 새로 넣은 조사일의 편입 물량은 막대에서 빼고 아래에 따로 적는다.
- **물건 목록** — 정렬 가능한 표이자 차트의 표 대응물. 조사일이 2개 이상이면 Δ호가와 신규/편입이 채워진다.

조사일 축 차트는 DB의 `unit_links`/`unit_events`가 매긴 `unit_id`를 그대로 쓴다 —
대시보드가 매칭을 다시 계산하지 않으므로 표와 DB가 어긋날 일이 없다.
안 훑은 조사일은 0이 아니라 결측으로 그린다(0으로 이으면 단지를 추가한 날 물량이 솟은 것처럼 보인다).

`node scripts/domcheck.mjs`가 대시보드 스크립트를 최소 DOM 위에서 실제로 돌려
런타임 오류와 빈 차트를 잡는다. 템플릿을 고쳤으면 굽고 나서 이걸 돌린다.
- 색은 단지를 따라간다(필터를 바꿔도 단지 색은 그대로). 라이트/다크 모두 대응.

## 물량은 밴드로 읽는다

네이버 매물의 96%는 층이 정확한 층수가 아니라 저/중/고 대역으로만 표기된다. 그래서 같은 동·대역·평형·가격·방향의 여러 매물이 중복 등록인지 별개 세대인지 데이터로는 확정할 수 없다. 이 도구는 억지로 하나의 수를 만들지 않고 두 수를 함께 보여준다.

- **상한** — 네이버 동일매물묶기 결과
- **하한** — 자체 2차 병합 결과 (방향 불일치는 배제, 가격·설명 유사도로 점수화)

첫 스냅샷(2026-08-22): 원시 1174건 → 밴드 592~716건.

## 단지 추가

`collect_naver.py` 상단 `COMPLEXES` 딕셔너리에 단지명을 추가한다. 단지번호는 자동 검색되며,
실패하면 `"단지명": 12345` 형태로 직접 지정한다. 현재 대상은 래미안신반포팰리스(108228),
래미안퍼스티지(23759), 삼성힐스테이트2단지(24008), LG한강자이(3143) 네 곳이다.

단지를 추가하거나 뺀 조사일에는 그 단지 물건이 전부 신규/소멸로 보일 수 있다.
`link_snapshots.py`가 이를 구분해 편입분은 `enter`로 남기고, 안 훑은 단지는 소멸로 세지 않는다.
집계할 때 `enter`는 신규에서 제외한다.

## 상태

| 구성요소 | 상태 |
|---|---|
| 수집 agent (`collect_naver.py`) | 완료 · 실데이터 검증됨 (누락 0) |
| DB 스키마 / 적재 (`ingest.py`) | 완료 · 실데이터 검증됨 |
| Dedup (규칙 + AI 2단계) | 규칙 완료 · AI 판정 자동화 미착수 |
| 정합성 감시 (`integrity.py`) | 완료 |
| 대시보드 v1 (`build_dashboard.py`) | 완료 |
| run_all 오케스트레이터 | 미착수 |

M1(첫 스냅샷 E2E)과 M2(대시보드 v1) 완료. 다음은 M3(AI dedup 자동화 + run_all) — `DEVELOPMENT_PLAN.md` §12 참조.
