# Windows acceptance test for EnergyManagerSetup.exe (runs in CI on a clean windows-latest machine).
# Checks: install, app start + UI in the installed app (WebView2 over CDP), all-in-one background EMS in Demo Mode, backend + database, live data,
# optimizer, WebSocket, settings save, app close (EMS keeps running), restart via the Windows
# sign-in autostart entry (simulated reboot), history kept, upgrade over the existing install,
# Testomgeving (virtual devices over Modbus TCP / SunSpec / HTTP), uninstall (user data kept), and an upgrade
# from the previous public release 0.5.0 with its UI cached in the app.
# Usage: .\windows\acceptance.ps1 -Installer dist\EnergyManagerSetup-<versie>.exe
param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = "Stop"
$App = Join-Path $env:LOCALAPPDATA "Programs\Energy Manager"
$Data = Join-Path $env:LOCALAPPDATA "EnergyManager"
$Base = "http://127.0.0.1:8080"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$UninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F0D2B7C-3E7A-4C55-9C7B-4D2E8E5A1F10}_is1"
$results = [System.Collections.Generic.List[string]]::new()

function Pass($msg) { Write-Host "PASS  $msg" -ForegroundColor Green; $results.Add("PASS  $msg") }
function Fail($msg) {
  Write-Host "FAIL  $msg" -ForegroundColor Red
  Get-Content (Join-Path $Data "logs\server.log") -ErrorAction SilentlyContinue | Select-Object -Last 60
  throw $msg
}
function Healthy([int]$seconds = 120) {
  for ($i = 0; $i -lt $seconds; $i += 2) {
    try { $h = Invoke-RestMethod "$Base/healthz" -TimeoutSec 3; if ($h.ok) { return $h } } catch { }
    Start-Sleep -Seconds 2
  }
  return $null
}
function PortClosed([int]$seconds = 60) {
  for ($i = 0; $i -lt $seconds; $i += 1) {
    try { Invoke-RestMethod "$Base/healthz" -TimeoutSec 2 | Out-Null } catch { return $true }
    Start-Sleep -Seconds 1
  }
  return $false
}
function Api($path, $method = "GET", $body = $null) {
  $req = @{ Uri = "$Base/api/v1$path"; Method = $method; Headers = @{ Authorization = "Bearer $script:Token" }; TimeoutSec = 30 }
  if ($body -ne $null) { $req.Body = ($body | ConvertTo-Json -Depth 6); $req.ContentType = "application/json" }
  return Invoke-RestMethod @req
}
function Login() {
  $r = Invoke-RestMethod "$Base/api/v1/auth/login" -Method POST -ContentType "application/json" `
    -Body (@{ username = "demo"; password = "demo" } | ConvertTo-Json)
  $script:Token = $r.token
}
# The installed app, with its WebView2 reachable over the Chrome DevTools Protocol, so the UI test drives the
# real app window (its own WebView2 profile and cache) — not a separate browser.
function StartAppDebug([string]$exe = (Join-Path $App "Energy Manager.exe")) {
  $env:ENERGY_MANAGER_WEBVIEW_DEBUG_PORT = "9222"      # test hook of the app (src-tauri/src/main.rs)
  $p = Start-Process $exe -PassThru
  Remove-Item Env:ENERGY_MANAGER_WEBVIEW_DEBUG_PORT
  for ($i = 0; $i -lt 90; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:9222/json/version" -TimeoutSec 2 | Out-Null; return $p } catch { Start-Sleep 1 }
  }
  Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" | Select-Object -First 3 -ExpandProperty CommandLine | Write-Host
  Fail "app WebView2 debug port did not open (app running: $(-not $p.HasExited))"
}
# The 0.5.0 app has no test hook: open the old interface with Edge on the app's own WebView2 profile, so the old
# files end up in exactly the cache the app uses.
function StartEdgeOnAppProfile() {
  $edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $edge) { Fail "Microsoft Edge not found on the runner" }
  $profileDir = Join-Path $env:LOCALAPPDATA "nl.energymanager.desktop\EBWebView"
  New-Item -ItemType Directory -Force $profileDir | Out-Null
  $p = Start-Process $edge -ArgumentList "--headless=new", "--remote-debugging-port=9222", "--user-data-dir=`"$profileDir`"", "about:blank" -PassThru
  for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:9222/json/version" -TimeoutSec 2 | Out-Null; return $p } catch { Start-Sleep 1 }
  }
  Fail "Edge debug port did not open"
}
function StopApp($p) {
  Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep 2
  Get-Process msedgewebview2, msedge -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  for ($i = 0; $i -lt 20; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:9222/json/version" -TimeoutSec 1 | Out-Null; Start-Sleep 1 } catch { return }
  }
}
function AppUi([string]$name, [string[]]$extra) {
  $out = Join-Path (Get-Location) "ui-$name"
  $env:PYTHONIOENCODING = "utf-8"
  python tests\ui\wizard_acceptance.py $Base $out --cdp "http://127.0.0.1:9222" @extra 2>&1 | Tee-Object -Variable log | Write-Host
  if ($LASTEXITCODE -ne 0) { Fail "UI in the installed app ($name) failed: $($log | Select-Object -Last 5)" }
  return ($log | Where-Object { "$_" -like "PASS *" }).Count
}

