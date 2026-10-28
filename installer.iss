; Установщик YM Mini (Inno Setup 6).
; Собирается командой build.bat или автоматически на GitHub (.github/workflows/build.yml).

#define MyAppName "YM Mini"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppExe "YMMini.exe"

[Setup]
AppId={{8F3C2A51-6B7D-4E2A-9C1F-5D4B3A2E1F70}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=YM Mini
DefaultDirName={localappdata}\Programs\YM Mini
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=YMMini-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; Flags: unchecked

[Files]
Source: "dist\YMMini\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; убрать файлы старой версии, чтобы не смешивались с новыми
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autoprograms}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; запуск вместе с Windows: программа тихо ждёт в фоне, пока не откроют Яндекс Музыку
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "YMMini"; ValueData: """{app}\{#MyAppExe}"" --background"; Flags: uninsdeletevalue

[Run]
; сразу запустить в фоне после установки
Filename: "{app}\{#MyAppExe}"; Parameters: "--background"; Flags: nowait

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im {#MyAppExe}"; Flags: runhidden; RunOnceId: "StopYMMini"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  { перед копированием файлов остановить уже запущенную копию }
  if CurStep = ssInstall then
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#MyAppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  { и старую версию, запущенную из исходников через Python (ym_mini.py) }
  if CurStep = ssInstall then
    Exec('powershell.exe',
      '-NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | ' +
      'Where-Object { $_.Name -like ''python*'' -and $_.CommandLine -like ''*ym_mini.py*'' } | ' +
      'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"',
      '', SW_HIDE, ewWaitUntilTerminated, Code);
end;
