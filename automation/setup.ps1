# Windows 셋업 스크립트
# 사용법: 이 폴더(automation\)에서 PowerShell을 관리자로 열고
#   PS> powershell -ExecutionPolicy Bypass -File .\setup.ps1
#
# 수행 내용:
#  1. Python 3 설치 확인
#  2. 가상환경 생성(.venv) 또는 기존 사용
#  3. pip install -r requirements.txt
#  4. playwright install chromium
#  5. credentials.json 없으면 템플릿 복사
#  6. Windows 작업스케줄러에 BocareKakaoAutoSend 등록 (월~금 07:25)
#
# 발송 시각·요일 바꾸려면 아래 $SendTime 또는 -DaysOfWeek 수정.

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

Write-Host "[1/6] Python 확인" -ForegroundColor Cyan
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python이 PATH에 없습니다. https://www.python.org/downloads/ 에서 설치 후 PATH 추가하세요." }

Write-Host "[2/6] 가상환경 생성" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }
$venvPy = Join-Path $Here ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { throw "venv Python 생성 실패: $venvPy" }

Write-Host "[3/6] pip install -r requirements.txt" -ForegroundColor Cyan
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements.txt

Write-Host "[4/6] playwright install chromium" -ForegroundColor Cyan
& $venvPy -m playwright install chromium

Write-Host "[5/6] credentials.json 확인" -ForegroundColor Cyan
if (-not (Test-Path "credentials.json")) {
    Copy-Item "credentials.json.template" "credentials.json"
    Write-Host "  -> credentials.json 생성됨. 메모장으로 열어서 값을 채워주세요:" -ForegroundColor Yellow
    Write-Host "     notepad $Here\credentials.json" -ForegroundColor Yellow
} else {
    Write-Host "  -> credentials.json 이미 존재 (변경 안 함)" -ForegroundColor Green
}

Write-Host "[6/6] 작업스케줄러 등록" -ForegroundColor Cyan
$TaskName = "BocareKakaoAutoSend"
$SendTime = "07:25"  # 변경 가능
$MainPy = Join-Path $Here "main.py"

$action = New-ScheduledTaskAction -Execute $venvPy -Argument "`"$MainPy`"" -WorkingDirectory $Here
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $SendTime
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "보험설계사용 카톡 자동 발송 (Claude + bocare.co.kr)"

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " 셋업 완료!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host " 다음 실행 시각:"
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object NextRunTime
Write-Host ""
Write-Host " credentials.json을 아직 안 채우셨다면 지금 채우세요:"
Write-Host "   notepad $Here\credentials.json"
Write-Host ""
Write-Host " 지금 테스트 실행 (실제 발송됨!):"
Write-Host "   Start-ScheduledTask -TaskName $TaskName"
Write-Host ""