function StartFromRunKey() {
  $cmd = (Get-ItemProperty $RunKey -Name EnergyManagerEMS).EnergyManagerEMS
  if ($cmd -notmatch '^"([^"]+)"\s*(.*)$') { Fail "autostart entry unreadable: $cmd" }
  Start-Process -FilePath $Matches[1] -ArgumentList $Matches[2] | Out-Null
}
function Install() {
  $p = Start-Process $Installer -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
    "/TASKS=autostart,desktopicon", "/LOG=$env:TEMP\em-install.log" -Wait -PassThru
  if ($p.ExitCode -ne 0) { Get-Content "$env:TEMP\em-install.log" | Select-Object -Last 40; Fail "installer exit code $($p.ExitCode)" }
}

# 1. Install -------------------------------------------------------------------------------
Install
foreach ($f in @("Energy Manager.exe", "server\EnergyManagerService.exe", "server\EnergyManagerServer.exe", "unins000.exe")) {
  if (-not (Test-Path (Join-Path $App $f))) { Fail "missing after install: $f" }
}
if (-not (Get-ItemProperty $RunKey -Name EnergyManagerEMS -ErrorAction SilentlyContinue)) { Fail "autostart entry missing" }
Pass "install: files, Start menu entries and autostart entry present"

# 2. App starts ------------------------------------------------------------------------------
$appProc = StartAppDebug
Start-Sleep -Seconds 5
if ($appProc.HasExited) { Fail "Windows app exited immediately (code $($appProc.ExitCode))" }
Pass "Windows app launches (WebView2) and stays open"

# 3. All-in-one EMS in Demo Mode (exactly what the app's 'Starten' does) --------------------
Start-Process (Join-Path $App "server\EnergyManagerService.exe") -ArgumentList "--background", "--demo" | Out-Null
if (-not (Healthy)) { Fail "background EMS did not become healthy" }
Login
$status = Api "/system/status"
if (-not $status.database.ok) { Fail "database not ok" }
if (-not (Test-Path (Join-Path $Data "demo\ems.db"))) { Fail "database file not created" }
Pass "background EMS (no window) runs; database created ($($status.database.backend), schema v$($status.database.schema_version))"
$build = (Api "/system/info").build
if (-not $build.commit -or $build.commit -eq "dev" -or -not $build.consistent) { Fail "installed server has no release build id ($($build.build))" }
Pass "build identity: server $($build.build), built $($build.built_at), web assets $($build.asset_hash)"

