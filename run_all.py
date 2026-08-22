#!/usr/bin/env python3
"""조사 1회를 끝까지 돌린다: 수집 → 적재 → dedup → 정합성 → 스냅샷 연결 → 대시보드.

이 프로젝트의 시간축은 **조사일**이다. 등록일 기준으로 물량을 세면 팔리거나
내린 매물이 응답에서 빠지는 탓에 언제나 조사일에 가까운 쪽이 많아 보인다.
그래서 조사 때마다 전량을 스냅샷으로 통째 저장하고, 조사일 축에서 비교한다.
이 스크립트를 주기적으로 돌리는 것 자체가 데이터를 만드는 일이다.

사용:
  python3 run_all.py                  수집부터 전부
  python3 run_all.py --no-collect     이미 받아둔 최신 naver_data 폴더로 적재부터
  python3 run_all.py --dir <폴더>      특정 수집 폴더로 적재부터
  python3 run_all.py --note "메모"     스냅샷에 메모를 남긴다
"""
import os, sys, glob, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "scripts"))

import collect_naver, ingest, dedup, integrity, link_snapshots, build_dashboard


def latest_dir():
    dirs = [d for d in glob.glob(os.path.join(HERE, "naver_data", "*")) if os.path.isdir(d)
            and not os.path.basename(d).startswith("_")]
    if not dirs:
        raise SystemExit("naver_data에 수집 폴더가 없습니다. --no-collect 없이 실행하세요.")
    return max(dirs, key=os.path.getmtime)


def step(n, total, title):
    print(f"\n[{n}/{total}] {title}", flush=True)


def main(argv):
    note = ""
    if "--note" in argv:
        note = argv[argv.index("--note") + 1]
    if "--dir" in argv:
        snap_dir = argv[argv.index("--dir") + 1]
    elif "--no-collect" in argv:
        snap_dir = latest_dir()
        print(f"수집 건너뜀 — 최신 폴더 사용: {snap_dir}")
    else:
        step(1, 6, "매물 수집 (네이버)")
        snap_dir = collect_naver.main()

    t0 = time.time()
    step(2, 6, "DB 적재")
    sid = ingest.ingest(snap_dir, note or os.path.basename(snap_dir))

    step(3, 6, "중복 판정 (네이버 묶음 + 자체 2차 병합)")
    dedup.run(sid)

    step(4, 6, "정합성 진단")
    status = integrity.run(sid, os.path.join(snap_dir, "counts.json"))

    step(5, 6, "조사일 간 물건 연결")
    link_snapshots.run()

    step(6, 6, "대시보드 생성")
    build_dashboard.main()

    print(f"\n{'='*58}")
    print(f"스냅샷 #{sid} 완료 · {time.time() - t0:.1f}초 · 정합성 {status}")
    if status == "missing":
        print("⚠ 수집 누락이 있습니다. 물량 수치를 쓰기 전에 수집기를 점검하세요.")
    print(f"대시보드: {os.path.join(HERE, 'dashboard', 'index.html')}")
    return 0 if status != "missing" else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        print("\n❌ 파이프라인 중단", file=sys.stderr)
        sys.exit(2)
