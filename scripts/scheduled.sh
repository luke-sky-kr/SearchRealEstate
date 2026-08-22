#!/usr/bin/env bash
# Windows 작업 스케줄러가 wsl.exe로 부르는 진입점.
#
# 노트북이 절전 중이어도 예약 시각에 깨어나 이걸 돌린다. 꺼져 있었으면
# 그 조사일은 결측으로 남는데, 그래도 된다 — 대시보드는 안 훑은 날을
# 0이 아니라 결측으로 그린다. 억지로 채운 값이 진짜 값보다 나쁘다.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$HERE/data/schedule.log"
mkdir -p "$HERE/data"

# 앞 조사가 아직 안 끝났으면 겹쳐 돌리지 않는다. 같은 수집 폴더를 두 번
# 적재하면 가짜 조사일이 생겨 추이가 통째로 왜곡된다.
exec 9>"$HERE/data/.schedule.lock"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] 앞 조사가 진행 중이라 건너뜀" >>"$LOG"
  exit 0
fi

{
  echo "===== $(date '+%F %T %z') 조사 시작"
  cd "$HERE" && python3 run_all.py --note "예약 조사"
  echo "===== $(date '+%F %T %z') 종료 (exit $?)"
  echo
} >>"$LOG" 2>&1
