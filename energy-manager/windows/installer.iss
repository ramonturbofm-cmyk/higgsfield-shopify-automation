; Inno Setup script — builds EnergyManager-Setup-<versie>.exe
; Build: iscc /DAppVersion=0.1.0 windows\installer.iss   (after PyInstaller produced dist\EnergyManager.exe)

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F0D2B7C-3E7A-4C55-9C7B-4D2E8E5A1F10}
AppName=Energy Manager
AppVersion={#AppVersion}
AppPublisher=Energy Manager
DefaultDirName={localappdata}\Programs\Energy Manager
DefaultGroupName=Energy Manager
DisableProgramGroupPage=yes
; Per-user install: no administrator rights needed
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=EnergyManager-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Energy Manager

[Languages]
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"

[Tasks]
Name: "desktopicon"; Description: "Snelkoppeling op het bureaublad"; GroupDescription: "Extra:"

[Files]
Source: "..\dist\EnergyManager.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Energy Manager"; Filename: "{app}\EnergyManager.exe"
Name: "{group}\Energy Manager verwijderen"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Energy Manager"; Filename: "{app}\EnergyManager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\EnergyManager.exe"; Description: "Energy Manager nu starten"; Flags: nowait postinstall skipifsilent

; User data (%LOCALAPPDATA%\EnergyManager: ems.yaml, logs, simulations) is kept on uninstall and update.
