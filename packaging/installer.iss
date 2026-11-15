; Music Video AutoSync installer (Inno Setup 6). One installer for the helper and the panel.
; Per-user install, no admin rights: %LOCALAPPDATA%\Programs\MusicVideoAutoSync
; Built by .github/workflows/release.yml:  iscc /DAppVersion=0.1.0 /DDistDir=... packaging\installer.iss

#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif
#ifndef DistDir
  #define DistDir "..\dist\MusicVideoAutoSync"
#endif
#ifndef CcxPath
  #define CcxPath "out\MusicVideoAutoSync_premierepro.ccx"
#endif
#define Upia "{commoncf64}\Adobe\Adobe Desktop Common\RemoteComponents\UPI\UnifiedPluginInstallerAgent\UnifiedPluginInstallerAgent.exe"

[Setup]
AppId={{6E2B6F1C-9D7A-4B0E-9C3A-6C1D5A7B8E21}
AppName=Music Video AutoSync
AppVersion={#AppVersion}
AppPublisher=Music Video AutoSync
DefaultDirName={localappdata}\Programs\MusicVideoAutoSync
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=out
OutputBaseFilename=MusicVideoAutoSync-Setup-{#AppVersion}
SetupIconFile=autosync.ico
UninstallDisplayIcon={app}\AutoSyncHelper.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
WizardStyle=modern

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "{#CcxPath}"; DestDir: "{app}\panel"; Flags: ignoreversion
; UPIA rejects .ccx paths with spaces (docs/PREMIERE_API_NOTES.md item 11): keep a copy in a
; space-free location on the system drive for the plugin installer.
Source: "{#CcxPath}"; DestDir: "{commonappdata}\MusicVideoAutoSync"; Flags: ignoreversion
Source: "..\docs\INSTALL.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\USER_GUIDE.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\CLIENT_SMOKE_TEST.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\LIMITATIONS.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\LICENSES.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Music Video AutoSync Helper"; Filename: "{app}\AutoSyncHelper.exe"; Parameters: "tray"
Name: "{userstartup}\Music Video AutoSync Helper"; Filename: "{app}\AutoSyncHelper.exe"; Parameters: "tray"
Name: "{userprograms}\Music Video AutoSync - Premiere panel (.ccx)"; Filename: "{app}\panel"

[Run]
; Pairing token for the panel (spec section 6: generated at install).
Filename: "{app}\AutoSyncHelper.exe"; Parameters: "init-token"; Flags: runhidden waituntilterminated
; Install the panel into Premiere through Adobe's plugin installer when Creative Cloud is present.
Filename: "{#Upia}"; Parameters: "/install ""{commonappdata}\MusicVideoAutoSync\MusicVideoAutoSync_premierepro.ccx"""; Flags: runhidden waituntilterminated; Check: UpiaExists; StatusMsg: "Installing the Premiere Pro panel..."
Filename: "{app}\AutoSyncHelper.exe"; Parameters: "tray"; Flags: nowait postinstall skipifsilent; Description: "Start the AutoSync helper now"

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM AutoSyncHelper.exe"; Flags: runhidden; RunOnceId: "StopHelper"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM autosync-engine.exe"; Flags: runhidden; RunOnceId: "StopEngine"
Filename: "{#Upia}"; Parameters: "/remove ""Music Video AutoSync"""; Flags: runhidden; Check: UpiaExists; RunOnceId: "RemovePanel"

[UninstallDelete]
; Token, config, logs and the analysis cache. Source footage is never stored here.
Type: filesandordirs; Name: "{userappdata}\MusicVideoAutoSync"
Type: filesandordirs; Name: "{localappdata}\MusicVideoAutoSync"
Type: filesandordirs; Name: "{commonappdata}\MusicVideoAutoSync"
Type: filesandordirs; Name: "{app}"

[Code]
function UpiaExists(): Boolean;
begin
  Result := FileExists(ExpandConstant('{#Upia}'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not UpiaExists()) then
    Log('Adobe UnifiedPluginInstallerAgent not found; the panel .ccx is in ' + ExpandConstant('{app}\panel') + ' for a double-click install.');
end;
