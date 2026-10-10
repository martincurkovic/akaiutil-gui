#define MyAppName "akaiutil-GUI"
#define MyAppPublisher "Martin Curkovic"
#define MyAppExeName "main.exe"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppId={{283FC58B-AB1F-48BC-8713-AA844EA758B8}
ArchitecturesInstallIn64BitMode=x64compatible
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputBaseFilename=akaiutil-GUI-Setup
OutputDir=..\..
Compression=lzma
SolidCompression=yes
LicenseFile=..\..\LICENSE
UninstallDisplayIcon={app}\{#MyAppExeName}
DisableProgramGroupPage=yes


[Files]
Source: "..\..\src\akaiutil-GUI.dist\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
