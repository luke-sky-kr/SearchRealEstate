#!/usr/bin/env python3
"""Dedup agent: 네이버 묶음 위에 얹는 보수적 2차 병합.

물량은 단일 수치가 아니라 **밴드**로 본다.
  상한 = 네이버 동일매물묶기 결과 (raw_articles.naver_group)
  하한 = 여기서 계산하는 2차 병합 결과 (dedup_assignments)

왜 밴드인가 (2026-08-22 실측 근거):
  수집된 대표매물 716건 중 정확한 층이 표기된 것은 28건(4%)뿐이고, 나머지는
  전부 저/중/고 대역이다. 34층 건물의 '저'는 11개 층에 걸치므로
  (106동, 저, 84.5㎡, 39.0억, 남향) 4건이 4중 등록인지 실제 4세대인지
  데이터만으로는 판별할 수 없다. 어느 한쪽을 정답으로 고르면 물량을 반드시
  과대 또는 과소 계상하게 되므로, 두 수치를 모두 남긴다.

1단계 (규칙, 자동): 같은 (단지, 거래유형, 동, 전용면적) 버킷 내 대표매물 쌍을 점수화.
  - 방향 불일치는 즉시 배제. 한 세대가 두 방향일 수 없다 — 층이 대역으로만
    주어지는 상황에서 가장 신뢰할 수 있는 분할자다.
  - 층: 정확층끼리 일치 +3.0 / 대역끼리 일치는 +0.5 (정보량이 적어 약하게)
        불일치는 배제
  - 가격 동일 +2.0, 방향 일치 +1.0, 설명 유사도 가점
  → 3.5 이상 병합 (= 동·층표기·전용·가격·방향 완전 일치 수준), 1.5~3.5는 애매 쌍

2단계 (AI 판정): 애매 쌍을 Claude가 읽고 verdicts JSON 작성 후 apply.

사용: python3 dedup.py <snapshot_id>
      python3 dedup.py apply <snapshot_id> <verdicts.json>
출력: dedup_assignments 테이블, data/ambiguous_<sid>.json
"""
import json, sqlite3, sys, os, re, itertools
from collections import defaultdict

DB = os.path.join(os.path.dirname(__file__), "..", "data", "monitor.db")

BANDS = ("저", "중", "고", "탑")


def floor_score(x, y):
    """(정확층/대역, 총층)으로 층 정합성 점수. 배제는 -99."""
    fx, fy = x["floor_target"], y["floor_target"]
    if not fx or not fy:
        return 0.0
    ex, ey = fx.isdigit(), fy.isdigit()
    if ex and ey:
        return 3.0 if fx == fy else -99
    if ex or ey:
        # 정확층 vs 대역: 대역 안에 드는지 확인
        exact, band = (int(fx), fy) if ex else (int(fy), fx)
        total = x["floor_total"] or y["floor_total"]
        if not total or band not in BANDS:
            return 0.0
        r = exact / total
        actual = "저" if r <= 1/3 else ("중" if r <= 2/3 else "고")
        return 0.5 if actual == band else -99
    return 0.5 if fx == fy else -99


def tokens(desc):
    return set(re.findall(r"[가-힣A-Za-z0-9]+", desc)) if desc else set()


def desc_sim(a, b):
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


_PHONE = re.compile(r"\d[\d.\-]{3,}\d")
# 긴 것부터: 앞 대안이 먼저 맞으면 '공인중개사무소'가 '공인중개사'+'무소'로 쪼개진다
_REALTOR_NOISE = re.compile(
    r"\(주\)|주식회사|유한회사|분사무소|공인중개사사무소|공인중개사무소|중개사사무소|"
    r"중개사무소|공인중개사|공인중개|부동산|중개사|중개|법인|사무소|사무|컨설팅")


# 표기 정규화로 안 잡히는 동일 사무소. 지명을 일괄로 걷어내면 '현대'/'반포현대'처럼
# 흔한 상호의 다른 사무소까지 묶이므로, 근거가 확인된 것만 하나씩 넣는다.
REALTOR_ALIAS = {
    "반포한국": "한국",  # '한국593-3000'과 같은 '韓國 O … O' 서명 문구 (snapshot 18 검수)
}


