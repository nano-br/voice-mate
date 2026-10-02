; Inno Setup script of the VoiceMate companion: a per-user installer (no admin rights)
; for the PyInstaller build in dist\VoiceMate (voicemate-companion.spec).
;
; Build it with `make companion-installer`, which runs `make companion-build` first and
; then ISCC (Inno Setup 6.3 or newer: `winget install JRSoftware.InnoSetup`).
; Output: dist\installer\VoiceMate-Setup-<version>.exe. The version comes from
; VoiceMate.exe's version resource, which the spec takes from pyproject.toml.

#if VER < EncodeVer(6, 3, 0)
  #error Inno Setup 6.3 or newer is required (x64compatible, UTF-8 script without BOM)
#endif

#define AppName "VoiceMate"
#define AppExeName "VoiceMate.exe"
#define AppPublisher "NanoBR"
#define AppURL "https://github.com/nano-br/voice-mate"
; Identifies the installation across upgrades: never change it.
#define AppGuid "36EAA32F-02FB-465C-B467-556918ACBC96"
; "{{" is Inno's escape for a literal "{": AppId becomes {36EAA32F-...}.
#define AppId "{{" + AppGuid + "}"
; Where Inno Setup registers a per-user installation of this AppId.
#define UninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\{" + AppGuid + "}_is1"
; The next three must equal app/companion/contract.py (tests/test_companion_packaging.py
; checks them). APP_USER_MODEL_ID: the shortcut's AppUserModelID must be the one the
; process sets, or Windows shows a second taskbar button next to the pinned one.
#define AppUserModelID "VoiceMate.Companion"
; INSTALLER_APP_MUTEX: the companion holds Local\VoiceMate.Companion while it runs.
#define AppMutex "VoiceMate.Companion"
; AUTOSTART_RUN_VALUE: the HKCU Run value, shared with the app's "start at sign-in" setting.
#define RunValueName "VoiceMate"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

#define DistDir AddBackslash(SourcePath) + "..\..\dist\VoiceMate"
#if !FileExists(DistDir + "\" + AppExeName)
  #error dist\VoiceMate\VoiceMate.exe not found: run `make companion-build` first
#endif
#ifndef AppVersion
  #define AppVersion GetStringFileInfo(DistDir + "\" + AppExeName, "ProductVersion")
