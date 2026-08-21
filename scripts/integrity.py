#!/usr/bin/env python3
"""정합성 감시: 수집 누락과 물량 불확실성 진단.

물량은 밴드로 본다 (근거는 dedup.py 상단 주석 참조).
  naver_listed  : 네이버가 알려준 총 수 (API totalCount, 대표매물 기준)
  collected     : 실제 수집한 대표매물 수  -> 적으면 'missing' (수집 누락)
  collected_raw : 원시 매물 수 (네이버 묶음 멤버 포함)
  naver_grouped : 네이버 동일매물묶기 결과 = 물량 상한
  our_grouped   : 자체 2차 병합 결과      = 물량 하한

'missing'은 수집 agent의 결함이므로 반드시 0이어야 한다.
'wide'는 결함이 아니라 데이터 한계(층이 대역으로만 표기됨)의 표면화다.
밴드가 지나치게 넓으면 그 단지의 물량 해석에 주의가 필요하다는 신호로 쓴다.

사용: python3 integrity.py <snapshot_id> [counts.json]
counts.json: {"<complex_no>_<trade_type>": totalCount, ...}
"""
import json, sqlite3, sys, os

DB = os.path.join(os.path.dirname(__file__), "..", "data", "monitor.db")
BAND_WIDE = 0.35   # (상한-하한)/상한 이 이 값을 넘으면 경고


def run(sid, counts_path=None):
    counts = {}
    if counts_path and os.path.exists(counts_path):
        counts = json.load(open(counts_path, encoding="utf-8"))
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("DELETE FROM integrity_checks WHERE snapshot_id=?", (sid,))

    rows = con.execute("""
        SELECT r.complex_no, r.trade_type,
               SUM(r.is_repr)                  AS collected,
               COUNT(*)                        AS collected_raw,
               COUNT(DISTINCT r.naver_group)   AS naver_grouped,
               COUNT(DISTINCT d.group_key)     AS our_grouped
        FROM raw_articles r
        LEFT JOIN dedup_assignments d
          ON d.snapshot_id = r.snapshot_id AND d.article_no = r.article_no
        WHERE r.snapshot_id = ?
        GROUP BY r.complex_no, r.trade_type
        ORDER BY r.complex_no, r.trade_type""", (sid,)).fetchall()

    names = dict(con.execute("SELECT complex_no, name FROM complexes").fetchall())
    worst = "ok"
    for complex_no, trade_type, collected, collected_raw, naver_grouped, our_grouped in rows:
        key = f"{complex_no}_{trade_type}"
        naver_listed = counts.get(key)
        status, detail = "ok", []

        if naver_listed is not None:
            if collected < naver_listed:
                status = "missing"
                detail.append(f"네이버 표시 {naver_listed}건 중 {collected}건만 수집됨 "
                              f"(누락 {naver_listed - collected}건)")
            elif collected > naver_listed:
                detail.append(f"수집 {collected} > 네이버 표시 {naver_listed} "
                              f"(표시 수 지연 가능)")

        if naver_grouped and our_grouped:
            band = (naver_grouped - our_grouped) / naver_grouped
            detail.append(f"물량 밴드 {our_grouped}~{naver_grouped}건 (폭 {band*100:.0f}%)")
            if band > BAND_WIDE and status == "ok":
                status = "wide"
                detail.append("밴드가 넓음 — 층 대역 표기로 중복 판별이 어려운 구간")
            if our_grouped > naver_grouped:
                status = "over"
                detail.append("하한이 상한보다 큼 — 병합 로직 오류")

        if status != "ok" and worst == "ok":
            worst = status
        cur.execute("INSERT INTO integrity_checks(snapshot_id, complex_no, trade_type,"
                    " naver_listed, collected, collected_raw, naver_grouped, our_grouped,"
                    " status, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (sid, complex_no, trade_type, naver_listed, collected,
                     collected_raw, naver_grouped, our_grouped, status,
                     "; ".join(detail)))
        label = {"A1": "매매", "B1": "전세", "B2": "월세", "B3": "단기"}.get(trade_type, trade_type)
        print(f"  {names.get(complex_no, complex_no)} {label}: "
              f"표시={naver_listed} 수집={collected} 원시={collected_raw} "
              f"물량={our_grouped}~{naver_grouped} [{status}]")
        if detail:
            print(f"      {'; '.join(detail)}")
    con.commit()
    print(f"\nsnapshot {sid} 종합: {worst}")
    return worst


if __name__ == "__main__":
    run(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