def realtor_key(name):
    """중개사 이름에서 표기 변형을 걷어낸 비교 키.

    같은 사무소가 매물마다 이름을 다르게 올린다(snapshot 17 애매 쌍 검수, 2026-10-17):
      '딥블루공인중개사사무소' / '압구정딥블루공인중개사사무소'
      '주식회사골드웰부동산중개법인' / '압구정골드웰부동산중개사무소'
    문자열 그대로 비교하면 동일 중개사 배제 규칙을 빠져나가고, 같은 문구 틀 덕에
    설명 유사도 가점까지 받는다. 법인 표기·전화번호·업종 접미어·공백과 앞의
    '압구정'을 걷어내고 비교한다. 지점명('여의도점')은 남긴다 — 럭셔리앤하우스
    지점들은 서로 다른 사무소다.
    키가 우연히 겹치면(예: '압구정현대' / '현대') 별개 세대 쪽으로 판정되어 하한이
    덜 조여질 뿐, 다른 세대를 합치는 쪽으로는 틀리지 않는다.
    """
    s = _REALTOR_NOISE.sub("", _PHONE.sub("", name or ""))
    s = re.sub(r"[^\w]", "", s)
    if s.startswith("압구정") and len(s) > 3:
        s = s[3:]
    return REALTOR_ALIAS.get(s, s)


def pair_score(x, y):
    """같은 (단지,거래유형,동,전용면적) 버킷 내 대표매물 쌍의 동일물건 점수.

    아래 두 휴리스틱은 snapshot 11 애매 쌍 733건을 실제로 판정해 확인한 패턴이다
    (2026-09-12 검수):
      - 동일 중개사 + 가격 다름 → 거의 항상 별개 세대. 한 사무소가 같은 동·같은
        층대역에 서로 다른 세대를 동시에 중개하며 각자 다른 특징을 적어 올린다.
        (예: "주인직.집보기좋음" vs "확장.초깨끗.정원뷰" — 같은 중개사, 가격도 다름)
      - 다른 중개사 + 가격 근접(3% 이내) + 설명 유사도 ≥0.5 → 같은 세대를 복수
        중개사가 공동중개로 올린 전형적 패턴. (예: "한강 premium 재건축" ↔
        "50P 매수추천, 한강 premium 재건축")
    """
    if x["direction"] and y["direction"] and x["direction"] != y["direction"]:
        return -99  # 한 세대가 두 방향일 수 없다
    s = floor_score(x, y)
    if s <= -90:
        return -99
    px, py = x["price_krw"], y["price_krw"]
    rx = realtor_key(x["realtor"])
    same_realtor = bool(rx) and rx == realtor_key(y["realtor"])
    diff = None
    if px and py:
        diff = abs(px - py) / max(px, py)
        if same_realtor and diff > 0:
            return -99  # 동일 중개사가 가격만 다르게 올린 두 건은 별개 세대로 확정
        if diff == 0:      s += 2.0
        elif diff <= 0.01: s += 1.0
        elif diff <= 0.03: s += 0.0
        else:              s -= 2.5
    if x["direction"] and y["direction"]:
        s += 1.0
    ds = desc_sim(x["description"], y["description"])
    if ds >= 0.6:   s += 1.5
    elif ds >= 0.3: s += 0.5
    if not same_realtor and ds >= 0.5 and diff is not None and diff <= 0.03:
        s += 1.5  # 공동중개 재구성 문구 패턴 — 동일 세대 쪽으로 강하게 밀어준다
    return s


RULE_SAME = 3.5      # 이상이면 규칙만으로 동일 물건 확정
RULE_DIFF = 1.5      # 미만이면 다른 물건 확정, 사이는 AI 판정 대상

PAIR_FIELDS = ("article_no", "building", "floor_info", "price_krw", "direction",
               "description", "realtor", "confirmed_date", "exposure_date",
               "naver_group_n")


class UF:
    def __init__(s, keys): s.p = {k: k for k in keys}
    def find(s, k):
        while s.p[k] != k:
            s.p[k] = s.p[s.p[k]]; k = s.p[k]
        return k
    def union(s, a, b):
        ra, rb = s.find(a), s.find(b)
        if ra != rb: s.p[ra] = rb


def load(con, sid):
    return [dict(r) for r in con.execute(
        "SELECT * FROM raw_articles WHERE snapshot_id=?", (sid,))]


