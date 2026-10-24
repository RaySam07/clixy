; Inno Setup script for Clixy.
;
; Wraps the PyInstaller --onedir output (dist/Clixy/) into one Setup.exe for
; GitHub Releases. Installs per user: no admin prompt, so it also installs on
; locked-down work machines. The release workflow builds it on every tag.
;
; Build:
;     iscc /DAppVersion=1.0.0 installer\clixy.iss
;
; Output: installer\Output\Clixy-Setup.exe
;
; Inno Setup 6+ required: https://jrsoftware.org/isdl.php (free).

#define AppName "Clixy"
#ifndef AppVersion
  #define AppVersion "1.0.1"
#endif
#define AppPublisher "Rayan Sameer"
#define AppURL "https://github.com/RaySam07/clixy"
#define AppExeName "Clixy.exe"

[Setup]
AppId={{6122212D-3657-41FD-9537-B7FC8A22C9FE}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
; Per-user install, no admin prompt. {userpf} = %LOCALAPPDATA%\Programs
DefaultDirName={userpf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=Output
; A stable name keeps /releases/latest/download/Clixy-Setup.exe working.
OutputBaseFilename=Clixy-Setup
SetupIconFile=..\assets\clixy.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
CloseApplications=yes
SetupLogging=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start Clixy when I sign in"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Clixy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Uninstall keeps your notes and Knowledge Folder. For a clean wipe, delete
; ~/.clixy/ and ~/Documents/Clixy Wiki/ by hand.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
// A running Clixy holds its files open. Close it before installing or
// uninstalling, so an update just works; it keeps nothing unsaved.
procedure CloseClixy();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(300);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseClixy();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    CloseClixy();
end;
