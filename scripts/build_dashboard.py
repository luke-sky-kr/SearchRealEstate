#!/usr/bin/env python3
"""DB에서 대시보드용 데이터를 추출해 자급자족 HTML 한 장으로 굽는다.

사용: python3 scripts/build_dashboard.py [출력경로]
출력: dashboard/index.html (기본) — 외부 CDN 의존 없음, 데이터는 JSON으로 임베드

물건(unit) = dedup 그룹. 그룹 대표 호가는 최저가(매수자 관점)를 쓰고 min/max를 병기한다.
물량은 밴드로 다룬다 (dedup.py 주석 참조):
  하한 = 물건 수, 상한 = 물건들이 걸쳐 있는 네이버 묶음 수의 합.
"""
import json, sqlite3, sys, os, statistics
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "..", "data", "monitor.db")
TEMPLATE = os.path.join(HERE, "..", "dashboard", "template.html")
DEFAULT_OUT = os.path.join(HERE, "..", "dashboard", "index.html")

PYEONG_M2 = 3.3058      # 1평 = 3.3058㎡
CLUSTER_GAP = 2.0       # 전용면적 단일연결 군집 임계 (㎡)


def cluster_areas(areas):
    """전용면적 목록을 단일연결로 묶어 {면적: 군집대표} 매핑을 만든다.

    같은 평형이라도 타입(A/B/C)에 따라 전용면적이 84.4~84.9로 흩어지므로,
    단순 반올림은 한 평형을 34평/35평으로 쪼갠다. 간격 기준 군집이 맞다.
    """
    out, cur = {}, []
    for a in sorted(set(areas)):
        if cur and a - cur[-1] > CLUSTER_GAP:
            rep = statistics.median(cur)
            out.update({x: rep for x in cur})
            cur = []
        cur.append(a)
    if cur:
        rep = statistics.median(cur)
        out.update({x: rep for x in cur})
    return out


def floor_band(target, total):
    """정확층은 대역으로 접어 필터 트리를 쓸 만한 크기로 유지한다."""
    if not target:
        return "미상"
    if not target.isdigit():
        return {"저": "저층", "중": "중층", "고": "고층"}.get(target, target)
    if not total:
        return "미상"
    r = int(target) / total
    return "저층" if r <= 1/3 else ("중층" if r <= 2/3 else "고층")


def build(db=DB):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    names = dict(con.execute("SELECT complex_no, name FROM complexes").fetchall())
    snapshots = [{"id": r["snapshot_id"], "at": r["collected_at"]}
                 for r in con.execute("SELECT * FROM snapshots ORDER BY snapshot_id")]

    arts = [dict(r) for r in con.execute("""
        SELECT r.*, d.group_key, d.method FROM raw_articles r
        JOIN dedup_assignments d
          ON d.snapshot_id = r.snapshot_id AND d.article_no = r.article_no""")]
    if not arts:
        raise SystemExit("dedup_assignments가 비어 있습니다. dedup.py를 먼저 실행하세요.")

    amap = cluster_areas([a["area_exclusive"] for a in arts if a["area_exclusive"]])
    # 군집 대표 전용면적 -> 평형 라벨 (군집 내 공급면적 중앙값 기준)
    supply_by_rep = defaultdict(list)
    for a in arts:
        if a["area_exclusive"] and a["area_supply"]:
            supply_by_rep[amap[a["area_exclusive"]]].append(a["area_supply"])
    pyeong = {rep: round(statistics.median(v) / PYEONG_M2)
              for rep, v in supply_by_rep.items()}

    # 조사일을 가로지르는 물건 식별자. 이게 있어야 신규/소멸을 조사일 축에서 셀 수 있다.
    uid = {(r["snapshot_id"], r["group_key"]): r["unit_id"]
           for r in con.execute("SELECT snapshot_id, group_key, unit_id FROM unit_links")}
    # 조사일마다 실제로 훑은 단지. 단지를 추가/제외한 것을 신규/소멸로 착각하면 안 된다.
    coverage = defaultdict(list)
    for r in con.execute("""SELECT snapshot_id, complex_no FROM raw_articles
                            GROUP BY snapshot_id, complex_no ORDER BY 1, 2"""):
        coverage[r["snapshot_id"]].append(r["complex_no"])

    groups = defaultdict(list)
    for a in arts:
        groups[(a["snapshot_id"], a["group_key"])].append(a)

    units = []
    for (sid, gk), members in groups.items():
        lead = min(members, key=lambda m: (m["price_krw"] is None, m["price_krw"]))
        prices = [m["price_krw"] for m in members if m["price_krw"] is not None]
        rep_area = amap.get(lead["area_exclusive"])
        expo = sorted(m["exposure_date"] for m in members if m["exposure_date"])
        conf = sorted(m["confirmed_date"] for m in members if m["confirmed_date"])
        units.append({
            "sid": sid, "gk": gk, "uid": uid.get((sid, gk)),
            "cx": lead["complex_no"], "tt": lead["trade_type"],
            "dong": lead["building"] or "미상",
            "pk": round(rep_area, 1) if rep_area else 0,      # 평형 군집 키
            "py": pyeong.get(rep_area, 0),                     # 평형(공급 기준)
            "ex": lead["area_exclusive"],
            "an": lead["area_name"],
            "fb": floor_band(lead["floor_target"], lead["floor_total"]),
            "fi": lead["floor_info"],
            "dir": lead["direction"],
            "p": lead["price_krw"],
            "pmin": min(prices) if prices else None,
            "pmax": max(prices) if prices else None,
            "expo": expo[0] if expo else None,                 # 그룹 최초 등록일
            "xd": expo,                                        # 멤버 매물 각자의 등록일
            "conf": conf[-1] if conf else None,                # 최종 확인일
            "n": len(members),                                 # 묶인 매물 수
            "ng": len({m["naver_group"] for m in members}),     # 네이버 묶음 수 = 상한 기여분
            "mth": lead["method"],
            "rt": sorted({m["realtor"] for m in members if m["realtor"]}),
            "d": lead["description"] or "",
            "arts": sorted(m["article_no"] for m in members),
        })
    units.sort(key=lambda u: (u["sid"], u["cx"], u["tt"], u["dong"], -(u["p"] or 0)))

    integrity = [dict(r) for r in con.execute(
        "SELECT * FROM integrity_checks ORDER BY snapshot_id, complex_no, trade_type")]

    return {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "complexes": names,
        "snapshots": snapshots,
        "coverage": {str(k): v for k, v in coverage.items()},
        "units": units,
        "integrity": integrity,
        "pyeongByKey": {str(round(k, 1)): v for k, v in pyeong.items()},
    }


def main(out_path=None):
    out_path = out_path or DEFAULT_OUT
    data = build()
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if "__DASHBOARD_DATA__" not in html:
        raise SystemExit("template.html에 __DASHBOARD_DATA__ 자리표시자가 없습니다")
    # </script> 가 데이터 안에 들어가면 스크립트 블록이 조기 종료된다
    html = html.replace("__DASHBOARD_DATA__", payload.replace("</", "<\\/"))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    n_units = len(data["units"])
    n_upper = sum(u["ng"] for u in data["units"])
    print(f"대시보드 생성: {os.path.abspath(out_path)}")
    print(f"  스냅샷 {len(data['snapshots'])}개 · 물건 {n_units}건(하한) ~ {n_upper}건(상한)")
    print(f"  평형: {', '.join(f'{v}평' for v in sorted(set(data['pyeongByKey'].values())))}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
