# 微信养号 — 一键环境搭建与启动检查
# 用法: 在项目根目录执行  powershell -ExecutionPolicy Bypass -File .\setup_run.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  微信养号 — 环境搭建" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. 检查 Python
Write-Host "`n[1/6] 检查 Python..."
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "  [FAIL] 未找到 python，请先安装 Python 3.10+" -ForegroundColor Red
    exit 1
}
python --version

# 2. 检查 ADB
Write-Host "`n[2/6] 检查 ADB 与设备..."
$adb = Get-Command adb -ErrorAction SilentlyContinue
if (-not $adb) {
    Write-Host "  [FAIL] 未找到 adb，请安装 Android Platform Tools 并加入 PATH" -ForegroundColor Red
    exit 1
}
adb devices

# 3. 创建虚拟环境
Write-Host "`n[3/6] 虚拟环境..."
if (-not (Test-Path "wechat_env\Scripts\python.exe")) {
    Write-Host "  创建 wechat_env ..."
    python -m venv wechat_env
} else {
    Write-Host "  [OK] wechat_env 已存在"
}

$PY = Join-Path $PSScriptRoot "wechat_env\Scripts\python.exe"
$PIP = Join-Path $PSScriptRoot "wechat_env\Scripts\pip.exe"

# 4. 安装依赖
Write-Host "`n[4/6] 安装依赖（首次含 torch/easyocr，可能较久）..."
& $PIP install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] pip install 失败" -ForegroundColor Red
    exit 1
}

# 5. 初始化 uiautomator2
Write-Host "`n[5/6] 初始化 uiautomator2 agent..."
& $PY -m uiautomator2 init

# 6. 初始化数据库 + 检测设备
Write-Host "`n[6/6] 初始化数据库并检测设备..."
& $PY main.py init

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  环境就绪。下一步:" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host @"

1) 查看设备序列号:
   adb devices

2) 录入账号（把序列号/微信号改成你的）:
   wechat_env\Scripts\python.exe -c "
from storage.db import Database
db = Database('wechat_farm.db')
db.insert_account(
    id='acc_001',
    wechat_id='你的微信号',
    device_serial='你的序列号',
    registration_date='2026-07-01',
    batch_name='batch_a',
    persona_id='p01',
)
db.bind_device(serial='你的序列号', account_id='acc_001', model='Moto')
db.add_friend(account_id='acc_001', friend_name='稀有气体')
print('账号已录入')
"

3) 功能验证（约 10~20 分钟）:
   wechat_env\Scripts\python.exe main.py fast-debug

4) 日常运行:
   wechat_env\Scripts\python.exe main.py run

"@
