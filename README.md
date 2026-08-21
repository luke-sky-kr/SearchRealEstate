# 네이버 부동산 매물 모니터

관심 아파트 단지의 매매·전세 매물 물량과 호가 추이를 추적하는 개인용 모니터링 도구.

- 설계·계획: [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md)
- Claude Code용 컨텍스트: [`CLAUDE.md`](./CLAUDE.md)

## 빠른 시작

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
```

## 단지 추가

`collect_naver.py` 상단 `COMPLEXES` 딕셔너리에 단지명을 추가한다. 단지번호는 자동 검색되며,
실패하면 `"단지명": "12345"` 형태로 직접 지정한다.

## 상태

| 구성요소 | 상태 |
|---|---|
| 수집 agent (`collect_naver.py`) | 작성 완료 · 실기기 검증 전 |
| DB 스키마 / 적재 | 작성 완료 · 실데이터 검증 전 |
| Dedup (규칙 + AI 2단계) | 작성 완료 · 임계값 튜닝 필요 |
| 정합성 감시 | 작성 완료 |
| 대시보드 | 미착수 (계획서 §9) |

다음 할 일은 M1(첫 스냅샷 E2E) — `DEVELOPMENT_PLAN.md` §12 참조.
