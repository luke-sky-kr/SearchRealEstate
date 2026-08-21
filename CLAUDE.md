# 네이버 부동산 매물 모니터 — 프로젝트 컨텍스트

이 문서는 Claude Code가 이 저장소에서 작업할 때 읽는 컨텍스트다. 상세 설계는 `DEVELOPMENT_PLAN.md` 참조.
단, 계획서의 §3·§6·§8은 2026-08-22 실측으로 갱신되었다 — 아래 "중요한 제약"과 "구조"가 현행 사실이다.

## 무엇을 만드는가

관심 아파트 단지(래미안신반포팰리스 108228, 래미안퍼스티지 23759)의 매매·전세 매물 물량과 호가 추이를 추적해 투자 시점을 센싱하는 개인용 모니터. 사용자가 "검토해줘" 하면 최신 스냅샷으로 DB를 갱신하고 대시보드를 새로 뽑는다.

핵심은 **매물 수를 정확히 세는 것**이다. 다만 "정확한 하나의 수"는 존재하지 않는다는 것이 실측 결론이다 — 아래 물량 밴드 항목 참조.

## 중요한 제약

**네이버는 Claude의 자동 접근을 차단한다.** Claude in Chrome 확장은 `*.naver.com`을 정책적으로 막고, Anthropic 클라우드 프록시도 403을 받는다. 단, **Claude Code는 사용자 PC에서 실행되므로 이 제약이 없다** — 수집 스크립트를 직접 실행·디버깅할 수 있다.

**API (2026-08-22 실측 · 구 m.land API는 폐기됨).**
`m.land.naver.com/complex/getComplexArticleList`는 `null`을 반환하고, m.land 경로는 `fin.land.naver.com`(네이버파이낸셜, Next.js)으로 리다이렉트된다. 현행 엔드포인트:

```
POST https://fin.land.naver.com/front-api/v1/complex/article/list
body: {complexNumber, tradeTypes:["A1"], size:<=30, userChannelType:"PC",
       articleSortType:"PRICE_ASC", seed, lastInfo}
```

- **쿠키 세션이 필요하다.** 쿠키 없이 호출하면 429(TOO_MANY_REQUESTS). 먼저 `fin.land.naver.com`을 한 번 GET 한다.
- 페이지네이션은 페이지 번호가 아니라 **커서**(`lastInfo`)다. 첫 응답의 `seed`를 이후 요청에 실어야 목록이 흔들리지 않는다.
- 응답 항목 = `representativeArticleInfo` + `duplicatedArticleInfo`(네이버가 묶은 중복매물 목록·중개사 수·가격범위). `totalCount`는 대표매물 기준이라 원시 매물 수는 그보다 많다.
- 가격은 **원 단위**로 온다 (3900000000 = 39억). DB에는 만원으로 환산해 넣는다.
- 요청 간 0.8초 지연을 유지하고, 디버깅 중 반복 호출을 최소화한다. IP 차단을 당하면 프로젝트 전체가 멈춘다.

## 물량 밴드 — 이 프로젝트의 핵심 판단

수집된 대표매물 716건 중 **정확한 층이 표기된 것은 28건(4%)뿐**이고 나머지는 전부 저/중/고 대역이다. 34층 건물의 "저"는 11개 층에 걸친다. 따라서 `(106동, 저, 84.5㎡, 39.0억, 남향)` 4건이 4중 등록인지 실제 4세대인지 **데이터만으로는 판별할 수 없다.**

어느 한쪽을 정답으로 고르면 물량을 반드시 과대 또는 과소 계상하게 되므로, 물량은 단일 수치가 아니라 밴드로 다룬다.

- **상한** = 네이버 동일매물묶기 결과 (`raw_articles.naver_group`)
- **하한** = 자체 2차 병합 결과 (`dedup_assignments`)

첫 스냅샷(2026-08-22) 기준 592~716건. 대시보드는 두 계열을 함께 그리고, 추이는 두 계열 모두에서 읽는다.

## 구조

```
collect_naver.py          수집 agent — fin.land front-api 커서 순회, naver_data/<ts>/ 에 JSON 출력
scripts/schema.sql        DB 스키마
scripts/ingest.py         JSON → SQLite 적재 (대표매물 + 묶음 멤버를 모두 펼쳐 넣음)
scripts/dedup.py          네이버 묶음 위의 보수적 2차 병합 + 애매 쌍 추출 / AI verdicts 반영(apply)
scripts/integrity.py      누락(missing) 감시 + 물량 밴드 폭(wide) 감시
data/monitor.db           SQLite
```

파이프라인: `collect → ingest → dedup → (AI 판정) → dedup apply → integrity → dashboard`

## 데이터 모델 요점

- `raw_articles` — 수집 원본. 스냅샷×매물번호 PK. 대표매물(`is_repr=1`)과 네이버 묶음 멤버(`is_repr=0`)가 모두 1행씩 들어간다. 층은 `floor_info`에 `저/34`(대역, 96%) 또는 `15/34`(정확층, 4%)로 들어오며 `floor_target`/`floor_total`로 분해되어 있다. 가격은 만원 단위 정수.
- `naver_group` = 네이버 묶음 대표 매물번호. `naver_group_n`/`naver_realtor_n`/`naver_grp_min`/`naver_grp_max`에 묶음 정보 보존.
- `exposure_date` = 등록(노출 시작) 시점. R8의 dot plot x축은 이 값을 쓴다.
- `dedup_assignments` — 자체 2차 병합. `method`는 `naver`(네이버 묶음 그대로) / `rule`(자체가 추가 병합) / `ai` / `singleton`.
- `integrity_checks` — 단지×거래유형별 진단.

## 작업 시 주의

- **방향(`direction`)은 dedup의 1차 분할자다.** 한 세대가 두 방향일 수 없으므로 불일치는 즉시 배제한다. 층이 대역으로만 주어지는 상황에서 가장 신뢰할 수 있는 신호다.
- `dedup.py`의 임계값(병합 3.5 / 애매 하한 1.5)은 첫 스냅샷 기준이다. 애매 쌍(`data/ambiguous_<sid>.json`)을 검수하며 조정할 것.
- 애매 쌍 판정 힌트: **같은 중개사가 가격만 다르게 올린 두 건은 대체로 별개 세대**다(한 중개사가 같은 집을 두 가격에 올릴 이유가 없다). 반대로 다른 중개사 + 동일 가격 + 동일 설명 토큰은 중복 가능성이 높다.
- 대시보드는 미착수. 사용자 요구의 핵심은 **단지·동·평형·층 체크박스로 고른 물건만의 추이**를 보는 것이다.

## 지금 할 일

M2 — 대시보드 v1. M1(첫 스냅샷 E2E)은 2026-08-22 완료: 4개 조합 모두 누락 0, 물량 밴드 592~716건.

## 사용자 선호

- 구조적이고 데이터에 근거한 분석, 인과 관계를 명시하는 설명을 선호한다.
- 우선순위가 분명한 직접적인 권고를 원한다.
- 한국어/영어 혼용, 기술 용어는 양쪽 다 사용.
