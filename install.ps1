# Buduje SonyPopup.exe i instaluje jak normalną aplikację.
# Uruchom:  powershell -ExecutionPolicy Bypass -File "$HOME\Tools\sony-popup\install.ps1"
$ErrorActionPreference = "Stop"
$src  = $PSScriptRoot
$dest = Join-Path $env:LOCALAPPDATA "Programs\SonyPopup"
$work = Join-Path $env:TEMP "sonypopup-build"

Write-Host "==> Zamykam działające kopie" -ForegroundColor Cyan
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*sony_popup*' -or $_.Name -eq 'SonyPopup.exe' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

Write-Host "==> Instaluję PyInstaller i zależności" -ForegroundColor Cyan
python -m pip install --quiet --upgrade pyinstaller -r "$src\requirements.txt"

Write-Host "==> Buduję SonyPopup.exe (1-2 min)" -ForegroundColor Cyan
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
# zdjęcia słuchawek: headphones.* oraz <model>.png (np. wh-ult900n.png)
$extra = @()
Get-ChildItem $src -File | Where-Object Name -match '^(headphones|wh-[a-z0-9-]+)\.(png|jpe?g|webp)$' |
    ForEach-Object { $extra += @('--add-data', "$($_.FullName);."); Write-Host "   + $($_.Name)" }
python -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name SonyPopup `
    --icon "$src\sony_popup.ico" `
    @extra `
    --add-data "$src\sony_popup.ico;." `
    --collect-all winrt `
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets `
    --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf `
    --distpath "$work\dist" --workpath "$work\build" --specpath $work `
    "$src\sony_popup.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller zakończył się błędem" }

Write-Host "==> Instaluję do $dest" -ForegroundColor Cyan
Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
Copy-Item "$work\dist\SonyPopup" $dest -Recurse
$exe = Join-Path $dest "SonyPopup.exe"

Write-Host "==> Skrót w menu Start + autostart" -ForegroundColor Cyan
$programs = [Environment]::GetFolderPath('Programs')
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$programs\Sony Popup.lnk")
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $dest
$lnk.IconLocation = "$exe,0"
$lnk.Description = "Popup i sterowanie Sony WH-1000XM6"
$lnk.Save()
Set-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\Run -Name SonyPopup -Value "`"$exe`""

Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
Start-Process $exe
Write-Host "`nGotowe. Sony Popup działa w trayu i jest w menu Start." -ForegroundColor Green
