; Energy Manager — Windows installer (Inno Setup 6).
; Contents: the Windows app (Tauri) and, optionally, the local EMS server.
; Build (from energy-manager\):  iscc /DAppVersion=0.2.0 windows\installer.iss
; Expects: dist\app\Energy Manager.exe  and  dist\EnergyManagerServer\ (PyInstaller one-folder)

#ifndef AppVersion
  #define AppVersion "0.2.0"
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
Name: "app"; Description: "Alleen de app (EMS draait op de Raspberry Pi)"
Name: "full"; Description: "App + lokale EMS-server (testen of Demo Mode zonder Raspberry Pi)"

[Components]
Name: "app"; Description: "Energy Manager-app"; Types: app full; Flags: fixed
Name: "server"; Description: "Lokale EMS-server (Demo Mode / testen)"; Types: full

[Tasks]
Name: "desktopicon"; Description: "Snelkoppeling op het bureaublad"; GroupDescription: "Extra:"

[Files]
Source: "..\dist\app\Energy Manager.exe"; DestDir: "{app}"; Components: app; Flags: ignoreversion
Source: "..\dist\EnergyManagerServer\*"; DestDir: "{app}\server"; Components: server; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Energy Manager"; Filename: "{app}\Energy Manager.exe"
Name: "{autodesktop}\Energy Manager"; Filename: "{app}\Energy Manager.exe"; Tasks: desktopicon
Name: "{group}\Energy Manager Server (Demo Mode)"; Filename: "{app}\server\EnergyManagerServer.exe"; Parameters: "--demo"; Components: server
Name: "{group}\Energy Manager Server"; Filename: "{app}\server\EnergyManagerServer.exe"; Components: server
Name: "{group}\Energy Manager verwijderen"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\server\EnergyManagerServer.exe"; Parameters: "--demo"; Description: "Demo-server nu starten"; Components: server; Flags: nowait postinstall skipifsilent unchecked
Filename: "{app}\Energy Manager.exe"; Description: "Energy Manager nu starten"; Flags: nowait postinstall skipifsilent

; User data (%LOCALAPPDATA%\EnergyManager and the app's WebView2 storage) is kept on update/uninstall.
