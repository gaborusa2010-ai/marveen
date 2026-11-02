# marveen-or.ps1 -- Windows-side watchdog for WSL/Marveen (#749).
#
# Every alert path of the fleet runs INSIDE WSL, so when WSL froze on
# 2026-11-01 (14:42-19:58, C: full) nobody told the owner for 5 hours. This
# script runs from Windows Task Scheduler every 5 minutes, outside WSL:
#   - DOWN: the dashboard (http://localhost:3420) gives no HTTP answer at all
#     for FAIL_TICKS consecutive ticks -> one Telegram alert per outage.
#     Any HTTP status (401 included) counts as alive: the process answered.
#   - UP again after an alert -> one recovery message with the downtime.
#   - C: free space under MIN_FREE_GB -> one warning per day.
# Telegram goes straight to the Bot API from Windows. The token is read from a
# copy of the channel's .env in this folder (line TELEGRAM_BOT_TOKEN=...).
# -DryRun prints the message instead of sending it (for tests).

param([switch]$DryRun, [string]$Url = 'http://localhost:3420/api/health', [double]$MinFreeGb = 15)

$ErrorActionPreference = 'Stop'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StateFile = Join-Path $Dir 'state.json'
$LogFile = Join-Path $Dir 'or.log'
$EnvFile = Join-Path $Dir 'telegram.env'
$ChatId = '8547011582'
$FailTicks = 2          # 2 x 5 min: a dashboard restart (~30 s) never alerts

function Log($m) {
  $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m
  Add-Content -Path $LogFile -Value $line -Encoding UTF8
  if ((Get-Item $LogFile).Length -gt 1MB) { Move-Item -Force $LogFile ($LogFile + '.1') }
}

function Send-Tg($text) {
  if ($DryRun) { Write-Host "DRYRUN: $text"; return $true }
  if (-not (Test-Path $EnvFile)) { Log 'SEND FAILED: telegram.env missing'; return $false }
  $tok = (Get-Content $EnvFile | Where-Object { $_ -match '^TELEGRAM_BOT_TOKEN=' } | Select-Object -First 1)
  if (-not $tok) { Log 'SEND FAILED: no TELEGRAM_BOT_TOKEN line'; return $false }
  $tok = $tok.Substring(19).Trim().Trim('"').Trim("'")
  try {
    $body = @{ chat_id = $ChatId; text = $text } | ConvertTo-Json -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$tok/sendMessage" `
      -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec 20 | Out-Null
    return $true
  } catch { Log ('SEND FAILED: ' + $_.Exception.Message); return $false }
}

$state = @{ fails = 0; alerted = $false; downSince = ''; diskDay = '' }
if (Test-Path $StateFile) {
  try {
    $j = Get-Content $StateFile -Raw | ConvertFrom-Json
    $state.fails = [int]$j.fails; $state.alerted = [bool]$j.alerted
    $state.downSince = [string]$j.downSince; $state.diskDay = [string]$j.diskDay
  } catch { Log 'state.json unreadable, starting fresh' }
}

$alive = $false
try {
  Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri $Url | Out-Null
  $alive = $true
} catch {
  # An HTTP error status still means the dashboard answered.
  if ($_.Exception.Response) { $alive = $true }
}

$now = Get-Date
if ($alive) {
  if ($state.alerted) {
    $mins = 0
    if ($state.downSince) { $mins = [int](($now - [datetime]$state.downSince).TotalMinutes) }
    if (Send-Tg "Marveen-or (Windows): a Marveen ujra valaszol. Kb. $mins percig nem valaszolt.") {
      Log "UP again after ~$mins min, recovery sent"
      $state.alerted = $false
    }
  }
  $state.fails = 0; $state.downSince = ''
} else {
  $state.fails++
  if (-not $state.downSince) { $state.downSince = $now.AddMinutes(-5).ToString('s') }
  Log "no answer from $Url (tick $($state.fails))"
  if ($state.fails -ge $FailTicks -and -not $state.alerted) {
    $since = ([datetime]$state.downSince).ToString('HH:mm')
    if (Send-Tg "Marveen-or (Windows): a Marveen kb. $since ota nem valaszol (a WSL vagy a dashboard all). Ilyenkor a tobbi riasztas sem jut el hozzad. Egy ujrainditas altalaban megoldja.") {
      $state.alerted = $true; Log 'DOWN alert sent'
    }
  }
}

$freeGb = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
$today = $now.ToString('yyyy-MM-dd')
if ($freeGb -lt $MinFreeGb -and $state.diskDay -ne $today) {
  if (Send-Tg "Marveen-or (Windows): a C: meghajton csak $freeGb GB szabad hely maradt. November 1-jen a tele C: allitotta le a WSL-t.") {
    $state.diskDay = $today; Log "disk warning sent ($freeGb GB)"
  }
}

$state | ConvertTo-Json -Compress | Set-Content -Path $StateFile -Encoding UTF8
if ($DryRun) { Write-Output ("alive=$alive fails=$($state.fails) alerted=$($state.alerted) freeGb=$freeGb") }
