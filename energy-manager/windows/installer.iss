; Energy Manager — Windows installer (Inno Setup 6).
; Contents: the Windows app (Tauri) and the built-in EMS server (all-in-one, no Raspberry Pi
; needed). The app starts the server in the background (EnergyManagerService.exe).
; Build (from energy-manager\):  iscc /DAppVersion=0.2.0 windows\installer.iss
; Expects: dist\app\Energy Manager.exe  and  dist\EnergyManagerServer\ (PyInstaller one-folder)

#ifndef AppVersion
  #define AppVersion "0.3.0"
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
// Stop a running built-in server before files are replaced (update), so it can release
// devices and Windows does not keep the files locked.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Exe: String;
  Code: Integer;
begin
  Exe := ExpandConstant('{app}\server\EnergyManagerService.exe');
  if FileExists(Exe) then
    Exec(Exe, '--stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