# 4. Live data and optimizer -----------------------------------------------------------------
$live = Api "/energy/live"
if ($live.flows -eq $null -or $live.flows.grid_w -eq $null) { Fail "no live flows" }
Pass "live dashboard data: grid $([math]::Round($live.flows.grid_w)) W, EMS status $($live.ems_status.state)"
$plan = $null
for ($i = 0; $i -lt 60; $i += 3) { $plan = Api "/optimizer/plan?hours=36"; if ($plan.slots.Count -gt 0) { break }; Start-Sleep 3 }
if ($plan.slots.Count -eq 0) { Fail "optimizer produced no plan" }
Pass "optimizer active: $($plan.slots.Count) planned slots, status $($plan.status)"

# 4b. The installed app shows the new wizard -------------------------------------------------
$n = AppUi "clean-demo" @("--demo")
Pass "installed app (WebView2): frontend = server build, new wizard (Soort -> Methode), 4 methods for all 15 categories, real brands with functions and alternatives ($n UI checks)"

# 5. WebSocket -------------------------------------------------------------------------------
$ws = New-Object System.Net.WebSockets.ClientWebSocket
$ticket = (Api "/auth/ws-ticket" "POST").ticket   # single-use ticket: no session token in the URL
$ws.ConnectAsync([Uri]"ws://127.0.0.1:8080/api/v1/ws?ticket=$ticket", [Threading.CancellationToken]::None).Wait(15000) | Out-Null
$buf = New-Object byte[] 65536
$got = $null
for ($n = 0; $n -lt 20 -and -not $got; $n++) {
  $seg = New-Object ArraySegment[byte] -ArgumentList (, $buf)
  $t = $ws.ReceiveAsync($seg, [Threading.CancellationToken]::None)
  if (-not $t.Wait(20000)) { break }
  $text = [Text.Encoding]::UTF8.GetString($buf, 0, $t.Result.Count)
  if ($text -match '"type"\s*:\s*"live"') { $got = $text }
}
if (-not $got) { Fail "no live message over WebSocket" }
Pass "WebSocket delivers live updates"

# 6. Settings save ---------------------------------------------------------------------------
Api "/settings" "PUT" @{ site = @{ name = "Acceptatietest" } } | Out-Null
if ((Api "/settings").site.name -ne "Acceptatietest") { Fail "setting not saved" }
Pass "settings saved"

# 7. Close the app: the EMS keeps running ----------------------------------------------------
StopApp $appProc
Start-Sleep -Seconds 3
if (-not (Healthy 10)) { Fail "EMS stopped when the app was closed" }
Pass "app closed, EMS keeps running in the background"
Start-Sleep -Seconds 20
$rows1 = (Api "/history?hours=1&resolution=raw").rows.Count
if ($rows1 -lt 1) { Fail "no history recorded" }

# 8. Simulated reboot: stop, then start through the sign-in autostart entry -------------------
& (Join-Path $App "server\EnergyManagerServer.exe") --stop
if (-not (PortClosed)) { Fail "EMS did not stop" }
StartFromRunKey
if (-not (Healthy)) { Fail "EMS did not start from the autostart entry" }
Login
if ((Api "/settings").site.name -ne "Acceptatietest") { Fail "setting lost after restart" }
$rows2 = (Api "/history?hours=1&resolution=raw").rows.Count
if ($rows2 -lt $rows1) { Fail "history lost after restart ($rows2 < $rows1)" }
if ((Api "/system/info" ).mode -ne "demo") { Fail "mode not remembered" }
Pass "restart via autostart (as after a Windows reboot): settings, mode and history kept ($rows1 -> $rows2 samples)"

# 9. Upgrade over the existing installation while the EMS runs -------------------------------
Install
if (-not (PortClosed 30)) { Fail "installer did not stop the running EMS before replacing files" }
StartFromRunKey
if (-not (Healthy)) { Fail "EMS did not start after upgrade" }
Login
if ((Api "/settings").site.name -ne "Acceptatietest") { Fail "setting lost after upgrade" }
Pass "upgrade over existing install: EMS stopped by installer, data and settings kept"

