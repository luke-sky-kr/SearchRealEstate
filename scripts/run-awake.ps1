# 작업이 도는 동안 시스템이 다시 대기로 들어가지 않게 붙잡는다.
#
# Modern Standby 노트북은 예약 작업을 시작시킨 뒤 몇 분 만에 도로 잠든다.
# 그러면 WSL VM이 통째로 정지하고, 수집이 중간에 멈춘 채로 시간 제한에
# 걸려 죽는다(2026-08-23 08:45 실측: 08:49 재진입 → 09:15 강제 종료).
# 수집은 3분이면 끝나므로 그동안만 잡아 두면 된다.
$sig = @'
[DllImport("kernel32.dll", SetLastError=true)]
public static extern uint SetThreadExecutionState(uint esFlags);
'@
$k = Add-Type -MemberDefinition $sig -Name Power -Namespace Win32 -PassThru
$CONTINUOUS = [uint32]0x80000000
$SYSTEM     = [uint32]0x00000001
$AWAYMODE   = [uint32]0x00000040   # S3 기기용. Modern Standby에서는 무시되지만 해롭지 않다.

[void]$k::SetThreadExecutionState($CONTINUOUS -bor $SYSTEM -bor $AWAYMODE)
try {
  $p = Start-Process -FilePath "$env:SystemRoot\System32\wsl.exe" `
        -ArgumentList '-d', 'Ubuntu', '-e', 'bash',
                      '/home/mike_lnx/projects/naver-monitor/naver-monitor/scripts/scheduled.sh' `
        -NoNewWindow -PassThru -Wait
  exit $p.ExitCode
} finally {
  [void]$k::SetThreadExecutionState($CONTINUOUS)   # 붙잡기 해제
}
