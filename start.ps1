$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$servicePort = if ($env:SURVEY_PORT) { $env:SURVEY_PORT } else { '5000' }
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 环境创建失败，请先安装 Python 3.11 或更高版本。' }
    & $projectPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。' }
}
& $projectPython bootstrap.py
if ($LASTEXITCODE -ne 0) { throw '数据库启动失败。' }
& $projectPython tools/probe_server.py
if ($LASTEXITCODE -eq 10) {
    Write-Host "网页服务已在运行：打开 http://127.0.0.1:$servicePort 即可。"
    exit 0
}
Write-Host "打开 http://127.0.0.1:$servicePort；按 Ctrl+C 停止网页服务。"
& $projectPython app.py
