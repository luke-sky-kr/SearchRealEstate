#!/usr/bin/env python3
"""정합성 감시: 누락(missing) / 과다(over) 추정 진단.

- naver_listed: 수집 시 네이버 API가 알려준 총 매물 수 (묶기 전, counts JSON)
- collected: 실제 DB에 들어온 매물 수 -> 다르면 'missing' (수집 누락)
- naver_grouped: sameAddrCnt 기반 네이버 묶음 수 추정
- our_grouped: 자체 dedup 그룹 수 -> 네이버 묶음보다 크게 많으면 'over' 의심

사용: python3 integrity.py <snapshot_id> <counts.json>
counts.json: {"<complex_no>_<trade_type>": naver_listed_count, ...}
"""
import json, sqlite3, sys, os

DB = os.path.join(os.path.dirname(__file__), "..", "data", "monitor.db")

def run(sid, counts_path):
    counts = json.load(open(counts_path)) if counts_path and os.path.exists(counts_path) else {}
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("DELETE FROM integrity_checks WHERE snapshot_id=?", (sid,))
    rows = con.execute("""
        SELECT r.complex_no, r.trade_type,
               COUNT(*) AS collected,
               COUNT(DISTINCT d.group_key) AS our_grouped,
               SUM(CASE WHEN r.same_addr_cnt IS NULL OR r.same_addr_cnt<=1 THEN 1 ELSE 0 END)
                 + 0 AS singles
        FROM raw_articles r
        LEFT JOIN dedup_assignments d
          ON d.snapshot_id=r.snapshot_id AND d.article_no=r.article_no
        WHERE r.snapshot_id=?
        GROUP BY r.complex_no, r.trade_type""", (sid,)).fetchall()
    for complex_no, trade_type, collected, our_grouped, _ in rows:
        # 네이버 묶음 수 추정: sameAddrCnt>1 인 매물은 (합/cnt)개 그룹으로 수렴
        sub = con.execute("""SELECT same_addr_cnt, COUNT(*) FROM raw_articles
            WHERE snapshot_id=? AND complex_no=? AND trade_type=?
            GROUP BY same_addr_cnt""", (sid, complex_no, trade_type)).fetchall()
        naver_grouped = 0
        for cnt, n in sub:
            if cnt is None or cnt <= 1:
                naver_grouped += n
            else:
                naver_grouped += max(1, round(n / cnt))
        key = f"{complex_no}_{trade_type}"
        naver_listed = counts.get(key)
        status, detail = "ok", []
        if naver_listed is not None and collected < naver_listed:
            status = "missing"
            detail.append(f"네이버 표시 {naver_listed}건 중 {collected}건만 수집됨")
        if naver_listed is not None and collected > naver_listed:
            detail.append(f"수집 {collected} > 네이버 표시 {naver_listed} (표시 수 지연 가능)")
        if our_grouped and naver_grouped and our_grouped > naver_grouped * 1.15:
            status = "over" if status == "ok" else status
            detail.append(f"자체 그룹 {our_grouped} > 네이버 묶음 추정 {naver_grouped} — 과다 추정 의심")
        if our_grouped and naver_grouped and our_grouped < naver_grouped * 0.8:
            detail.append(f"자체 그룹 {our_grouped} < 네이버 묶음 추정 {naver_grouped} — 과도 병합 의심")
        cur.execute("INSERT INTO integrity_checks VALUES (?,?,?,?,?,?,?,?,?)",
                    (sid, complex_no, trade_type, naver_listed, collected,
                     naver_grouped, our_grouped, status, "; ".join(detail)))
        print(f"  {key}: listed={naver_listed} collected={collected} "
              f"naver_grp≈{naver_grouped} our_grp={our_grouped} [{status}] {'; '.join(detail)}")
    con.commit()

if __name__ == "__main__":
    run(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