#endif

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#GetVersionNumbersString(DistDir + "\" + AppExeName)}
; Per user: no UAC prompt, everything under the user's profile and HKCU.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
AppMutex={#AppMutex}
SetupMutex={#AppMutex}.Setup
; Qt 6 needs Windows 10 1809 or newer, 64-bit.
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=..\..\app\companion\assets\voicemate.ico
OutputDir=..\..\dist\installer
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=auto

[Languages]
; English first: the fallback when Windows speaks none of these languages.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
; Windows 11 does not let installers pin to the taskbar: tell the user how to do it.
english.FinishedLabel=Setup has finished installing [name] on your computer.%n%nTo pin it to the taskbar: open Start, search for VoiceMate, right-click it and choose "Pin to taskbar".
brazilianportuguese.FinishedLabel=O instalador terminou de instalar o [name] no seu computador.%n%nPara fixá-lo na barra de tarefas: abra o Iniciar, pesquise VoiceMate, clique nele com o botão direito e escolha "Fixar na barra de tarefas".
spanish.FinishedLabel=El programa completó la instalación de [name] en su sistema.%n%nPara anclarlo a la barra de tareas: abra Inicio, busque VoiceMate, haga clic derecho sobre él y elija "Anclar a la barra de tareas".

[CustomMessages]
english.AppComment=Voice to clipboard with local Whisper
brazilianportuguese.AppComment=Voz para a área de transferência com Whisper local
spanish.AppComment=Voz al portapapeles con Whisper local
english.QuitBeforeSetup=VoiceMate is running. Setup will close it, and the engine it started, before installing.%n%nContinue?
brazilianportuguese.QuitBeforeSetup=O VoiceMate está em execução. O instalador vai fechá-lo, junto com o motor que ele iniciou, antes de instalar.%n%nContinuar?
spanish.QuitBeforeSetup=VoiceMate se está ejecutando. El instalador lo cerrará, junto con el motor que inició, antes de instalar.%n%n¿Continuar?

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Off by default, like the companion's `start_at_login` setting, and offered on a fresh
; install only: after that the app's own setting owns the Run value, and UsePreviousTasks
; would turn it back on at every upgrade after the user switched it off in the app.
Name: "autostart"; Description: "{cm:AutoStartProgram,{#AppName}}"; GroupDescription: "{cm:AutoStartProgramGroupDescription}"; Flags: unchecked; Check: not IsInstalled

[InstallDelete]
; An upgrade replaces the whole bundle: stale DLLs from another PySide6 must not linger.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Every shortcut carries the AUMID (see above), the desktop one included.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Comment: "{cm:AppComment}"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Comment: "{cm:AppComment}"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "{#RunValueName}"; ValueData: """{app}\{#AppExeName}"" --autostart"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; skipifsilent: a silent install (/SILENT or /VERYSILENT, e.g. a future auto-update) does
; not relaunch the app; such an updater has to start VoiceMate.exe itself afterwards.
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Caches the companion regenerates, and the Not copied list (pending.json, plus its
; .tmp and .broken-* copies), which holds transcription text: nothing of it may stay
; in the profile. The settings in %APPDATA%\VoiceMate are kept.
Type: filesandordirs; Name: "{localappdata}\{#AppName}\cues"
Type: filesandordirs; Name: "{localappdata}\{#AppName}\logs"
Type: files; Name: "{localappdata}\{#AppName}\pending.json*"
Type: dirifempty; Name: "{localappdata}\{#AppName}"

[Code]
const
  QuitTimeoutMs = 20000; { the companion caps its own quit at 15 s }
  PollMs = 250;

function IsAppRunning(): Boolean;
begin
  Result := CheckForMutexes('{#AppMutex}');
end;

{ Where this app is already installed for the current user, from its uninstall key. }
function InstalledDir(var Dir: String): Boolean;
begin
  Result := RegQueryStringValue(HKCU, '{#UninstallKey}', 'Inno Setup: App Path', Dir);
end;

{ Check function of the autostart task: true on upgrades and reinstalls. }
function IsInstalled(): Boolean;
var
  Dir: String;
begin
  Result := InstalledDir(Dir);
end;

{ Ask the running companion to quit through its single-instance channel, so it stops
  the engine it started cleanly, then wait for its mutex to go away. The forwarding
  process is not waited for: polling the mutex bounds the wait in every case. If the
  app is still running afterwards, AppMutex makes Setup/Uninstall ask the user.
  Nothing is shown during the wait (at most QuitTimeoutMs, usually a few seconds):
  neither the wizard nor the uninstall progress form exists yet at this point, and a
  custom form would depend on CreateCustomForm, whose prototype changed in 6.6. }
procedure QuitRunningApp(const ExePath: String);
var
  ResultCode, WaitedMs: Integer;
begin
  if (not IsAppRunning()) or (not FileExists(ExePath)) then
    Exit;
  Log('Asking VoiceMate to quit: "' + ExePath + '" --command quit');
  if not Exec(ExePath, '--command quit', '', SW_HIDE, ewNoWait, ResultCode) then
  begin
    Log('Could not start it: ' + SysErrorMessage(ResultCode));
    Exit;
  end;
  WaitedMs := 0;
  while IsAppRunning() and (WaitedMs < QuitTimeoutMs) do
  begin
    Sleep(PollMs);
    WaitedMs := WaitedMs + PollMs;
  end;
end;

{ Runs after the language dialog and before Setup's own AppMutex check. Upgrade while
  running: quit the installed copy (with the user's consent) instead of asking them to
  find the tray icon. }
function InitializeSetup(): Boolean;
var
  Dir: String;
begin
  Result := True;
  if not IsAppRunning() then
    Exit;
  if not InstalledDir(Dir) then
    Exit; { not installed (e.g. run from source): the AppMutex prompt handles it }
  if SuppressibleMsgBox(CustomMessage('QuitBeforeSetup'), mbConfirmation, MB_OKCANCEL, IDOK) <> IDOK then
  begin
    Result := False;
    Exit;
  end;
  QuitRunningApp(AddBackslash(Dir) + '{#AppExeName}');
end;

{ Uninstall order: InitializeUninstall, the "remove VoiceMate?" confirmation,
  usAppMutexCheck, the AppMutex check, usUninstall, usPostUninstall. Quitting at
  usAppMutexCheck means a cancelled uninstall leaves the app running. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  case CurUninstallStep of
    usAppMutexCheck:
      QuitRunningApp(ExpandConstant('{app}\{#AppExeName}'));
    usPostUninstall:
      { The app may have created the Run value itself ("start at sign-in" in its
        settings), which [Registry] does not know about: remove it in every case. }
      RegDeleteValue(HKCU, '{#RunKey}', '{#RunValueName}');
  end;
end;
