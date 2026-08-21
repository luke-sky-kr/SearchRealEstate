#!/usr/bin/env python3
"""Dedup agent 1단계: 규칙 기반 중복 매물 판정.

같은 실물(물건)을 여러 중개사가 올린 매물을 그룹으로 묶는다.
- 동일 그룹 후보: 같은 (단지, 거래유형, 동, 전용면적)
- 층 일치성: 정확층 일치(강), 층대역(저/중/고) 정합(중), 불일치(배제)
- 가격 근접성, 방향, 설명 유사도로 점수화
- 확신 구간은 rule로 확정, 애매 구간은 ambiguous로 내보내 AI가 판정

사용: python3 dedup.py <snapshot_id>
출력: dedup_assignments 테이블 (rule/singleton), data/ambiguous_<sid>.json
"""
import json, sqlite3, sys, os, re, itertools
from collections import defaultdict

DB = os.path.join(os.path.dirname(__file__), "..", "data", "monitor.db")

def parse_floor(fi):
    """'15/23' -> (15, 23, None), '중/23' -> (None, 23, '중')"""
    if not fi:
        return (None, None, None)
    parts = str(fi).split("/")
    total = None
    if len(parts) == 2 and parts[1].isdigit():
        total = int(parts[1])
    f = parts[0]
    if f.isdigit():
        return (int(f), total, None)
    band = f if f in ("저", "중", "고", "탑", "B1") else None
    return (None, total, band)

def band_of(floor, total):
    if floor is None or not total:
        return None
    r = floor / total
    return "저" if r <= 1/3 else ("중" if r <= 2/3 else "고")

def floor_score(a, b):
    fa, ta, ba = parse_floor(a); fb, tb, bb = parse_floor(b)
    if fa is not None and fb is not None:
        return 3.0 if fa == fb else -99
    if fa is not None and bb:  # 정확층 vs 대역
        return 1.5 if band_of(fa, ta or tb) == bb else -99
    if fb is not None and ba:
        return 1.5 if band_of(fb, tb or ta) == ba else -99
    if ba and bb:
        return 1.0 if ba == bb else -99
    return 0.5  # 정보 부족 — 판단 불가, 약한 가점만

def tokens(desc):
    if not desc:
        return set()
    return set(re.findall(r"[가-힣A-Za-z0-9]+", desc))

def desc_sim(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def pair_score(x, y):
    """같은 (단지,거래유형,동,전용면적) 내 매물 쌍의 동일물건 점수"""
    s = floor_score(x["floor_info"], y["floor_info"])
    if s <= -90:
        return -99
    px, py = x["price_krw"], y["price_krw"]
    if px and py:
        diff = abs(px - py) / max(px, py)
        if diff == 0:      s += 2.0
        elif diff <= 0.01: s += 1.0
        elif diff <= 0.03: s += 0.0
        else:              s -= 2.5
    if x["direction"] and y["direction"]:
        s += 0.5 if x["direction"] == y["direction"] else -1.5
    ds = desc_sim(x["description"], y["description"])
    if ds >= 0.6:   s += 1.5
    elif ds >= 0.3: s += 0.5
    return s

RULE_SAME = 4.0      # 이상이면 규칙만으로 동일 물건 확정
RULE_DIFF = 1.0      # 미만이면 다른 물건 확정, 사이는 AI 판정 대상

class UF:
    def __init__(s, keys): s.p = {k: k for k in keys}
    def find(s, k):
        while s.p[k] != k:
            s.p[k] = s.p[s.p[k]]; k = s.p[k]
        return k
    def union(s, a, b): s.p[s.find(a)] = s.find(b)

def run(sid):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    arts = [dict(r) for r in con.execute(
        "SELECT * FROM raw_articles WHERE snapshot_id=?", (sid,))]
    buckets = defaultdict(list)
    for a in arts:
        key = (a["complex_no"], a["trade_type"], a["building"] or "?",
               round(a["area_exclusive"] or 0, 1))
        buckets[key].append(a)

    uf = UF([a["article_no"] for a in arts])
    ambiguous = []
    for key, items in buckets.items():
        for x, y in itertools.combinations(items, 2):
            s = pair_score(x, y)
            if s >= RULE_SAME:
                uf.union(x["article_no"], y["article_no"])
            elif s >= RULE_DIFF:
                ambiguous.append({
                    "score": round(s, 2), "bucket": list(map(str, key)),
                    "a": {k: x[k] for k in ("article_no","floor_info","price_krw","direction","description","realtor","confirmed_date","same_addr_cnt")},
                    "b": {k: y[k] for k in ("article_no","floor_info","price_krw","direction","description","realtor","confirmed_date","same_addr_cnt")},
                })

    con.execute("DELETE FROM dedup_assignments WHERE snapshot_id=?", (sid,))
    groups = defaultdict(list)
    for a in arts:
        groups[uf.find(a["article_no"])].append(a["article_no"])
    for root, members in groups.items():
        gkey = f"g{sid}_{root}"
        for m in members:
            method = "singleton" if len(members) == 1 else "rule"
            con.execute("INSERT INTO dedup_assignments VALUES (?,?,?,?,?,?)",
                        (sid, m, gkey, method, 0.95 if method == "rule" else 1.0, None))
    con.commit()

    amb_path = os.path.join(os.path.dirname(DB), f"ambiguous_{sid}.json")
    json.dump(ambiguous, open(amb_path, "w"), ensure_ascii=False, indent=1)
    n_groups = len(groups)
    print(f"snapshot {sid}: {len(arts)} articles -> {n_groups} rule-groups, "
          f"{len(ambiguous)} ambiguous pairs -> {amb_path}")

def apply_ai_verdicts(sid, verdict_path):
    """AI 판정 결과 [{a, b, same: true/false, reason}] 반영 후 그룹 재계산"""
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    verdicts = json.load(open(verdict_path))
    rows = [dict(r) for r in con.execute(
        "SELECT article_no, group_key, method FROM dedup_assignments WHERE snapshot_id=?", (sid,))]
    uf = UF([r["article_no"] for r in rows])
    ai_pairs = set()
    for r in rows:  # 기존 rule 그룹 유지
        pass
    gm = defaultdict(list)
    for r in rows:
        gm[r["group_key"]].append(r["article_no"])
    for members in gm.values():
        for m in members[1:]:
            uf.union(members[0], m)
    for v in verdicts:
        if v.get("same"):
            uf.union(str(v["a"]), str(v["b"]))
            ai_pairs.add(str(v["a"])); ai_pairs.add(str(v["b"]))
    con.execute("DELETE FROM dedup_assignments WHERE snapshot_id=?", (sid,))
    groups = defaultdict(list)
    for r in rows:
        groups[uf.find(r["article_no"])].append(r["article_no"])
    for root, members in groups.items():
        gkey = f"g{sid}_{root}"
        for m in members:
            if len(members) == 1:
                method, conf = "singleton", 1.0
            elif m in ai_pairs:
                method, conf = "ai", 0.85
            else:
                method, conf = "rule", 0.95
            con.execute("INSERT INTO dedup_assignments VALUES (?,?,?,?,?,?)",
                        (sid, m, gkey, method, conf, None))
    con.commit()
    print(f"snapshot {sid}: AI verdicts applied -> {len(groups)} final groups")

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "apply":
        apply_ai_verdicts(int(sys.argv[2]), sys.argv[3])
    else:
        run(int(sys.argv[1]))
