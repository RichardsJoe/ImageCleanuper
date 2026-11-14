; Inno Setup script: per-user install (no admin rights needed).
; settings.ini and the data\ folder are created by the app and are never touched by
; installs, updates or uninstall, so settings and learned data survive.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
AppId={{6C1C6F2E-3B1A-4C55-9C2B-1F7E3D2A9B10}
AppName=ImageCleanuper
AppVersion={#AppVersion}
AppPublisher=ImageCleanuper
; Per-user install: {userpf} is C:\Users\<you>\AppData\Local\Programs. No admin rights needed.
DefaultDirName={userpf}\ImageCleanuper
DefaultGroupName=ImageCleanuper
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
UsePreviousAppDir=no
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=ImageCleanuper-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ImageCleanuper.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\ImageCleanuper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ImageCleanuper"; Filename: "{app}\ImageCleanuper.exe"
Name: "{userdesktop}\ImageCleanuper"; Filename: "{app}\ImageCleanuper.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ImageCleanuper.exe"; Description: "Start ImageCleanuper"; Flags: nowait postinstall skipifsilent
