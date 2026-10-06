; Inno Setup script — Open Marketing OS (Windows x64)
;
; Produces OpenMarketingOS-Quick-Setup.exe: a normal, non-technical Windows
; installer. No AI configuration happens here — that belongs in the app's
; first-run wizard, because the app can change provider later without the user
; reinstalling.
;
; Design rules this script enforces:
;   * No technical questions during install. Only language, install location,
;     and an optional desktop shortcut.
;   * Uninstall removes application files ONLY. User data lives in
;     %LOCALAPPDATA%\OpenMarketingOS and is never touched, silently or
;     otherwise. See the "User data" note on the uninstall page.
;   * Single instance for the app, so a running backend is not left orphaned.
;
; Build with:  scripts\package\build_installer.ps1
; Requires Inno Setup 6 (`choco install innosetup`, or https://jrsoftware.org/isdl.php)

#define AppName        "Open Marketing OS"
#define AppShortName   "OpenMarketingOS"
#define AppExeName     "OpenMarketingOS.exe"
#define AppVersion     "1.1.0-beta.1"
#define AppPublisher   "Open Marketing OS"
#define MinWindows     "10.0"
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef PayloadDir
  #define PayloadDir "..\build\payload"
#endif

; AppURL / AppReleasesURL are GENERATED from app/project_urls.py so the URL baked
; into every user's Add/Remove Programs entry cannot drift from the README or
; the security contact link. Regenerate with:
;     python scripts/package/sync_project_urls.py
#include "project_url.iss"

[Setup]
AppId={{7C4E1B2A-9F3D-4A6E-8B21-0D5E7C9A4F13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
; Updates land on the releases page, not the source tree: a user who clicks
; "check for updates" wants a new installer, not a diff.
AppUpdatesURL={#AppReleasesURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=OpenMarketingOS-Quick-Setup
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; PrivilegesRequired=admin installs per-machine under Program Files, which is
; read-only for the app. That is deliberate: it is what forces every mutable
; byte into %LOCALAPPDATA%, and it proves it on every install.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion={#MinWindows}
CloseApplications=yes
RestartApplications=no
; This beta is UNSIGNED. There is no SignTool directive and no certificate, and
; we do not pretend otherwise: Windows SmartScreen will show an
; "Unknown publisher" warning and the README says so. See
; docs/distribution/windows-packaging-decision.md for the future signing path.
; A signing certificate must be purchased deliberately by the project owner;
; it is not bundled, configured or implied here.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
; The frozen payload built by scripts\package\build_windows.ps1.
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "_internal\runtime\*"; Flags: ignoreversion recursesubdirs createallsubdirs
; The managed Local runtime lives in the per-user writable root expected by
; LocalRuntimeManager. The Python bundle and model cache remain separate.
Source: "{#PayloadDir}\_internal\runtime\*"; DestDir: "{localappdata}\OpenMarketingOS\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
; Nothing else is required: the interpreter, every Python dependency, SQLite and
; the prebuilt React bundle are all inside the payload.

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; \
    Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only build-time leftovers inside the install folder. Never the user data.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
const
  UserDataDir = 'OpenMarketingOS';

function DataDir(): string;
begin
  { %LOCALAPPDATA% is per-user and correct here: OMOS is a single-user app
    whose credentials are DPAPI-bound to the Windows account. }
  Result := ExpandConstant('{localappdata}') + '\' + UserDataDir;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDirPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    { Create the data directory up front and make it writable, so the first run
      never has to deal with a permissions surprise. }
    DataDirPath := DataDir();
    if not DirExists(DataDirPath) then
      CreateDir(DataDirPath);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Response: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { Deliberately does NOT delete the user data folder. Projects, chats, files
      and encrypted credentials survive an uninstall, so reinstalling or
      downgrading cannot cost a user their work. Say so at the moment the user
      is actually wondering about it, and offer the explicit separate choice
      rather than deciding for them. }
    { Note: no continuation line may begin with '#'. Inno's preprocessor reads
      a leading '#' as a directive and aborts with "Unknown preprocessor
      directive" — which is why the line breaks below are placed after the
      '+ #13#10 +' fragments rather than before them. }
    Response := MsgBox(
      'Open Marketing OS has been uninstalled.'
      + #13#10 + #13#10
      + 'Your projects, files and credentials were kept in:'
      + #13#10 + DataDir()
      + #13#10 + #13#10
      + 'Reinstalling will pick them up again.'
      + #13#10 + #13#10
      + 'Would you like to delete that folder and all of its data now?',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
    if Response = IDYES then
    begin
      { Explicit, separate, and only after the app files are gone. Guarded so a
        failure here cannot turn a successful uninstall into a rolled-back one. }
      try
        DelTree(DataDir(), True, True, True);
      except
        { Left in place on purpose; better an orphaned data folder the user can
          remove by hand than an uninstaller that reports failure. }
      end;
    end;
  end;
end;
