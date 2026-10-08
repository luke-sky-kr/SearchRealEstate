#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""네이버 부동산 매물 수집기 (로컬 실행용, 표준 라이브러리만 사용)

실행:  python3 collect_naver.py        (Windows이면: py collect_naver.py)
출력:  스크립트 옆 naver_data/<수집시각>/ 폴더에
       - <단지번호>_A1.json (매매), <단지번호>_B1.json (전세)
       - counts.json  (네이버가 알려준 총 매물 수 — 누락 검증용)
       - complexes.json (단지번호-이름 매핑)

새 단지 추가: 아래 COMPLEXES 에 이름만 추가하면 자동으로 번호를 찾습니다.
자동 검색이 실패하면 {"이름": 12345} 형태로 번호를 직접 적으세요.

── API 메모 (2026-08-22 실측) ────────────────────────────────────────
구 m.land.naver.com API(getComplexArticleList)는 폐기되었다. null을 반환하며
m.land 경로는 fin.land.naver.com(네이버파이낸셜)으로 리다이렉트된다.

현행 엔드포인트:
  POST https://fin.land.naver.com/front-api/v1/complex/article/list
  body: {complexNumber, tradeTypes:["A1"], size:<=30, userChannelType:"PC",
         articleSortType:"PRICE_ASC", seed, lastInfo}
  응답: {result: {totalCount, hasNextPage, lastInfo, seed, list:[...]}}

- 쿠키 없이 호출하면 429(TOO_MANY_REQUESTS). 먼저 페이지를 한 번 GET 해서
  세션 쿠키를 받아야 한다.
- 페이지네이션은 페이지 번호가 아니라 커서(lastInfo)다. 첫 응답의 seed를
  이후 요청에 그대로 실어야 목록이 흔들리지 않는다.
- 응답의 각 항목은 대표매물(representativeArticleInfo) + 네이버가 묶은
  중복매물(duplicatedArticleInfo.articleInfoList) 구조다. totalCount는
  대표매물 기준 개수이므로, 원시 매물 수는 그보다 많다.
