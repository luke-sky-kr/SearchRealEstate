#!/usr/bin/env python3
"""수집 agent가 받아온 네이버 부동산 매물 JSON을 DB에 적재.

입력: naver_data/<수집시각>/
  - <complex_no>_<trade_type>.json — fin.land front-api v1
    /complex/article/list 응답의 result.list 를 페이지 순회로 이어붙인 배열.
    각 항목 = {representativeArticleInfo, duplicatedArticleInfo?}
  - complexes.json — {단지번호: 단지명}
  - counts.json    — {"<complex_no>_<trade_type>": totalCount}
출력: snapshots / complexes / raw_articles 테이블에 삽입.

대표매물과 네이버가 묶은 중복매물을 **모두** 1행씩 펼쳐 넣는다. 네이버의 묶기
결과는 naver_group 계열 컬럼에 보존하고, 자체 dedup은 별도 테이블에서 교차 검증한다.
"""
import json, sqlite3, sys, glob, os, re
from datetime import datetime
from zoneinfo import ZoneInfo

DB = os.path.join(os.path.dirname(__file__), "..", "data", "monitor.db")
KST = ZoneInfo("Asia/Seoul")

# <단지번호>_<거래유형>.json 만 매물 파일로 취급한다.
# (같은 폴더의 counts.json / complexes.json 을 매물로 오인하지 않도록)
ARTICLE_FILE_RE = re.compile(r"^(\d+)_([AB]\d)$")


def to_manwon(won):
    """원 단위 -> 만원 단위 int. 네이버 응답 가격은 원 단위(3900000000 = 39억)."""
    if won in (None, "", 0):
        return 0 if won == 0 else None
    try:
        return int(won) // 10000
    except (TypeError, ValueError):
        return None


def split_floor(floor_info):
    """'저/34' -> ('저', 34), '3/34' -> ('3', 34)"""
    if not floor_info:
        return (None, None)
    parts = str(floor_info).split("/")
    target = parts[0] or None
    total = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return (target, total)


def price_for(trade_type, price_info):
    """거래유형별 대표 가격(만원). 매매=매매가, 그 외=보증금."""
    pi = price_info or {}
    return to_manwon(pi.get("dealPrice") if trade_type == "A1"
                     else pi.get("warrantyPrice"))


def norm_article(a, dup, repr_no, trade_type_fallback):
    """fin.land articleInfo 항목 -> raw_articles 행"""
    si = a.get("spaceInfo") or {}
    ad = a.get("articleDetail") or {}
    vi = a.get("verificationInfo") or {}
    bi = a.get("brokerInfo") or {}
    pi = a.get("priceInfo") or {}
    tt = a.get("tradeType") or trade_type_fallback
    target, total = split_floor(ad.get("floorInfo"))
    fd = ad.get("floorDetailInfo") or {}
    if total is None and str(fd.get("totalFloor", "")).isdigit():
        total = int(fd["totalFloor"])

    dup = dup or {}
    grp_price = ((dup.get("representativePriceInfo") or {})
                 .get("dealPrice" if tt == "A1" else "warrantyPrice")) or {}
    members = dup.get("articleInfoList") or []

    return dict(
        article_no=str(a["articleNumber"]),
        trade_type=tt,
        building=a.get("dongName"),
        floor_info=ad.get("floorInfo"),
        floor_target=target,
        floor_total=total,
        price_krw=price_for(tt, pi),
        deal_price_krw=to_manwon(pi.get("dealPrice")),
        warrant_price_krw=to_manwon(pi.get("warrantyPrice")),
        rent_price_krw=to_manwon(pi.get("rentPrice")),
        mgmt_fee_krw=pi.get("managementFeeAmount"),
        price_change=pi.get("priceChangeStatus"),
        area_name=si.get("supplySpaceName"),
        area_supply=si.get("supplySpace"),
        area_exclusive=si.get("exclusiveSpace"),
        direction=ad.get("direction"),
        direction_std=ad.get("directionStandard"),
        description=ad.get("articleFeatureDescription"),
        realtor=bi.get("brokerageName"),
        broker_cp=bi.get("cpId"),
        confirmed_date=vi.get("articleConfirmDate"),
        exposure_date=vi.get("exposureStartDate"),
        verification=vi.get("verificationType"),
        naver_group=repr_no,
        is_repr=1 if str(a["articleNumber"]) == repr_no else 0,
        naver_group_n=len(members) or 1,
        naver_realtor_n=dup.get("realtorCount"),
        naver_grp_min=to_manwon(grp_price.get("minPrice")),
        naver_grp_max=to_manwon(grp_price.get("maxPrice")),
        raw_json=json.dumps(a, ensure_ascii=False),
    )