def write_groups(con, sid, arts, uf, ai_pairs=frozenset()):
    """union-find 결과를 dedup_assignments에 기록. 그룹별 method 판정 포함."""
    con.execute("DELETE FROM dedup_assignments WHERE snapshot_id=?", (sid,))
    by_no = {a["article_no"]: a for a in arts}
    groups = defaultdict(list)
    for a in arts:
        groups[uf.find(a["article_no"])].append(a["article_no"])
    for root, members in groups.items():
        gkey = f"g{sid}_{root}"
        naver_groups = {by_no[m]["naver_group"] for m in members}
        if len(members) == 1:
            method, conf = "singleton", 1.0
        elif any(m in ai_pairs for m in members):
            method, conf = "ai", 0.85
        elif len(naver_groups) > 1:
            method, conf = "rule", 0.9      # 자체 2차 병합이 추가로 합친 그룹
        else:
            method, conf = "naver", 1.0     # 네이버 묶음 그대로
        for m in members:
            con.execute(
                "INSERT INTO dedup_assignments(snapshot_id, article_no, group_key,"
                " method, confidence, note) VALUES (?,?,?,?,?,?)",
                (sid, m, gkey, method, conf, None))
    con.commit()
    return groups


def base_uf(arts):
    """네이버 묶음을 하한 계산의 출발점으로 삼는다 (상한 = 네이버 묶음)."""
    uf = UF([a["article_no"] for a in arts])
    for a in arts:
        if a["naver_group"]:
            uf.union(a["article_no"], a["naver_group"])
    return uf


def run(sid):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    arts = load(con, sid)
    if not arts:
        raise SystemExit(f"snapshot {sid}에 매물이 없습니다")
    uf = base_uf(arts)
    naver_groups = len({a["naver_group"] for a in arts})

    reps = [a for a in arts if a["is_repr"]]
    buckets = defaultdict(list)
    for a in reps:
        buckets[(a["complex_no"], a["trade_type"], a["building"] or "?",
                 round(a["area_exclusive"] or 0, 1))].append(a)

    ambiguous = []
    for key, items in buckets.items():
        for x, y in itertools.combinations(items, 2):
            s = pair_score(x, y)
            if s >= RULE_SAME:
                uf.union(x["article_no"], y["article_no"])
            elif s >= RULE_DIFF:
                ambiguous.append({
                    "score": round(s, 2), "bucket": list(map(str, key)),
                    "a": {k: x[k] for k in PAIR_FIELDS},
                    "b": {k: y[k] for k in PAIR_FIELDS},
                })

    groups = write_groups(con, sid, arts, uf)
    amb_path = os.path.join(os.path.dirname(DB), f"ambiguous_{sid}.json")
    json.dump(ambiguous, open(amb_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"snapshot {sid}: 원시 {len(arts)}건 / 네이버 묶음 {naver_groups}건(상한) "
          f"-> 자체 2차 병합 {len(groups)}건(하한)")
    print(f"  애매 쌍 {len(ambiguous)}건 -> {amb_path}")


def apply_ai_verdicts(sid, verdict_path):
    """AI 판정 결과 [{a, b, same: true/false, reason}] 반영 후 그룹 재계산."""
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    arts = load(con, sid)
    verdicts = json.load(open(verdict_path, encoding="utf-8"))
    # 기존 그룹(규칙 결과)을 출발점으로 삼아 AI가 same이라 한 쌍만 추가 병합한다.
    uf = UF([a["article_no"] for a in arts])
    gm = defaultdict(list)
    for r in con.execute("SELECT article_no, group_key FROM dedup_assignments "
                         "WHERE snapshot_id=?", (sid,)):
        gm[r["group_key"]].append(r["article_no"])
    for members in gm.values():
        for m in members[1:]:
            uf.union(members[0], m)
    ai_pairs = set()
    for v in verdicts:
        if v.get("same"):
            uf.union(str(v["a"]), str(v["b"]))
            ai_pairs.update({str(v["a"]), str(v["b"])})
    groups = write_groups(con, sid, arts, uf, ai_pairs)
    print(f"snapshot {sid}: AI 판정 {len(verdicts)}건 반영 -> 최종 {len(groups)}그룹")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "apply":
        apply_ai_verdicts(int(sys.argv[2]), sys.argv[3])
    else:
        run(int(sys.argv[1]))