"""
import json, os, re, sys, time, urllib.request, urllib.parse, urllib.error
import http.cookiejar
from datetime import datetime

# ── 설정 ──────────────────────────────────────────────────────────
COMPLEXES = {
    "래미안신반포팰리스": 108228,  # None = 자동 검색, 또는 108228 처럼 직접 지정
    "래미안퍼스티지": None,
    "삼성힐스테이트2단지": 24008,   # 강남구 삼성동, 926세대
    "LG한강자이": 3143,             # 용산구 이촌동, 656세대
    "신현대(현대9,11,12차)": 3037,  # 강남구 압구정동 — "신현대"만으로는 전국에 열 곳 넘는다
    "한가람": 867,                  # 용산구 이촌동, 2036세대 — "한가람"만으로는 전국에 여럿
}
TRADE_TYPES = ["A1", "B1"]      # A1=매매, B1=전세 (B2=월세, B3=단기)
DELAY_SEC = 0.8                 # 요청 간격 (서버 부담 방지)
PAGE_SIZE = 30                  # API 최대값
# ──────────────────────────────────────────────────────────────────

API = "https://fin.land.naver.com/front-api/v1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def _headers(referer):
    return {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept": "application/json, text/plain, */*", "Referer": referer}


def http_get(url, referer="https://fin.land.naver.com/", follow_redirect=True):
    req = urllib.request.Request(url, headers=_headers(referer))
    if follow_redirect:
        with _opener.open(req, timeout=20) as r:
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return e.code, e.headers.get("Location", ""), ""
        raise


def api_post(path, body, referer):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(), method="POST",
        headers={**_headers(referer), "Content-Type": "application/json",
                 "Origin": "https://fin.land.naver.com"})
    try:
        with _opener.open(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        if e.code == 429:
            raise RuntimeError(
                "429 TOO_MANY_REQUESTS — 네이버가 요청을 제한하고 있습니다. "
                "몇 분 기다렸다 다시 실행하세요.") from None
        raise RuntimeError(f"HTTP {e.code} {path}: {raw[:200]}") from None
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"JSON 파싱 실패 (차단 가능성): {path}\n{raw[:300]}")
    if not d.get("isSuccess", True):
        raise RuntimeError(f"API 오류 {d.get('detailCode')}: {d.get('message')}")
    return d


def open_session():
    """세션 쿠키 획득. 이걸 건너뛰면 API가 429를 돌려준다."""
    http_get("https://fin.land.naver.com/")


def resolve_complex_no(name):
    """단지명 -> 네이버 단지번호.

    1차: new.land 검색 API. 이름이 정확히 일치하는 아파트를 우선 고른다.
    2차: m.land 검색 리다이렉트. 검색어가 한 단지로 딱 떨어질 때만 통한다.
    """
    url = ("https://new.land.naver.com/api/search?page=1&keyword="
           + urllib.parse.quote(name))
    try:
        _, _, body = http_get(url)
        cands = json.loads(body).get("complexes") or []
    except Exception:
        cands = []
    if cands:
        def norm(x): return re.sub(r"[\s()]", "", x or "")
        exact = [c for c in cands if norm(c.get("complexName")) == norm(name)]
        picked = (exact or cands)[0]
        if not exact and len(cands) > 1:
            others = ", ".join(f"{c.get('complexName')}({c.get('complexNo')})"
                               for c in cands[1:4])
            print(f"    ! '{name}' 검색 결과가 여러 개입니다. "
                  f"{picked.get('complexName')}({picked.get('complexNo')})를 씁니다. "
                  f"다른 후보: {others}")
        return int(picked["complexNo"])

    m2 = "https://m.land.naver.com/search/result/" + urllib.parse.quote(name)
    code, loc, body = http_get(m2, follow_redirect=False)
    for target in (loc or "", body or ""):
        m = re.search(r"/complex(?:es|/info)/(\d+)", target)
        if m:
            return int(m.group(1))
    code, final_url, body = http_get(m2)
    m = (re.search(r"/complex(?:es|/info)/(\d+)", final_url)
         or re.search(r"/complex(?:es|/info)/(\d+)", body)
         or re.search(r'complexNumber["\']?\s*[:=]\s*["\']?(\d+)', body))
    if m:
        return int(m.group(1))
    raise RuntimeError(f"단지번호 자동검색 실패: {name} — COMPLEXES에 번호를 직접 지정하세요")


def fetch_articles(complex_no, trade_type):
    """커서 페이지네이션으로 전 매물 수집. (항목 리스트, 네이버 표시 총 수) 반환."""
    items, seed, last_info, total = [], None, None, None
    referer = f"https://fin.land.naver.com/complexes/{complex_no}"
    page = 0
    while True:
        page += 1
        body = {"complexNumber": complex_no, "tradeTypes": [trade_type],
                "size": PAGE_SIZE, "userChannelType": "PC",
                "articleSortType": "PRICE_ASC"}
        if seed:
            body["seed"] = seed
        if last_info:
            body["lastInfo"] = last_info
        result = api_post("/complex/article/list", body, referer)["result"]
        lst = result.get("list") or []
        items.extend(lst)
        if total is None:
            total = result.get("totalCount")
        seed = seed or result.get("seed")
        last_info = result.get("lastInfo")
        print(f"    page {page}: +{len(lst)} (누적 {len(items)} / 총 {total})")
        if not result.get("hasNextPage") or not lst:
            break
        if page > 200:
            raise RuntimeError("페이지 순회가 끝나지 않습니다 — 커서 로직 확인 필요")
        time.sleep(DELAY_SEC)
    return items, total


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    ts = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    out = os.path.join(base, "naver_data", ts)
    os.makedirs(out, exist_ok=True)

    print("[세션] 쿠키 획득 중...")
    open_session()
    time.sleep(DELAY_SEC)

    counts, cmap = {}, {}
    for name, no in COMPLEXES.items():
        if not no:
            print(f"[검색] {name} 단지번호 조회 중...")
            no = resolve_complex_no(name)
            time.sleep(DELAY_SEC)
        cmap[str(no)] = name
        print(f"[수집] {name} (단지번호 {no})")
        for tt in TRADE_TYPES:
            label = {"A1": "매매", "B1": "전세", "B2": "월세", "B3": "단기"}.get(tt, tt)
            print(f"  - {label}({tt})")
            items, tot = fetch_articles(no, tt)
            with open(os.path.join(out, f"{no}_{tt}.json"), "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
            counts[f"{no}_{tt}"] = tot
            print(f"    완료: 대표매물 {len(items)}건 / 네이버 표시 {tot}건")
            time.sleep(DELAY_SEC)

    with open(os.path.join(out, "counts.json"), "w", encoding="utf-8") as f:
        json.dump(counts, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "complexes.json"), "w", encoding="utf-8") as f:
        json.dump(cmap, f, ensure_ascii=False, indent=1)
    print(f"\n✅ 수집 완료 → {out}")
    print(f"다음: python3 scripts/ingest.py {out}")
    return out


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 오류: {e}", file=sys.stderr)
        sys.exit(1)