def flatten(item, trade_type_fallback):
    """응답 항목 1개 -> 대표매물 + 묶음 멤버 전체를 행 리스트로 펼친다."""
    rep = item.get("representativeArticleInfo") or {}
    if not rep.get("articleNumber"):
        return []
    repr_no = str(rep["articleNumber"])
    dup = item.get("duplicatedArticleInfo") or {}
    members = dup.get("articleInfoList") or []
    # 묶임이 없으면 대표매물 하나. 있으면 articleInfoList가 대표를 포함한다.
    sources = members if members else [rep]
    if members and repr_no not in {str(m.get("articleNumber")) for m in members}:
        sources = [rep] + list(members)
    return [norm_article(a, dup, repr_no, trade_type_fallback) for a in sources]


COLUMNS = ["snapshot_id", "article_no", "complex_no", "trade_type", "building",
           "floor_info", "floor_target", "floor_total", "price_krw",
           "deal_price_krw", "warrant_price_krw", "rent_price_krw", "mgmt_fee_krw",
           "price_change", "area_name", "area_supply", "area_exclusive",
           "direction", "direction_std", "description", "realtor", "broker_cp",
           "confirmed_date", "exposure_date", "verification", "naver_group",
           "is_repr", "naver_group_n", "naver_realtor_n", "naver_grp_min",
           "naver_grp_max", "raw_json"]

INSERT_SQL = (f"INSERT OR REPLACE INTO raw_articles ({','.join(COLUMNS)}) "
              f"VALUES ({','.join(':' + c for c in COLUMNS)})")


def ensure_source_dir_column(con):
    """기존 DB에도 source_dir을 붙인다 (CREATE IF NOT EXISTS는 컬럼을 추가하지 않는다)."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(snapshots)")]
    if "source_dir" not in cols:
        con.execute("ALTER TABLE snapshots ADD COLUMN source_dir TEXT")


def ingest(snapshot_dir, notes="", force=False):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    ensure_source_dir_column(con)
    src = os.path.basename(os.path.normpath(snapshot_dir))
    dup = con.execute("SELECT snapshot_id FROM snapshots WHERE source_dir=?", (src,)).fetchone()
    if dup and not force:
        # 같은 수집 폴더를 두 번 적재하면 가짜 조사일이 생겨 추이가 통째로 왜곡된다
        raise SystemExit(f"수집 폴더 {src} 는 이미 스냅샷 #{dup[0]}으로 적재되어 있습니다. "
                         f"의도한 재적재라면 force=True로 부르세요.")

    # 단지 마스터 갱신
    cx_path = os.path.join(snapshot_dir, "complexes.json")
    if os.path.exists(cx_path):
        for no, name in json.load(open(cx_path, encoding="utf-8")).items():
            cur.execute("INSERT INTO complexes(complex_no, name) VALUES (?,?) "
                        "ON CONFLICT(complex_no) DO UPDATE SET name=excluded.name",
                        (str(no), name))

    collected_at = datetime.now(KST).isoformat(timespec="seconds")
    cur.execute("INSERT INTO snapshots(collected_at, notes, source_dir) VALUES (?,?,?)",
                (collected_at, notes, src))
    sid = cur.lastrowid

    n_rows = n_repr = 0
    for path in sorted(glob.glob(os.path.join(snapshot_dir, "*.json"))):
        m = ARTICLE_FILE_RE.match(os.path.basename(path)[:-len(".json")])
        if not m:
            continue  # counts.json / complexes.json 등은 건너뛴다
        complex_no, trade_type = m.group(1), m.group(2)
        items = json.load(open(path, encoding="utf-8"))
        seen, reprs = set(), set()
        for item in items:
            for row in flatten(item, trade_type):
                if row["article_no"] in seen:
                    continue  # 페이지 경계 중복 / 묶음 간 중복 방지
                seen.add(row["article_no"])
                if row["is_repr"]:
                    reprs.add(row["article_no"])
                row["snapshot_id"] = sid
                row["complex_no"] = complex_no
                cur.execute(INSERT_SQL, row)
        n_rows += len(seen)
        n_repr += len(reprs)
        print(f"  {complex_no}_{trade_type}: 대표 {len(reprs)}건 / 원시 {len(seen)}건")

    con.commit()
    print(f"snapshot #{sid} @ {collected_at}: 대표 {n_repr}건 / 원시 {n_rows}건 적재")
    return sid


if __name__ == "__main__":
    ingest(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
