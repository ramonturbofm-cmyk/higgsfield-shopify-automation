# Windows acceptance test for EnergyManagerSetup.exe (runs in CI on a clean windows-latest machine).
# Checks: install, app start, all-in-one background EMS in Demo Mode, backend + database, live data,
# optimizer, WebSocket, settings save, app close (EMS keeps running), restart via the Windows
# sign-in autostart entry (simulated reboot), history kept, upgrade over the existing install,
# uninstall (user data kept).
# Usage: .\windows\acceptance.ps1 -Installer dist\EnergyManagerSetup-0.3.0.exe
param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = "Stop"
$App = Join-Path $env:LOCALAPPDATA "Programs\Energy Manager"
$Data = Join-Path $env:LOCALAPPDATA "EnergyManager"
$Base = "http://127.0.0.1:8080"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
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
$appProc = Start-Process (Join-Path $App "Energy Manager.exe") -PassThru
Start-Sleep -Seconds 10
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

# 4. Live data and optimizer -----------------------------------------------------------------
$live = Api "/energy/live"
if ($live.flows -eq $null -or $live.flows.grid_w -eq $null) { Fail "no live flows" }
Pass "live dashboard data: grid $([math]::Round($live.flows.grid_w)) W, EMS status $($live.ems_status.state)"
$plan = $null
for ($i = 0; $i -lt 60; $i += 3) { $plan = Api "/optimizer/plan?hours=36"; if ($plan.slots.Count -gt 0) { break }; Start-Sleep 3 }
if ($plan.slots.Count -eq 0) { Fail "optimizer produced no plan" }
Pass "optimizer active: $($plan.slots.Count) planned slots, status $($plan.status)"

# 5. WebSocket -------------------------------------------------------------------------------
$ws = New-Object System.Net.WebSockets.ClientWebSocket
$ws.ConnectAsync([Uri]"ws://127.0.0.1:8080/api/v1/ws?token=$script:Token", [Threading.CancellationToken]::None).Wait(15000) | Out-Null
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
Stop-Process -Id $appProc.Id -Force
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

# 10. Uninstall ------------------------------------------------------------------------------
Start-Process (Join-Path $App "unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
for ($i = 0; $i -lt 60 -and (Test-Path (Join-Path $App "Energy Manager.exe")); $i++) { Start-Sleep 1 }
if (Test-Path (Join-Path $App "Energy Manager.exe")) { Fail "app still present after uninstall" }
if (-not (PortClosed 30)) { Fail "EMS still running after uninstall" }
if (Get-ItemProperty $RunKey -Name EnergyManagerEMS -ErrorAction SilentlyContinue) { Fail "autostart entry left behind" }
if (-not (Test-Path (Join-Path $Data "demo\ems.db"))) { Fail "user data was removed by uninstall" }
Pass "uninstall: program, autostart entry and background EMS removed; user data kept"

Write-Host ""
Write-Host "WINDOWS ACCEPTANCE: PASS ($($results.Count) checks)" -ForegroundColor Green
$results | Set-Content -Encoding utf8 acceptance-results.txt