# 9b. Version and downgrade protection ---------------------------------------------------------
$installed = (Get-ItemProperty $UninstKey).DisplayVersion
$serverVersion = (Invoke-RestMethod "$Base/healthz").version
if ($installed -ne $serverVersion) { Fail "installed version $installed differs from running server $serverVersion" }
Set-ItemProperty $UninstKey -Name DisplayVersion -Value "99.0.0"     # pretend a newer version is installed
$p = Start-Process $Installer -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$env:TEMP\em-downgrade.log" -Wait -PassThru
Set-ItemProperty $UninstKey -Name DisplayVersion -Value $installed
if ($p.ExitCode -eq 0) { Fail "installing an older version over a newer one was not blocked" }
if (-not (Select-String -Path "$env:TEMP\em-downgrade.log" -Pattern "Downgrade blocked" -Quiet)) { Fail "downgrade block not logged" }
if (-not (Healthy 10)) { Fail "EMS affected by the refused downgrade" }
Pass "version $installed everywhere; downgrade over a newer version refused (exit code $($p.ExitCode)), EMS untouched"

# 9c. Testomgeving: virtual devices over real protocols, bundled Modbus/SunSpec stack ------------
& (Join-Path $App "server\EnergyManagerServer.exe") --stop
if (-not (PortClosed)) { Fail "EMS did not stop before switching to the Testomgeving" }
Start-Process (Join-Path $App "server\EnergyManagerService.exe") -ArgumentList "--background", "--lab" | Out-Null
$h = Healthy
if (-not $h -or $h.profile -ne "lab") { Fail "Testomgeving did not start (profile $($h.profile))" }
Login
$lab = Api "/lab"
if (-not $lab.active -or $lab.devices.Count -lt 8) { Fail "virtual devices not running ($($lab.devices.Count))" }
$scan = Api "/discovery/scan" "POST" @{ mdns = $false; serial = $false }
$hp = $scan.results | Where-Object { $_.profile_id -eq "energymanager.lab.heat_pump" }
if (-not $hp -or $hp.confidence -ne "EXACT_MODEL") { Fail "automatic search did not recognise the virtual heat pump" }
$body = @{ name = "Warmtepomp"; category = "heat_pump"; driver = "library.modbus"; connection = $hp.connection }
$probe = Api "/devices/probe" "POST" $body
if (-not $probe.reachable -or $probe.identity.serial_number -ne "EMLAB-HP-0001") { Fail "Modbus TCP probe failed" }
$dev = Api "/devices" "POST" $body
if ((Api "/devices/$($dev.id)/test" "POST").reachable -ne $true) { Fail "connection test failed" }
Api "/devices/$($dev.id)/commissioning" "PUT" @{ level = "shadow" } | Out-Null
$wt = Api "/devices/$($dev.id)/commissioning/write-test" "POST"
if (-not $wt.ok) { Fail "write test on the virtual heat pump failed: $($wt.detail)" }
$pv = $scan.results | Where-Object { $_.driver_id -eq "library.sunspec" }
if (-not $pv) { Fail "SunSpec inverter not found" }
Pass "Testomgeving: $($lab.devices.Count) virtual devices; search recognises exact model; Modbus TCP probe, identity, add, test, shadow and write test OK; SunSpec found"
$appProc = StartAppDebug
$n = AppUi "lab" @()
StopApp $appProc
Pass "installed app, Testomgeving: brand/model with Modbus profile, automatic search, SunSpec, P1, Home Assistant via entity lists, edit form, fault injection ($n UI checks)"
& (Join-Path $App "server\EnergyManagerServer.exe") --stop
if (-not (PortClosed)) { Fail "EMS did not stop after the Testomgeving" }
Start-Process (Join-Path $App "server\EnergyManagerService.exe") -ArgumentList "--background", "--demo" | Out-Null
if (-not (Healthy)) { Fail "Demo did not start again after the Testomgeving" }

