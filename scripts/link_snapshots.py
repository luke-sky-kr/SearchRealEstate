#!/usr/bin/env python3
"""조사일(스냅샷)을 가로질러 같은 물건을 잇고, 조사일별 변화를 기록한다.

왜 필요한가:
  등록일(exposure_date) 축으로 물량을 세면 언제나 조사일에 가까운 쪽이 많아 보인다.
  팔리거나 내린 매물은 응답에서 사라져 과거 등록일의 물량이 구조적으로 깎이기
  때문이다(생존 편향). 조사일마다 전량을 저장해 두고 조사일 축에서 세야 물량과
  호가의 진짜 시간 변화를 볼 수 있다. raw_articles는 이미 스냅샷 단위로 쌓이지만
  dedup 그룹키는 스냅샷 안에서만 유효하므로, 여기서 스냅샷을 잇는 unit_id를 만든다.

매칭 규칙 (강한 순):
  1) 매물번호 겹침 — 같은 네이버 매물이 두 조사일에 다 있으면 같은 물건이다.
  2) 물건 키 일치 — (단지, 거래유형, 동, 전용면적, 층표기, 방향) + 호가 근접.
     중개사가 매물을 내렸다 다시 올리면 매물번호가 바뀌므로 이 경로가 필요하다.
  탐욕적 1:1 배정. 점수가 같으면 호가 차이가 작은 쪽을 먼저 붙인다.

사용: python3 scripts/link_snapshots.py [--rebuild]
      기본은 아직 연결되지 않은 스냅샷만 이어 붙인다. --rebuild는 전체 재계산.
"""
import sqlite3, sys, os
from collections import defaultdict
from datetime import date

DB = os.path.join(os.path.dirname(__file__), "..", "data", "monitor.db")

PRICE_NEAR = 0.03      # 호가가 이 비율 안이면 물건키 매칭 허용
KEY_FIELDS = ("complex_no", "trade_type", "building", "area_key", "floor_info", "direction")


def load_groups(con, sid):
    """스냅샷의 dedup 그룹을 물건 단위로 모은다."""
    rows = con.execute("""
        SELECT d.group_key, r.article_no, r.complex_no, r.trade_type, r.building,
               r.area_exclusive, r.floor_info, r.direction, r.price_krw, r.exposure_date
        FROM raw_articles r JOIN dedup_assignments d
          ON d.snapshot_id = r.snapshot_id AND d.article_no = r.article_no
        WHERE r.snapshot_id = ?""", (sid,)).fetchall()
    g = defaultdict(lambda: {"arts": set(), "members": []})
    for r in rows:
        g[r["group_key"]]["arts"].add(r["article_no"])
        g[r["group_key"]]["members"].append(dict(r))
    out = {}
    for gk, v in g.items():
        lead = min(v["members"],
                   key=lambda m: (m["price_krw"] is None, m["price_krw"]))
        out[gk] = {
            "gk": gk, "arts": v["arts"], "price": lead["price_krw"],
            "complex_no": lead["complex_no"], "trade_type": lead["trade_type"],
            "building": lead["building"],
            "area_key": round(lead["area_exclusive"] or 0, 1),
            "floor_info": lead["floor_info"], "direction": lead["direction"],
        }
    return out


def key_of(u):
    return tuple(u[f] for f in KEY_FIELDS)


def match(prev, cur):
    """이전 조사일 그룹 -> 현재 조사일 그룹의 1:1 배정. {cur_gk: (prev_gk, 방식)}"""
    cands = []
    by_art = defaultdict(list)
    for gk, u in prev.items():
        for a in u["arts"]:
            by_art[a].append(gk)

    seen = set()
    for gk, u in cur.items():
        hits = defaultdict(int)
        for a in u["arts"]:
            for pg in by_art.get(a, ()):
                hits[pg] += 1
        for pg, n in hits.items():
            cands.append((-100 * n, price_gap(u, prev[pg]), gk, pg, "article"))
            seen.add((gk, pg))

    by_key = defaultdict(list)
    for gk, u in prev.items():
        by_key[key_of(u)].append(gk)
    for gk, u in cur.items():
        for pg in by_key.get(key_of(u), ()):
            if (gk, pg) in seen:
                continue
            gap = price_gap(u, prev[pg])
            if gap <= PRICE_NEAR:
                cands.append((-1, gap, gk, pg, "key"))

    cands.sort()
    taken_cur, taken_prev, out = set(), set(), {}
    for _, _, gk, pg, how in cands:
        if gk in taken_cur or pg in taken_prev:
            continue
        taken_cur.add(gk); taken_prev.add(pg)
        out[gk] = (pg, how)
    return out


def price_gap(a, b):
    pa, pb = a["price"], b["price"]
    if not pa or not pb:
        return 1.0
    return abs(pa - pb) / max(pa, pb)


