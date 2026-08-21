#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""네이버 부동산 매물 수집기 (로컬 실행용, 표준 라이브러리만 사용)

실행:  python3 collect_naver.py        (Windows이면: py collect_naver.py)
출력:  스크립트 옆 naver_data/<수집시각>/ 폴더에
       - <단지번호>_A1.json (매매), <단지번호>_B1.json (전세)
       - counts.json  (네이버가 알려준 총 매물 수 — 누락 검증용)
       - complexes.json (단지번호-이름 매핑)

새 단지 추가: 아래 COMPLEXES 에 이름만 추가하면 자동으로 번호를 찾습니다.
자동 검색이 실패하면 {"이름": "단지번호"} 형태로 번호를 직접 적으세요.
"""
import json, os, re, sys, time, urllib.request, urllib.parse
from datetime import datetime

# ── 설정 ──────────────────────────────────────────────────────────
COMPLEXES = {
    "래미안신반포팰리스": None,   # None = 자동 검색, 또는 "12345" 처럼 직접 지정
    "래미안퍼스티지": None,
}
TRADE_TYPES = ["A1", "B1"]      # A1=매매, B1=전세
DELAY_SEC = 0.8                 # 요청 간격 (서버 부담 방지)
# ──────────────────────────────────────────────────────────────────

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")

def http_get(url, referer="https://m.land.naver.com/", follow_redirect=True):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": referer,
        "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    if follow_redirect:
        with urllib.request.urlopen(req, timeout=20) as r:
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

def resolve_complex_no(name):
    """단지명 -> 네이버 단지번호(hscpNo). m.land 검색 리다이렉트/본문에서 추출."""
    url = "https://m.land.naver.com/search/result/" + urllib.parse.quote(name)
    code, loc, body = http_get(url, follow_redirect=False)
    for target in (loc or "", body or ""):
        m = re.search(r"/complex/info/(\d+)", target)
        if m:
            return m.group(1)
    # 리다이렉트가 아니면 본문에서 hscpNo 패턴 탐색
    code, _, body = http_get(url)
    m = re.search(r"/complex/info/(\d+)", body) or re.search(r'hscpNo["\']?[:=]["\']?(\d+)', body)
    if m:
        return m.group(1)
    raise RuntimeError(f"단지번호 자동검색 실패: {name} — COMPLEXES에 번호를 직접 지정하세요")

def fetch_articles(hscp_no, trade_type):
    """전 페이지 순회 수집. (매물 리스트, 네이버 총 매물 수) 반환."""
    items, page, tot_cnt = [], 1, None
    referer = f"https://m.land.naver.com/complex/info/{hscp_no}"
    while True:
        url = ("https://m.land.naver.com/complex/getComplexArticleList"
               f"?hscpNo={hscp_no}&tradTpCd={trade_type}&order=point_&showR0=N&page={page}")
        code, _, body = http_get(url, referer=referer)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"JSON 파싱 실패 (차단 가능성): {url}\n{body[:300]}")
        result = data.get("result") or {}
        lst = result.get("list") or []
        if tot_cnt is None:
            tot_cnt = result.get("totAtclCnt") or result.get("totCnt")
        items.extend(lst)
        more = result.get("moreDataYn")
        print(f"    page {page}: +{len(lst)} (누적 {len(items)})")
        if more != "Y" or not lst:
            break
        page += 1
        time.sleep(DELAY_SEC)
    return items, tot_cnt

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    ts = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    out = os.path.join(base, "naver_data", ts)
    os.makedirs(out, exist_ok=True)
    counts, cmap = {}, {}
    for name, no in COMPLEXES.items():
        if not no:
            print(f"[검색] {name} 단지번호 조회 중...")
            no = resolve_complex_no(name)
            time.sleep(DELAY_SEC)
        cmap[no] = name
        print(f"[수집] {name} (단지번호 {no})")
        for tt in TRADE_TYPES:
            label = {"A1": "매매", "B1": "전세"}.get(tt, tt)
            print(f"  - {label}({tt})")
            items, tot = fetch_articles(no, tt)
            with open(os.path.join(out, f"{no}_{tt}.json"), "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
            counts[f"{no}_{tt}"] = tot
            print(f"    완료: {len(items)}건 수집 / 네이버 표시 {tot}건")
            time.sleep(DELAY_SEC)
    with open(os.path.join(out, "counts.json"), "w", encoding="utf-8") as f:
        json.dump(counts, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "complexes.json"), "w", encoding="utf-8") as f:
        json.dump(cmap, f, ensure_ascii=False, indent=1)
    print(f"\n✅ 수집 완료 → {out}")
    print("이 폴더(naver_data)를 Claude 세션에 연결하거나, 안의 JSON들을 대화에 첨부해 주세요.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 오류: {e}", file=sys.stderr)
        sys.exit(1)
