; Inno Setup script — wraps the PyInstaller one-folder build into a single
; setup.exe with a Start Menu shortcut and an uninstall entry.
;
; Build (Windows only — needs Inno Setup's ISCC.exe):
;   1) run build.ps1 first  -> produces dist\SalesRetro\
;   2) "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;   Output: packaging\Output\SalesRetro-Setup.exe
;
; Design: PER-USER install (no admin / no UAC prompt). Installs to
; %LOCALAPPDATA%\Programs\SalesRetro. The app writes its data to
; %LOCALAPPDATA%\SalesRetro (see launcher.py), so a read-only program dir
; is fine.

#define AppName "Sales Retro"
#define AppVersion "0.1.0"
#define AppPublisher "Sales Retro"
#define AppExeName "SalesRetro.exe"

[Setup]
; A stable GUID identifies the app across upgrades/uninstall. Keep it fixed.
AppId={{8F3C2A91-5D7E-4B6A-9C0F-2E1A7D4B9C33}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\SalesRetro
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=SalesRetro-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
; Only Default.isl ships with a stock Inno Setup 6 install (what choco gives
; the CI runner). ChineseSimplified.isl is an unofficial add-on and is NOT
; bundled, so referencing it makes ISCC fail. English wizard is fine for a
; double-click one-click installer.
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
; Ship the entire PyInstaller one-folder output produced by build.ps1.
Source: "dist\SalesRetro\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch right after install; it opens http://127.0.0.1:8765/backend.html
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
