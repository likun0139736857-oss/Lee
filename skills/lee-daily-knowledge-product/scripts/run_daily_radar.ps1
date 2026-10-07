param(
  [Parameter(Mandatory = $true)][string]$OutputDirectory,
  [string]$RunDate = (Get-Date -Format 'yyyy-MM-dd'),
  [int]$ReadLimitPerAccount = 1,
  [string]$VerifiedWeChatInput = '',
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$skillRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$accountRunner = Join-Path $PSScriptRoot 'run_wechat_account_retrieval.ps1'
$creatorRunner = Join-Path $PSScriptRoot 'run_creator_buddy.ps1'
$prepare = Join-Path $PSScriptRoot 'prepare_topic_radar.py'
$deliveryGuard = Join-Path $PSScriptRoot 'daily_delivery_guard.py'
$topicConfig = Join-Path $skillRoot 'config\topic-clusters.json'
$sourcePool = Join-Path $skillRoot 'config\editorial-source-pool.json'
$dayDirectory = Join-Path $OutputDirectory $RunDate
$report = Join-Path $dayDirectory 'lee-daily-report.md'
$data = Join-Path $dayDirectory 'data.json'
$accountRadar = Join-Path $dayDirectory 'wechat-account-radar.json'
$radar = Join-Path $dayDirectory 'topic-radar.json'

if (!(Test-Path -LiteralPath $python)) { throw 'Codex bundled Python was not found.' }
if (!(Test-Path -LiteralPath $deliveryGuard)) { throw 'Daily delivery guard was not found.' }

# Hard idempotency gate: do not touch account retrieval, Creator Buddy, or any
# existing daily artifacts after a valid review-only report has been delivered.
if (!$Force) {
  $guardJson = & $python -X utf8 $deliveryGuard $report
  if ($LASTEXITCODE -ne 0) {
    throw "Daily delivery guard exited with code $LASTEXITCODE"
  }
  $guard = $guardJson | ConvertFrom-Json
  if ($guard.already_delivered) {
    @{
      status = 'already_delivered'
      report = $report
      reason = $guard.reason
    } | ConvertTo-Json -Compress
    exit 0
  }
}

& $accountRunner -OutputDirectory $OutputDirectory -RunDate $RunDate `
  -ReadLimitPerAccount $ReadLimitPerAccount `
  -VerifiedInput $VerifiedWeChatInput
if ($LASTEXITCODE -ne 0) {
  throw "WeChat account retrieval exited with code $LASTEXITCODE"
}

& $creatorRunner -OutputDirectory $OutputDirectory -RunDate $RunDate
if ($LASTEXITCODE -ne 0) {
  throw "Creator Buddy exited with code $LASTEXITCODE"
}

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

& $python -X utf8 $prepare $data $topicConfig $sourcePool $radar `
  $RunDate $accountRadar
if ($LASTEXITCODE -ne 0) {
  throw "Topic radar preparation exited with code $LASTEXITCODE"
}

@{
  topic_radar_json = $radar
  account_radar_json = $accountRadar
  creator_buddy_json = $data
} | ConvertTo-Json -Compress