def run(rebuild=False):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    snaps = [dict(r) for r in con.execute(
        "SELECT snapshot_id, collected_at FROM snapshots ORDER BY snapshot_id")]
    if not snaps:
        raise SystemExit("스냅샷이 없습니다.")

    if rebuild:
        con.execute("DELETE FROM unit_links")
        con.execute("DELETE FROM unit_events")
        done = set()
    else:
        done = {r[0] for r in con.execute("SELECT DISTINCT snapshot_id FROM unit_links")}

    seq = 0
    for r in con.execute("SELECT unit_id FROM unit_links"):
        try: seq = max(seq, int(r["unit_id"][1:]))
        except (ValueError, IndexError): pass

    first_seen = {}   # unit_id -> 최초 관측 조사일(date)
    for r in con.execute("""SELECT l.unit_id, MIN(s.collected_at) at FROM unit_links l
                            JOIN snapshots s USING(snapshot_id) GROUP BY l.unit_id"""):
        first_seen[r["unit_id"]] = r["at"][:10]

    # 조사일마다 실제로 훑은 단지 집합. 단지를 새로 추가하거나 빼면
    # 그 단지의 물건이 통째로 신규/소멸로 잡히는데, 그건 시장이 아니라
    # 조사 범위가 바뀐 것이다. 이걸 구분하려면 커버리지를 알아야 한다.
    covered = {}
    for r in con.execute("""SELECT snapshot_id, complex_no FROM raw_articles
                            GROUP BY snapshot_id, complex_no"""):
        covered.setdefault(r["snapshot_id"], set()).add(r["complex_no"])

    prev_groups, prev_sid, prev_unit = None, None, {}
    if done:
        last = max(done)
        prev_sid = last
        prev_groups = load_groups(con, last)
        prev_unit = {r["group_key"]: r["unit_id"] for r in con.execute(
            "SELECT group_key, unit_id FROM unit_links WHERE snapshot_id=?", (last,))}
        # 연결 뒤에 지난 스냅샷의 dedup을 다시 돌리면 그룹키가 바뀌어 저장된
        # unit_links와 어긋난다. 그 위에 이어 붙이면 깨지므로 전체를 다시 잇는다.
        if set(prev_unit) != set(prev_groups):
            print(f"  스냅샷 {last}의 dedup 그룹이 연결 이후 바뀜 -> 전체 재연결")
            con.close()
            return run(rebuild=True)

    for sn in snaps:
        sid = sn["snapshot_id"]
        if sid in done:
            continue
        cur = load_groups(con, sid)
        cur_cov = covered.get(sid, set())
        prev_cov = covered.get(prev_sid, set()) if prev_sid else set()
        today = sn["collected_at"][:10]
        assigned = match(prev_groups, cur) if prev_groups else {}

        con.execute("DELETE FROM unit_links WHERE snapshot_id=?", (sid,))
        con.execute("DELETE FROM unit_events WHERE snapshot_id=?", (sid,))
        cur_unit = {}
        for gk, u in cur.items():
            hit = assigned.get(gk)
            if hit:
                uid, how = prev_unit[hit[0]], hit[1]
                prev_price = prev_groups[hit[0]]["price"]
            else:
                seq += 1
                uid, how, prev_price = f"u{seq:06d}", "new", None
                first_seen[uid] = today
            cur_unit[gk] = uid
            con.execute("INSERT INTO unit_links VALUES (?,?,?,?)", (sid, gk, uid, how))

            if prev_price is None:
                # 직전 조사에 아예 없던 단지라면 신규 매물이 아니라 조사 편입이다.
                ev = "new" if (not prev_sid or u["complex_no"] in prev_cov) else "enter"
                delta = None
            else:
                delta = (u["price"] or 0) - prev_price
                ev = "flat" if delta == 0 else ("up" if delta > 0 else "down")
            con.execute("INSERT INTO unit_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (sid, uid, u["complex_no"], u["trade_type"], ev, u["price"],
                         prev_price, delta, prev_sid if prev_price is not None else None,
                         days_between(first_seen.get(uid, today), today)))

        # 직전 조사일에는 있었는데 이번에 사라진 물건
        if prev_groups:
            alive = set(cur_unit.values())
            for pg, u in prev_groups.items():
                uid = prev_unit[pg]
                if uid in alive or u["complex_no"] not in cur_cov:
                    continue   # 이번에 안 훑은 단지는 사라진 게 아니라 안 본 것이다
                con.execute("INSERT INTO unit_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (sid, uid, u["complex_no"], u["trade_type"], "gone", None,
                             u["price"], None, prev_sid,
                             days_between(first_seen.get(uid, today), today)))

        cnt = dict(con.execute("""SELECT event, COUNT(*) FROM unit_events
                                  WHERE snapshot_id=? GROUP BY event""", (sid,)).fetchall())
        n_new, n_gone, n_enter = cnt.get("new", 0), cnt.get("gone", 0), cnt.get("enter", 0)
        skipped = sorted(prev_cov - cur_cov) if prev_sid else []
        chg = dict(con.execute("""SELECT event, COUNT(*) FROM unit_events
                                  WHERE snapshot_id=? AND event IN ('up','down')
                                  GROUP BY event""", (sid,)).fetchall())
        line = (f"  스냅샷 {sid} ({today}): 물건 {len(cur)}건 · 신규 {n_new} · 소멸 {n_gone} · "
                f"호가↑ {chg.get('up', 0)} · 호가↓ {chg.get('down', 0)}")
        if n_enter:
            line += f" · 조사편입 {n_enter}(단지 추가분, 신규 아님)"
        if skipped:
            line += f" · 이번에 안 훑은 단지 {', '.join(skipped)}(소멸로 세지 않음)"
        print(line)

        prev_groups, prev_sid, prev_unit = cur, sid, cur_unit
        con.commit()

    total = con.execute("SELECT COUNT(DISTINCT unit_id) FROM unit_links").fetchone()[0]
    print(f"누적 물건 {total}건이 {len(snaps)}개 조사일에 걸쳐 연결됨")


def days_between(a, b):
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    run(rebuild="--rebuild" in sys.argv)
