; Instalator Sony Popup (Inno Setup 6)
; Lokalnie:  iscc /DMyAppVersion=1.0.0 installer.iss   (po zbudowaniu dist\SonyPopup przez PyInstaller)

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName "Sony Popup"
#define MyAppExe  "SonyPopup.exe"

[Setup]
AppId={{6B1C5E2A-3F4D-4E8B-9A71-5D2C0F9E7B13}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Krystian Borys
AppPublisherURL=https://github.com/cristianwolff33/sony-popup
DefaultDirName={localappdata}\Programs\SonyPopup
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; instalacja bez uprawnień administratora (dla bieżącego użytkownika)
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=SonyPopup-Setup
SetupIconFile=sony_popup.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0

[Languages]
Name: "polish";  MessagesFile: "compiler:Languages\Polish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
polish.AutoStart=Uruchamiaj razem z Windows
english.AutoStart=Start with Windows
polish.LaunchNow=Uruchom Sony Popup
english.LaunchNow=Launch Sony Popup

[Tasks]
Name: "autostart";   Description: "{cm:AutoStart}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\SonyPopup\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{userdesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "SonyPopup"; ValueData: """{app}\{#MyAppExe}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchNow}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\sony_popup.log"

[Code]
procedure KillApp();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#MyAppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(400);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillApp();   // aktualizacja działającej aplikacji
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // bez zadania "autostart" usuń ewentualny stary wpis
  if (CurStep = ssPostInstall) and not WizardIsTaskSelected('autostart') then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'SonyPopup');
end;

function InitializeUninstall(): Boolean;
begin
  KillApp();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'SonyPopup');
end;
