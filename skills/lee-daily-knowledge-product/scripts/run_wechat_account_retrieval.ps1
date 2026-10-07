param(
  [Parameter(Mandatory = $true)][string]$OutputDirectory,
  [string]$RunDate = (Get-Date -Format 'yyyy-MM-dd'),
  [int]$ReadLimitPerAccount = 1,
  [string]$VerifiedInput = ''
)

$ErrorActionPreference = 'Stop'
$skillRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$script = Join-Path $PSScriptRoot 'wechat_account_retrieval.py'
$config = Join-Path $skillRoot 'config\editorial-source-pool.json'
$dayDirectory = Join-Path $OutputDirectory $RunDate
$output = Join-Path $dayDirectory 'wechat-account-radar.json'

if (!(Test-Path -LiteralPath $python)) { throw 'Codex bundled Python was not found.' }
if (!(Test-Path -LiteralPath $script)) { throw 'WeChat account retrieval script was not found.' }

New-Item -ItemType Directory -Force -Path $dayDirectory | Out-Null
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$arguments = @(
  '-X', 'utf8', $script, 'fetch',
  '--config', $config,
  '--output', $output,
  '--run-date', $RunDate,
  '--hot-days', '3',
  '--case-days', '30',
  '--read-limit-per-account', "$ReadLimitPerAccount"
)
if ($VerifiedInput) {
  $arguments += @('--verified-input', $VerifiedInput)
}

& $python @arguments

if ($LASTEXITCODE -ne 0) {
  throw "WeChat account retrieval exited with code $LASTEXITCODE"
}

@{
  account_radar_json = $output
} | ConvertTo-Json -Compress
