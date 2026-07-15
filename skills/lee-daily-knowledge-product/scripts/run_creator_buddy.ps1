param(
  [Parameter(Mandatory = $true)][string]$OutputDirectory,
  [string]$RunDate = (Get-Date -Format 'yyyy-MM-dd')
)

$ErrorActionPreference = 'Stop'
$creatorRoot = Join-Path $env:USERPROFILE '.codex\skills\creator-buddy'
$python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$script = Join-Path $creatorRoot 'skills\baokuan-article-analysis\scripts\daily_sector_trends.py'

if (!(Test-Path -LiteralPath $python)) { throw 'Codex bundled Python was not found.' }
if (!(Test-Path -LiteralPath $script)) { throw 'Creator Buddy sector script was not found.' }

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

& $python -X utf8 $script `
  --sector '连锁经营=连锁经营,连锁企业,品牌连锁,门店管理' `
  --sector '加盟治理=加盟连锁,加盟商,加盟商管理,特许经营' `
  --sector '餐饮连锁=餐饮连锁,餐饮加盟,茶饮连锁,糖水铺' `
  --sector '单店模型=门店盈利,回本周期,开店模型,闭店' `
  --sector '消费商业=消费品牌,零售,产品创新,渠道,供应链,用户需求' `
  --sector '企业案例=企业案例,增长,转型,组织变革,商业模式,失败复盘,现金流' `
  --sector '组织与认知=决策,管理,学习,协作,长期主义,个人成长' `
  --days 30 --output-dir $OutputDirectory --report-date $RunDate

if ($LASTEXITCODE -ne 0) { throw "Creator Buddy exited with code $LASTEXITCODE" }

$dayDirectory = Join-Path $OutputDirectory $RunDate
@{
  data_json = (Join-Path $dayDirectory 'data.json')
  report_html = (Join-Path $dayDirectory 'report.html')
} | ConvertTo-Json -Compress

