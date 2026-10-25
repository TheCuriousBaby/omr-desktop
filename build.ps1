# 构建 OMR阅卷助手 发行版，输出到 dist\OMR阅卷助手
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    python -m venv .venv
    & $python -m pip install -r requirements.txt
}
& $python tools\make_icon.py
& $python -m PyInstaller --noconfirm --clean omr_desktop.spec
Write-Host "构建完成：dist\OMR阅卷助手\OMR阅卷助手.exe"
