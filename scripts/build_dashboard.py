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
SUPPLY_GAP = 3.5        # 같은 전용 군집 안에서 공급면적이 이만큼 벌어지면 다른 평형 (≈1평)

# 분양 관례 평형. 공급면적 환산값과 시장에서 부르는 이름이 어긋나는 타입이 있다.
# LG한강자이 179M은 179.26㎡ = 54.2평이지만 분양 때부터 55평형으로 불렸고
# 매물 광고도 호가 비교도 전부 그 이름으로 돈다. 환산값은 그대로 두고(툴팁에 남는다)
# 라벨만 관례명으로 덮는다. (단지번호, 네이버 타입명) -> 평형.
PYEONG_ALIAS = {
    ("3143", "179M"): 55,   # LG한강자이 50평대
    ("3143", "180L"): 55,
}


def _chain(values, gap):
    """정렬 후 간격이 gap을 넘는 곳에서 끊는 단일연결 군집."""
    groups, cur = [], []
    for v in sorted(set(values)):
        if cur and v - cur[-1] > gap:
            groups.append(cur)
            cur = []
        cur.append(v)
    if cur:
        groups.append(cur)
    return groups


def cluster_areas(pairs):
    """(전용, 공급) 목록을 평형 군집으로 묶어 {(전용, 공급): 군집대표전용} 매핑을 만든다.

    1차는 전용면적이다. 같은 평형이라도 타입(A/B/C)에 따라 전용이 84.4~84.9로
    흩어지므로 단순 반올림은 한 평형을 34평/35평으로 쪼갠다.

    2차로 그 안에서 공급면적을 다시 끊는다. 전용이 붙어 있어도 공급이 1평 넘게
    벌어지면 다른 평형이다 — 신현대 183(전용 170.4·공급 183.5·55평)과
    188B(전용 171.4·공급 188.9·57평)는 전용이 1㎡ 차이라 한 덩어리가 되지만
    분양 평형은 두 평 다르다. 평형을 정하는 것은 전용이 아니라 공급이다.
    """
    by_ex = {}
    for ex, sup in pairs:
        by_ex.setdefault(ex, set()).add(sup)
    out = {}
    for exs in _chain(by_ex, CLUSTER_GAP):
        sups = {s for ex in exs for s in by_ex[ex]}
        for grp in _chain(sups, SUPPLY_GAP):
            member = set(grp)
            mine = [ex for ex in exs if by_ex[ex] & member]
            rep = statistics.median(mine)
            for ex in mine:
                for sup in by_ex[ex] & member:
                    out[(ex, sup)] = rep
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
    added = dict(con.execute("SELECT complex_no, added_at FROM complexes").fetchall())
    snapshots = [{"id": r["snapshot_id"], "at": r["collected_at"]}
                 for r in con.execute("SELECT * FROM snapshots ORDER BY snapshot_id")]

    arts = [dict(r) for r in con.execute("""
        SELECT r.*, d.group_key, d.method FROM raw_articles r
        JOIN dedup_assignments d
          ON d.snapshot_id = r.snapshot_id AND d.article_no = r.article_no""")]
    if not arts:
        raise SystemExit("dedup_assignments가 비어 있습니다. dedup.py를 먼저 실행하세요.")

    # 평형 군집은 단지 안에서만 묶는다. 단지를 가로질러 묶으면 전용면적이 비슷해도
    # 전용률이 달라 공급 평형이 어긋난다. 실제로 LG한강자이 55평(전용 134·공급 179)이
    # 래미안퍼스티지 52평(전용 136·공급 173), 팰리스 51평(전용 133·공급 168)과
    # 한 덩어리가 되어 셋 다 52평으로 표시됐다.
    ex_by_cx = defaultdict(list)
    for a in arts:
        if a["area_exclusive"] and a["area_supply"]:
            ex_by_cx[a["complex_no"]].append((a["area_exclusive"], a["area_supply"]))
    amap = {cx: cluster_areas(v) for cx, v in ex_by_cx.items()}
    rep_of = lambda a: amap.get(a["complex_no"], {}).get(
        (a["area_exclusive"], a["area_supply"]))

    # (단지, 군집 대표 전용면적) -> 평형 라벨. 공급면적 중앙값을 그 단지 안에서만 낸다.
    supply_by_rep, types_by_rep = defaultdict(list), defaultdict(set)
    for a in arts:
        rep = rep_of(a)
        if rep is None:
            continue
        if a["area_supply"]:
            supply_by_rep[(a["complex_no"], rep)].append(a["area_supply"])
        if a["area_name"]:
            types_by_rep[(a["complex_no"], rep)].add(a["area_name"])
    def label_pyeong(key, supplies):
        """군집의 평형 라벨. 속한 타입이 모두 같은 관례명을 가리키면 그걸 쓴다."""
        al = {PYEONG_ALIAS[(key[0], t)] for t in types_by_rep.get(key, ())
              if (key[0], t) in PYEONG_ALIAS}
        if len(al) == 1:
            return al.pop()
        return round(statistics.median(supplies) / PYEONG_M2)

    pyeong = {k: label_pyeong(k, v) for k, v in supply_by_rep.items()}

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
        rep_area = rep_of(lead)
        expo = sorted(m["exposure_date"] for m in members if m["exposure_date"])
        conf = sorted(m["confirmed_date"] for m in members if m["confirmed_date"])
        units.append({
            "sid": sid, "gk": gk, "uid": uid.get((sid, gk)),
            "cx": lead["complex_no"], "tt": lead["trade_type"],
            "dong": lead["building"] or "미상",
            "pk": round(rep_area, 1) if rep_area else 0,      # 평형 군집 키
            "py": pyeong.get((lead["complex_no"], rep_area), 0),  # 평형(공급 기준, 단지별)
            "ex": lead["area_exclusive"],
            "an": lead["area_name"],
            "ty": lead["area_name"] or "",                      # 공급면적 타입 (네이버 표기)
            "sup": lead["area_supply"],                         # 공급면적 ㎡
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
        # 색 슬롯은 추가 순서로 고정한다. 이름순으로 매기면 단지를 하나 넣을 때
        # 그 뒤 단지들의 색이 통째로 밀린다 — 색이 곧 단지 이름인데 이름이 바뀐다.
        "addedAt": added,
        "snapshots": snapshots,
        "coverage": {str(k): v for k, v in coverage.items()},
        "units": units,
        "integrity": integrity,
        # 평형 키는 단지마다 따로다 — "단지번호|대표전용면적"
        "pyeongByKey": {f"{cx}|{round(rep, 1)}": v for (cx, rep), v in pyeong.items()},
        "areaTypes": {f"{cx}|{round(rep, 1)}": sorted(v)
                      for (cx, rep), v in types_by_rep.items()},
        "supplyByKey": {f"{cx}|{round(rep, 1)}": round(statistics.median(v), 1)
                        for (cx, rep), v in supply_by_rep.items()},
        # 타입 단위 관례 평형 — 트리 잎 라벨이 상위 평형 노드와 어긋나지 않게 한다
        "aliasByType": {f"{cx}|{ty}": v for (cx, ty), v in PYEONG_ALIAS.items()},
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
    for cx, nm in sorted(data["complexes"].items(), key=lambda kv: kv[1]):
        pys = sorted({v for k, v in data["pyeongByKey"].items() if k.startswith(cx + "|")})
        print(f"  {nm}: {', '.join(f'{v}평' for v in pys)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
