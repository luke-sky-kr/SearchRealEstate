#!/usr/bin/env python3
"""수집 agent가 브라우저에서 받아온 네이버 부동산 매물 JSON을 DB에 적재.

입력: data/raw/<snapshot_ts>/<complex_no>_<trade_type>.json
  - 네이버 new.land.naver.com/api/articles/complexes/{complexNo} 응답의
    articleList 항목들을 페이지 순회로 이어붙인 배열, 또는
    m.land.naver.com getComplexArticleList 응답(result.list) 배열.
출력: snapshots / raw_articles 테이블에 삽입.
"""
import json, sqlite3, sys, glob, os, re
from datetime import datetime
from zoneinfo import ZoneInfo

DB = os.path.join(os.path.dirname(__file__), "..", "data", "monitor.db")
KST = ZoneInfo("Asia/Seoul")

def parse_price(p):
    """'42억' '42억 5,000' '5억7,000' '124,000' -> 만원 단위 int"""
    if p is None:
        return None
    if isinstance(p, (int, float)):
        return int(p)
    s = str(p).replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    m = re.match(r"^(?:(\d+)억)?(\d+)?$", s)
    if not m:
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else None
    eok = int(m.group(1)) if m.group(1) else 0
    man = int(m.group(2)) if m.group(2) else 0
    return eok * 10000 + man

def norm_article(a):
    """new.land / m.land 두 응답 포맷을 공통 스키마로 정규화"""
    if "articleNo" in a:  # new.land.naver.com 포맷
        return dict(
            article_no=str(a["articleNo"]),
            trade_type=a.get("tradeTypeCode") or ("A1" if a.get("tradeTypeName") == "매매" else "B1" if a.get("tradeTypeName") == "전세" else a.get("tradeTypeName")),
            building=a.get("buildingName"),
            floor_info=a.get("floorInfo"),
            price_krw=parse_price(a.get("dealOrWarrantPrc")),
            area_name=a.get("areaName"),
            area_supply=a.get("area1"),
            area_exclusive=a.get("area2"),
            direction=a.get("direction"),
            description=a.get("articleFeatureDesc"),
            realtor=a.get("realtorName"),
            confirmed_date=a.get("articleConfirmYmd"),
            same_addr_cnt=a.get("sameAddrCnt"),
            same_addr_max=parse_price(a.get("sameAddrMaxPrc")),
            same_addr_min=parse_price(a.get("sameAddrMinPrc")),
            raw_json=json.dumps(a, ensure_ascii=False),
        )
    else:  # m.land.naver.com 포맷 (atclNo 등)
        return dict(
            article_no=str(a["atclNo"]),
            trade_type=a.get("tradTpCd"),
            building=a.get("bildNm"),
            floor_info=a.get("flrInfo"),
            price_krw=parse_price(a.get("prc") if a.get("prc") else a.get("hanPrc")),
            area_name=a.get("atclNm2") or a.get("spc1Nm"),
            area_supply=float(a["spc1"]) if a.get("spc1") not in (None, "-") else None,
            area_exclusive=float(a["spc2"]) if a.get("spc2") not in (None, "-") else None,
            direction=a.get("direction"),
            description=a.get("atclFetrDesc"),
            realtor=a.get("rltrNm"),
            confirmed_date=a.get("cfmYmd"),
            same_addr_cnt=a.get("sameAddrCnt"),
            same_addr_max=parse_price(a.get("sameAddrMaxPrc")),
            same_addr_min=parse_price(a.get("sameAddrMinPrc")),
            raw_json=json.dumps(a, ensure_ascii=False),
        )

def ingest(snapshot_dir, notes=""):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    collected_at = datetime.now(KST).isoformat(timespec="seconds")
    cur.execute("INSERT INTO snapshots(collected_at, notes) VALUES (?,?)", (collected_at, notes))
    sid = cur.lastrowid
    n_total = 0
    for path in sorted(glob.glob(os.path.join(snapshot_dir, "*.json"))):
        fname = os.path.basename(path).replace(".json", "")
        complex_no, trade_type = fname.split("_", 1)
        arts = json.load(open(path))
        seen = set()
        for a in arts:
            na = norm_article(a)
            if na["article_no"] in seen:
                continue  # 페이지 경계 중복 수집 방지
            seen.add(na["article_no"])
            na["snapshot_id"] = sid
            na["complex_no"] = complex_no
            if not na["trade_type"]:
                na["trade_type"] = trade_type
            cur.execute("""INSERT OR REPLACE INTO raw_articles
                (snapshot_id, article_no, complex_no, trade_type, building, floor_info,
                 price_krw, area_name, area_supply, area_exclusive, direction, description,
                 realtor, confirmed_date, same_addr_cnt, same_addr_max, same_addr_min, raw_json)
                VALUES (:snapshot_id,:article_no,:complex_no,:trade_type,:building,:floor_info,
                 :price_krw,:area_name,:area_supply,:area_exclusive,:direction,:description,
                 :realtor,:confirmed_date,:same_addr_cnt,:same_addr_max,:same_addr_min,:raw_json)""", na)
        n_total += len(seen)
        print(f"  {fname}: {len(seen)} articles")
    con.commit()
    print(f"snapshot #{sid} @ {collected_at}: {n_total} articles ingested")
    return sid

if __name__ == "__main__":
    ingest(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