# 10. Uninstall ------------------------------------------------------------------------------
Start-Process (Join-Path $App "unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
for ($i = 0; $i -lt 60 -and (Test-Path (Join-Path $App "Energy Manager.exe")); $i++) { Start-Sleep 1 }
if (Test-Path (Join-Path $App "Energy Manager.exe")) { Fail "app still present after uninstall" }
if (-not (PortClosed 30)) { Fail "EMS still running after uninstall" }
if (Get-ItemProperty $RunKey -Name EnergyManagerEMS -ErrorAction SilentlyContinue) { Fail "autostart entry left behind" }
if (-not (Test-Path (Join-Path $Data "demo\ems.db"))) { Fail "user data was removed by uninstall" }
Pass "uninstall: program, autostart entry and background EMS removed; user data kept"

# 11. Upgrade from the previous public release (0.5.0) with the old UI cached in the app ---------------------
Remove-Item -Recurse -Force $Data, (Join-Path $env:LOCALAPPDATA "nl.energymanager.desktop") -ErrorAction SilentlyContinue
$old = Join-Path $env:TEMP "EnergyManagerSetup-0.5.0.exe"
Invoke-WebRequest "https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation/releases/download/energy-manager-windows/EnergyManagerSetup-0.5.0.exe" -OutFile $old
if ((Get-FileHash $old -Algorithm SHA256).Hash.ToLower() -ne "97575a14129c89a62723c27d782aa9ccee1aa6ce42255327139681aa65643541") { Fail "0.5.0 installer checksum mismatch" }
$p = Start-Process $old -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=autostart,desktopicon", "/LOG=$env:TEMP\em-050.log" -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "0.5.0 install failed ($($p.ExitCode))" }
Start-Process (Join-Path $App "server\EnergyManagerService.exe") -ArgumentList "--background", "--demo" | Out-Null
$h = Healthy
if (-not $h -or $h.version -ne "0.5.0") { Fail "0.5.0 did not start ($($h.version))" }
Login
Api "/settings" "PUT" @{ site = @{ name = "Upgradetest 0.5.0" } } | Out-Null
Start-Sleep -Seconds 25
$rowsOld = (Api "/history?hours=1&resolution=raw").rows.Count
$appProc = Start-Process (Join-Path $App "Energy Manager.exe") -PassThru      # the 0.5.0 app creates its profile
Start-Sleep -Seconds 8
StopApp $appProc
$edgeProc = StartEdgeOnAppProfile
$n = AppUi "old-0.5.0" @("--prime-old")
StopApp $edgeProc
Pass "0.5.0 installed and used: old wizard opened on the app's WebView2 profile (its files cached), settings changed, $rowsOld history samples"
Install
if (-not (PortClosed 30)) { Fail "installer did not stop the running 0.5.0 EMS" }
StartFromRunKey
$h = Healthy
if (-not $h) { Fail "EMS did not start after the upgrade from 0.5.0" }
Login
$installed = (Get-ItemProperty $UninstKey).DisplayVersion
if ($h.version -ne $installed -or (Api "/system/info").build.version -ne $installed) { Fail "running server $($h.version) is not the installed $installed" }
if ((Api "/settings").site.name -ne "Upgradetest 0.5.0") { Fail "settings lost in the upgrade from 0.5.0" }
$rowsNew = (Api "/history?hours=1&resolution=raw").rows.Count
if ($rowsNew -lt $rowsOld) { Fail "history lost in the upgrade ($rowsNew < $rowsOld)" }
if (-not (Api "/system/status").database.ok) { Fail "database not ok after the upgrade" }
$procs = @(Get-Process EnergyManagerService, EnergyManagerServer -ErrorAction SilentlyContinue)
if ($procs.Count -ne 1) { Fail "expected exactly one EMS server process, found $($procs.Count)" }
$appProc = StartAppDebug
$n = AppUi "upgrade-demo" @("--demo")
StopApp $appProc
Pass "upgrade 0.5.0 -> ${installed}: one server process of version ${installed}, settings and history kept ($rowsOld -> $rowsNew samples); the app shows the NEW wizard (4 methods, all categories) despite the cached 0.5.0 files ($n UI checks)"

Write-Host ""
Write-Host "WINDOWS ACCEPTANCE: PASS ($($results.Count) checks)" -ForegroundColor Green
$results | Set-Content -Encoding utf8 acceptance-results.txt
