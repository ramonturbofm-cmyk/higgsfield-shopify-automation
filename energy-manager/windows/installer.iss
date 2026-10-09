; Energy Manager — Windows installer (Inno Setup 6).
; Contents: the Windows app (Tauri) and the built-in EMS server (all-in-one, no Raspberry Pi
; needed). The app starts the server in the background (EnergyManagerService.exe).
; Build (from energy-manager\):  iscc /DAppVersion=<versie> windows\installer.iss  (versie uit ems.__version__)
; Downgrade protection: installing an older version over a newer one is refused (the newer
; database cannot be read by an older program). Override only with /ALLOWDOWNGRADE=1.
; Expects: dist\app\Energy Manager.exe  and  dist\EnergyManagerServer\ (PyInstaller one-folder)

#ifndef AppVersion
  #define AppVersion "0.5.0"
#endif

[Setup]
AppId={{6F0D2B7C-3E7A-4C55-9C7B-4D2E8E5A1F10}
AppName=Energy Manager
AppVersion={#AppVersion}
AppPublisher=Energy Manager
DefaultDirName={localappdata}\Programs\Energy Manager
DefaultGroupName=Energy Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=EnergyManagerSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Energy Manager
UninstallDisplayIcon={app}\Energy Manager.exe
SetupIconFile=..\windows-app\src-tauri\icons\icon.ico
; Our own PrepareToInstall stops the EMS gracefully first; Restart Manager only closes what is left.
CloseApplications=force

[Languages]
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"

[Types]
Name: "full"; Description: "Alles op deze computer (aanbevolen, geen Raspberry Pi nodig)"
Name: "app"; Description: "Alleen de app (het EMS draait op een Raspberry Pi)"

[Components]
Name: "app"; Description: "Energy Manager-app"; Types: full app; Flags: fixed
Name: "server"; Description: "Ingebouwde EMS-server (draait op de achtergrond op deze pc)"; Types: full

[Tasks]
Name: "autostart"; Description: "EMS automatisch starten bij aanmelden in Windows (aanbevolen: regelt dan ook zonder dat de app open is)"; GroupDescription: "EMS op deze computer:"; Components: server
Name: "desktopicon"; Description: "Snelkoppeling op het bureaublad"; GroupDescription: "Extra:"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "EnergyManagerEMS"; ValueData: """{app}\server\EnergyManagerService.exe"" --background"; Tasks: autostart; Flags: uninsdeletevalue

[Files]
Source: "..\dist\app\Energy Manager.exe"; DestDir: "{app}"; Components: app; Flags: ignoreversion
Source: "..\dist\EnergyManagerServer\*"; DestDir: "{app}\server"; Components: server; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Energy Manager"; Filename: "{app}\Energy Manager.exe"
Name: "{autodesktop}\Energy Manager"; Filename: "{app}\Energy Manager.exe"; Tasks: desktopicon
Name: "{group}\Hulpmiddelen\EMS stoppen"; Filename: "{app}\server\EnergyManagerServer.exe"; Parameters: "--stop"; Components: server
Name: "{group}\Hulpmiddelen\EMS starten met venster (foutzoeken)"; Filename: "{app}\server\EnergyManagerServer.exe"; Components: server
Name: "{group}\Energy Manager verwijderen"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\Energy Manager.exe"; Description: "Energy Manager nu starten"; Flags: nowait postinstall skipifsilent

; User data (%LOCALAPPDATA%\EnergyManager and the app's WebView2 storage) is kept on update/uninstall.
[UninstallRun]
Filename: "{app}\server\EnergyManagerService.exe"; Parameters: "--stop"; RunOnceId: "StopEMS"; Flags: runhidden waituntilterminated; Components: server

[Code]
const
  UninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F0D2B7C-3E7A-4C55-9C7B-4D2E8E5A1F10}_is1';

// Compare dotted versions numerically: -1 a<b, 0 equal, 1 a>b.
function CompareVersions(A, B: String): Integer;
var
  PA, PB, NA, NB: Integer;
begin
  Result := 0;
  while (Result = 0) and ((A <> '') or (B <> '')) do
  begin
    PA := Pos('.', A); PB := Pos('.', B);
    if PA = 0 then begin NA := StrToIntDef(A, 0); A := ''; end
    else begin NA := StrToIntDef(Copy(A, 1, PA - 1), 0); A := Copy(A, PA + 1, Length(A)); end;
    if PB = 0 then begin NB := StrToIntDef(B, 0); B := ''; end
    else begin NB := StrToIntDef(Copy(B, 1, PB - 1), 0); B := Copy(B, PB + 1, Length(B)); end;
    if NA < NB then Result := -1 else if NA > NB then Result := 1;
  end;
end;

function InitializeSetup(): Boolean;
var
  Installed: String;
begin
  Result := True;
  if RegQueryStringValue(HKCU, UninstKey, 'DisplayVersion', Installed) and
     (CompareVersions(Installed, '{#AppVersion}') > 0) and
     (ExpandConstant('{param:ALLOWDOWNGRADE|0}') <> '1') then
  begin
    Log('Downgrade blocked: installed ' + Installed + ', this installer {#AppVersion}');
    SuppressibleMsgBox('Er is al een nieuwere versie van Energy Manager geinstalleerd (' + Installed +
      '). Deze installer bevat versie {#AppVersion}. Een oudere versie kan de gegevens van de nieuwere versie niet ' +
      'veilig gebruiken; de installatie wordt afgebroken.', mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

// True while one of our server processes is still running (tasklist via cmd; exit code 0 = found).
function ServerRunning(): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'),
    '/C tasklist /NH /FI "IMAGENAME eq EnergyManagerService.exe" | find /I "EnergyManager" >NUL || ' +
    'tasklist /NH /FI "IMAGENAME eq EnergyManagerServer.exe" | find /I "EnergyManager" >NUL',
    '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

// Stop a running built-in server before files are replaced (update), so it can release
// devices and Windows does not keep the files locked. The stop command returns when the port is
// closed; the process may still be finishing, so wait until it has really exited (max. 60 s).
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Exe: String;
  Code, I: Integer;
begin
  Exe := ExpandConstant('{app}\server\EnergyManagerService.exe');
  if FileExists(Exe) then
  begin
    Exec(Exe, '--stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
    I := 0;
    while ServerRunning() and (I < 120) do
    begin
      Sleep(500);
      I := I + 1;
    end;
    if ServerRunning() then
      Log('EMS still running after graceful stop; Restart Manager will close it')
    else
      Log('EMS stopped before replacing files');
  end;
  Result := '';
end;
